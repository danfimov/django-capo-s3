import json
from collections.abc import Callable
from pathlib import Path

from capo_s3 import S3Client
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage

from django_capo_s3.static import S3ManifestStaticStorage, S3StaticStorage


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
