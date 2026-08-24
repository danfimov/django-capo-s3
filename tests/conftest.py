import socket
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import pytest
from capo_s3 import Credentials, S3Client
from django.core.files.storage import Storage
from typing_extensions import Unpack

from django_capo_s3.core import S3StorageOptions
from django_capo_s3.static import S3ManifestStaticStorage, S3StaticStorage
from django_capo_s3.storage import S3Storage

ENDPOINT = "http://localhost:9000"
REGION = "us-east-1"
CREDS = Credentials(access_key="minioadmin", secret_key="minioadmin")  # noqa: S106


def _minio_alive() -> bool:
    try:
        with socket.create_connection(("localhost", 9000), timeout=1):
            return True
    except OSError:
        return False


def _empty_bucket(client: S3Client, name: str) -> None:
    token = None
    with ThreadPoolExecutor(max_workers=16) as pool:
        while True:
            result = client.list_objects_v2(name, continuation_token=token)
            keys = [obj["key"] for obj in result.get("contents", []) if obj.get("key")]
            list(pool.map(partial(client.delete_object, name), keys))
            if not result.get("is_truncated"):
                break
            token = result.get("next_continuation_token")


@pytest.fixture(scope="session")
def s3_client() -> Iterator[S3Client]:
    if not _minio_alive():
        pytest.skip("MinIO is not reachable on localhost:9000 (run `make run_infra`).")
    with S3Client(endpoint=ENDPOINT, region=REGION, force_path_style=True, credentials=CREDS) as client:
        yield client


@pytest.fixture
def bucket(s3_client: S3Client) -> Iterator[str]:
    name = f"test-{uuid.uuid4().hex[:12]}"
    s3_client.create_bucket(name)
    yield name
    _empty_bucket(s3_client, name)
    s3_client.delete_bucket(name)


@pytest.fixture
def storage_options(bucket: str) -> S3StorageOptions:
    """The options pointing a storage at the local MinIO, for building storage subclasses directly."""
    return {
        "bucket": bucket,
        "endpoint": ENDPOINT,
        "region": REGION,
        "force_path_style": True,
        "credentials": CREDS,
    }


@pytest.fixture
def storage_factory(storage_options: S3StorageOptions) -> Callable[..., S3Storage]:
    def _make(**overrides: Unpack[S3StorageOptions]) -> S3Storage:
        options: S3StorageOptions = {**storage_options}
        options.update(overrides)
        return S3Storage(**options)

    return _make


@pytest.fixture
def storage(storage_factory: Callable[..., S3Storage]) -> S3Storage:
    return storage_factory()


@pytest.fixture
def static_storage_factory(bucket: str) -> Callable[..., S3ManifestStaticStorage]:
    def _make(
        manifest_storage: Storage | None = None,
        **overrides: Unpack[S3StorageOptions],
    ) -> S3ManifestStaticStorage:
        options: S3StorageOptions = {
            "bucket": bucket,
            "endpoint": ENDPOINT,
            "region": REGION,
            "force_path_style": True,
            "credentials": CREDS,
            "location": "static",
        }
        options.update(overrides)
        return S3ManifestStaticStorage(manifest_storage=manifest_storage, **options)

    return _make


@pytest.fixture
def manifest_static_storage(
    static_storage_factory: Callable[..., S3ManifestStaticStorage],
) -> S3ManifestStaticStorage:
    return static_storage_factory()


@pytest.fixture
def plain_static_storage(bucket: str) -> S3StaticStorage:
    return S3StaticStorage(
        bucket=bucket,
        endpoint=ENDPOINT,
        region=REGION,
        force_path_style=True,
        credentials=CREDS,
        location="static",
    )
