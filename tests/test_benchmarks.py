from collections.abc import Callable
from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.files.base import ContentFile
from pytest_codspeed import BenchmarkFixture

from django_capo_s3 import core
from django_capo_s3.cloudfront import CloudFrontSigner
from django_capo_s3.storage import S3Storage
from django_capo_s3.transfer import ObjectMeta, S3Uploader

if TYPE_CHECKING:
    from capo_s3 import S3Client


@pytest.mark.timeout(0)
def test_bench_normalize_key(benchmark: BenchmarkFixture):
    benchmark(core.normalize_key, "media/uploads", "sub/dir/deep/report-2026.txt")


@pytest.mark.timeout(0)
def test_bench_build_public_url(benchmark: BenchmarkFixture):
    options: core.S3StorageOptions = {"bucket": "assets", "region": "eu-central-1"}
    benchmark(core.build_public_url, options, "media/sub/dir/file.txt")


@pytest.mark.timeout(0)
def test_bench_cloudfront_sign(benchmark: BenchmarkFixture):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    signer = CloudFrontSigner("KEYID", pem)
    benchmark(signer.signed_url, "https://cdn.example.com/media/file.txt", expires_at=2000000000)


@pytest.mark.timeout(0)
def test_bench_multipart_uploader(benchmark: BenchmarkFixture):
    client = Mock()
    client.create_multipart_upload.return_value = {"upload_id": "u"}
    client.upload_part.return_value = {"e_tag": "etag"}
    uploader = S3Uploader(cast("S3Client", client), threshold=1, chunk_size=5 * 1024 * 1024, concurrency=1)
    payload = b"x" * (11 * 1024 * 1024)  # ~3 parts, exercises chunk reading + part assembly

    def run() -> None:
        # Fresh ContentFile per run (a cheap BytesIO wrapper) so chunks() starts from the beginning.
        uploader.upload("bucket", "key", content=ContentFile(payload), size=len(payload), meta=ObjectMeta())

    benchmark(run)


@pytest.mark.timeout(0)
def test_bench_s3_roundtrip(benchmark: BenchmarkFixture, storage_factory: Callable[..., S3Storage]):
    storage = storage_factory(file_overwrite=True)
    content = ContentFile(b"benchmark-payload")

    def run() -> None:
        name = storage.save("bench.txt", content)
        storage.exists(name)
        storage.delete(name)

    benchmark(run)
