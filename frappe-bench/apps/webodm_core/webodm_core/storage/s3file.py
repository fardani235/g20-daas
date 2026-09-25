"""A seekable, read-only file object over one S3 object, backed by range reads.

``zipfile.ZipFile`` needs ``seek``/``tell``/``read`` to locate the central
directory at the end of an archive and then walk members. Wrapping this in an
``io.BufferedReader`` (see ``ObjectStorage.open_seekable``) turns the small
reads ``zipfile`` issues into a few large ``Range`` requests, so NodeODM's
``all.zip`` can be unpacked straight from S3 into per-asset objects without
ever landing on the host disk.
"""

from __future__ import annotations

import io


class S3RangeFile(io.RawIOBase):
    def __init__(self, store, key: str, size: int):
        super().__init__()
        self._store = store
        self._key = key
        self._size = int(size)
        self._pos = 0

    # -- io.RawIOBase ------------------------------------------------------

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            new = offset
        elif whence == io.SEEK_CUR:
            new = self._pos + offset
        elif whence == io.SEEK_END:
            new = self._size + offset
        else:
            raise ValueError(f"invalid whence: {whence}")
        if new < 0:
            raise ValueError("negative seek position")
        self._pos = new
        return self._pos

    def readinto(self, buffer) -> int:
        if self._pos >= self._size:
            return 0
        want = min(len(buffer), self._size - self._pos)
        if want <= 0:
            return 0
        data = self._store.read_range(self._key, self._pos, self._pos + want - 1)
        n = len(data)
        buffer[:n] = data
        self._pos += n
        return n

    def readall(self) -> bytes:
        chunks = []
        while self._pos < self._size:
            buf = bytearray(min(8 * 1024 * 1024, self._size - self._pos))
            n = self.readinto(buf)
            if not n:
                break
            chunks.append(bytes(buf[:n]))
        return b"".join(chunks)

    @property
    def size(self) -> int:
        return self._size
