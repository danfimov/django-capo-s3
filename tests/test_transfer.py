from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest
from django.core.files.base import ContentFile

from django_capo_s3.transfer import ObjectMeta, S3Uploader

if TYPE_CHECKING:
    from capo_s3 import S3Client


class _BoomError(Exception):
    pass


def test_uploader_aborts_when_a_part_fails():
    client = Mock()
    client.create_multipart_upload.return_value = {"upload_id": "u1"}
    client.upload_part.side_effect = _BoomError

    uploader = S3Uploader(cast("S3Client", client), threshold=1, chunk_size=5 * 1024 * 1024)
    with pytest.raises(_BoomError):
        uploader.upload("bucket", "key", content=ContentFile(b"x" * 10), size=10, meta=ObjectMeta())

    client.abort_multipart_upload.assert_called_once()
