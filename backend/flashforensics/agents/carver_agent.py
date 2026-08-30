"""Carver agent: extract fragments from the regions the scanner isolated.

Carving is confined to two kinds of region. Clusters the allocation table still
reserves but no directory entry claims, which are lost files with intact
allocation. And free space that the entropy map says is not actually empty, which
is deleted files whose allocation was released but whose bytes were never
overwritten, since flash controllers do not zero a cluster just because it was
freed.

Regions belonging to files the filesystem can still describe are excluded. Carving
those would produce duplicates of files the user can already see, which is one of
the main reasons conventional carver output is so tedious to sift.
"""

from __future__ import annotations

import hashlib
import logging

from ..disk import signatures as sig
from ..disk.carver import Carver, Fragment
from ..disk.entropy import chi_square_uniformity, printable_ratio, shannon_entropy
from ..disk.validators import ValidationResult, validate
from .state import RecoveryState, Stage, emit

logger = logging.getLogger(__name__)


def carve_fragments(state: RecoveryState) -> RecoveryState:
    """Run the carver over orphaned clusters and non-empty free space."""
    if state.get("stage") == Stage.FAILED.value:
        return state

    image = state["_image"]
    entropy_map = state["_entropy_map"]
    settings = state["settings"]
    cluster_size = state.get("cluster_size") or image.sector_size

    alignment = settings.carve_alignment or cluster_size
    emit(
        state,
        Stage.CARVING,
        f"Scanning for file signatures on {alignment} byte boundaries",
        52,
        agent="carver",
    )

    carver = Carver(
        image,
        max_fragment=settings.max_fragment_bytes,
        output_dir=settings.exports_dir / state["session_id"],
        alignment=alignment,
        min_confidence=settings.min_carve_confidence,
    )

    orphan_runs = [(start, end) for start, end in state.get("orphan_runs", [])]
    referenced = [(start, end) for start, end in state.get("referenced_ranges", [])]

    fragments = []
    if orphan_runs:
        emit(
            state,
            Stage.CARVING,
            f"Carving {len(orphan_runs)} orphaned cluster runs",
            55,
            agent="carver",
        )
        fragments.extend(
            carver.carve_runs(
                orphan_runs,
                progress=lambda pct: emit(
                    state, Stage.CARVING, f"Orphaned regions {pct}%", 55 + int(pct * 0.10), agent="carver"
                ),
            )
        )

    emit(state, Stage.CARVING, "Carving unallocated space that still holds data", 66, agent="carver")

    covered = referenced + orphan_runs
    free_space_fragments = carver.carve_with_entropy_map(
        entropy_map,
        skip_ranges=covered,
        progress=lambda pct: emit(
            state, Stage.CARVING, f"Free space {pct}%", 66 + int(pct * 0.09), agent="carver"
        ),
    )
    for fragment in free_space_fragments:
        fragment.in_orphaned_region = False
    fragments.extend(free_space_fragments)

    emit(state, Stage.CARVING, "Verifying files still reachable through the directory tree", 74, agent="carver")
    fragments.extend(_verify_referenced_files(state))

    seen: set[int] = set()
    unique = []
    for fragment in sorted(fragments, key=lambda item: item.offset):
        if fragment.offset in seen:
            continue
        seen.add(fragment.offset)
        unique.append(fragment)

    for fragment in unique:
        fragment.cluster_start = _offset_to_cluster(state, fragment.offset)
        fragment.cluster_end = _offset_to_cluster(state, fragment.offset + fragment.length)

    emit(
        state,
        Stage.CARVING,
        f"Carved {len(unique)} fragments, rejected {carver.rejected} chance signature matches",
        75,
        agent="carver",
        carved=len(unique),
        rejected=carver.rejected,
    )

    state.update(
        {
            "fragments": [fragment.to_dict() for fragment in unique],
            "carve_stats": {
                "carved": len(unique),
                "rejected": carver.rejected,
                "from_orphaned_clusters": sum(1 for f in unique if f.in_orphaned_region),
                "from_free_space": sum(1 for f in unique if not f.in_orphaned_region),
                "alignment": alignment,
                "bytes_carved": sum(f.length for f in unique),
            },
            "stage": Stage.CLASSIFYING.value,
            "_fragment_objects": unique,
            "_carver": carver,
        }
    )
    return state


