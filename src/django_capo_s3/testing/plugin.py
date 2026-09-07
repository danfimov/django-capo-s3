from collections.abc import Iterator

import pytest

from django_capo_s3.storage import S3Storage
from django_capo_s3.testing.service import FakeS3, mock_s3


@pytest.fixture
def s3() -> Iterator[FakeS3]:
    """Yield an in-memory S3 service with the transport patched, so every S3 request lands on it."""
    with mock_s3() as service:
        yield service


@pytest.fixture
def s3_storage(s3: FakeS3) -> S3Storage:
    """Build a media storage pointed at the in-memory service, with media/ as its location."""
    return s3.storage(location="media")
