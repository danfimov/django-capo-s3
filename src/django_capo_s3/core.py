import logging
import mimetypes
import ssl
from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import timedelta
from typing import Any, Final, Protocol, TypedDict, TypeVar, cast
from urllib.parse import quote

from capo_s3 import Credentials
from capo_s3.types.object_canned_acl import ObjectCannedACL
from django.conf import settings
from typing_extensions import Sentinel
from zapros import BaseHandler


class ClientBuilder(Protocol):
    """A transport's own configuration object, as returned by the http_client_builder option.

    Deliberately structural and empty: builders expose different methods, and the ones the storage knows about
    are looked up by name at runtime, so requiring any of them here would rule out transports that simply
    name things differently.
    """


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
    default_content_type: str
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
    http_client_builder: Callable[[], ClientBuilder] | None
    http_handler: Callable[[Any], BaseHandler] | None


DEFAULT_GZIP_CONTENT_TYPES: Final[tuple[str, ...]] = (
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/json",
    "application/xml",
    "image/svg+xml",
)

DEFAULT_CONTENT_TYPE: Final[str] = "application/octet-stream"

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
    "file_overwrite": True,
    "default_acl": None,
    "gzip": False,
    "gzip_content_types": DEFAULT_GZIP_CONTENT_TYPES,
    "default_content_type": DEFAULT_CONTENT_TYPE,
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
    "http_client_builder": None,
    "http_handler": None,
}

logger = logging.getLogger(__name__)

SETTING_NAMES: Final[tuple[tuple[str, str], ...]] = (
    ("AWS_STORAGE_BUCKET_NAME", "bucket"),
    ("AWS_LOCATION", "location"),
    ("AWS_S3_ENDPOINT_URL", "endpoint"),
    ("AWS_S3_REGION_NAME", "region"),
    ("AWS_QUERYSTRING_AUTH", "querystring_auth"),
    ("AWS_QUERYSTRING_EXPIRE", "url_expire"),
    ("AWS_S3_CUSTOM_DOMAIN", "custom_domain"),
    ("AWS_CLOUDFRONT_KEY", "cloudfront_key"),
    ("AWS_CLOUDFRONT_KEY_ID", "cloudfront_key_id"),
    ("AWS_S3_FILE_OVERWRITE", "file_overwrite"),
    ("AWS_DEFAULT_ACL", "default_acl"),
    ("AWS_IS_GZIPPED", "gzip"),
    ("GZIP_CONTENT_TYPES", "gzip_content_types"),
    ("AWS_S3_OBJECT_PARAMETERS", "object_parameters"),
    ("AWS_S3_MAX_MEMORY_SIZE", "max_memory_size"),
    ("AWS_S3_SESSION_PROFILE", "session_profile"),
    ("AWS_S3_PROXIES", "proxies"),
)

_UNSET: Final = Sentinel("_UNSET")


def _first_setting(*names: str) -> object:
    for name in names:
        value = getattr(settings, name, None)
        if value:
            return value
    return None


def options_from_settings() -> S3StorageOptions:
    """Collect storage options from the django-storages AWS_* settings.

    Only settings that are actually present are reported, so they layer between the defaults and a storage's
    own OPTIONS: anything given in OPTIONS still wins. Credentials are assembled from the key settings when
    both halves are there; otherwise they are left alone so capo resolves them from the environment.
    """
    if not settings.configured:  # usable outside a Django project, e.g. from a script
        return {}
    found: dict[str, Any] = {}
    for name, option in SETTING_NAMES:
        value = getattr(settings, name, _UNSET)
        if value is not _UNSET:
            found[option] = value

    protocol = getattr(settings, "AWS_S3_URL_PROTOCOL", None)
    if isinstance(protocol, str):
        found["url_protocol"] = protocol.rstrip(":")  # django-storages writes it as "https:"

    style = getattr(settings, "AWS_S3_ADDRESSING_STYLE", None)
    if isinstance(style, str):
        found["force_path_style"] = style == "path"

    verify = getattr(settings, "AWS_S3_VERIFY", _UNSET)
    if verify is not _UNSET and verify is not None:  # None there means "library default", which is ours
        found["verify"] = verify

    access_key = _first_setting("AWS_S3_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
    secret_key = _first_setting("AWS_S3_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY")
    if isinstance(access_key, str) and isinstance(secret_key, str):
        credentials = Credentials(access_key=access_key, secret_key=secret_key)
        token = _first_setting("AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN")
        if isinstance(token, str):
            credentials["session_token"] = token
        found["credentials"] = credentials
    return cast("S3StorageOptions", found)


# Storage option -> the builder method that applies it. Builders name things differently and expose different
# knobs, so this is applied best-effort: a builder without the method is logged, not an error.
TRANSPORT_METHODS: Final[tuple[tuple[str, str], ...]] = (
    ("connect_timeout", "connect_timeout"),
    ("read_timeout", "read_timeout"),
    ("write_timeout", "write_timeout"),
    ("total_timeout", "timeout"),
    ("max_connections_per_host", "pool_max_idle_per_host"),
)

# Durations go in as timedelta rather than seconds, which is what builders of this shape expect.
_DURATION_OPTIONS: Final[frozenset[str]] = frozenset(
    {"connect_timeout", "read_timeout", "write_timeout", "total_timeout"}
)

# These need a transport-specific object (a proxy builder, a parsed certificate), so they stay with whoever
# owns the builder.
_BUILDER_OWNED_OPTIONS: Final[tuple[str, ...]] = ("proxies", "verify")


def configure_builder(builder: ClientBuilder, options: Mapping[str, Any]) -> ClientBuilder:
    """Apply the storage's transport options to a client builder by calling the matching method for each."""
    for option, method in TRANSPORT_METHODS:
        value = options.get(option)
        if value is None:
            continue
        apply = getattr(builder, method, None)
        if apply is None:
            logger.warning("Ignoring %s: the client builder has no %s().", option, method)
            continue
        builder = apply(timedelta(seconds=value) if option in _DURATION_OPTIONS else value)
    for option in _BUILDER_OWNED_OPTIONS:
        if options.get(option) not in (None, True):
            logger.warning("Ignoring %s: set it on the client builder instead.", option)
    return builder


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


T = TypeVar("T")


def batched(sequence: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    """Yield successive slices of at most size items. Will be replaced with itertools.batched in Python 3.12."""
    for start in range(0, len(sequence), size):
        yield sequence[start : start + size]


def build_public_url(options: S3StorageOptions, key: str) -> str:
    """Build an unsigned, publicly addressable URL for an object.

    Prefers a custom domain (path- or virtual-host style, per force_path_style), then a custom endpoint (likewise), and
    otherwise falls back to the AWS regional host.
    """
    encoded = quote(key)
    protocol = options.get("url_protocol", "https")
    bucket = options.get("bucket")

    custom_domain = options.get("custom_domain")
    if custom_domain:
        base = f"{protocol}://{custom_domain.rstrip('/')}"
        return f"{base}/{bucket}/{encoded}" if options.get("force_path_style") else f"{base}/{encoded}"

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
