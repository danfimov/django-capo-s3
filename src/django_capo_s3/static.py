from django.contrib.staticfiles.storage import ManifestFilesMixin
from django.core.files.storage import Storage
from typing_extensions import Unpack

from django_capo_s3.core import S3StorageOptions
from django_capo_s3.storage import S3Storage


class S3StaticStorage(S3Storage):
    """S3 storage tuned for static files, without content hashing.

    Meant for the staticfiles backend when you don't need hashed names. Two defaults differ from the base
    storage: files are overwritten in place rather than getting a unique suffix, and URLs are unsigned so a
    CDN or browser can cache them.
    """

    def __init__(self, **options: Unpack[S3StorageOptions]) -> None:
        """Default to overwriting files and unsigned URLs, then hand off to the base storage."""
        options.setdefault("file_overwrite", True)
        options.setdefault("querystring_auth", False)
        super().__init__(**options)


# Django's HashedFilesMixin.url(name, force=False) is a narrower override than S3Storage.url(name, *, expire=...);
# harmless here because hashed static assets are always served as unsigned URLs, so expire never applies.
class S3ManifestStaticStorage(ManifestFilesMixin, S3StaticStorage):  # ty: ignore[invalid-method-override]
    """S3 static storage that adds content-hashed names and a manifest.

    During collectstatic each file is stored under a content-hashed name like style.<hash>.css, references
    inside CSS and JS are rewritten to those names, and the static template tag resolves through the manifest —
    so assets get immutable, aggressively cacheable URLs.

    Pass manifest_storage to keep the manifest somewhere other than the bucket (say, a local FileSystemStorage)
    so workers don't fetch it from S3 on startup. By default it lives in the bucket next to the assets.
    """

    def __init__(
        self,
        *,
        manifest_storage: Storage | None = None,
        **options: Unpack[S3StorageOptions],
    ) -> None:
        """Forward the manifest location through the mixins to the static base."""
        super().__init__(manifest_storage=manifest_storage, **options)
