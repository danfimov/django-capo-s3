# The fake service is the thing under test here, so unlike the rest of the suite these run without MinIO.
import asyncio
import gzip

import pytest
from capo_s3 import AsyncS3Client
from capo_s3.errors import UnknownServiceError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from zapros import Response
from zapros.matchers import method
from zapros.mock import Mock

from django_capo_s3.static import S3ManifestStaticStorage
from django_capo_s3.storage import S3Storage
from django_capo_s3.testing import FakeS3, mock_s3, parse_presigned_url, s3_error


def test_when_a_file_is_saved_then_it_reads_back_under_the_prefixed_key(s3: FakeS3, s3_storage: S3Storage):
    name = s3_storage.save("report.csv", ContentFile(b"a,b,c"))

    assert name == "report.csv"
    assert s3["media/report.csv"] == b"a,b,c"  # the stored key carries the location prefix
    assert s3_storage.read_bytes("report.csv") == b"a,b,c"
    assert s3_storage.size("report.csv") == 5
    assert s3_storage.exists("report.csv")


def test_when_a_file_is_deleted_then_the_object_is_gone(s3: FakeS3, s3_storage: S3Storage):
    s3.put("media/report.csv", b"a,b,c")

    s3_storage.delete("report.csv")

    assert s3.keys() == []
    assert not s3_storage.exists("report.csv")


def test_when_many_names_are_deleted_then_one_bulk_request_removes_them(s3: FakeS3, s3_storage: S3Storage):
    for index in range(3):
        s3.put(f"media/{index}.txt", b"x")

    s3_storage.delete_objects(["0.txt", "2.txt"])

    assert s3.keys() == ["media/1.txt"]
    assert s3.calls.operations == ["DeleteObjects"]


def test_when_a_missing_object_is_read_then_file_not_found_is_raised(s3_storage: S3Storage):
    with pytest.raises(FileNotFoundError, match=r"missing\.csv"):
        _ = s3_storage.read_bytes("missing.csv")


def test_when_a_write_handle_is_closed_then_the_buffer_is_uploaded(s3: FakeS3, s3_storage: S3Storage):
    with s3_storage.open("notes.txt", "wb") as handle:
        _ = handle.write(b"written")

    assert s3["media/notes.txt"] == b"written"


def test_when_gzip_is_on_then_the_object_is_stored_compressed_and_read_back_plain(s3: FakeS3):
    storage = s3.storage(gzip=True)
    body = b"body{color:red}" * 100

    storage.save("style.css", ContentFile(body))

    stored = s3.stored("style.css")
    assert stored.content_encoding == "gzip"
    assert stored.content_type == "text/css"
    assert gzip.decompress(stored.data) == body
    assert storage.open("style.css").read() == body


def test_when_object_parameters_are_configured_then_they_reach_the_wire(s3: FakeS3):
    storage = s3.storage(object_parameters={"storage_class": "INTELLIGENT_TIERING", "cache_control": "max-age=86400"})

    storage.save("logo.png", ContentFile(b"png"))

    stored = s3.stored("logo.png")
    assert stored.headers["x-amz-storage-class"] == "INTELLIGENT_TIERING"
    assert stored.headers["cache-control"] == "max-age=86400"


def test_when_metadata_is_configured_then_it_is_stored_and_reported_back(s3: FakeS3):
    storage = s3.storage(object_parameters={"metadata": {"owner": "reports"}})

    storage.save("report.csv", ContentFile(b"a,b"))

    assert s3.stored("report.csv").metadata == {"owner": "reports"}
    assert s3.calls.last.metadata() == {"owner": "reports"}


def test_when_the_content_declares_its_type_then_it_wins_over_the_extension(s3: FakeS3, s3_storage: S3Storage):
    s3_storage.save("report.txt", SimpleUploadedFile("report.txt", b"x", content_type="application/pdf"))

    assert s3.stored("media/report.txt").content_type == "application/pdf"


