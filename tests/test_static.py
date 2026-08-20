import json
from collections.abc import Callable
from pathlib import Path

import pytest
from capo_s3 import S3Client
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage

from django_capo_s3.static import S3ManifestStaticStorage, S3StaticStorage


def _record_uploads(monkeypatch: pytest.MonkeyPatch, storage: S3StaticStorage) -> list[str]:
    """Spy on the uploader so a test can assert which keys were actually PUT to the bucket."""
    keys: list[str] = []
    real = storage._uploader.upload  # noqa: SLF001

    def spy(bucket: str, key: str, **kwargs: object) -> None:
        keys.append(key)
        return real(bucket, key, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(storage._uploader, "upload", spy)  # noqa: SLF001
    return keys


def test_plain_static_defaults(plain_static_storage: S3StaticStorage):
    assert plain_static_storage.options["file_overwrite"] is True
    assert plain_static_storage.options["querystring_auth"] is False


def test_plain_static_does_not_hash_and_serves_unsigned(plain_static_storage: S3StaticStorage):
    name = plain_static_storage.save("app.js", ContentFile(b"console.log(1)"))
    assert name == "app.js"  # no hash suffix
    url = plain_static_storage.url("app.js")
    assert "X-Amz-Signature" not in url
    assert url.endswith("/static/app.js")


def test_defaults_are_static_friendly(manifest_static_storage: S3ManifestStaticStorage):
    assert manifest_static_storage.options["file_overwrite"] is True
    assert manifest_static_storage.options["querystring_auth"] is False


def test_post_process_hashes_and_url_resolves(manifest_static_storage: S3ManifestStaticStorage):
    storage = manifest_static_storage
    storage.save("app.css", ContentFile(b"body { color: red; }"))

    list(storage.post_process({"app.css": (storage, "app.css")}))

    hashed = storage.stored_name("app.css")
    assert hashed != "app.css"
    assert hashed.startswith("app.")
    assert hashed.endswith(".css")
    assert storage.exists(hashed)

    # url() resolves the logical name to the hashed object (unsigned public URL)
    url = storage.url("app.css")
    assert hashed.rsplit("/", 1)[-1] in url
    assert "X-Amz-Signature" not in url


def test_manifest_is_written_and_maps_names(
    manifest_static_storage: S3ManifestStaticStorage,
    bucket: str,
    s3_client: S3Client,
):
    storage = manifest_static_storage
    storage.save("app.css", ContentFile(b".a{color:blue}"))
    list(storage.post_process({"app.css": (storage, "app.css")}))

    # manifest object exists in the bucket under the location prefix
    assert storage.exists(storage.manifest_name)
    with s3_client.get_object(bucket, f"static/{storage.manifest_name}") as out:
        manifest = json.loads(b"".join(out["body"]))
    assert "app.css" in manifest["paths"]
    assert manifest["paths"]["app.css"] == storage.stored_name("app.css")


def test_manifest_storage_option_keeps_manifest_out_of_s3(
    static_storage_factory: Callable[..., S3ManifestStaticStorage],
    tmp_path: Path,
):
    local = FileSystemStorage(location=str(tmp_path))
    storage = static_storage_factory(manifest_storage=local)
    storage.save("app.css", ContentFile(b".a{color:blue}"))
    list(storage.post_process({"app.css": (storage, "app.css")}))

    # manifest lives in the local storage, not in the bucket
    assert local.exists(storage.manifest_name)
    assert not storage.exists(storage.manifest_name)
    # hashed asset itself still went to S3
    assert storage.exists(storage.stored_name("app.css"))


def test_skip_unchanged_is_on_by_default(manifest_static_storage: S3ManifestStaticStorage):
    assert manifest_static_storage.options["skip_unchanged"] is True
    assert manifest_static_storage.keep_intermediate_files is False


def test_second_collect_skips_unchanged_uploads(
    manifest_static_storage: S3ManifestStaticStorage,
    monkeypatch: pytest.MonkeyPatch,
):
    storage = manifest_static_storage
    storage.save("app.css", ContentFile(b"body{color:red}"))
    paths = {"app.css": (storage, "app.css")}
    list(storage.post_process(paths))  # first collect uploads the hashed asset + manifest

    recorded = _record_uploads(monkeypatch, storage)
    list(storage.post_process(paths))  # nothing changed on disk
    assert recorded == []  # so nothing is re-uploaded


def test_changed_content_is_reuploaded(
    manifest_static_storage: S3ManifestStaticStorage,
    monkeypatch: pytest.MonkeyPatch,
):
    storage = manifest_static_storage
    storage.save("app.css", ContentFile(b"body{color:red}"))
    paths = {"app.css": (storage, "app.css")}
    list(storage.post_process(paths))

    storage.save("app.css", ContentFile(b"body{color:blue}"))  # content changes -> new hash
    recorded = _record_uploads(monkeypatch, storage)
    list(storage.post_process(paths))
    assert any("app." in key for key in recorded)  # the new hashed asset is uploaded


def test_skip_unchanged_disabled_always_uploads(
    static_storage_factory: Callable[..., S3ManifestStaticStorage],
    monkeypatch: pytest.MonkeyPatch,
):
    storage = static_storage_factory(skip_unchanged=False)
    storage.save("app.css", ContentFile(b"body{color:red}"))
    paths = {"app.css": (storage, "app.css")}
    list(storage.post_process(paths))

    recorded = _record_uploads(monkeypatch, storage)
    list(storage.post_process(paths))  # identical content, but optimization is off
    assert recorded != []  # so Django's normal flow re-uploads


def test_skip_is_by_content_not_mere_existence(
    manifest_static_storage: S3ManifestStaticStorage,
    monkeypatch: pytest.MonkeyPatch,
    bucket: str,
    s3_client: S3Client,
):
    # The skip must compare content, not just presence: an object sitting at the target key with *different*
    # bytes has to be re-uploaded, otherwise a stale/corrupt object would be left in place forever.
    storage = manifest_static_storage
    storage.save("app.css", ContentFile(b"body{color:red}"))
    paths = {"app.css": (storage, "app.css")}
    list(storage.post_process(paths))

    key = storage.key(storage.stored_name("app.css"))
    s3_client.put_object(bucket, key, body=b"tampered", content_length=len(b"tampered"))  # same key, other bytes

    recorded = _record_uploads(monkeypatch, storage)
    list(storage.post_process(paths))  # source unchanged, but the stored object no longer matches
    assert key in recorded  # so it is re-uploaded rather than skipped on existence
    with s3_client.get_object(bucket, key) as out:
        assert b"".join(out["body"]) == b"body{color:red}"  # correct content restored


def test_gzipped_asset_skips_unchanged(
    static_storage_factory: Callable[..., S3ManifestStaticStorage],
    monkeypatch: pytest.MonkeyPatch,
):
    # CSS is gzipped at rest; the skip must still match on redeploy, which needs reproducible (mtime=0) gzip.
    storage = static_storage_factory(gzip=True)
    storage.save("app.css", ContentFile(b"body{color:red}" * 50))
    paths = {"app.css": (storage, "app.css")}
    list(storage.post_process(paths))

    recorded = _record_uploads(monkeypatch, storage)
    list(storage.post_process(paths))
    assert recorded == []
