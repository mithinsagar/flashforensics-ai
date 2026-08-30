"""Scanner agent: parse the filesystem and map the volume.

First node in the graph. It answers three questions that everything downstream
depends on: what filesystem is this, what can still be read through it, and which
regions hold data the filesystem can no longer account for.

That last question is the handoff to the carver. Clusters marked allocated with
nothing referencing them, plus high-entropy blocks sitting in supposedly free
space, are precisely the regions where a lost file is, and confining the carve to
them is what keeps the output list short enough for a person to read.
"""

from __future__ import annotations

import logging

from ..disk.entropy import EntropyMap
from ..disk.exfat import ExfatParser
from ..disk.fat32 import Fat32Parser
from ..disk.image import DiskImage
from .state import RecoveryState, Stage, emit

logger = logging.getLogger(__name__)


def scan_filesystem(state: RecoveryState) -> RecoveryState:
    """Detect and parse the volume, then profile it with an entropy map."""
    emit(state, Stage.SCANNING, "Opening the image", 0, agent="scanner")

    image = DiskImage(state["image_path"])
    settings = state["settings"]

    try:
        if Fat32Parser.detect(image):
            parser = Fat32Parser(image)
            filesystem = "FAT32"
        elif ExfatParser.detect(image):
            parser = ExfatParser(image)
            filesystem = "exFAT"
        else:
            return _unformatted(state, image)

        emit(state, Stage.SCANNING, f"{filesystem} volume detected, reading the boot sector", 5, agent="scanner")
        boot = parser.parse_boot_sector()
        cluster_size = boot.cluster_size

        emit(state, Stage.SCANNING, "Walking the directory tree", 15, agent="scanner")
        entries = parser.walk()
        files = [entry for entry in entries if not entry.is_directory]

        emit(
            state,
            Stage.SCANNING,
            f"Found {len(files)} files the filesystem can still describe",
            25,
            agent="scanner",
            files_found=len(files),
        )

        orphans = parser.orphaned_clusters(entries)
        orphan_runs = parser.cluster_runs(orphans)
        referenced_runs = parser.cluster_runs(parser.referenced_clusters(entries))

        summary = parser.summary(entries)

        if orphans:
            emit(
                state,
                Stage.SCANNING,
                f"{len(orphans)} clusters are allocated but unreferenced, so their directory entries are gone",
                30,
                agent="scanner",
                orphaned_clusters=len(orphans),
            )

        emit(state, Stage.MAPPING, "Measuring entropy across the volume", 32, agent="scanner")

        entropy_map = EntropyMap(settings.entropy_block_size)
        entropy_map.scan(
            image,
            progress=lambda pct: emit(
                state,
                Stage.MAPPING,
                f"Entropy scan {pct}%",
                32 + int(pct * 0.18),
                agent="scanner",
            ),
        )

        referenced_ranges = [
            [parser.cluster_to_offset(start), parser.cluster_to_offset(end) + cluster_size]
            for start, end in referenced_runs
        ]
        anomalies = entropy_map.find_anomalies(referenced_ranges)

        emit(
            state,
            Stage.MAPPING,
            f"Entropy map complete, {len(anomalies)} anomalies flagged",
            50,
            agent="scanner",
            anomalies=len(anomalies),
        )

        state.update(
            {
                "filesystem": filesystem,
                "boot_sector": boot.to_dict(),
                "cluster_size": cluster_size,
                "files": [entry.to_dict(cluster_size) for entry in entries],
                "damage": [report.to_dict() for report in parser.damage],
                "filesystem_summary": summary,
                "orphan_runs": [
                    [parser.cluster_to_offset(start), parser.cluster_to_offset(end) + cluster_size]
                    for start, end in orphan_runs
                ],
                "referenced_ranges": referenced_ranges,
                "entropy_points": entropy_map.downsample(512),
                "entropy_detail": _detail_profile(entropy_map),
                "entropy_stats": entropy_map.statistics(),
                "anomalies": [anomaly.to_dict() for anomaly in anomalies],
                "stage": Stage.MAPPING.value,
                "_entropy_map": entropy_map,
                "_image": image,
                "_parser": parser,
                "_entries": entries,
            }
        )
        return state

    except Exception as error:
        logger.exception("scanner failed")
        image.close()
        state.update({"stage": Stage.FAILED.value, "error": f"filesystem scan failed: {error}"})
        return state


def _detail_profile(entropy_map: EntropyMap) -> dict:
    """Second, finer profile covering only the part of the volume that holds data."""
    start, end = entropy_map.occupied_extent()
    return {
        "start": start,
        "end": end,
        "points": entropy_map.downsample_range(start, end, 512),
    }


def _unformatted(state: RecoveryState, image: DiskImage) -> RecoveryState:
    """Handle an image with no recognisable filesystem.

    A wiped or reformatted card still holds the previous contents in full. There
    is no directory tree to walk, so the entire volume becomes the carving
    target and the entropy map alone decides where to look.
    """
    emit(
        state,
        Stage.SCANNING,
        "No recognisable filesystem, treating the whole volume as unallocated space",
        10,
        agent="scanner",
    )
    settings = state["settings"]
    entropy_map = EntropyMap(settings.entropy_block_size)
    entropy_map.scan(
        image,
        progress=lambda pct: emit(state, Stage.MAPPING, f"Entropy scan {pct}%", 10 + int(pct * 0.4), agent="scanner"),
    )
    anomalies = entropy_map.find_anomalies([])

    state.update(
        {
            "filesystem": "unknown",
            "boot_sector": {},
            "cluster_size": image.sector_size,
            "files": [],
            "damage": [
                {
                    "kind": "boot_sector_invalid",
                    "detail": (
                        "No FAT32 or exFAT boot sector in either the primary or backup location. "
                        "The volume was reformatted or its metadata region was destroyed."
                    ),
                    "cluster": None,
                    "sector": 0,
                    "path": None,
                }
            ],
            "filesystem_summary": {"filesystem": "unknown", "files_found": 0},
            "orphan_runs": [[0, image.size]],
            "referenced_ranges": [],
            "entropy_points": entropy_map.downsample(512),
            "entropy_detail": _detail_profile(entropy_map),
            "entropy_stats": entropy_map.statistics(),
            "anomalies": [anomaly.to_dict() for anomaly in anomalies],
            "stage": Stage.MAPPING.value,
            "_entropy_map": entropy_map,
            "_image": image,
            "_parser": None,
            "_entries": [],
        }
    )
    return state
