import hashlib
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Self, Unpack
from unittest.mock import patch
from urllib.parse import parse_qsl, unquote

from capo_s3 import Credentials
from capo_s3._protocol.xml import fromstring
from capo_s3._services._aws_config import load_aws_settings
from capo_s3.types import (
    complete_multipart_upload_output,
    create_multipart_upload_output,
    delete_objects_output,
    list_objects_v2_output,
)
from capo_s3.types.copy_object_output import CopyObjectOutput
from capo_s3.types.copy_object_output import serialize_xml as serialize_copy_object_output
from capo_s3.types.object import Object
from typing_extensions import override
from zapros import AsyncBaseHandler, AsyncStdNetworkHandler, BaseHandler, Request, Response, StdNetworkHandler
from zapros.mock import MockRouter

from django_capo_s3.core import S3StorageOptions
from django_capo_s3.storage import S3Storage
from django_capo_s3.testing.calls import S3Call, S3Calls, aread_body, read_body
from django_capo_s3.testing.responses import not_found, s3_error, xml_response

DEFAULT_BUCKET = "test-bucket"
DEFAULT_REGION = "us-east-1"
DEFAULT_CREDENTIALS = Credentials(access_key="test-access-key", secret_key="test-secret-key")  # noqa: S106

# Signing and framing headers: they say nothing about the object, so they are not stored with it.
_TRANSIENT_HEADERS = frozenset(
    {
        "x-amz-content-sha256",
        "x-amz-date",
        "x-amz-decoded-content-length",
        "x-amz-sdk-checksum-algorithm",
        "x-amz-security-token",
        "x-amz-trailer",
    }
)

# Object headers S3 stores and gives back that are not x-amz-prefixed.
_STORED_HEADERS = frozenset({"cache-control", "content-disposition", "content-language", "expires"})

# Operations the client names with a valueless query parameter rather than with x-id.
_QUERY_OPERATIONS = (
    ("uploads", "CreateMultipartUpload"),
    ("delete", "DeleteObjects"),
    ("list-type", "ListObjectsV2"),
)

# What is left once the query says nothing: the method, and whether the URL carries a key. (object, bucket)
_METHOD_OPERATIONS = {
    "GET": ("GetObject", ""),
    "HEAD": ("HeadObject", "HeadBucket"),
    "PUT": ("PutObject", "CreateBucket"),
    "DELETE": ("DeleteObject", "DeleteBucket"),
}


