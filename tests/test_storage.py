from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from capo_s3 import S3Client
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.utils import timezone

from django_capo_s3.storage import S3Storage


def test_save_open_roundtrip(storage: S3Storage):
    name = storage.save("dir/hello.txt", ContentFile(b"hi there"))
    assert name == "dir/hello.txt"
    assert storage.exists(name)
    with storage.open(name) as handle:
        assert handle.read() == b"hi there"
    assert storage.size(name) == len(b"hi there")


def test_save_applies_location_prefix(
    storage_factory: Callable[..., S3Storage],
    bucket: str,
    s3_client: S3Client,
):
    storage = storage_factory(location="media")
    storage.save("photos/cat.txt", ContentFile(b"x"))
    # The raw key in the bucket carries the location prefix.
    assert s3_client.head_object(bucket, "media/photos/cat.txt")["content_length"] == 1


def test_file_overwrite_reuses_name(storage_factory: Callable[..., S3Storage]):
    storage = storage_factory(file_overwrite=True)
    first = storage.save("same.txt", ContentFile(b"one"))
    second = storage.save("same.txt", ContentFile(b"two"))
    assert first == second == "same.txt"
    with storage.open("same.txt") as handle:
        assert handle.read() == b"two"


def test_default_naming_avoids_collision(storage: S3Storage):
    first = storage.save("same.txt", ContentFile(b"one"))
    second = storage.save("same.txt", ContentFile(b"two"))
    assert first == "same.txt"
    assert second != first


def test_delete_is_idempotent(storage: S3Storage):
    name = storage.save("gone.txt", ContentFile(b"x"))
    storage.delete(name)
    assert not storage.exists(name)
    storage.delete(name)  # deleting a missing key must not raise


def test_delete_objects_removes_all_keys(storage: S3Storage):
    names = [storage.save(f"bulk/{i}.txt", ContentFile(b"x")) for i in range(5)]
    storage.delete_objects(names)
    assert all(not storage.exists(name) for name in names)


def test_delete_objects_tolerates_missing_keys(storage: S3Storage):
    name = storage.save("present.txt", ContentFile(b"x"))
    storage.delete_objects([name, "never/existed.txt"])  # missing keys count as deleted
    assert not storage.exists(name)


def test_delete_objects_clears_many_keys_under_a_prefix(
    storage_factory: Callable[..., S3Storage],
    bucket: str,
    s3_client: S3Client,
):
    storage = storage_factory(location="many")
    names = [f"obj-{i}.txt" for i in range(40)]  # many keys deleted in one bulk request
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda n: s3_client.put_object(bucket, f"many/{n}", body=b"x", content_length=1), names))
    storage.delete_objects(names)
    assert not s3_client.list_objects_v2(bucket, prefix="many/").get("contents")


def test_url_presigned_by_default(storage: S3Storage):
    storage.save("file.txt", ContentFile(b"x"))
    url = storage.url("file.txt")
    assert "X-Amz-Signature" in url


def test_url_custom_domain_is_plain(storage_factory: Callable[..., S3Storage]):
    storage = storage_factory(custom_domain="cdn.example.com")
    assert storage.url("a/b.txt") == "https://cdn.example.com/a/b.txt"


def test_url_expire_override(storage: S3Storage):
    assert "X-Amz-Expires=42" in storage.url("file.txt", expire=42)


def test_url_response_parameters(storage: S3Storage):
    url = storage.url("file.txt", parameters={"response_content_disposition": "attachment"})
    assert "response-content-disposition" in url


def test_url_head_method_is_presigned(storage: S3Storage):
    assert "X-Amz-Signature" in storage.url("file.txt", http_method="HEAD")


def test_url_unsupported_method_is_rejected(storage: S3Storage):
    with pytest.raises(ValueError, match="only GET, HEAD and PUT"):
        storage.url("file.txt", http_method="DELETE")


