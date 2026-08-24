import gzip
import io
import time
from datetime import datetime
from functools import cached_property
from typing import IO, TYPE_CHECKING, Self, Unpack, cast
from urllib.parse import urlencode

from capo_s3 import (
    AssumeRoleCredentialsProvider,
    CachedProvider,
    ChainedProvider,
    CredentialsProvider,
    ProfileCredentialsProvider,
    S3Client,
    SsoCredentialsProvider,
    WebIdentityCredentialsProvider,
)
from capo_s3.errors import NoSuchKey, NotFound
from capo_s3.types.head_object_output import HeadObjectOutput
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile, File
from django.core.files.storage import Storage
from django.utils import timezone
from django.utils.deconstruct import deconstructible
from typing_extensions import override
from zapros import BaseHandler, Client, StdNetworkHandler, SyncTransport

from django_capo_s3.cloudfront import CloudFrontSigner
from django_capo_s3.core import (
    DEFAULTS,
    S3StorageOptions,
    batched,
    build_public_url,
    guess_content_type,
    normalize_key,
    ssl_context_for,
)
from django_capo_s3.files import S3File
from django_capo_s3.proxy import ProxyHandler
from django_capo_s3.transfer import ObjectMeta, S3Uploader

if TYPE_CHECKING:
    from capo_s3.types.object_identifier import ObjectIdentifier


