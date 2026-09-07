"""Test helpers for code that stores files in S3.

The centrepiece is FakeS3: an in-memory S3 service that speaks the real wire protocol, so a test drives the
actual storage backend — signing, XML, gzip, multipart — without a bucket or a network. See the
[testing guide](https://danfimov.github.io/django-capo-s3/tutorial/testing/).
"""

from django_capo_s3.testing.calls import (
    PresignedUrl,
    S3Call,
    S3Calls,
    SigV4,
    parse_presigned_url,
)
from django_capo_s3.testing.responses import not_found, s3_error, xml_response
from django_capo_s3.testing.service import FakeS3, StoredObject, mock_s3

__all__ = [
    "FakeS3",
    "PresignedUrl",
    "S3Call",
    "S3Calls",
    "SigV4",
    "StoredObject",
    "mock_s3",
    "not_found",
    "parse_presigned_url",
    "s3_error",
    "xml_response",
]