def test_listdir_splits_dirs_and_files(storage: S3Storage):
    storage.save("top.txt", ContentFile(b"1"))
    storage.save("sub/nested.txt", ContentFile(b"2"))
    storage.save("sub/deep/leaf.txt", ContentFile(b"3"))
    dirs, files = storage.listdir("")
    assert "sub" in dirs
    assert "top.txt" in files
    assert "sub/nested.txt" not in files


@pytest.mark.timeout(30)  # seeds 1001 objects over the network; well past the default per-test budget
def test_listdir_paginates(storage: S3Storage, bucket: str, s3_client: S3Client):
    # One more than the default max-keys page size, to force a second listing page. Seed concurrently.
    total = 1001

    def seed(index: int) -> None:
        s3_client.put_object(bucket, f"page/obj-{index:04d}.txt", body=b"x")

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(seed, range(total)))

    _dirs, files = storage.listdir("page")
    assert len(files) == total


def test_missing_object_raises_file_not_found(storage: S3Storage):
    assert storage.exists("missing.txt") is False
    with pytest.raises(FileNotFoundError):
        storage.size("missing.txt")
    with pytest.raises(FileNotFoundError), storage.open("missing.txt") as handle:
        handle.read()


def test_get_modified_time_is_recent_and_aware(storage: S3Storage):
    before = timezone.now()
    storage.save("stamp.txt", ContentFile(b"x"))
    modified = storage.get_modified_time("stamp.txt")
    assert modified.tzinfo is not None
    assert abs((modified - before).total_seconds()) < 300


def test_large_file_streaming_roundtrip(storage: S3Storage):
    payload = b"abcd" * (512 * 1024)  # 2 MiB — exercises the spooled buffer + streaming put
    storage.save("big.bin", ContentFile(payload))
    with storage.open("big.bin") as handle:
        assert handle.read() == payload


def test_write_mode_file_flushes_on_close(storage: S3Storage):
    handle = storage.open("written.txt", "wb")
    handle.write(b"streamed")
    handle.close()
    with storage.open("written.txt") as reopened:
        assert reopened.read() == b"streamed"


def test_text_mode_read_returns_str(storage: S3Storage):
    storage.save("poem.txt", ContentFile("café\nnaïve".encode()))
    with storage.open("poem.txt", "rt") as handle:
        content = handle.read()
    assert content == "café\nnaïve"
    assert isinstance(content, str)


def test_text_mode_iterates_by_line(storage: S3Storage):
    storage.save("lines.txt", ContentFile(b"one\ntwo\nthree"))
    with storage.open("lines.txt", "rt") as handle:
        assert list(handle) == ["one\n", "two\n", "three"]


def test_text_mode_write_roundtrips_non_ascii(storage: S3Storage, bucket: str, s3_client: S3Client):
    handle = storage.open("greeting.txt", "wt")
    handle.write("héllo wörld")
    handle.close()
    # Stored as UTF-8 bytes on the wire...
    with s3_client.get_object(bucket, "greeting.txt") as out:
        assert b"".join(out["body"]) == "héllo wörld".encode()
    # ...and decoded back to str on a text read.
    with storage.open("greeting.txt", "rt") as reopened:
        assert reopened.read() == "héllo wörld"


def test_non_ascii_text_upload_is_not_truncated(storage: S3Storage, bucket: str, s3_client: S3Client):
    """A multi-byte character must upload its full byte length, not a char count, and pass MinIO's v4 check."""
    text = "héllo" * 100  # 500 chars, 600 UTF-8 bytes
    name = storage.save("unicode.txt", ContentFile(text.encode()))
    assert s3_client.head_object(bucket, "unicode.txt")["content_length"] == len(text.encode())
    with storage.open(name) as handle:
        assert handle.read() == text.encode()


def test_storages_registration_roundtrip(s3_client: S3Client):
    storage = storages["default"]
    assert isinstance(storage, S3Storage)
    bucket_name = storage.options["bucket"]
    s3_client.create_bucket(bucket_name)
    try:
        name = storage.save("registered.txt", ContentFile(b"ok"))
        with storage.open(name) as handle:
            assert handle.read() == b"ok"
        storage.delete(name)
    finally:
        s3_client.delete_bucket(bucket_name)
