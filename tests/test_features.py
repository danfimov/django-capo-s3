from collections.abc import Callable

import pytest
from capo_s3 import S3Client
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile

from django_capo_s3.storage import S3Storage


@pytest.mark.parametrize(
    ("name", "declared", "options", "expected"),
    [
        pytest.param("notes.txt", None, {}, "text/plain", id="guessed-from-the-extension"),
        pytest.param("statements/8f21", "application/pdf", {}, "application/pdf", id="from-the-content"),
        pytest.param("report.txt", "application/pdf", {}, "application/pdf", id="content-beats-the-extension"),
        pytest.param("statements/9c04", None, {}, "application/octet-stream", id="nothing-known-so-the-default"),
        pytest.param(
            "statements/1a77",
            None,
            {"default_content_type": "binary/octet-stream"},
            "binary/octet-stream",
            id="the-default-is-configurable",
        ),
        pytest.param(
            "report.txt",
            "application/pdf",
            {"object_parameters": {"content_type": "application/x-forced"}},
            "application/x-forced",
            id="object-parameters-beat-everything",
        ),
    ],
)
def test_content_type_resolution(  # noqa: PLR0913, PLR0917
    storage_factory: Callable[..., S3Storage],
    bucket: str,
    s3_client: S3Client,
    name: str,
    declared: str | None,
    options: dict[str, object],
    expected: str,
):
    storage = storage_factory(**options)
    content = ContentFile(b"x") if declared is None else SimpleUploadedFile(name, b"x", content_type=declared)
    storage.save(name, content)
    with s3_client.get_object(bucket, name) as out:
        assert out.get("content_type") == expected


def test_gzip_uses_the_content_reported_type(
    storage_factory: Callable[..., S3Storage],
    bucket: str,
    s3_client: S3Client,
):
    storage = storage_factory(gzip=True)
    body = b"body{color:red}" * 100
    storage.save("assets/1b3f", SimpleUploadedFile("style", body, content_type="text/css"))
    with s3_client.get_object(bucket, "assets/1b3f") as out:
        assert out.get("content_encoding") == "gzip"
        assert out.get("content_type") == "text/css"


def test_gzip_compresses_matching_content_types(
    storage_factory: Callable[..., S3Storage],
    bucket: str,
    s3_client: S3Client,
):
    storage = storage_factory(gzip=True)
    body = b"body{color:red}" * 100  # 1500 bytes, highly compressible
    storage.save("style.css", ContentFile(body))

    entry = next(o for o in s3_client.list_objects_v2(bucket).get("contents", []) if o["key"] == "style.css")
    assert entry["size"] < len(body)  # stored compressed at rest

    with s3_client.get_object(bucket, "style.css") as out:
        assert out.get("content_encoding") == "gzip"

    with storage.open("style.css") as handle:  # transparently decompressed on read
        assert handle.read() == body


def test_gzipped_object_must_still_be_openable_in_text_mode_and_yield_decoded_str(
    storage_factory: Callable[..., S3Storage],
    bucket: str,
    s3_client: S3Client,
):
    storage = storage_factory(gzip=True)
    body = "body{content:'café'}" * 50
    storage.save("style.css", ContentFile(body.encode()))

    with s3_client.get_object(bucket, "style.css") as out:
        assert out.get("content_encoding") == "gzip"  # stored compressed

    with storage.open("style.css", "rt") as handle:
        content = handle.read()
    assert content == body
    assert isinstance(content, str)


def test_gzip_skips_non_matching_content_types(
    storage_factory: Callable[..., S3Storage],
    bucket: str,
    s3_client: S3Client,
):
    storage = storage_factory(gzip=True)
    body = b"\x89PNG\r\n" + b"\x00" * 300
    storage.save("image.png", ContentFile(body))
    with s3_client.get_object(bucket, "image.png") as out:
        assert out.get("content_encoding") is None
        assert b"".join(out["body"]) == body


def test_object_parameters_pass_through_to_put(
    storage_factory: Callable[..., S3Storage],
    bucket: str,
    s3_client: S3Client,
):
    storage = storage_factory(object_parameters={"cache_control": "max-age=60"})
    storage.save("cached.txt", ContentFile(b"x"))
    with s3_client.get_object(bucket, "cached.txt") as out:
        assert out.get("cache_control") == "max-age=60"


def test_default_acl_is_accepted(storage_factory: Callable[..., S3Storage]):
    # MinIO ignores canned ACLs, but the option must flow through put_object without error.
    storage = storage_factory(default_acl="private")
    name = storage.save("acl.txt", ContentFile(b"x"))
    assert storage.exists(name)


def test_max_memory_size_option_round_trips(storage_factory: Callable[..., S3Storage]):
    # A tiny in-memory limit forces the read buffer to spill to disk; content must still round-trip.
    storage = storage_factory(max_memory_size=10)
    payload = b"x" * 1000
    storage.save("spill.bin", ContentFile(payload))
    with storage.open("spill.bin") as handle:
        assert handle.read() == payload


@pytest.mark.limit_memory("12 MB")
def test_reading_an_object_streams_it_into_the_spool_instead_of_buffering_it_whole(
    storage_factory: Callable[..., S3Storage],
):
    storage = storage_factory(max_memory_size=64 * 1024)
    payload = b"x" * (8 * 1024 * 1024)
    storage.save("stream.bin", ContentFile(payload))
    with storage.open("stream.bin") as handle:
        assert handle.read(16) == b"x" * 16  # enough to trigger the fetch, too little to materialize the object


def test_large_file_uploads_via_multipart(
    storage_factory: Callable[..., S3Storage],
    bucket: str,
    s3_client: S3Client,
):
    part = 5 * 1024 * 1024  # S3's minimum part size
    storage = storage_factory(multipart_threshold=part, multipart_chunksize=part)
    payload = b"a" * (part * 2 + 1024)  # 3 parts: 5 MiB + 5 MiB + 1 KiB
    storage.save("big.bin", ContentFile(payload))

    entry = next(o for o in s3_client.list_objects_v2(bucket).get("contents", []) if o["key"] == "big.bin")
    assert "-" in entry["e_tag"]  # a multipart object's ETag is <hash>-<part-count>

    with storage.open("big.bin") as handle:
        assert handle.read() == payload


def test_multipart_upload_is_correct_with_concurrency(storage_factory: Callable[..., S3Storage]):
    part = 5 * 1024 * 1024
    storage = storage_factory(multipart_threshold=part, multipart_chunksize=part, multipart_concurrency=3)
    # Distinct bytes per part so a wrong assembly order would corrupt the round-trip.
    payload = b"1" * part + b"2" * part + b"3" * 2048
    storage.save("ordered.bin", ContentFile(payload))
    with storage.open("ordered.bin") as handle:
        assert handle.read() == payload
