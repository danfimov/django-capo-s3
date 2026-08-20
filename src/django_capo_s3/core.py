import mimetypes
import ssl
from typing import Final
from urllib.parse import quote

from capo_s3 import Credentials
from capo_s3.types.object_canned_acl import ObjectCannedACL
from typing_extensions import TypedDict


class S3StorageOptions(TypedDict, total=False):
    """Options accepted by the S3Storage backend.

    Only the bucket is required at runtime; everything else falls back to a default.
    Modelling these as a TypedDict lets the storage options in settings be
    type-checked as a plain dict, without importing or building a config object.
    """

    bucket: str
    location: str
    endpoint: str | None
    region: str | None
    force_path_style: bool
    credentials: Credentials | None
    querystring_auth: bool
    url_expire: int
    custom_domain: str | None
    url_protocol: str
    cloudfront_key: str | None
    cloudfront_key_id: str | None
    verify: bool | str
    file_overwrite: bool
    default_acl: ObjectCannedACL | None
    gzip: bool
    gzip_content_types: tuple[str, ...]
    object_parameters: dict[str, str]
    max_memory_size: int
    multipart_threshold: int
    multipart_chunksize: int
    multipart_concurrency: int
    session_profile: str | None
    skip_unchanged: bool
    retry_max_attempts: int | None
    connect_timeout: float | None
    read_timeout: float | None
    write_timeout: float | None
    total_timeout: float | None
    max_connections_per_host: int | None
    proxies: dict[str, str] | None


DEFAULT_GZIP_CONTENT_TYPES: Final[tuple[str, ...]] = (
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/json",
    "application/xml",
    "image/svg+xml",
)

DEFAULTS: Final[S3StorageOptions] = {
    "location": "",
    "endpoint": None,
    "region": None,
    "force_path_style": False,
    "credentials": None,
    "querystring_auth": True,
    "url_expire": 3600,
    "custom_domain": None,
    "url_protocol": "https",
    "cloudfront_key": None,
    "cloudfront_key_id": None,
    "verify": True,
    "file_overwrite": False,
    "default_acl": None,
    "gzip": False,
    "gzip_content_types": DEFAULT_GZIP_CONTENT_TYPES,
    "object_parameters": {},
    "max_memory_size": 10 * 1024 * 1024,
    "multipart_threshold": 64 * 1024 * 1024,
    "multipart_chunksize": 16 * 1024 * 1024,
    "multipart_concurrency": 4,
    "session_profile": None,
    "skip_unchanged": False,
    "retry_max_attempts": None,
    "connect_timeout": None,
    "read_timeout": None,
    "write_timeout": None,
    "total_timeout": None,
    "max_connections_per_host": None,
    "proxies": None,
}


def normalize_key(location: str, name: str) -> str:
    """Join the location prefix and a file name into an S3 object key.

    Leading slashes and backslashes are stripped, and a parent-directory segment is
    rejected so a name can't escape the configured location.
    """
    cleaned = name.replace("\\", "/")
    parts: list[str] = []
    for part in cleaned.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            msg = f"Detected path traversal attempt in name {name!r}"
            raise ValueError(msg)
        parts.append(part)
    key = "/".join(parts)
    prefix = location.strip("/")
    return f"{prefix}/{key}" if prefix else key


def guess_content_type(name: str) -> str | None:
    """Guess a file's MIME type from its extension, or None when unknown."""
    content_type, _ = mimetypes.guess_type(name)
    return content_type


def build_public_url(options: S3StorageOptions, key: str) -> str:
    """Build an unsigned, publicly addressable URL for an object.

    Prefers a custom domain, then a custom endpoint (path- or virtual-host style),
    and otherwise falls back to the AWS regional host.
    """
    encoded = quote(key)
    protocol = options.get("url_protocol", "https")

    custom_domain = options.get("custom_domain")
    if custom_domain:
        return f"{protocol}://{custom_domain.rstrip('/')}/{encoded}"

    bucket = options["bucket"]
    endpoint = options.get("endpoint")
    if endpoint:
        base = endpoint.rstrip("/")
        if options.get("force_path_style"):
            return f"{base}/{bucket}/{encoded}"
        scheme, _, host = base.partition("://")
        return f"{scheme}://{bucket}.{host}/{encoded}"

    region = options.get("region")
    host = f"s3.{region}.amazonaws.com" if region else "s3.amazonaws.com"
    return f"{protocol}://{bucket}.{host}/{encoded}"


def ssl_context_for(*, verify: bool | str) -> ssl.SSLContext | None:
    """Return a TLS context for the given verify setting, or None to keep the client's default.

    True keeps the default verification, a path loads a custom CA bundle, and False turns verification off.
    """
    if verify is True:
        return None
    context = ssl.create_default_context(cafile=verify) if isinstance(verify, str) else ssl.create_default_context()
    if verify is False:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    return context
