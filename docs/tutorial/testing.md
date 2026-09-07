---
title: Testing
---

# Testing

`django_capo_s3.testing` ships an in-memory S3 service. It speaks the real wire protocol, so your test drives
the actual storage backend — signing, XML, gzip, pagination, multipart transfers — without a bucket, a
container, or a network. What it keeps is a dict of objects you can read back, and a record of every request
it saw.

```python
from django.core.files.base import ContentFile
from django_capo_s3.testing import mock_s3


def test_report_is_uploaded():
    with mock_s3() as s3:
        storage = s3.storage(location="media")

        storage.save("report.csv", ContentFile(b"a,b,c"))

        assert s3["media/report.csv"] == b"a,b,c"
        assert s3.calls.operations == ["PutObject"]
```

Nothing is mocked at the storage API level: `save()` really signs a `PUT`, serializes the body, and parses the
response. So a test that passes here is a test of your code _and_ of the way it is configured — the location
prefix, the content type, the object parameters, whether an upload crosses the multipart threshold.

!!! note "Not a substitute for the real thing"

    The service implements the operations this backend uses, faithfully enough that the client cannot tell
    the difference. It is not a complete S3: bucket policies, versioning, lifecycle rules, and the like are
    not there. An operation it does not implement answers with a `NotImplemented` error naming the request,
    so you find out immediately rather than getting a wrong answer.

    It is also permissive about buckets — a request for one it has never seen is served, not refused. Use
    `s3.fail(...)` when the missing-bucket or denied path is what a test is about.

## Two ways to install it

**Hand the service to a storage.** Nothing global is patched, which makes it the better fit when the test
builds its own storage:

```python
from django_capo_s3.testing import FakeS3

s3 = FakeS3()
storage = s3.storage(location="media", gzip=True)
```

`s3.storage(**options)` builds an `S3Storage` bound to the service; anything you pass overrides its defaults.
For another storage class, take the options and pass them yourself:

```python
from django_capo_s3 import S3ManifestStaticStorage

static = S3ManifestStaticStorage(**s3.storage_options(location="static"))
```

**Or patch the transport for a block.** `mock_s3()` catches every S3 request made inside it, including ones
from storages Django built out of the `STORAGES` setting, from `default_storage`, and from `collectstatic`:

```python
from django.core.files.storage import default_storage
from django_capo_s3.testing import mock_s3


def test_upload_through_the_default_storage():
    with mock_s3(bucket="my-bucket") as s3:
        default_storage.save("notes.txt", ContentFile(b"hi"))

        assert s3.keys() == ["media/notes.txt"]
```

For the length of the block the AWS environment is pinned: throwaway keys, and the service's own region.
Both are things a deployed process picks up from its surroundings, and a test that inherits them either walks
out to the real credential chain (on EC2, as far as the instance metadata service) or fails to resolve an
endpoint at all. The service accepts any credentials; what it signs with only matters if you assert on it.

Both of capo's clients are covered. This backend is synchronous, but a project that reaches for
`AsyncS3Client` elsewhere would otherwise have those requests slip past the service and onto the network, so
they are served out of the same objects and recorded on the same `s3.calls`.

## Pytest fixtures

Two fixtures come ready-made. Load the plugin from your root `conftest.py`:

```python
pytest_plugins = ["django_capo_s3.testing.plugin"]
```

or from your pytest configuration:

```ini
[pytest]
addopts = -p django_capo_s3.testing.plugin
```

`s3` is a fresh service with the transport patched, and `s3_storage` is a media storage pointed at it:

```python
def test_report_is_uploaded(s3, s3_storage):
    s3_storage.save("report.csv", ContentFile(b"a,b,c"))

    assert s3["media/report.csv"] == b"a,b,c"
```

## Reading the stored objects

`s3[key]` returns the bytes exactly as they are stored — still gzipped for a gzip-compressed object, which is
what makes the compression itself assertable. Keys are **object keys**, so they include the storage's
`location` prefix. An object outside the default bucket is addressed as `s3[bucket, key]`, the way the
`objects` dict is keyed.

```python
body = b"body{color:red}" * 100
storage = s3.storage(gzip=True)
storage.save("style.css", ContentFile(body))

stored = s3.stored("style.css")
assert stored.content_encoding == "gzip"
assert stored.content_type == "text/css"
assert stored.size < len(body)                 # smaller at rest than what went in
assert gzip.decompress(stored.data) == body    # and it round-trips
```

|                              | Gives you                            |
| ---------------------------- | ------------------------------------ |
| `s3[key]`                    | the stored bytes, or `KeyError`      |
| `s3[bucket, key]`            | the same, in another bucket          |
| `key in s3`                  | whether anything is stored under it  |
| `s3.stored(key, bucket=...)` | the object with its metadata:        |
| `s3.keys(bucket=...)`        | the stored keys, sorted              |
| `s3.objects`                 | everything, keyed by `(bucket, key)` |

`s3.put(key, data, content_type=..., **headers)` stores an object without going through the client — use it
for the state a test starts from, so the arrange step doesn't show up among the recorded calls:

```python
s3.put("media/report.csv", b"a,b,c", content_type="text/csv")

assert storage.read_bytes("report.csv") == b"a,b,c"
assert s3.calls.operations == ["GetObject"]
```

`s3.reset()` drops the objects, the calls, and any canned responses.

## Asserting on the calls

