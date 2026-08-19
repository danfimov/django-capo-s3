import io
from tempfile import SpooledTemporaryFile
from typing import TYPE_CHECKING

from django.core.files.base import File

if TYPE_CHECKING:
    from django_capo_s3.storage import S3Storage


class S3File(File):
    """A lazy, buffered handle to a single S3 object.

    On first read the object is streamed into a buffer, and in write mode the buffer is flushed back to S3
    when the file is closed. A binary handle spills to disk once it grows past the storage's max_memory_size;
    a text handle (any mode without "b") decodes to and from UTF-8 through a TextIOWrapper and stays in
    memory — text assets are small, and TextIOWrapper can't wrap a SpooledTemporaryFile before Python 3.11.
    """

    def __init__(self, name: str, mode: str, storage: "S3Storage") -> None:
        """Set up the handle; nothing is fetched from S3 until the buffer is first read."""
        self.name = name
        self.mode = mode
        self._name = name
        self._storage = storage
        self._raw: SpooledTemporaryFile[bytes] | io.BytesIO | None = None
        self._text: io.TextIOWrapper | None = None
        self._is_dirty = False

    @property
    def raw(self) -> "SpooledTemporaryFile[bytes] | io.BytesIO":
        """The binary buffer, populated from S3 on first access in read mode.

        A binary handle spills to disk past max_memory_size; a text handle stays in an in-memory BytesIO so a
        TextIOWrapper can wrap it (SpooledTemporaryFile only gained the io protocol in Python 3.11).
        """
        if self._raw is None:
            if "b" in self.mode:
                # Kept open for the lifetime of the file; released in close().
                raw: SpooledTemporaryFile[bytes] | io.BytesIO = SpooledTemporaryFile(  # noqa: SIM115
                    max_size=self._storage.options["max_memory_size"],
                    prefix="django-capo-s3.",
                )
            else:
                raw = io.BytesIO()
            if "r" in self.mode:
                raw.write(self._storage.read_bytes(self._name))
                raw.seek(0)
            self._raw = raw
        return self._raw

    @property
    def file(self) -> "SpooledTemporaryFile[bytes] | io.BytesIO | io.TextIOWrapper":  # type: ignore[override]
        """The stream callers read and write through: a text wrapper in text mode, else the binary buffer.

        Both file operations (read, write, seek, ...) and iteration are forwarded here by FileProxyMixin, so
        returning the wrapper in text mode keeps every access consistently decoded.
        """
        if "b" in self.mode:
            return self.raw
        if self._text is None:
            self._text = io.TextIOWrapper(
                self.raw,  # type: ignore[arg-type]  # a text handle always backs `raw` with a BytesIO
                encoding="utf-8",
                newline="",  # keeps byte-for-byte round trips: no \r\n <-> \n translation on read or write.
            )
        return self._text

    def read(self, size: int | None = None) -> bytes | str:
        """Read from the buffer, either a given number of bytes/characters or the whole object."""
        if "r" not in self.mode:
            msg = "File was not opened in read mode."
            raise AttributeError(msg)
        if size is None:
            return self.file.read()
        return self.file.read(size)

    def write(self, content: bytes | str) -> int:  # type: ignore[override]
        """Buffer written data in memory; it is flushed to S3 when the file is closed."""
        if "w" not in self.mode:
            msg = "File was not opened in write mode."
            raise AttributeError(msg)
        self._is_dirty = True
        return self.file.write(content)  # type: ignore[arg-type]

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