def _verify_referenced_files(state: RecoveryState) -> list[Fragment]:
    """Validate the files the filesystem can still name, not just the lost ones.

    A file that still has a directory entry is not automatically a healthy file.
    A photo whose last clusters were zeroed when the card was pulled mid-write
    still appears in the listing at its full declared size, and the user only
    discovers the damage when they open it and see half an image.

    So every reachable file is read back through its cluster chain and put
    through the same structural validators as a carved fragment. It then flows
    through classification and adjudication on equal terms, which is what lets
    the final report cover the whole volume instead of only the parts that were
    already obviously broken.
    """
    parser = state.get("_parser")
    entries = state.get("_entries") or []
    if parser is None or not entries:
        return []

    settings = state["settings"]
    image = state["_image"]
    sector_size = image.sector_size
    verified: list[Fragment] = []

    for entry in entries:
        if entry.is_directory or not entry.clusters or entry.size == 0:
            continue

        read_limit = min(entry.size, settings.max_fragment_bytes)
        try:
            data = parser.read_file(entry, limit=read_limit)
        except Exception:
            logger.debug("could not read %s through its cluster chain", entry.path)
            continue
        if not data:
            continue

        extension = entry.name.rsplit(".", 1)[-1].lower() if "." in entry.name else ""
        candidates = _candidates_for(data, extension)
        best: ValidationResult | None = None
        for candidate in candidates:
            signature = sig.lookup(candidate)
            result = validate(data, candidate, signature.footer if signature else None)
            if best is None or result.confidence > best.confidence:
                best = result

        offset = parser.cluster_to_offset(entry.clusters[0])
        sample = data[: 64 * 1024]

        fragment = Fragment(
            fragment_id=hashlib.sha1(entry.path.encode()).hexdigest()[:12],
            offset=offset,
            length=len(data),
            candidates=candidates,
            ambiguity_group=None,
            entropy=shannon_entropy(sample),
            chi_square=chi_square_uniformity(sample),
            printable_ratio=printable_ratio(sample),
            sha256=hashlib.sha256(data).hexdigest(),
            header_hex=data[:16].hex(),
            validation=best,
            sector_start=offset // sector_size,
            sector_end=(offset + len(data)) // sector_size,
            cluster_start=entry.clusters[0],
            cluster_end=entry.clusters[-1],
            in_orphaned_region=False,
        )
        fragment.classification = {}
        fragment.verdict = {}
        fragment.saved_path = None
        fragment.source_path = entry.path
        fragment.source = "filesystem"
        fragment.declared_size = entry.size
        fragment.chain_damage = [report.to_dict() for report in entry.damage]
        verified.append(fragment)

    return verified


def _candidates_for(data: bytes, extension: str) -> list[str]:
    """Pick validators for a named file, trusting bytes over the file extension.

    A renamed file is common and a wrong extension is worse than none, so the
    magic bytes decide first and the extension is only a fallback when nothing
    in the signature table matches.
    """
    matches: list[str] = []
    for header, header_offset, members in sig.distinct_headers():
        window = data[header_offset : header_offset + len(header)]
        if window == header:
            matches.extend(member.extension for member in members)
    if matches:
        return list(dict.fromkeys(matches))
    if extension:
        return [extension]

    printable = sum(1 for byte in data[:4096] if 32 <= byte <= 126 or byte in (9, 10, 13))
    if data and printable / min(len(data), 4096) > 0.85:
        return ["txt"]
    return ["unknown"]


def _offset_to_cluster(state: RecoveryState, offset: int) -> int | None:
    boot = state.get("boot_sector") or {}
    cluster_size = state.get("cluster_size")
    data_start = boot.get("data_start_sector")
    sector_size = boot.get("bytes_per_sector")
    if not (cluster_size and data_start and sector_size):
        return None
    data_offset = data_start * sector_size
    if offset < data_offset:
        return None
    return ((offset - data_offset) // cluster_size) + 2