`s3.calls` is every request the service was asked to serve, in order, each resolved to the S3 operation it
stands for. It is a sequence, and the filters return a narrowed one, so they chain:

```python
storage.save("one.txt", ContentFile(b"1"))
storage.save("two.txt", ContentFile(b"2"))
storage.exists("one.txt")

assert s3.calls.operations == ["PutObject", "PutObject", "HeadObject"]
assert s3.calls.of("PutObject").keys == ["media/one.txt", "media/two.txt"]
assert s3.calls.trace == [
    ("PutObject", "media/one.txt"),
    ("PutObject", "media/two.txt"),
    ("HeadObject", "media/one.txt"),
]
assert s3.calls.for_key("media/one.txt").operations == ["PutObject", "HeadObject"]
assert s3.calls.for_bucket("archive") == []
```

|                                         | Gives you                                              |
| --------------------------------------- | ------------------------------------------------------ |
| `.of(*operations)`                      | the calls for those operations                         |
| `.for_key(key)` / `.for_bucket(bucket)` | the calls for one object, or one bucket                |
| `.operations` / `.keys`                 | the operation names, or the object keys, in call order |
| `.trace`                                | each call as `(operation, key)`                        |
| `.last`                                 | the most recent call                                   |

Each call carries what the request looked like on the wire:

```python
call = s3.calls.last
assert call.operation == "PutObject"
assert (call.bucket, call.key) == ("test-bucket", "media/report.csv")
assert call.body == b"a,b,c"
assert call.headers["content-type"] == "text/csv"
assert call.headers["x-amz-storage-class"] == "INTELLIGENT_TIERING"
assert call.metadata() == {"owner": "reports"}
```

`call.signature` parses the `Authorization` header, for when what a request was _signed with_ is the point —
a per-region storage, a switched credential set:

```python
assert s3.calls.last.signature.access_key == "test-access-key"
assert s3.calls.last.signature.region == "eu-central-1"
```

A streaming upload arrives as a one-shot iterator; `call.body` has already drained it and put the bytes back
in its place, so reading it twice is fine.

## Presigned URLs

`url()` never leaves the process, so it needs no mocking — but picking a signed URL apart with `urlparse` and
`parse_qs` gets old. `parse_presigned_url` does it:

```python
from django_capo_s3.testing import parse_presigned_url

url = storage.url("report.csv", expire=300, parameters={"response_content_disposition": "attachment"})

signed = parse_presigned_url(url)
assert signed.host == "my-bucket.s3.eu-central-1.amazonaws.com"
assert signed.path == "/media/report.csv"
assert signed.expires == 300
assert signed.access_key == "test-access-key"
assert signed.overrides == {"response-content-disposition": "attachment"}
```

`overrides` is everything the URL carries beyond the signature itself, which is usually the part under test.

## Making S3 fail

`s3.fail()` makes matching requests come back as an S3 error, which is how the error paths get covered:

```python
s3.fail("PutObject", code="AccessDenied", status=403)

with pytest.raises(UnknownServiceError):
    storage.save("report.csv", ContentFile(b"a,b"))
```

Narrow it with `key=` to one object, drop the operation to fail everything, and pass `times=` to let the
service take over again afterwards — which is how a retry or a fallback path gets tested:

```python
s3.fail("HeadObject", key="media/report.csv", code="AccessDenied", status=403, times=1)
```

For a reply that isn't an error, `s3.respond_with(response, operation=..., key=..., times=...)` takes any
[zapros](https://zapros.dev/mocking.html) `Response`. Reach for it when the service won't produce what the
test needs — a listing truncated at a chosen token, a header only some providers send:

```python
from capo_s3.types import list_objects_v2_output
from django_capo_s3.testing import xml_response

s3.respond_with(
    xml_response(
        list_objects_v2_output.serialize_xml,
        {"contents": [{"key": "media/a.txt"}], "is_truncated": True, "next_continuation_token": "page-2"},
        "ListBucketResult",
    ),
    operation="ListObjectsV2",
    times=1,
)
```

`xml_response` builds the body with capo's own serializer for that operation, so the XML can't drift out of
step with what the client parses. `s3_error(code, status=..., message=...)` builds an error body the same way,
and `not_found()` is the body-less 404 that answers a `HEAD` for a missing object.

## Dropping down to zapros

The service is a [zapros](https://zapros.dev/mocking.html) handler with a `MockRouter` in front of it, and
`s3.router` is that router. Anything you mount on it takes the request before the service sees it, with the
full matcher vocabulary available:

```python
from zapros import Response
from zapros.matchers import method, path
from zapros.mock import Mock

put = Mock.given(method("PUT")).and_(path("/media/report.csv")).respond(Response(status=200)).once()
s3.router.add(put)

storage.save("report.csv", ContentFile(b"a,b"))

put.assert_called_once()
assert s3.keys() == []  # the mock answered, so the service stored nothing
```

Requests are recorded either way, so `s3.calls` still sees everything. Leaving a `mock_s3()` block cleanly
verifies what you mounted, so a `.once()` that never fired fails the test — `s3.fail(..., times=n)` is a
ceiling rather than an expectation and is not verified.

## Not testing S3 at all

Plenty of tests touch storage only because the code under test writes a file, and have nothing to say about
S3. Point those at Django's own in-memory backend instead — it's faster, and it says what you mean:

```python
def test_something_else(settings):
    settings.STORAGES = {"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}}
    ...
```

Keep `FakeS3` for the tests that are actually about the trip to S3.