def _md5(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


@dataclass
class StoredObject:
    """An object held by the fake service, with the metadata S3 would report back for it."""

    data: bytes
    content_type: str | None = None
    content_encoding: str | None = None
    last_modified: datetime = field(default_factory=lambda: datetime.now(UTC))
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def size(self) -> int:
        """The stored size in bytes — the compressed size for a gzipped object, as on the wire."""
        return len(self.data)

    @property
    def etag(self) -> str:
        """The ETag, without the quotes S3 wraps it in. A content MD5, which is what skip_unchanged compares."""
        return _md5(self.data)

    @property
    def metadata(self) -> Mapping[str, str]:
        """The user metadata, with the x-amz-meta- prefix stripped off the names."""
        prefix = "x-amz-meta-"
        return {name[len(prefix) :]: value for name, value in self.headers.items() if name.startswith(prefix)}


@dataclass
class _Upload:
    bucket: str
    key: str
    content_type: str | None
    content_encoding: str | None
    headers: dict[str, str]
    parts: dict[int, bytes] = field(default_factory=dict)


@dataclass
class _Canned:
    response: Response
    operation: str | None
    key: str | None
    remaining: int | None

    def matches(self, call: S3Call) -> bool:
        if self.remaining is not None and self.remaining <= 0:
            return False
        if self.operation is not None and self.operation != call.operation:
            return False
        return self.key is None or self.key == call.key


class FakeS3(BaseHandler, AsyncBaseHandler):
    """An in-memory S3 service that answers the real wire protocol, so no request leaves the process.

    Requests are signed, serialized, and parsed by the actual client, which means everything between the
    storage API and the network is exercised: keys, headers, gzip, pagination, multipart transfers. What the
    service keeps is a dict of objects the test can read back, and every request it saw is recorded on calls.

    Install it either by patching the transport for a block, which catches storages Django built from
    settings:

        with mock_s3() as s3:
            default_storage.save("report.csv", ContentFile(b"a,b"))
        assert s3["media/report.csv"] == b"a,b"

    or by handing it to a storage directly, which patches nothing:

        s3 = FakeS3()
        storage = s3.storage(location="media")
    """

    def __init__(
        self,
        *,
        bucket: str = DEFAULT_BUCKET,
        region: str = DEFAULT_REGION,
        force_path_style: bool | None = None,
    ) -> None:
        """Set up an empty service.

        The bucket is the one the helpers default to, and the one storage() points a storage at. Addressing
        is worked out per request from the URL; set force_path_style to settle it up front for a bucket the
        service has not seen written to yet.
        """
        self.bucket = bucket
        self.region = region
        # Which bucket names have been seen, so _split can tell path-style addressing from virtual-host.
        self._buckets: set[str] = {bucket}
        self.objects: dict[tuple[str, str], StoredObject] = {}
        self.router = MockRouter()
        self._force_path_style = force_path_style
        self._calls: list[S3Call] = []
        self._canned: list[_Canned] = []
        self._uploads: dict[str, _Upload] = {}
        self._operations: dict[str, Callable[[S3Call], Response]] = {
            "PutObject": self._put_object,
            "GetObject": self._get_object,
            "HeadObject": self._head_object,
            "DeleteObject": self._delete_object,
            "DeleteObjects": self._delete_objects,
            "ListObjectsV2": self._list_objects_v2,
            "CopyObject": self._copy_object,
            "CreateMultipartUpload": self._create_multipart_upload,
            "UploadPart": self._upload_part,
            "CompleteMultipartUpload": self._complete_multipart_upload,
            "AbortMultipartUpload": self._abort_multipart_upload,
            "CreateBucket": self._create_bucket,
            "DeleteBucket": self._delete_bucket,
            "HeadBucket": self._head_bucket,
        }

    @property
    def calls(self) -> S3Calls:
        """Every request the service was asked to serve, in order, resolved to the S3 operation it stands for."""
        return S3Calls(self._calls)

    def __getitem__(self, key: str | tuple[str, str]) -> bytes:
        """Return a stored object's bytes, as they are stored — still gzipped for a gzip-compressed object.

        Address an object in the default bucket by key, and one in any other bucket by (bucket, key), which is
        how the objects dict is keyed too.
        """
        return self.objects[self._address(key)].data

    def __contains__(self, key: str | tuple[str, str]) -> bool:
        """Whether an object is stored under this key, addressed as for a lookup."""
        return self._address(key) in self.objects

    def stored(self, key: str, *, bucket: str | None = None) -> StoredObject:
        """Return a stored object with its metadata, raising KeyError when nothing is stored under the key."""
        return self.objects[(bucket or self.bucket, key)]

    def _address(self, key: str | tuple[str, str]) -> tuple[str, str]:
        return key if isinstance(key, tuple) else (self.bucket, key)

    def put(
        self,
        key: str,
        data: bytes,
        *,
        bucket: str | None = None,
        content_type: str | None = None,
        content_encoding: str | None = None,
        **headers: str,
    ) -> StoredObject:
        """Store an object without going through the client, to set up the state a test starts from."""
        name = bucket or self.bucket
        self._buckets.add(name)
        stored = StoredObject(
            data=data,
            content_type=content_type,
            content_encoding=content_encoding,
            headers={name.replace("_", "-").lower(): value for name, value in headers.items()},
        )
        self.objects[(name, key)] = stored
        return stored

    def keys(self, *, bucket: str | None = None) -> list[str]:
        """Return the keys stored in a bucket, sorted the way a listing returns them."""
        name = bucket or self.bucket
        return sorted(key for stored_bucket, key in self.objects if stored_bucket == name)

    def respond_with(
        self,
        response: Response,
        *,
        operation: str | None = None,
        key: str | None = None,
        times: int | None = None,
    ) -> None:
        """Answer matching requests with a canned response instead of serving them.

        With no operation or key it matches everything; times limits how many requests it answers before the
        service takes over again, which is how a retry or fallback path gets tested.
        """
        self._canned.append(_Canned(response=response, operation=operation, key=key, remaining=times))

    # PLR0913 does not apply: every argument is a keyword-only knob on one rule, and an options object to
    # carry them would read worse at the call site than naming them there.
    def fail(  # noqa: PLR0913
        self,
        operation: str | None = None,
        *,
        key: str | None = None,
        code: str = "InternalError",
        status: int = 500,
        message: str | None = None,
        times: int | None = None,
    ) -> None:
        """Make matching requests fail with an S3 error, for instance an AccessDenied on every upload."""
        self.respond_with(
            s3_error(code, status=status, message=message),
            operation=operation,
            key=key,
            times=times,
        )

    def reset(self) -> None:
        """Return the service to how it started: no objects, no recorded calls, no canned responses."""
        self.objects.clear()
        self._buckets = {self.bucket}
        self._calls.clear()
        self._canned.clear()
        self._uploads.clear()

    def storage_options(self, **overrides: Unpack[S3StorageOptions]) -> S3StorageOptions:
        """Return storage options bound to this service, with anything passed in taking precedence.

        Use it to build a storage class the storage() shortcut doesn't cover:

            S3ManifestStaticStorage(**s3.storage_options(location="static"))
        """
        options: S3StorageOptions = {
            "bucket": self.bucket,
            "region": self.region,
            "credentials": DEFAULT_CREDENTIALS,
            "force_path_style": bool(self._force_path_style),
            "http_handler": lambda _builder: self,
        }
        options.update(overrides)
        return options

    def storage(self, **overrides: Unpack[S3StorageOptions]) -> S3Storage:
        """Build an S3Storage that talks to this service and nothing else."""
        return S3Storage(**self.storage_options(**overrides))

    @contextmanager
    def installed(self) -> Iterator[Self]:
        """Route every S3 request in the block to this service, whatever built the storage that makes it.

        Patches the client's transport, so it also covers storages Django built from the STORAGES setting,
        collectstatic, and code that reaches for default_storage.

        The AWS environment is pinned for the length of the block. A client configured without credentials
        would otherwise walk capo's provider chain all the way out to the instance metadata service, and one
        configured without a region would fail to resolve an endpoint at all — both are things the deployed
        process gets from its surroundings and a test has no business inheriting.

        Leaving the block without an exception verifies the mocks mounted on the router, so an expectation
        such as Mock.given(...).once() fails the test when it went unmet — the same contract zapros' own
        mock_http() gives. A canned response's times= is a ceiling, not an expectation, and is not verified.
        """
        environment = {
            "AWS_ACCESS_KEY_ID": DEFAULT_CREDENTIALS["access_key"],
            "AWS_SECRET_ACCESS_KEY": DEFAULT_CREDENTIALS["secret_key"],
            "AWS_REGION": self.region,
            "AWS_EC2_METADATA_DISABLED": "true",
        }
        # capo memoizes the ambient region and endpoint for the whole process on first use, so the pinned
        # environment only reaches a client if whatever an earlier test resolved is thrown away first.
        load_aws_settings.cache_clear()
        try:
            with (
                patch.dict(os.environ, environment),
                patch.object(StdNetworkHandler, "handle", self.handle),
                patch.object(AsyncStdNetworkHandler, "ahandle", self.ahandle),
            ):
                yield self
        finally:
            load_aws_settings.cache_clear()
        # Not reached when the block raised: a failing test should surface as itself, not as an unmet mock.
        self.router.verify()

    @override
    def handle(self, request: Request) -> Response:
        """Record a request and answer it: a canned response if one matches, otherwise the service itself."""
        return self._answer(self._record(request, read_body(request)), request)

    @override
    async def ahandle(self, request: Request) -> Response:
        """Serve an async client out of the same objects and onto the same recording as the sync one.

        Worth having even though this backend is synchronous: a project that reaches for capo's async client
        anywhere else would otherwise have those requests slip past the service and onto the network.
        """
        return self._answer(self._record(request, await aread_body(request)), request)

    def _record(self, request: Request, body: bytes) -> S3Call:
        bucket, key = self._split(request)
        params = dict(parse_qsl(request.url.search.lstrip("?"), keep_blank_values=True))
        call = S3Call(
            operation=_operation(request.method, key, params),
            method=request.method,
            bucket=bucket,
            key=key,
            url=str(request.url),
            headers=request.headers,
            params=params,
            body=body,
        )
        self._calls.append(call)
        return call

    def _answer(self, call: S3Call, request: Request) -> Response:
        response = self._canned_response(call) or self.router.dispatch(request) or self._operate(call)
        response.request = request
        return response

    def _canned_response(self, call: S3Call) -> Response | None:
        for canned in self._canned:
            if canned.matches(call):
                if canned.remaining is not None:
                    canned.remaining -= 1
                return canned.response
        return None

    def _operate(self, call: S3Call) -> Response:
        operate = self._operations.get(call.operation)
        if operate is None:
            # A client-side status on purpose: 501 is retryable, and backing off three times to reach the same answer
            # only slows the test down and buries the message.
            message = f"FakeS3 does not implement {call.method} {call.url}"
            return s3_error("NotImplemented", status=400, message=message)
        return operate(call)

    def _split(self, request: Request) -> tuple[str, str]:
        """Work out which bucket and key a request addresses, under either addressing style."""
        path = unquote(request.url.pathname).lstrip("/")
        first, _, rest = path.partition("/")
        label, _, domain = (request.url.hostname or "").partition(".")
        if self._force_path_style is not None:
            return (first, rest) if self._force_path_style else (label, path)
        if domain and label in self._buckets:
            return label, path
        if first in self._buckets:
            return first, rest
        # An unseen bucket: only the host's shape is left to go on, and AWS puts s3 right after the bucket.
        if domain.startswith(("s3.", "s3-")):
            return label, path
        return first, rest

    def _put_object(self, call: S3Call) -> Response:
        self._buckets.add(call.bucket)
        stored = StoredObject(
            data=call.body,
            content_type=call.headers.get("content-type"),
            content_encoding=call.headers.get("content-encoding"),
            headers=_object_headers(call.headers),
        )
        self.objects[(call.bucket, call.key)] = stored
        return Response(status=200, headers={"ETag": f'"{stored.etag}"'})

    def _get_object(self, call: S3Call) -> Response:
        stored = self.objects.get((call.bucket, call.key))
        if stored is None:
            return self._no_such_key(call)
        return Response(status=200, headers=_response_headers(stored), content=stored.data)

    def _head_object(self, call: S3Call) -> Response:
        stored = self.objects.get((call.bucket, call.key))
        if stored is None:
            return not_found()
        return Response(status=200, headers=_response_headers(stored))

    def _delete_object(self, call: S3Call) -> Response:
        _ = self.objects.pop((call.bucket, call.key), None)
        return Response(status=204)

    def _delete_objects(self, call: S3Call) -> Response:
        root = fromstring(call.body)
        quiet_element = root.find("Quiet")
        quiet = quiet_element is not None and (quiet_element.text or "").lower() == "true"
        deleted: list[str] = []
        for element in root.findall("Object"):
            key_element = element.find("Key")
            key = (key_element.text or "") if key_element is not None else ""
            _ = self.objects.pop((call.bucket, key), None)
            deleted.append(key)
        output: delete_objects_output.DeleteObjectsOutput = {}
        if not quiet:
            output["deleted"] = [{"key": key} for key in deleted]
        return xml_response(delete_objects_output.serialize_xml, output, "DeleteResult")

    def _list_objects_v2(self, call: S3Call) -> Response:
        default_max_keys = 1000  # what S3 returns when the request does not ask for fewer
        prefix = call.params.get("prefix", "")
        delimiter = call.params.get("delimiter", "")
        max_keys = int(call.params.get("max-keys", str(default_max_keys)))
        after = call.params.get("continuation-token") or call.params.get("start-after")
        candidates = [key for key in self.keys(bucket=call.bucket) if key.startswith(prefix)]
        if after:
            candidates = [key for key in candidates if key > after]

        contents: list[Object] = []
        prefixes: list[str] = []
        truncated = False
        next_token: str | None = None
        scanned: str | None = None
        for key in candidates:
            head, separator, _ = key[len(prefix) :].partition(delimiter) if delimiter else ("", "", "")
            common = prefix + head + delimiter if separator else None
            if common is not None and common in prefixes:
                scanned = key  # rolled into a prefix already returned, so it doesn't count against max-keys
                continue
            if len(contents) + len(prefixes) >= max_keys:
                truncated, next_token = True, scanned
                break
            if common is not None:
                prefixes.append(common)
            else:
                contents.append(self._entry(call.bucket, key))
            scanned = key

        output: list_objects_v2_output.ListObjectsV2Output = {
            "name": call.bucket,
            "prefix": prefix,
            "max_keys": max_keys,
            "key_count": len(contents) + len(prefixes),
            "is_truncated": truncated,
        }
        if contents:
            output["contents"] = contents
        if prefixes:
            output["common_prefixes"] = [{"prefix": value} for value in prefixes]
        if delimiter:
            output["delimiter"] = delimiter
        if next_token is not None:
            output["next_continuation_token"] = next_token
        return xml_response(list_objects_v2_output.serialize_xml, output, "ListBucketResult")

    def _entry(self, bucket: str, key: str) -> Object:
        stored = self.objects[(bucket, key)]
        return {
            "key": key,
            "size": stored.size,
            "e_tag": f'"{stored.etag}"',
            "last_modified": stored.last_modified,
            "storage_class": "STANDARD",
        }

    def _copy_object(self, call: S3Call) -> Response:
        source = unquote(call.headers.get("x-amz-copy-source", "")).lstrip("/")
        bucket, _, key = source.partition("/")
        stored = self.objects.get((bucket, key))
        if stored is None:
            return self._no_such_key(call)
        copied = StoredObject(
            data=stored.data,
            content_type=call.headers.get("content-type") or stored.content_type,
            content_encoding=call.headers.get("content-encoding") or stored.content_encoding,
            headers=_object_headers(call.headers) or dict(stored.headers),
        )
        self.objects[(call.bucket, call.key)] = copied
        output: CopyObjectOutput = {
            "copy_object_result": {"e_tag": f'"{copied.etag}"', "last_modified": copied.last_modified}
        }
        return xml_response(serialize_copy_object_output, output, "CopyObjectResult")

    def _create_multipart_upload(self, call: S3Call) -> Response:
        upload_id = f"upload-{len(self._uploads) + 1}"
        self._uploads[upload_id] = _Upload(
            bucket=call.bucket,
            key=call.key,
            content_type=call.headers.get("content-type"),
            content_encoding=call.headers.get("content-encoding"),
            headers=_object_headers(call.headers),
        )
        output: create_multipart_upload_output.CreateMultipartUploadOutput = {
            "bucket": call.bucket,
            "key": call.key,
            "upload_id": upload_id,
        }
        return xml_response(create_multipart_upload_output.serialize_xml, output, "InitiateMultipartUploadResult")

    def _upload_part(self, call: S3Call) -> Response:
        upload = self._uploads.get(call.params.get("uploadId", ""))
        if upload is None:
            return self._no_such_upload()
        number = int(call.params["partNumber"])
        upload.parts[number] = call.body
        return Response(status=200, headers={"ETag": f'"{_md5(call.body)}"'})

    def _complete_multipart_upload(self, call: S3Call) -> Response:
        upload_id = call.params.get("uploadId", "")
        upload = self._uploads.pop(upload_id, None)
        if upload is None:
            return self._no_such_upload()
        # The client lists the parts to assemble, in order; an empty body means "everything uploaded".
        requested = [
            int(element.text)
            for part in (fromstring(call.body).findall("Part") if call.body else [])
            if (element := part.find("PartNumber")) is not None and element.text
        ]
        numbers = requested or sorted(upload.parts)
        data = b"".join(upload.parts[number] for number in numbers if number in upload.parts)
        stored = StoredObject(
            data=data,
            content_type=upload.content_type,
            content_encoding=upload.content_encoding,
            headers=upload.headers,
        )
        self.objects[(upload.bucket, upload.key)] = stored
        digests = b"".join(bytes.fromhex(_md5(upload.parts[number])) for number in numbers if number in upload.parts)
        output: complete_multipart_upload_output.CompleteMultipartUploadOutput = {
            "bucket": upload.bucket,
            "key": upload.key,
            "e_tag": f'"{_md5(digests)}-{len(numbers)}"',
        }
        return xml_response(complete_multipart_upload_output.serialize_xml, output, "CompleteMultipartUploadResult")

    def _abort_multipart_upload(self, call: S3Call) -> Response:
        _ = self._uploads.pop(call.params.get("uploadId", ""), None)
        return Response(status=204)

    def _create_bucket(self, call: S3Call) -> Response:
        self._buckets.add(call.bucket)
        return Response(status=200, headers={"Location": f"/{call.bucket}"})

    def _delete_bucket(self, call: S3Call) -> Response:
        self._buckets.discard(call.bucket)
        for bucket, key in list(self.objects):
            if bucket == call.bucket:
                del self.objects[(bucket, key)]
        return Response(status=204)

    def _head_bucket(self, call: S3Call) -> Response:  # noqa: ARG002
        # Every other operation accepts a bucket the service has not seen, so this one does too; reach for
        # fail("HeadBucket") when the missing-bucket path is what a test is about.
        return Response(status=200, headers={"x-amz-bucket-region": self.region})

    def _no_such_key(self, call: S3Call) -> Response:
        return s3_error(
            "NoSuchKey",
            status=404,
            message="The specified key does not exist.",
            resource=f"/{call.bucket}/{call.key}",
        )

    def _no_such_upload(self) -> Response:
        return s3_error(
            "NoSuchUpload",
            status=404,
            message="The specified multipart upload does not exist.",
        )


@contextmanager
def mock_s3(
    *,
    bucket: str = DEFAULT_BUCKET,
    region: str = DEFAULT_REGION,
    force_path_style: bool | None = None,
) -> Iterator[FakeS3]:
    """Route every S3 request made in the block to a fresh in-memory service, and yield it.

        with mock_s3() as s3:
            storage = s3.storage(location="media")
            storage.save("report.csv", ContentFile(b"a,b"))

        assert s3["media/report.csv"] == b"a,b"
        assert s3.calls.operations == ["PutObject"]

    The transport is patched for the length of the block, so storages Django built from settings are covered
    too. Pass the same arguments as FakeS3 to point it at a different default bucket or region.
    """
    service = FakeS3(bucket=bucket, region=region, force_path_style=force_path_style)
    with service.installed():
        yield service


def _operation(method: str, key: str, params: Mapping[str, str]) -> str:
    """Name the S3 operation a request stands for, the way the client's URL and query say it."""
    if "x-id" in params:  # the client tags most object operations explicitly
        return params["x-id"]
    if method == "POST" and "uploadId" in params:
        return "CompleteMultipartUpload"
    for param, operation in _QUERY_OPERATIONS:
        if param in params:
            return operation
    if not params:  # anything left with a query is a subresource this service knows nothing about
        on_object, on_bucket = _METHOD_OPERATIONS.get(method, ("", ""))
        operation = on_object if key else on_bucket
        if operation:
            return operation
    return f"Unknown{method.title()}"


def _object_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Keep the request headers that describe the object, so a later GET or HEAD can report them back."""
    kept: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in _TRANSIENT_HEADERS:
            continue
        if lowered.startswith("x-amz-") or lowered in _STORED_HEADERS:
            kept[lowered] = value
    return kept


def _response_headers(stored: StoredObject) -> dict[str, str]:
    """Build the headers S3 answers a GET or HEAD with, in the casing the client reads them under."""
    headers = {
        "ETag": f'"{stored.etag}"',
        "Content-Length": str(stored.size),
        "Last-Modified": format_datetime(stored.last_modified, usegmt=True),
        **stored.headers,
    }
    if stored.content_type is not None:
        headers["Content-Type"] = stored.content_type
    if stored.content_encoding is not None:
        headers["Content-Encoding"] = stored.content_encoding
    return headers