@pytest.mark.parametrize(
    ("size", "expected_operations"),
    [
        pytest.param(1024, ["PutObject"], id="below-the-threshold-a-single-put"),
        pytest.param(
            6 * 1024 * 1024,
            ["CreateMultipartUpload", "UploadPart", "UploadPart", "CompleteMultipartUpload"],
            id="above-the-threshold-two-parts",
        ),
    ],
)
def test_when_an_upload_crosses_the_multipart_threshold_then_it_is_sent_in_parts(
    s3: FakeS3,
    size: int,
    expected_operations: list[str],
):
    part_size = 5 * 1024 * 1024  # the smallest part S3 accepts, so a 6 MiB body splits in two
    storage = s3.storage(multipart_threshold=part_size, multipart_chunksize=part_size)
    payload = b"x" * size

    storage.save("big.bin", ContentFile(payload))

    assert s3.calls.operations == expected_operations
    assert s3["big.bin"] == payload


def test_when_a_part_upload_fails_then_the_transfer_is_aborted(s3: FakeS3):
    part_size = 5 * 1024 * 1024
    storage = s3.storage(multipart_threshold=part_size, multipart_chunksize=part_size)
    s3.fail("UploadPart", code="AccessDenied", status=403)

    with pytest.raises(UnknownServiceError):
        storage.save("big.bin", ContentFile(b"x" * (6 * 1024 * 1024)))

    assert s3.calls.operations[-1] == "AbortMultipartUpload"
    assert "big.bin" not in s3


def test_when_a_path_is_listed_then_directories_and_files_come_back_apart(s3: FakeS3, s3_storage: S3Storage):
    for key in ("media/report.csv", "media/sub/one.txt", "media/sub/two.txt", "media/other/three.txt"):
        s3.put(key, b"x")

    assert s3_storage.listdir("") == (["other", "sub"], ["report.csv"])
    assert s3_storage.listdir("sub") == ([], ["one.txt", "two.txt"])


def test_when_a_listing_fits_one_page_then_it_costs_one_request(s3: FakeS3, s3_storage: S3Storage):
    for index in range(7):
        s3.put(f"media/{index}.txt", b"x")

    _, files = s3_storage.listdir("")

    assert len(s3.calls.of("ListObjectsV2")) == 1
    assert files == [f"{index}.txt" for index in range(7)]


def test_when_a_listing_is_truncated_then_the_token_continues_it(s3: FakeS3):
    for index in range(5):
        s3.put(f"page/{index}.txt", b"x")
    storage = s3.storage()

    first = storage.client.list_objects_v2(s3.bucket, prefix="page/", max_keys=2)
    second = storage.client.list_objects_v2(
        s3.bucket,
        prefix="page/",
        max_keys=2,
        continuation_token=first["next_continuation_token"],
    )

    assert first["is_truncated"] is True
    assert [entry["key"] for entry in first["contents"]] == ["page/0.txt", "page/1.txt"]
    assert [entry["key"] for entry in second["contents"]] == ["page/2.txt", "page/3.txt"]


def test_when_an_object_is_copied_then_the_bytes_and_type_are_duplicated(s3: FakeS3):
    s3.put("src.txt", b"payload", content_type="text/plain")
    storage = s3.storage()

    _ = storage.client.copy_object(s3.bucket, f"{s3.bucket}/src.txt", "dst.txt")

    assert s3["dst.txt"] == b"payload"
    assert s3.stored("dst.txt").content_type == "text/plain"


def test_when_for_bucket_is_used_then_the_request_targets_that_bucket(s3: FakeS3):
    storage = s3.storage()

    storage.for_bucket("archive").save("cold.txt", ContentFile(b"cold"))

    assert s3.keys(bucket="archive") == ["cold.txt"]
    assert s3.calls.last.bucket == "archive"
    assert s3["archive", "cold.txt"] == b"cold"  # a bucket other than the default one is addressed as a pair
    assert ("archive", "cold.txt") in s3


def test_when_a_storage_configures_no_region_then_the_pinned_environment_supplies_one():
    with mock_s3(region="eu-central-1") as s3:
        storage = S3Storage(bucket=s3.bucket)

        storage.save("notes.txt", ContentFile(b"x"))

        signature = s3.calls.last.signature
        assert signature is not None
        assert signature.region == "eu-central-1"


