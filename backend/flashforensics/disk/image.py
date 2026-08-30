"""Raw block device / disk image access.

Everything in FlashForensics reads through this class. It gives the rest of the
system a sector-addressed view of a file on disk without loading the whole image
into memory, and it records every read so damaged regions can be reported rather
than silently returning zeroes.
"""

from __future__ import annotations

import hashlib
import mmap
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path


class DiskReadError(Exception):
    """Raised when a requested region falls outside the image."""


@dataclass
class ReadStats:
    """Bookkeeping for how much of the image the pipeline actually touched."""

    reads: int = 0
    bytes_read: int = 0
    short_reads: int = 0
    out_of_range: int = 0
    damaged_sectors: set[int] = field(default_factory=set)


class DiskImage:
    """Memory-mapped, sector-addressed view over a raw image file.

    The sector size defaults to 512 and is corrected by the filesystem parser
    once a boot sector has been read, because the BPB is the only authority on
    the real sector size.
    """

    DEFAULT_SECTOR_SIZE = 512

    def __init__(self, path: str | Path, sector_size: int = DEFAULT_SECTOR_SIZE):
        self.path = Path(path)
        if not self.path.is_file():
            raise DiskReadError(f"image not found: {self.path}")
        self.sector_size = sector_size
        self.size = self.path.stat().st_size
        self.stats = ReadStats()
        self._fh = open(self.path, "rb")
        try:
            self._mm = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
        except ValueError:
            self._mm = None

    def __enter__(self) -> DiskImage:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def close(self) -> None:
        if self._mm is not None:
            self._mm.close()
            self._mm = None
        if not self._fh.closed:
            self._fh.close()

    @property
    def sector_count(self) -> int:
        return self.size // self.sector_size

    def set_sector_size(self, sector_size: int) -> None:
        if sector_size not in (512, 1024, 2048, 4096):
            raise DiskReadError(f"implausible sector size: {sector_size}")
        self.sector_size = sector_size

    def read(self, offset: int, length: int) -> bytes:
        """Read `length` bytes at byte `offset`, clamped to the end of the image.

        A read that runs off the end returns what is available instead of raising,
        because a truncated image is a normal thing to analyse and the caller
        needs whatever bytes still exist.
        """
        if offset < 0 or length < 0:
            raise DiskReadError(f"negative read: offset={offset} length={length}")
        if offset >= self.size:
            self.stats.out_of_range += 1
            return b""

        end = min(offset + length, self.size)
        if end - offset < length:
            self.stats.short_reads += 1

        if self._mm is not None:
            data = self._mm[offset:end]
        else:
            self._fh.seek(offset)
            data = self._fh.read(end - offset)

        self.stats.reads += 1
        self.stats.bytes_read += len(data)
        return data

    def read_sector(self, lba: int, count: int = 1) -> bytes:
        return self.read(lba * self.sector_size, count * self.sector_size)

    def iter_chunks(self, chunk_size: int, start: int = 0, end: int | None = None) -> Iterator[tuple[int, bytes]]:
        """Yield (offset, data) pairs walking the image in fixed-size chunks."""
        end = self.size if end is None else min(end, self.size)
        offset = start
        while offset < end:
            data = self.read(offset, min(chunk_size, end - offset))
            if not data:
                break
            yield offset, data
            offset += len(data)

    def mark_damaged(self, lba: int) -> None:
        self.stats.damaged_sectors.add(lba)

    def sha256(self) -> str:
        digest = hashlib.sha256()
        for _offset, data in self.iter_chunks(1 << 20):
            digest.update(data)
        return digest.hexdigest()

    def describe(self) -> dict:
        return {
            "path": str(self.path),
            "size_bytes": self.size,
            "sector_size": self.sector_size,
            "sector_count": self.sector_count,
            "reads": self.stats.reads,
            "bytes_read": self.stats.bytes_read,
        }


def is_probably_zero(data: bytes, sample: int = 4096) -> bool:
    """Cheap emptiness test used to skip unallocated runs during carving."""
    if not data:
        return True
    window = data[:sample]
    return window.count(0) == len(window)


def human_bytes(n: int) -> str:
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} PB"


def default_workspace() -> Path:
    root = Path(os.environ.get("FF_WORKSPACE", Path.home() / ".flashforensics"))
    root.mkdir(parents=True, exist_ok=True)
    return root
