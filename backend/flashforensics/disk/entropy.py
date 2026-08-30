"""Shannon entropy mapping over a raw image.

Scanning an entire card byte by byte for signatures is wasteful and it tells you
nothing about where the interesting regions are. Entropy is a cheap proxy for
what kind of content occupies a block, because different content classes occupy
different, separable bands:

    ~0.0        a run of identical bytes, almost always unallocated free space
    1.0 - 4.0   structured binary, filesystem metadata, sparse records
    4.0 - 6.5   natural language text, source code, uncompressed markup
    6.5 - 7.5   mixed container formats, office documents, lightly packed data
    > 7.5       compressed or encrypted payloads: JPEG, PNG, ZIP, MP4, AES blobs

One pass gives a map that tells the carver where to spend its time, and the
transitions in that map are themselves evidence: a JPEG that abruptly drops to
zero entropy halfway through has been truncated, and a high entropy block sitting
inside a region the filesystem believes is free is an orphaned fragment.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np

from .image import DiskImage

DEFAULT_BLOCK_SIZE = 4096
MAX_MAP_POINTS = 4096


class ContentBand(str, Enum):
    EMPTY = "empty"
    STRUCTURED = "structured"
    TEXT = "text"
    MIXED = "mixed"
    COMPRESSED = "compressed"


BAND_THRESHOLDS: tuple[tuple[float, ContentBand], ...] = (
    (0.5, ContentBand.EMPTY),
    (4.0, ContentBand.STRUCTURED),
    (6.5, ContentBand.TEXT),
    (7.5, ContentBand.MIXED),
    (8.01, ContentBand.COMPRESSED),
)


def classify_band(entropy: float) -> ContentBand:
    for ceiling, band in BAND_THRESHOLDS:
        if entropy < ceiling:
            return band
    return ContentBand.COMPRESSED


def shannon_entropy(data: bytes) -> float:
    """Bits of entropy per byte, in the range 0.0 to 8.0.

    Implemented with a byte histogram rather than a Python loop because this runs
    over every block of the image and the naive version dominates total runtime.
    """
    if not data:
        return 0.0
    counts = np.bincount(np.frombuffer(data, dtype=np.uint8), minlength=256)
    counts = counts[counts > 0]
    probabilities = counts / counts.sum()
    return float(-np.sum(probabilities * np.log2(probabilities)))


def chi_square_uniformity(data: bytes) -> float:
    """Chi-square statistic against a uniform byte distribution.

    Entropy alone cannot separate compressed data from encrypted data because
    both sit near 8.0. Encrypted output is much closer to genuinely uniform, so
    the chi-square residual is what distinguishes them.
    """
    if len(data) < 256:
        return 0.0
    counts = np.bincount(np.frombuffer(data, dtype=np.uint8), minlength=256).astype(np.float64)
    expected = len(data) / 256.0
    return float(np.sum((counts - expected) ** 2 / expected))


def printable_ratio(data: bytes) -> float:
    """Fraction of bytes that are printable ASCII or common whitespace."""
    if not data:
        return 0.0
    array = np.frombuffer(data, dtype=np.uint8)
    printable = np.count_nonzero(((array >= 32) & (array <= 126)) | np.isin(array, [9, 10, 13]))
    return float(printable / len(array))


@dataclass
class EntropyBlock:
    """One measured block of the image."""

    offset: int
    length: int
    entropy: float
    band: ContentBand
    zero_ratio: float

    def to_dict(self) -> dict:
        return {
            "offset": self.offset,
            "length": self.length,
            "entropy": round(self.entropy, 3),
            "band": self.band.value,
            "zero_ratio": round(self.zero_ratio, 3),
        }


@dataclass
class EntropyRegion:
    """A run of adjacent blocks sharing a content band."""

    start: int
    end: int
    band: ContentBand
    mean_entropy: float
    block_count: int

    @property
    def length(self) -> int:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "length": self.length,
            "band": self.band.value,
            "mean_entropy": round(self.mean_entropy, 3),
            "block_count": self.block_count,
        }


@dataclass
class Anomaly:
    """A place where the entropy profile contradicts what the filesystem claims."""

    offset: int
    kind: str
    detail: str
    severity: str

    def to_dict(self) -> dict:
        return {
            "offset": self.offset,
            "kind": self.kind,
            "detail": self.detail,
            "severity": self.severity,
        }


class EntropyMap:
    """Block-level entropy profile of a whole image."""

    def __init__(self, block_size: int = DEFAULT_BLOCK_SIZE):
        self.block_size = block_size
        self.blocks: list[EntropyBlock] = []
        self.anomalies: list[Anomaly] = []

    def scan(self, image: DiskImage, progress=None) -> EntropyMap:
        """Measure every block in the image, reporting progress as a fraction."""
        self.blocks = []
        total = max(1, image.size)
        last_reported = -1

        for offset, data in image.iter_chunks(self.block_size):
            entropy = shannon_entropy(data)
            zeros = data.count(0) / len(data) if data else 1.0
            self.blocks.append(
                EntropyBlock(
                    offset=offset,
                    length=len(data),
                    entropy=entropy,
                    band=classify_band(entropy),
                    zero_ratio=zeros,
                )
            )
            if progress is not None:
                percent = int(offset * 100 / total)
                if percent != last_reported and percent % 5 == 0:
                    last_reported = percent
                    progress(percent)

        return self

    def entropy_at(self, offset: int) -> float:
        index = offset // self.block_size
        if 0 <= index < len(self.blocks):
            return self.blocks[index].entropy
        return 0.0

    def band_at(self, offset: int) -> ContentBand:
        index = offset // self.block_size
        if 0 <= index < len(self.blocks):
            return self.blocks[index].band
        return ContentBand.EMPTY

    def regions(self, min_blocks: int = 1) -> list[EntropyRegion]:
        """Collapse the block list into contiguous same-band regions."""
        if not self.blocks:
            return []

        regions: list[EntropyRegion] = []
        start_block = self.blocks[0]
        current_band = start_block.band
        entropies: list[float] = [start_block.entropy]
        start_offset = start_block.offset

        for block in self.blocks[1:]:
            if block.band == current_band:
                entropies.append(block.entropy)
                continue
            if len(entropies) >= min_blocks:
                regions.append(
                    EntropyRegion(
                        start=start_offset,
                        end=block.offset,
                        band=current_band,
                        mean_entropy=sum(entropies) / len(entropies),
                        block_count=len(entropies),
                    )
                )
            current_band = block.band
            entropies = [block.entropy]
            start_offset = block.offset

        last = self.blocks[-1]
        if len(entropies) >= min_blocks:
            regions.append(
                EntropyRegion(
                    start=start_offset,
                    end=last.offset + last.length,
                    band=current_band,
                    mean_entropy=sum(entropies) / len(entropies),
                    block_count=len(entropies),
                )
            )
        return regions

    def candidate_regions(self, min_entropy: float = 3.0) -> list[EntropyRegion]:
        """Regions worth carving: anything that is not obviously empty space."""
        return [
            region
            for region in self.regions()
            if region.band is not ContentBand.EMPTY and region.mean_entropy >= min_entropy
        ]

    def find_anomalies(self, allocated_ranges: list[tuple[int, int]] | None = None) -> list[Anomaly]:
        """Flag entropy behaviour that indicates damage rather than normal content.

        Three patterns matter. A sharp cliff from high to zero entropy is a file
        that stops mid-stream. A block of real data inside a range the filesystem
        calls free is an orphan the directory tree has lost. And near-perfect
        uniformity is either encryption or a wear-levelling scrub, both of which
        change what a recovery verdict should say.
        """
        self.anomalies = []
        if not self.blocks:
            return self.anomalies

        for index in range(1, len(self.blocks)):
            previous = self.blocks[index - 1]
            current = self.blocks[index]
            if previous.entropy > 7.0 and current.zero_ratio > 0.98:
                self.anomalies.append(
                    Anomaly(
                        offset=current.offset,
                        kind="truncation_cliff",
                        detail=(
                            f"Entropy falls from {previous.entropy:.2f} to a zero-filled block, "
                            f"consistent with a file whose tail was never written or was erased"
                        ),
                        severity="high",
                    )
                )
            elif previous.entropy > 7.0 and current.entropy < 1.0 and current.zero_ratio < 0.9:
                self.anomalies.append(
                    Anomaly(
                        offset=current.offset,
                        kind="entropy_cliff",
                        detail=(
                            f"Abrupt entropy drop from {previous.entropy:.2f} to {current.entropy:.2f} "
                            f"without zero fill, consistent with an overwrite boundary"
                        ),
                        severity="medium",
                    )
                )

        if allocated_ranges:
            for block in self.blocks:
                if block.band in (ContentBand.EMPTY, ContentBand.STRUCTURED):
                    continue
                inside = any(start <= block.offset < end for start, end in allocated_ranges)
                if not inside:
                    self.anomalies.append(
                        Anomaly(
                            offset=block.offset,
                            kind="orphaned_data",
                            detail=(
                                f"Block carries {block.entropy:.2f} bits of entropy but sits in space "
                                f"the filesystem reports as free, so its directory entry is gone"
                            ),
                            severity="high",
                        )
                    )

        return self.anomalies

    def downsample(self, points: int = 512) -> list[dict]:
        """Reduce the map to a fixed number of points for plotting.

        A 2 GB card at a 4 KB block size produces half a million measurements,
        which no browser should be asked to render. Each output point keeps the
        max as well as the mean so a single high entropy block inside an
        otherwise empty region stays visible instead of being averaged away.
        """
        if not self.blocks:
            return []

        points = max(1, min(points, MAX_MAP_POINTS, len(self.blocks)))
        bucket_size = math.ceil(len(self.blocks) / points)
        output: list[dict] = []

        for index in range(0, len(self.blocks), bucket_size):
            bucket = self.blocks[index : index + bucket_size]
            values = [block.entropy for block in bucket]
            mean_entropy = sum(values) / len(values)
            output.append(
                {
                    "offset": bucket[0].offset,
                    "length": sum(block.length for block in bucket),
                    "mean": round(mean_entropy, 3),
                    "max": round(max(values), 3),
                    "min": round(min(values), 3),
                    "band": classify_band(mean_entropy).value,
                }
            )
        return output

    def occupied_extent(self, pad_ratio: float = 0.06) -> tuple[int, int]:
        """Byte range spanning everything that is not empty space.

        On a card that is mostly free, this is the only part worth looking at:
        2 MB of photos on a 128 MB volume occupies 1.6% of the width, and a
        profile drawn across the whole device renders that as a single stripe.
        """
        live = [block for block in self.blocks if block.band is not ContentBand.EMPTY]
        if not live:
            return 0, self.blocks[-1].offset + self.blocks[-1].length if self.blocks else 0

        start = live[0].offset
        end = live[-1].offset + live[-1].length
        pad = max(int((end - start) * pad_ratio), self.block_size * 4)
        total = self.blocks[-1].offset + self.blocks[-1].length
        return max(0, start - pad), min(total, end + pad)

    def downsample_range(self, start: int, end: int, points: int = 512) -> list[dict]:
        """Profile one byte range at whatever resolution the blocks allow.

        The overview profile is bucketed across the whole volume, so zooming into
        it cannot show detail that was averaged away before it reached the
        browser. Re-bucketing over a narrower range recovers that detail from the
        block measurements, which are still at full resolution in memory.
        """
        selected = [
            block for block in self.blocks if block.offset + block.length > start and block.offset < end
        ]
        if not selected:
            return []

        points = max(1, min(points, MAX_MAP_POINTS, len(selected)))
        bucket_size = math.ceil(len(selected) / points)
        output: list[dict] = []

        for index in range(0, len(selected), bucket_size):
            bucket = selected[index : index + bucket_size]
            values = [block.entropy for block in bucket]
            mean_entropy = sum(values) / len(values)
            output.append(
                {
                    "offset": bucket[0].offset,
                    "length": sum(block.length for block in bucket),
                    "mean": round(mean_entropy, 3),
                    "max": round(max(values), 3),
                    "min": round(min(values), 3),
                    "band": classify_band(mean_entropy).value,
                }
            )
        return output

    def statistics(self) -> dict:
        if not self.blocks:
            return {"blocks": 0}
        values = np.array([block.entropy for block in self.blocks])
        band_counts: dict[str, int] = {}
        for block in self.blocks:
            band_counts[block.band.value] = band_counts.get(block.band.value, 0) + 1
        occupied = sum(count for band, count in band_counts.items() if band != ContentBand.EMPTY.value)
        return {
            "blocks": len(self.blocks),
            "block_size": self.block_size,
            "mean_entropy": round(float(values.mean()), 3),
            "median_entropy": round(float(np.median(values)), 3),
            "max_entropy": round(float(values.max()), 3),
            "bands": band_counts,
            "occupancy_ratio": round(occupied / len(self.blocks), 3),
            "anomalies": len(self.anomalies),
        }
