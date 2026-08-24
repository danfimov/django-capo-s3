import contextlib
import gzip
import hashlib
from collections.abc import Generator, Iterator
from typing import Any, Unpack

from django.contrib.staticfiles.storage import ManifestFilesMixin
from django.core.files.base import ContentFile, File
from django.core.files.storage import Storage
from typing_extensions import override

from django_capo_s3.core import S3StorageOptions
from django_capo_s3.storage import S3Storage


class S3StaticStorage(S3Storage):
    """S3 storage tuned for static files, without content hashing.

    Meant for the staticfiles backend when you don't need hashed names. Two defaults differ from the base
    storage: files are overwritten in place rather than getting a unique suffix, and URLs are unsigned so a
    CDN or browser can cache them.
    """

    def __init__(self, **options: Unpack[S3StorageOptions]) -> None:
        """Default to overwriting files and serving unsigned URLs, then hand off to the base storage."""
        _ = options.setdefault("file_overwrite", True)
        _ = options.setdefault("querystring_auth", False)
        super().__init__(**options)


class S3ManifestStaticStorage(ManifestFilesMixin, S3StaticStorage):  # type: ignore[misc]
    """S3 static storage that adds content-hashed names and a manifest.

    During collectstatic each file is stored under a content-hashed name like style.<hash>.css, references
    inside CSS and JS are rewritten to those names, and the static template tag resolves through the manifest —
    so assets get immutable, aggressively cacheable URLs.

    Pass manifest_storage to keep the manifest somewhere other than the bucket (say, a local FileSystemStorage)
    so workers don't fetch it from S3 on startup. By default it lives in the bucket next to the assets.

    On collectstatic the hashing pass lists the bucket once and skips re-uploading any asset whose content is
    already stored, so an unchanged redeploy costs no uploads. Turn it off with skip_unchanged=False (e.g. for
    an S3-compatible store whose ETag isn't a content MD5).
    """

    # Don't upload the pre-substitution hashed file that each pass would otherwise overwrite — only the final
    # one is ever referenced, and skipping the intermediates saves an upload per adjustable file per pass.
    keep_intermediate_files: bool = False

    def __init__(
        self,
        *,
        manifest_storage: Storage | None = None,
        **options: Unpack[S3StorageOptions],
    ) -> None:
        """Enable skip-unchanged by default, then forward the manifest location through the mixins."""
        _ = options.setdefault("skip_unchanged", True)
        self._is_collectstatic_running: bool = False
        self._remote_etags: dict[str, str] = {}
        super().__init__(manifest_storage=manifest_storage, **options)

    @override
    def post_process(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Iterator[tuple[str, str, bool] | tuple[str, None, RuntimeError]]:
        """Run the hashing pass under a collect session, so unchanged assets are not re-uploaded.

        A dry run, or skip_unchanged turned off, falls straight through to Django's behaviour.
        """
        if kwargs.get("dry_run") or not self.options.get("skip_unchanged"):
            yield from super().post_process(*args, **kwargs)
            return
        with self._collect_session():
            yield from super().post_process(*args, **kwargs)

    @contextlib.contextmanager
    def _collect_session(self) -> Generator[None, None, None]:
        """Make deletes and saves skip-aware for the length of one hashing pass."""
        self._remote_etags = self._list_remote_etags()
        self._is_collectstatic_running = True
        try:
            yield
        finally:
            self._is_collectstatic_running = False
            self._remote_etags = {}

    @override
    def delete(self, name: str) -> None:
        """While collecting, skip the delete: its caller re-saves the same key next and S3 PUT overwrites.

        --clear runs before post_process (outside a collect session), so real deletes still happen there.
        """
        if self._is_collectstatic_running:
            return
        super().delete(name)

    @override
    def _save(self, name: str, content: File) -> str:
        """Store the file, or (while collecting) skip the upload when identical content is already stored."""
        if not self._is_collectstatic_running:
            return super()._save(name, content)
        content.seek(0)
        data = content.read()
        if isinstance(data, str):
            data = data.encode()
        gzipped = self._should_gzip(self.content_type(name, content))
        stored = gzip.compress(data, mtime=0) if gzipped else data
        key = self.key(name)
        digest = hashlib.md5(stored, usedforsecurity=False).hexdigest()
        if self._remote_etags.get(key) == digest:
            return name  # identical content already stored — no upload needed
        body = ContentFile(stored)
        self._uploader.upload(
            self.bucket,
            key,
            content=body,
            size=body.size,
            meta=self._object_meta(name, content=content, content_encoding="gzip" if gzipped else None),
        )
        self._remote_etags[key] = digest
        return name

    def _list_remote_etags(self) -> dict[str, str]:
        index: dict[str, str] = {}
        prefix = self.options.get("location", "").strip("/")
        if prefix:
            prefix += "/"
        token: str | None = None
        while True:
            result = self.client.list_objects_v2(self.bucket, prefix=prefix, continuation_token=token)
            for obj in result.get("contents", []):
                key = obj.get("key")
                etag = obj.get("e_tag")
                if key and etag:
                    index[key] = etag.strip('"')
            if not result.get("is_truncated"):
                break
            token = result.get("next_continuation_token")
        return index
