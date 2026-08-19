from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING

from django.core.files.base import File

if TYPE_CHECKING:
    from django_capo_s3.storage import S3Storage


class S3File(File):
    """A lazy, buffered handle to a single S3 object.

    On first read the object is streamed into a temporary file that spills to disk once it grows past the
    storage's max_memory_size. In write mode the buffer is flushed back to S3 when the file is closed.
    """

    def __init__(self, name: str, mode: str, storage: "S3Storage") -> None:
        """Set up the handle; nothing is fetched from S3 until the buffer is first read."""
        self.name = name
        self.mode = mode
        self._name = name  # typed as str for the storage I/O calls
        self._storage = storage
        self._file: SpooledTemporaryFile[bytes] | None = None
        self._is_dirty = False

    @property
    def file(self) -> SpooledTemporaryFile[bytes]:
        """The backing buffer, populated from S3 on first access in read mode."""
        if self._file is None:
            # Kept open for the lifetime of the file; released in close().
            buffer: SpooledTemporaryFile[bytes] = SpooledTemporaryFile(  # noqa: SIM115
                max_size=self._storage.options["max_memory_size"],
                prefix="django-capo-s3.",
            )
            if "r" in self.mode:
                buffer.write(self._storage.read_bytes(self._name))
                buffer.seek(0)
            self._file = buffer
        return self._file

    @file.setter
    def file(self, value: SpooledTemporaryFile[bytes]) -> None:
        self._file = value

    def read(self, size: int | None = None) -> bytes:
        """Read from the buffer, either a given number of bytes or the whole object."""
        if "r" not in self.mode:
            msg = "File was not opened in read mode."
            raise AttributeError(msg)
        if size is None:
            return self.file.read()
        return self.file.read(size)

    def write(self, content: bytes) -> int:  # type: ignore[override]  # binary-only handle; narrows IO.write to bytes
        """Buffer written data in memory; it is flushed to S3 when the file is closed."""
        if "w" not in self.mode:
            msg = "File was not opened in write mode."
            raise AttributeError(msg)
        self._is_dirty = True
        return self.file.write(content)

    def close(self) -> None:
        """Flush pending writes back to S3 and release the buffer."""
        if self._file is None:
            return
        if self._is_dirty:
            self._file.seek(0)
            self._storage.write_bytes(self._name, self._file.read())
            self._is_dirty = False
        self._file.close()
        self._file = None
