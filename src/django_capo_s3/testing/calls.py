import re
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Self, overload
from urllib.parse import parse_qsl, unquote, urlsplit

from typing_extensions import override
from zapros import Request

_AUTHORIZATION = re.compile(
    r"AWS4-HMAC-SHA256\s+"
    r"Credential=(?P<access_key>[^/]+)/(?P<date>[^/]+)/(?P<region>[^/]+)/(?P<service>[^/]+)/aws4_request,\s*"
    r"SignedHeaders=(?P<signed_headers>[^,]*),\s*"
    r"Signature=(?P<signature>\w+)"
)

# Query parameters of a presigned URL that belong to the signature rather than to the operation.
_SIGNING_PARAMS = frozenset(
    {
        "X-Amz-Algorithm",
        "X-Amz-Credential",
        "X-Amz-Date",
        "X-Amz-Expires",
        "X-Amz-Security-Token",
        "X-Amz-SignedHeaders",
        "X-Amz-Signature",
        "x-id",
    }
)


def read_body(request: Request) -> bytes:
    """Return a request's body as bytes, draining a streaming body and caching the result on the request.

    A streaming upload arrives as an iterator that can be consumed only once, so the bytes are put back on the
    request in its place: matchers and repeated assertions then see the same body the service was handed.
    """
    body = request.body
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    if not isinstance(body, Iterator):
        msg = f"Cannot read an async request body synchronously: {type(body).__name__}"
        raise TypeError(msg)
    data = b"".join(body)
    request.body = data
    return data


async def aread_body(request: Request) -> bytes:
    """Return an async request's body as bytes, draining and caching it the way read_body does."""
    body = request.body
    if isinstance(body, AsyncIterator):
        data = b"".join([chunk async for chunk in body])
        request.body = data
        return data
    return read_body(request)


@dataclass(frozen=True)
class SigV4:
    """The parts of a SigV4 Authorization header, so tests can assert on what a request was signed with."""

    access_key: str
    date: str
    region: str
    service: str
    signed_headers: tuple[str, ...]
    signature: str

    @classmethod
    def parse(cls, header: str) -> Self | None:
        """Parse an Authorization header, or return None when it is not a SigV4 one."""
        match = _AUTHORIZATION.fullmatch(header.strip())
        if match is None:
            return None
        return cls(
            access_key=match["access_key"],
            date=match["date"],
            region=match["region"],
            service=match["service"],
            signed_headers=tuple(match["signed_headers"].split(";")),
            signature=match["signature"],
        )


@dataclass(frozen=True)
class PresignedUrl:
    """A parsed presigned URL, so a test can assert on its parts instead of picking apart a query string."""

    url: str
    host: str
    path: str
    params: Mapping[str, str]

    @property
    def access_key(self) -> str | None:
        """The access key the URL was signed with."""
        credential = self.params.get("X-Amz-Credential")
        return credential.split("/")[0] if credential else None

    @property
    def region(self) -> str | None:
        """The region in the credential scope."""
        region_in_scope = 2  # the credential scope reads <access-key>/<date>/<region>/<service>/aws4_request
        credential = self.params.get("X-Amz-Credential")
        parts = credential.split("/") if credential else []
        return parts[region_in_scope] if len(parts) > region_in_scope else None

    @property
    def expires(self) -> int | None:
        """The URL's lifetime in seconds."""
        value = self.params.get("X-Amz-Expires")
        return int(value) if value else None

    @property
    def signature(self) -> str | None:
        """The computed signature."""
        return self.params.get("X-Amz-Signature")

    @property
    def overrides(self) -> Mapping[str, str]:
        """Everything the URL carries beyond the signature — response overrides, upload headers, and the like."""
        return {name: value for name, value in self.params.items() if name not in _SIGNING_PARAMS}


def parse_presigned_url(url: str) -> PresignedUrl:
    """Split a presigned URL into its host, path, and query parameters."""
    parts = urlsplit(url)
    return PresignedUrl(
        url=url,
        host=parts.netloc,
        path=unquote(parts.path),
        params=dict(parse_qsl(parts.query, keep_blank_values=True)),
    )