@deconstructible
class S3Storage(Storage):
    """Store Django files in an S3-compatible bucket through the capo-s3 client.

    Everything is configured through the storage options in settings; only the
    bucket is required.
    """

    def __init__(self, **options: Unpack[S3StorageOptions]) -> None:
        """Fill in the defaults and require a bucket to be configured."""
        merged: S3StorageOptions = {**DEFAULTS, **options}
        if not merged.get("bucket"):
            msg = "S3Storage requires a non-empty 'bucket' option."
            raise ImproperlyConfigured(msg)
        self.options = merged

    @property
    def bucket(self) -> str:
        """The target bucket name."""
        return self.options["bucket"]

    @cached_property
    def _region_clones(self) -> dict[str, Self]:
        return {}

    def for_region(self, region: str) -> Self:
        """Return a storage bound to a different region, reusing this instance's other options.

        Clones are cached per region, so repeated calls reuse the same client and connection pool. Build the
        base storage once, then call for_region(...) to route objects to different regions at runtime — e.g.
        from a FileField that picks the region per model instance.
        """
        if region == self.options.get("region"):
            return self
        clone = self._region_clones.get(region)
        if clone is None:
            options: S3StorageOptions = {**self.options, "region": region}
            clone = type(self)(**options)
            self._region_clones[region] = clone
        return clone

    @cached_property
    def client(self) -> S3Client:
        """A single long-lived client, built lazily from the storage options."""
        return S3Client(
            endpoint=self.options.get("endpoint"),
            region=self.options.get("region"),
            force_path_style=self.options.get("force_path_style"),
            credentials=self.options.get("credentials"),
            credentials_provider=self._credentials_provider(),
            retry_max_attempts=self.options.get("retry_max_attempts"),
            http_handler=self._http_handler(),
        )

    def _credentials_provider(self) -> CredentialsProvider | None:
        """Build the provider for a named AWS profile, or None so capo uses its default resolution chain.

        For a named profile we mirror the relevant slice of capo's default chain (assume-role, web-identity,
        SSO, then static keys) bound to that profile, wrapped in CachedProvider. That way a profile backed by
        role_arn / web_identity_token_file / SSO resolves and auto-refreshes on expiry — a bare
        ProfileCredentialsProvider only reads static keys and would fail outright on those profiles.
        """
        if self.options.get("credentials") is not None:
            return None
        profile = self.options.get("session_profile")
        if not profile:
            return None
        client = Client(self._http_handler())
        chain = CachedProvider(
            ChainedProvider(
                AssumeRoleCredentialsProvider(client, profile),
                WebIdentityCredentialsProvider(client, profile),
                SsoCredentialsProvider(client, profile),
                ProfileCredentialsProvider(profile=profile),
            )
        )
        return cast("CredentialsProvider", chain)  # capo's public credentials_provider param is typed narrower

    def _http_handler(self) -> BaseHandler | None:
        """Build a network handler only when TLS, timeouts, the pool size, or a proxy are customized."""
        context = ssl_context_for(verify=self.options.get("verify", True))
        pool = self.options.get("max_connections_per_host")
        proxies = self.options.get("proxies")
        timeouts = (
            self.options.get("connect_timeout"),
            self.options.get("read_timeout"),
            self.options.get("write_timeout"),
            self.options.get("total_timeout"),
        )
        if context is None and pool is None and not proxies and all(timeout is None for timeout in timeouts):
            return None
        handler: BaseHandler = StdNetworkHandler(
            transport=SyncTransport(ssl_context=context) if context is not None else None,
            connect_timeout=timeouts[0],
            read_timeout=timeouts[1],
            write_timeout=timeouts[2],
            total_timeout=timeouts[3],
            http1={"max_connections_per_host": pool} if pool is not None else True,
        )
        if proxies:
            handler = ProxyHandler(handler, proxies)
        return handler

    @cached_property
    def _cloudfront_signer(self) -> CloudFrontSigner | None:
        key = self.options.get("cloudfront_key")
        key_id = self.options.get("cloudfront_key_id")
        if key and key_id:
            return CloudFrontSigner(key_id, key)
        return None

    def key(self, name: str) -> str:
        """Return the full object key for a file, including the location prefix."""
        return normalize_key(self.options.get("location", ""), name)

    def read_bytes(self, name: str) -> bytes:
        """Download an object in full and return its bytes."""
        buffer = io.BytesIO()
        self.download_into(name, buffer)
        return buffer.getvalue()

    def download_into(self, name: str, target: IO[bytes]) -> None:
        """Stream an object into a writable binary target, one chunk at a time."""
        try:
            with self.client.get_object(self.bucket, self.key(name)) as output:
                for chunk in output["body"]:
                    target.write(chunk)
        except NoSuchKey as exc:
            msg = f"File does not exist: {name}"
            raise FileNotFoundError(msg) from exc

    def write_bytes(self, name: str, data: bytes) -> None:
        """Upload bytes to a file's exact key, without collision-avoidance renaming."""
        body = ContentFile(data)
        self._uploader.upload(self.bucket, self.key(name), content=body, size=body.size, meta=self._object_meta(name))

    @cached_property
    def _uploader(self) -> S3Uploader:
        return S3Uploader(
            self.client,
            threshold=self.options["multipart_threshold"],
            chunk_size=self.options["multipart_chunksize"],
            concurrency=self.options["multipart_concurrency"],
        )

    def content_type(self, name: str, content: File | None = None) -> str:
        """Resolve the Content-Type to store an object under."""
        return (
            self.options.get("object_parameters", {}).get("content_type")
            or getattr(content, "content_type", None)
            or guess_content_type(name)
            or self.options["default_content_type"]
        )

    def _object_meta(
        self, name: str, *, content: File | None = None, content_encoding: str | None = None
    ) -> ObjectMeta:
        extra = {k: v for k, v in self.options.get("object_parameters", {}).items() if k != "content_type"}
        return ObjectMeta(
            content_type=self.content_type(name, content),
            content_encoding=content_encoding,
            acl=self.options.get("default_acl"),
            extra=extra,
        )

    def _open(self, name: str, mode: str = "rb") -> File:
        """Open a file as a lazily fetched, buffered handle."""
        return S3File(name, mode, self)

    def _save(self, name: str, content: File) -> str:
        """Store a file, gzip-compressing it first when its content type is eligible."""
        content.seek(0)
        key = self.key(name)
        if self._should_gzip(self.content_type(name, content)):
            body = ContentFile(
                gzip.compress(
                    content.read(),
                    mtime=0,  # keeps the compressed bytes reproducible run-to-run, so skip_unchanged can match by ETag
                )
            )
            self._uploader.upload(
                self.bucket,
                key,
                content=body,
                size=body.size,
                meta=self._object_meta(name, content=content, content_encoding="gzip"),
            )
        else:
            self._uploader.upload(
                self.bucket, key, content=content, size=content.size, meta=self._object_meta(name, content=content)
            )
        return name

    @override
    def delete(self, name: str) -> None:
        """Delete an object, treating an already-missing one as success."""
        try:
            _ = self.client.delete_object(self.bucket, self.key(name))
        except (NoSuchKey, NotFound):
            return

    def delete_objects(self, names: list[str]) -> None:
        """Delete many objects with one bulk request per batch; missing keys are ignored."""
        for batch in batched(names, 1000):  # S3 DeleteObjects caps each request at 1000 keys
            objects: list[ObjectIdentifier] = [{"key": self.key(name)} for name in batch]
            result = self.client.delete_objects(self.bucket, {"objects": objects})
            errors = result.get("errors")
            if errors:
                failed = ", ".join(f"{error.get('key')} ({error.get('code')})" for error in errors)
                msg = f"Failed to delete objects: {failed}"
                raise OSError(msg)

    @override
    def exists(self, name: str) -> bool:
        """Report whether an object with this name is already stored."""
        try:
            _ = self._head(name)
        except FileNotFoundError:
            return False
        return True

    @override
    def size(self, name: str) -> int:
        """Return the stored size in bytes — the compressed size for gzipped objects."""
        return self._head(name).get("content_length", 0)

    @override
    def get_modified_time(self, name: str) -> datetime:
        """Return the last-modified time, made naive in the current zone when USE_TZ is off."""
        last_modified = self._head(name).get("last_modified")
        if last_modified is None:
            msg = f"No last-modified time available for: {name}"
            raise FileNotFoundError(msg)
        if settings.USE_TZ:
            return last_modified
        return timezone.make_naive(last_modified)

    @override
    def url(  # type: ignore[override]  # extends Django's Storage.url(name) with S3-specific presigning options
        self,
        name: str,
        *,
        expire: int | None = None,
        parameters: dict[str, str] | None = None,
        http_method: str = "GET",
    ) -> str:
        """Return a URL for an object.

        For a custom domain: a CloudFront-signed URL when a signing key is configured, otherwise a plain public URL.
        Otherwise: a presigned S3 URL, unless signing is turned off. Pass expire to override the signed URL's lifetime,
        http_method to sign a HEAD or PUT instead of a GET, and parameters to add response overrides such as
        {"response_content_disposition": "attachment"} to a presigned GET, or upload headers such as
        {"content_type": "image/png"} to a presigned PUT. On a CloudFront-signed URL the response overrides are
        signed in too; the distribution must be configured to forward them to the S3 origin.
        """
        key = self.key(name)
        expires_in = expire if expire is not None else self.options["url_expire"]
        if self.options.get("custom_domain"):
            public = build_public_url(self.options, key)
            signer = self._cloudfront_signer
            if signer is not None and self.options.get("querystring_auth"):
                if parameters:
                    query = urlencode({param.replace("_", "-"): value for param, value in parameters.items()})
                    public = f"{public}?{query}"
                return signer.signed_url(public, expires_at=int(time.time()) + expires_in)
            return public
        if self.options.get("querystring_auth"):
            return self._presigned(key, expires_in, parameters or {}, http_method)
        return build_public_url(self.options, key)

    def _presigned(self, key: str, expire: int, parameters: dict[str, str], http_method: str) -> str:
        method = http_method.upper()
        if method == "GET":
            return self.client.presigned_get_object(
                self.bucket,
                key,
                expire,
                **parameters,  # type: ignore[arg-type]
            )
        if method == "HEAD":
            return self.client.presigned_head_object(self.bucket, key, expire)
        if method == "PUT":
            return self.client.presigned_put_object(
                self.bucket,
                key,
                expire,
                **parameters,  # type: ignore[arg-type]
            )
        msg = f"Unsupported http_method for url(): {http_method!r} (only GET, HEAD and PUT are supported)."
        raise ValueError(msg)

    @override
    def listdir(self, path: str) -> tuple[list[str], list[str]]:
        """List the immediate subdirectories and files under a path, following pagination."""
        prefix = self.key(path) if path else self.options.get("location", "").strip("/")
        if prefix and not prefix.endswith("/"):
            prefix += "/"

        directories: list[str] = []
        files: list[str] = []
        token: str | None = None
        while True:
            result = self.client.list_objects_v2(
                self.bucket,
                prefix=prefix,
                delimiter="/",
                continuation_token=token,
            )
            for common_prefix in result.get("common_prefixes", []):
                value = common_prefix.get("prefix")
                if value:
                    directories.append(value[len(prefix) :].rstrip("/"))
            for obj in result.get("contents", []):
                obj_key = obj.get("key")
                if obj_key and obj_key != prefix:
                    files.append(obj_key[len(prefix) :])
            if not result.get("is_truncated"):
                break
            token = result.get("next_continuation_token")
        return directories, files

    @override
    def get_available_name(self, name: str, max_length: int | None = None) -> str:
        """Reuse the given name when overwriting is enabled, otherwise fall back to Django's collision handling."""
        if self.options.get("file_overwrite"):
            return name
        return super().get_available_name(name, max_length)

    def _head(self, name: str) -> HeadObjectOutput:
        """Return an object's metadata, or raise FileNotFoundError when it is missing."""
        try:
            return self.client.head_object(self.bucket, self.key(name))
        except NotFound as exc:
            msg = f"File does not exist: {name}"
            raise FileNotFoundError(msg) from exc

    def _should_gzip(self, content_type: str | None) -> bool:
        return bool(
            self.options.get("gzip") and content_type and content_type in self.options.get("gzip_content_types", ()),
        )
