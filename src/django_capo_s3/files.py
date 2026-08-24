import io
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING

from django.core.files.base import File
from typing_extensions import override

if TYPE_CHECKING:
    from django_capo_s3.storage import S3Storage


class S3File(File):
    """A lazy, buffered handle to a single S3 object.

    On first read the object is streamed into a buffer that spills to disk once it grows past the storage's
    max_memory_size, and in write mode the buffer is flushed back to S3 when the file is closed. A text handle
    (any mode without "b") decodes to and from UTF-8 through a TextIOWrapper over that same buffer.
    """

    def __init__(self, name: str, mode: str, storage: "S3Storage") -> None:
        """Set up the handle; nothing is fetched from S3 until the buffer is first read."""
        self.name = name
        self.mode = mode
        self._name = name
        self._storage = storage
        self._raw: SpooledTemporaryFile[bytes] | None = None
        self._text: io.TextIOWrapper | None = None
        self._is_dirty = False

    @property
    def raw(self) -> SpooledTemporaryFile[bytes]:
        """The binary buffer, populated from S3 on first access in read mode; spills to disk past max_memory_size."""
        if self._raw is None:
            buffer: SpooledTemporaryFile[bytes] = SpooledTemporaryFile(  # noqa: SIM115
                max_size=self._storage.options["max_memory_size"],
                prefix="django-capo-s3.",
            )
            if "r" in self.mode:
                self._storage.download_into(self._name, buffer)
                buffer.seek(0)
            self._raw = buffer
        return self._raw

    @property
    @override
    def file(self) -> SpooledTemporaryFile[bytes] | io.TextIOWrapper:  # type: ignore[override, mutable-override]
        """The stream callers read and write through: a text wrapper in text mode, else the binary buffer.

        Both file operations (read, write, seek, ...) and iteration are forwarded here by FileProxyMixin, so
        returning the wrapper in text mode keeps every access consistently decoded.
        """
        if "b" in self.mode:
            return self.raw
        if self._text is None:
            self._text = io.TextIOWrapper(
                buffer=self.raw,
                encoding="utf-8",
                newline="",  # keeps byte-for-byte round trips: no \r\n <-> \n translation on read or write.
            )
        return self._text

    @override
    def read(self, size: int | None = None) -> bytes | str:
        """Read from the buffer, either a given number of bytes/characters or the whole object."""
        if "r" not in self.mode:
            msg = "File was not opened in read mode."
            raise AttributeError(msg)
        if size is None:
            return self.file.read()
        return self.file.read(size)

    @override
    def write(self, content: bytes | str) -> int:  # type: ignore[override]
        """Buffer written data in memory; it is flushed to S3 when the file is closed."""
        if "w" not in self.mode:
            msg = "File was not opened in write mode."
            raise AttributeError(msg)
        self._is_dirty = True
        return self.file.write(content)  # type: ignore[arg-type]

    @override
    def close(self) -> None:
        """Flush pending writes back to S3 and release the buffer."""
        if self._raw is None:
            return
        if self._is_dirty:
            if self._text is not None:
                self._text.flush()
            self._raw.seek(0)
            self._storage.write_bytes(self._name, self._raw.read())
            self._is_dirty = False
        if self._text is not None:
            self._text.detach()  # Detach so closing the wrapper doesn't also close the buffer we close ourselves below.
            self._text = None
        self._raw.close()
        self._raw = None