@pytest.mark.parametrize(
    ("options", "expected_url"),
    [
        pytest.param(
            {},
            "https://test-bucket.s3.us-east-1.amazonaws.com/notes.txt?x-id=PutObject",
            id="virtual-host",
        ),
        pytest.param(
            {"endpoint": "http://localhost:9000", "force_path_style": True},
            "http://localhost:9000/test-bucket/notes.txt?x-id=PutObject",
            id="path-style",
        ),
    ],
)
def test_when_a_request_is_recorded_then_its_bucket_and_key_follow_the_addressing_style(
    s3: FakeS3,
    options: dict[str, object],
    expected_url: str,
):
    storage = s3.storage(**options)

    storage.save("notes.txt", ContentFile(b"x"))

    call = s3.calls.last
    assert call.url == expected_url
    assert (call.bucket, call.key) == ("test-bucket", "notes.txt")


def test_when_calls_are_narrowed_then_only_the_matching_ones_remain(s3: FakeS3, s3_storage: S3Storage):
    s3_storage.save("one.txt", ContentFile(b"1"))
    s3_storage.save("two.txt", ContentFile(b"2"))
    _ = s3_storage.exists("one.txt")

    assert s3.calls.operations == ["PutObject", "PutObject", "HeadObject"]
    assert s3.calls.of("PutObject").keys == ["media/one.txt", "media/two.txt"]
    assert s3.calls.trace == [
        ("PutObject", "media/one.txt"),
        ("PutObject", "media/two.txt"),
        ("HeadObject", "media/one.txt"),
    ]
    assert s3.calls.for_key("media/one.txt").operations == ["PutObject", "HeadObject"]
    assert s3.calls.for_bucket("nowhere") == []
    assert s3.calls.last.operation == "HeadObject"


def test_when_nothing_was_called_then_asking_for_the_last_call_says_so(s3: FakeS3):
    with pytest.raises(AssertionError, match="No S3 calls"):
        _ = s3.calls.last


def test_when_a_request_is_made_then_it_is_signed_with_the_configured_credentials(s3: FakeS3, s3_storage: S3Storage):
    s3_storage.save("report.csv", ContentFile(b"a,b"))

    signature = s3.calls.last.signature
    assert signature is not None
    assert signature.access_key == "test-access-key"
    assert signature.region == "us-east-1"
    assert signature.service == "s3"
    assert "host" in signature.signed_headers


def test_when_a_presigned_url_is_parsed_then_its_parts_are_readable(s3_storage: S3Storage):
    url = s3_storage.url(
        "report.csv",
        expire=300,
        parameters={"response_content_disposition": "attachment; filename=x.csv"},
    )

    signed = parse_presigned_url(url)
    assert signed.host == "test-bucket.s3.us-east-1.amazonaws.com"
    assert signed.path == "/media/report.csv"
    assert signed.expires == 300
    assert signed.access_key == "test-access-key"
    assert signed.region == "us-east-1"
    assert signed.signature is not None
    assert signed.overrides == {"response-content-disposition": "attachment; filename=x.csv"}


def test_when_a_streamed_body_is_read_twice_then_the_bytes_are_still_there(s3: FakeS3, s3_storage: S3Storage):
    s3_storage.save("report.csv", ContentFile(b"a,b,c"))

    assert s3.calls.last.body == b"a,b,c"
    assert s3.calls.last.body == b"a,b,c"


@pytest.mark.parametrize(
    ("code", "status"),
    [
        pytest.param("AccessDenied", 403, id="access-denied"),
        pytest.param("NoSuchBucket", 404, id="no-such-bucket"),
        pytest.param("SlowDown", 503, id="throttled"),
    ],
)
def test_when_an_operation_is_made_to_fail_then_the_client_raises_that_error(
    s3: FakeS3,
    s3_storage: S3Storage,
    code: str,
    status: int,
):
    s3.fail("PutObject", code=code, status=status)

    with pytest.raises(UnknownServiceError) as raised:
        s3_storage.save("report.csv", ContentFile(b"a,b"))

    assert raised.value.code == code
    assert "media/report.csv" not in s3


def test_when_a_failure_names_a_key_then_only_that_object_fails(s3: FakeS3, s3_storage: S3Storage):
    s3.fail("PutObject", key="media/blocked.txt", code="AccessDenied", status=403)

    s3_storage.save("allowed.txt", ContentFile(b"x"))
    with pytest.raises(UnknownServiceError):
        s3_storage.save("blocked.txt", ContentFile(b"x"))

    assert s3.keys() == ["media/allowed.txt"]


