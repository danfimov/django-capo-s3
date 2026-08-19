import threading
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from capo_s3 import S3Client
from capo_s3.types.completed_part import CompletedPart
from capo_s3.types.object_canned_acl import ObjectCannedACL
from django.core.files.base import File


@dataclass(frozen=True)
class ObjectMeta:
    """Metadata attached to an uploaded object, shared by the single-PUT and multipart paths."""

    content_type: str | None = None
    content_encoding: str | None = None
    acl: ObjectCannedACL | None = None
    extra: Mapping[str, str] = field(default_factory=dict)


class S3Uploader:
    """Store a file in S3, either in one PUT or as a multipart transfer once it crosses a size threshold.

    Keeps the create/upload_part/complete/abort operations behind a single upload() call, and aborts a
    partial transfer if anything fails partway through.
    """

    def __init__(self, client: S3Client, *, threshold: int, chunk_size: int, concurrency: int = 1) -> None:
        """Bind the client and size limits; the part size is raised to S3's minimum if it's too small."""
        self._client = client
        self._threshold = threshold
        self._chunk_size = max(chunk_size, 5 * 1024 * 1024)
        self._concurrency = max(concurrency, 1)

    def upload(self, bucket: str, key: str, *, content: File, size: int, meta: ObjectMeta) -> None:
        """Store content, splitting it into a multipart transfer once it reaches the threshold."""
        if size >= self._threshold:
            self._multipart(bucket, key, content, meta)
        else:
            self._single(bucket, key, content, size, meta)

    def _single(self, bucket: str, key: str, content: File, size: int, meta: ObjectMeta) -> None:
        self._client.put_object(
            bucket,
            key,
            body=content.chunks(),
            content_length=size,
            content_type=meta.content_type,
            content_encoding=meta.content_encoding,
            acl=meta.acl,
            **meta.extra,  # type: ignore[arg-type]
        )

    def _multipart(self, bucket: str, key: str, content: File, meta: ObjectMeta) -> None:
        created = self._client.create_multipart_upload(
            bucket,
            key,
            content_type=meta.content_type,
            content_encoding=meta.content_encoding,
            acl=meta.acl,
            **meta.extra,  # type: ignore[arg-type]
        )
        upload_id = created["upload_id"]
        try:
            parts = self._upload_parts(bucket, key, content, upload_id)
            self._client.complete_multipart_upload(bucket, key, upload_id, multipart_upload={"parts": parts})
        except BaseException:
            # Leave no dangling upload behind if a part fails or completion is interrupted.
            self._client.abort_multipart_upload(bucket, key, upload_id)
            raise

    def _upload_parts(self, bucket: str, key: str, content: File, upload_id: str) -> list[CompletedPart]:
        # Read parts sequentially but upload them concurrently. A semaphore caps how many chunks are
        # in flight, so memory stays around concurrency * chunk_size rather than the whole file.
        in_flight = threading.BoundedSemaphore(self._concurrency)

        def upload_one(number: int, chunk: bytes) -> CompletedPart:
            """Upload a single part and release the in-flight slot when it finishes."""
            try:
                result = self._client.upload_part(bucket, key, number, upload_id, body=chunk, content_length=len(chunk))
                return {"part_number": number, "e_tag": result["e_tag"]}
            finally:
                in_flight.release()

        submitted: list[Future[CompletedPart]] = []
        with ThreadPoolExecutor(max_workers=self._concurrency) as pool:
            for number, chunk in enumerate(content.chunks(chunk_size=self._chunk_size), start=1):
                in_flight.acquire()
                submitted.append(pool.submit(upload_one, number, chunk))
            return [future.result() for future in submitted]