@dataclass(frozen=True, repr=False)
class S3Call:
    """One request the fake service was asked to serve, resolved down to the S3 operation it stands for."""

    operation: str
    method: str
    bucket: str
    key: str
    url: str
    # Left out of the hash: both are mappings, and the URL and body already identify the call.
    headers: Mapping[str, str] = field(hash=False)
    params: Mapping[str, str] = field(hash=False)
    body: bytes

    @cached_property
    def signature(self) -> SigV4 | None:
        """The parsed SigV4 Authorization header, or None on an unsigned request."""
        header = self.headers.get("authorization")
        return SigV4.parse(header) if header else None

    def metadata(self) -> Mapping[str, str]:
        """Return the user metadata the request carried, with the x-amz-meta- prefix stripped off the names."""
        prefix = "x-amz-meta-"
        return {
            name[len(prefix) :].lower(): value
            for name, value in self.headers.items()
            if name.lower().startswith(prefix)
        }

    @override
    def __repr__(self) -> str:
        """Identify the call by operation and target, which is what a failing assertion needs to show."""
        target = f"{self.bucket}/{self.key}" if self.key else self.bucket
        return f"S3Call({self.operation} {target})"


class S3Calls(Sequence[S3Call]):
    """The recorded calls, as a sequence that can be narrowed down before asserting on it.

    Filters return a new S3Calls, so they chain: calls.of("PutObject").for_key("media/report.csv").
    """

    def __init__(self, calls: Sequence[S3Call] = ()) -> None:
        """Wrap a sequence of calls, copying it so later recording doesn't change this view."""
        self._calls = tuple(calls)

    @overload
    def __getitem__(self, index: int) -> S3Call: ...

    @overload
    def __getitem__(self, index: slice) -> "S3Calls": ...

    @override
    def __getitem__(self, index: int | slice) -> "S3Call | S3Calls":
        """Index into the calls, or slice them into a narrower view."""
        if isinstance(index, slice):
            return S3Calls(self._calls[index])
        return self._calls[index]

    @override
    def __len__(self) -> int:
        """Count the recorded calls."""
        return len(self._calls)

    @override
    def __eq__(self, other: object) -> bool:
        """Compare against another view or a plain list of calls, so asserting on a narrowed view reads plainly."""
        if isinstance(other, S3Calls):
            return self._calls == tuple(other)
        if isinstance(other, (list, tuple)):
            return list(self._calls) == list(other)
        return False

    @override
    def __hash__(self) -> int:
        """Hash by the calls held, matching __eq__."""
        return hash(self._calls)

    @override
    def __iter__(self) -> Iterator[S3Call]:
        """Iterate the calls in the order they were made."""
        return iter(self._calls)

    @override
    def __repr__(self) -> str:
        """List the calls, so a failed length assertion shows what was actually recorded."""
        return f"S3Calls({list(self._calls)!r})"

    def of(self, *operations: str) -> "S3Calls":
        """Keep only the calls for the given operations, such as PutObject or UploadPart."""
        wanted = frozenset(operations)
        return S3Calls([call for call in self._calls if call.operation in wanted])

    def for_key(self, key: str) -> "S3Calls":
        """Keep only the calls targeting one object key — the full key, location prefix included."""
        return S3Calls([call for call in self._calls if call.key == key])

    def for_bucket(self, bucket: str) -> "S3Calls":
        """Keep only the calls targeting one bucket."""
        return S3Calls([call for call in self._calls if call.bucket == bucket])

    @property
    def operations(self) -> list[str]:
        """The operation names in call order, for asserting on the sequence of requests a code path makes."""
        return [call.operation for call in self._calls]

    @property
    def keys(self) -> list[str]:
        """The object keys in call order."""
        return [call.key for call in self._calls]

    @property
    def trace(self) -> list[tuple[str, str]]:
        """Each call as (operation, key), for asserting on what a code path did and to which object.

        The pair is what an assertion about a sequence of requests usually needs: operations alone don't say
        which object each one touched, and reading them off two separate lists puts the burden on the reader.
        """
        return [(call.operation, call.key) for call in self._calls]

    @property
    def last(self) -> S3Call:
        """The most recent call."""
        if not self._calls:
            msg = "No S3 calls were recorded."
            raise AssertionError(msg)
        return self._calls[-1]