def test_when_a_canned_response_runs_out_then_the_service_answers_again(s3: FakeS3, s3_storage: S3Storage):
    s3.put("media/report.csv", b"a,b,c")
    s3.respond_with(s3_error("AccessDenied", status=403), operation="HeadObject", times=1)

    with pytest.raises(UnknownServiceError):
        _ = s3_storage.size("report.csv")

    assert s3_storage.size("report.csv") == 5


def test_when_a_mock_is_mounted_then_it_answers_instead_of_the_service(s3: FakeS3, s3_storage: S3Storage):
    _ = s3.router.add(Mock.given(method("PUT")).respond(Response(status=200, headers={"ETag": '"stub"'})))

    s3_storage.save("report.csv", ContentFile(b"a,b"))

    assert s3.keys() == []  # the mock answered, so the service stored nothing
    assert s3.calls.operations == ["PutObject"]  # the request is recorded either way


def test_when_a_mounted_mock_goes_unused_then_leaving_the_block_says_so():
    with pytest.raises(AssertionError, match="expected 1 calls, got 0"), mock_s3() as s3:
        _ = s3.router.add(Mock.given(method("PUT")).respond(Response(status=200)).once())


def test_when_the_block_raises_then_the_failure_is_not_masked_by_an_unused_mock():
    def leave_a_mock_unused_and_fail() -> None:
        with mock_s3() as s3:
            _ = s3.router.add(Mock.given(method("PUT")).respond(Response(status=200)).once())
            msg = "the real failure"
            raise ValueError(msg)

    with pytest.raises(ValueError, match="the real failure"):
        leave_a_mock_unused_and_fail()


def test_when_the_service_is_reset_then_objects_and_calls_are_dropped(s3: FakeS3, s3_storage: S3Storage):
    s3_storage.save("report.csv", ContentFile(b"a,b"))
    s3_storage.for_bucket("archive").save("cold.txt", ContentFile(b"cold"))

    s3.reset()

    assert s3.keys() == []
    assert len(s3.calls) == 0
    assert s3.keys(bucket="archive") == []  # including in a bucket only a request introduced


def test_when_an_async_client_is_used_then_it_is_served_by_the_same_service(s3: FakeS3):
    async def exercise() -> int:
        async with AsyncS3Client() as client:  # region and keys come from the pinned environment
            _ = await client.put_object(s3.bucket, "async.txt", body=b"written by the async client")
            return (await client.head_object(s3.bucket, "async.txt")).get("content_length", 0)

    size = asyncio.run(exercise())

    assert size == len(b"written by the async client")
    assert s3["async.txt"] == b"written by the async client"
    assert s3.calls.trace == [("PutObject", "async.txt"), ("HeadObject", "async.txt")]


def test_when_a_storage_comes_from_settings_then_its_requests_are_still_caught():
    with mock_s3(bucket="django-capo-s3-default") as s3:
        default_storage.save("notes.txt", ContentFile(b"hi"))

        assert s3.keys() == ["notes.txt"]
        assert s3.calls.operations == ["PutObject"]


def test_when_an_unimplemented_operation_is_called_then_the_error_names_the_request(s3: FakeS3):
    storage = s3.storage()

    with pytest.raises(UnknownServiceError, match="NotImplemented"):
        _ = storage.client.get_bucket_versioning(s3.bucket)


def test_when_an_asset_is_already_stored_then_collectstatic_does_not_re_upload_it(s3: FakeS3):
    static = S3ManifestStaticStorage(**s3.storage_options(location="static"))
    s3.put("static/style.css", b"body{color:red}", content_type="text/css")

    processed = list(static.post_process({"style.css": (static, "style.css")}))

    uploads = s3.calls.of("PutObject").keys
    assert [name for name, _, _ in processed] == ["style.css"]
    assert "static/style.css" not in uploads  # unchanged, so it was not re-uploaded
    assert any(key.startswith("static/style.") and key.endswith(".css") for key in uploads)


def test_when_storage_options_are_reused_then_another_storage_talks_to_the_service(s3: FakeS3):
    storage = S3Storage(**s3.storage_options(location="uploads", file_overwrite=False))

    storage.save("report.csv", ContentFile(b"a,b"))

    assert s3.keys() == ["uploads/report.csv"]
