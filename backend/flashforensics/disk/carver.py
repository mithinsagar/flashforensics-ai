"""Signature-based file carving over orphaned regions of an image.

Carving recovers files whose directory entries are gone by ignoring the
filesystem entirely and looking for format signatures in the raw bytes. The
classic implementation of this idea scans every sector of the card and emits
everything it finds, which is why tools like PhotoRec hand back thousands of
unnamed fragments.

Two things are different here. The entropy map decides where to scan, so empty
space is skipped instead of ground through. And the end of each fragment is
established by asking the format itself where it ends, through the structural
validators, rather than by dumping a fixed number of bytes and hoping.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import signatures as sig
from .entropy import EntropyMap, chi_square_uniformity, printable_ratio, shannon_entropy
from .image import DiskImage
from .validators import ValidationResult, validate

DEFAULT_MAX_FRAGMENT = 64 * 1024 * 1024
PREVIEW_BYTES = 256
SCAN_OVERLAP = 4096


@dataclass
class Fragment:
    """One carved region plus everything measured about it."""

    fragment_id: str
    offset: int
    length: int
    candidates: list[str]
    ambiguity_group: str | None
    entropy: float
    chi_square: float
    printable_ratio: float
    sha256: str
    header_hex: str
    validation: ValidationResult | None = None
    sector_start: int = 0
    sector_end: int = 0
    cluster_start: int | None = None
    cluster_end: int | None = None
    in_orphaned_region: bool = True
    classification: dict = field(default_factory=dict)
    verdict: dict = field(default_factory=dict)
    saved_path: str | None = None
    source: str = "carved"
    source_path: str | None = None
    declared_size: int | None = None
    chain_damage: list = field(default_factory=list)

    @property
    def format_guess(self) -> str:
        if self.classification.get("format"):
            return self.classification["format"]
        if self.validation and self.validation.format_detected:
            return self.validation.format_detected
        return self.candidates[0] if self.candidates else "unknown"

    def to_dict(self) -> dict:
        return {
            "fragment_id": self.fragment_id,
            "offset": self.offset,
            "length": self.length,
            "candidates": self.candidates,
            "ambiguity_group": self.ambiguity_group,
            "entropy": round(self.entropy, 3),
            "chi_square": round(self.chi_square, 1),
            "printable_ratio": round(self.printable_ratio, 3),
            "sha256": self.sha256,
            "header_hex": self.header_hex,
            "sector_start": self.sector_start,
            "sector_end": self.sector_end,
            "cluster_start": self.cluster_start,
            "cluster_end": self.cluster_end,
            "in_orphaned_region": self.in_orphaned_region,
            "format_guess": self.format_guess,
            "mime": sig.mime_for(self.format_guess),
            "category": sig.category_of(self.format_guess),
            "validation": self.validation.to_dict() if self.validation else None,
            "classification": self.classification,
            "verdict": self.verdict,
            "saved_path": self.saved_path,
            "source": self.source,
            "source_path": self.source_path,
            "declared_size": self.declared_size,
            "chain_damage": self.chain_damage,
        }


class Carver:
    """Locates and extracts file fragments from raw regions of a disk image."""

    def __init__(
        self,
        image: DiskImage,
        max_fragment: int = DEFAULT_MAX_FRAGMENT,
        output_dir: Path | None = None,
        alignment: int = 512,
        min_confidence: float = 0.35,
    ):
        self.image = image
        self.max_fragment = max_fragment
        self.output_dir = output_dir
        self.alignment = max(1, alignment)
        self.min_confidence = min_confidence
        self.probes = sig.distinct_headers()
        self.rejected = 0

    def scan_region(self, start: int, end: int) -> list[tuple[int, list[sig.Signature]]]:
        """Find every signature hit in a byte range.

        Regions are read in overlapping windows so a signature that straddles a
        window boundary is still seen. Without the overlap a JPEG starting three
        bytes before the end of a window would be missed entirely.
        """
        hits: list[tuple[int, list[sig.Signature]]] = []
        window_size = 1 << 20
        position = start

        while position < end:
            chunk_end = min(position + window_size, end)
            read_end = min(chunk_end + SCAN_OVERLAP, self.image.size, end + SCAN_OVERLAP)
            data = self.image.read(position, read_end - position)
            if not data:
                break

            for header, header_offset, members in self.probes:
                search_from = 0
                while True:
                    found = data.find(header, search_from)
                    if found == -1:
                        break
                    search_from = found + 1
                    absolute = position + found - header_offset
                    if absolute < start or absolute >= end:
                        continue
                    if absolute >= position + (chunk_end - position):
                        continue
                    hits.append((absolute, members))

            position = chunk_end

        hits.sort(key=lambda item: item[0])
        return hits

    def determine_extent(self, offset: int, candidates: list[sig.Signature], region_end: int) -> tuple[int, ValidationResult | None]:
        """Decide where a fragment ends by asking the format, not by guessing.

        The read is capped first, then the best-matching validator is run, and if
        it reports a true size the fragment is trimmed to exactly that. This is
        what stops a 40 KB photo from being emitted as a 16 MB blob with another
        three files buried inside it.

        Note that `region_end` deliberately does not cap the read. A single file
        does not have uniform entropy: a JPEG's EXIF header sits in a different
        band from its compressed scan data, so the entropy map often splits one
        photo across several regions. The map is the right tool for deciding
        where to start looking and the wrong tool for deciding where a file ends,
        which is the format's own business.
        """
        ceiling = min(
            max(candidate.max_size for candidate in candidates),
            self.max_fragment,
            self.image.size - offset,
        )
        if ceiling <= 0:
            return 0, None

        data = self.image.read(offset, ceiling)
        if not data:
            return 0, None

        best: ValidationResult | None = None
        for candidate in candidates:
            result = validate(data, candidate.extension, candidate.footer)
            if best is None or result.confidence > best.confidence:
                best = result

        if best and best.true_size and 0 < best.true_size <= len(data):
            return best.true_size, best

        for candidate in candidates:
            if not candidate.footer:
                continue
            position = data.rfind(candidate.footer)
            if position > 0:
                return position + len(candidate.footer), best

        return self._bound_by_free_space(data, region_end - offset), best

    def _bound_by_free_space(self, data: bytes, region_hint: int) -> int:
        """Bound a footerless format at the first run of unallocated space.

        Formats like MP3, BMP and raw camera files carry no end marker, so
        nothing inside the bytes says where they stop. On FAT the allocator writes
        a file into consecutive clusters and leaves the rest of the volume zeroed,
        so the first sustained run of zeroes after the header is the file's edge.
        Without this, a footerless hit swallows the entire read ceiling and lands
        in the results as a 64 MB fragment.
        """
        window = self.alignment if self.alignment > 1 else 4096
        threshold = max(2 * window, 8192)
        zero_run = 0
        position = window

        while position < len(data):
            chunk = data[position : position + window]
            if not chunk:
                break
            if chunk.count(0) == len(chunk):
                zero_run += len(chunk)
                if zero_run >= threshold:
                    return position + len(chunk) - zero_run
            else:
                zero_run = 0
            position += window

        if 0 < region_hint < len(data):
            return region_hint
        return len(data)

    def carve_region(self, start: int, end: int, orphaned: bool = True) -> list[Fragment]:
        """Carve one byte range into fragments, skipping overlaps.

        Two filters do most of the work of keeping the output list short enough
        to be useful. Alignment is the strong one: FAT allocates in clusters, so
        a real file always begins on a cluster boundary, and a signature found at
        an arbitrary offset is almost always a coincidence inside compressed data
        rather than a file. Confidence is the weak one, dropping hits whose
        structure never validated at all. Between them, the short two-byte
        signatures that would otherwise flood the results stay out.
        """
        fragments: list[Fragment] = []
        consumed_until = start

        for offset, candidates in self.scan_region(start, end):
            if offset < consumed_until:
                continue
            if self.alignment > 1 and offset % self.alignment != 0:
                self.rejected += 1
                continue

            length, validation = self.determine_extent(offset, candidates, end)
            if length < 64:
                continue
            if validation is not None and validation.confidence < self.min_confidence:
                self.rejected += 1
                continue

            data = self.image.read(offset, min(length, self.max_fragment))
            if not data:
                continue

            fragments.append(self._build_fragment(offset, data, candidates, validation, orphaned))
            consumed_until = offset + length

        return fragments

    def _build_fragment(
        self,
        offset: int,
        data: bytes,
        candidates: list[sig.Signature],
        validation: ValidationResult | None,
        orphaned: bool,
    ) -> Fragment:
        sample = data[: 64 * 1024]
        groups = {candidate.ambiguity_group for candidate in candidates if candidate.ambiguity_group}
        sector_size = self.image.sector_size

        return Fragment(
            fragment_id=uuid.uuid4().hex[:12],
            offset=offset,
            length=len(data),
            candidates=[candidate.extension for candidate in candidates],
            ambiguity_group=next(iter(groups)) if groups else None,
            entropy=shannon_entropy(sample),
            chi_square=chi_square_uniformity(sample),
            printable_ratio=printable_ratio(sample),
            sha256=hashlib.sha256(data).hexdigest(),
            header_hex=data[:16].hex(),
            validation=validation,
            sector_start=offset // sector_size,
            sector_end=(offset + len(data)) // sector_size,
            in_orphaned_region=orphaned,
        )

    def carve_runs(self, runs: list[tuple[int, int]], progress=None) -> list[Fragment]:
        """Carve a list of byte ranges, reporting progress as a percentage."""
        fragments: list[Fragment] = []
        total = max(1, sum(end - start for start, end in runs))
        done = 0
        last_percent = -1

        for start, end in runs:
            fragments.extend(self.carve_region(start, end))
            done += end - start
            if progress is not None:
                percent = int(done * 100 / total)
                if percent != last_percent:
                    last_percent = percent
                    progress(percent)

        return fragments

    def carve_with_entropy_map(
        self,
        entropy_map: EntropyMap,
        skip_ranges: list[tuple[int, int]] | None = None,
        progress=None,
    ) -> list[Fragment]:
        """Carve only the regions the entropy map says are worth reading.

        Ranges belonging to files the filesystem can still describe are excluded,
        because re-carving an intact file adds a duplicate the user then has to
        dismiss. The output is meant to be the set of things the filesystem
        cannot account for.
        """
        candidate_regions = entropy_map.candidate_regions()
        skip_ranges = skip_ranges or []
        runs: list[tuple[int, int]] = []

        for region in candidate_regions:
            segments = [(region.start, region.end)]
            for skip_start, skip_end in skip_ranges:
                next_segments: list[tuple[int, int]] = []
                for seg_start, seg_end in segments:
                    if skip_end <= seg_start or skip_start >= seg_end:
                        next_segments.append((seg_start, seg_end))
                        continue
                    if skip_start > seg_start:
                        next_segments.append((seg_start, skip_start))
                    if skip_end < seg_end:
                        next_segments.append((skip_end, seg_end))
                segments = next_segments
            runs.extend(segment for segment in segments if segment[1] - segment[0] >= 512)

        return self.carve_runs(runs, progress)

    def save_fragment(self, fragment: Fragment, directory: Path | None = None) -> Path:
        """Write a fragment to disk under a name describing what it turned out to be."""
        target_dir = Path(directory or self.output_dir or ".")
        target_dir.mkdir(parents=True, exist_ok=True)
        extension = fragment.format_guess
        filename = f"{fragment.offset:012d}_{fragment.fragment_id}.{extension}"
        path = target_dir / filename
        data = self.image.read(fragment.offset, fragment.length)
        path.write_bytes(data)
        fragment.saved_path = str(path)
        return path

    def read_fragment(self, fragment: Fragment, limit: int | None = None) -> bytes:
        return self.image.read(fragment.offset, min(fragment.length, limit or fragment.length))
