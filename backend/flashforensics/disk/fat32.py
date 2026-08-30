"""FAT32 filesystem parser written directly against the on-disk structures.

Recovery tools normally lean on libtsk through pytsk3. We parse the boot sector,
the allocation table and the directory tree ourselves for two reasons: the
project has to install cleanly without a compiled C dependency, and damage
tolerance is the whole point here. A library built for healthy filesystems
raises on the first inconsistency, whereas this parser records the inconsistency
as evidence and keeps walking, which is exactly the signal the agents need.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .image import DiskImage

FREE_CLUSTER = 0x00000000
BAD_CLUSTER = 0x0FFFFFF7
END_OF_CHAIN = 0x0FFFFFF8
CLUSTER_MASK = 0x0FFFFFFF

ATTR_READ_ONLY = 0x01
ATTR_HIDDEN = 0x02
ATTR_SYSTEM = 0x04
ATTR_VOLUME_ID = 0x08
ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LONG_NAME = 0x0F

DIR_ENTRY_SIZE = 32
ENTRY_FREE = 0xE5
ENTRY_END = 0x00

MAX_CHAIN_LENGTH = 1_000_000
MAX_DIR_DEPTH = 32


class DamageKind(str, Enum):
    BOOT_SECTOR_INVALID = "boot_sector_invalid"
    FAT_MIRROR_MISMATCH = "fat_mirror_mismatch"
    CLUSTER_OUT_OF_RANGE = "cluster_out_of_range"
    CIRCULAR_CHAIN = "circular_chain"
    CHAIN_TRUNCATED = "chain_truncated"
    BAD_CLUSTER_MARKED = "bad_cluster_marked"
    DIRECTORY_UNREADABLE = "directory_unreadable"
    SIZE_CHAIN_MISMATCH = "size_chain_mismatch"
    ORPHANED_ALLOCATION = "orphaned_allocation"


@dataclass
class DamageReport:
    """One structural inconsistency found while parsing.

    These are what the scanner agent reasons over, so each carries enough
    context to be explained in words rather than just counted.
    """

    kind: DamageKind
    detail: str
    cluster: int | None = None
    sector: int | None = None
    path: str | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "detail": self.detail,
            "cluster": self.cluster,
            "sector": self.sector,
            "path": self.path,
        }


@dataclass
class BootSector:
    """Parsed BIOS Parameter Block for a FAT32 volume."""

    oem_name: str
    bytes_per_sector: int
    sectors_per_cluster: int
    reserved_sectors: int
    num_fats: int
    root_entry_count: int
    total_sectors_16: int
    media: int
    fat_size_16: int
    total_sectors_32: int
    fat_size_32: int
    ext_flags: int
    root_cluster: int
    fsinfo_sector: int
    backup_boot_sector: int
    volume_id: int
    volume_label: str
    fs_type: str
    signature_ok: bool

    @property
    def total_sectors(self) -> int:
        return self.total_sectors_32 or self.total_sectors_16

    @property
    def fat_size(self) -> int:
        return self.fat_size_32 or self.fat_size_16

    @property
    def cluster_size(self) -> int:
        return self.bytes_per_sector * self.sectors_per_cluster

    @property
    def fat_start_sector(self) -> int:
        return self.reserved_sectors

    @property
    def data_start_sector(self) -> int:
        return self.reserved_sectors + self.num_fats * self.fat_size

    @property
    def cluster_count(self) -> int:
        data_sectors = self.total_sectors - self.data_start_sector
        if self.sectors_per_cluster <= 0:
            return 0
        return max(0, data_sectors // self.sectors_per_cluster)

    @property
    def max_cluster(self) -> int:
        return self.cluster_count + 1

    def is_plausible(self) -> bool:
        """Sanity gate before we trust these numbers enough to seek with them."""
        return (
            self.bytes_per_sector in (512, 1024, 2048, 4096)
            and self.sectors_per_cluster in (1, 2, 4, 8, 16, 32, 64, 128)
            and self.reserved_sectors > 0
            and 1 <= self.num_fats <= 2
            and self.fat_size > 0
            and self.total_sectors > 0
            and self.root_cluster >= 2
        )

    def to_dict(self) -> dict:
        return {
            "oem_name": self.oem_name,
            "bytes_per_sector": self.bytes_per_sector,
            "sectors_per_cluster": self.sectors_per_cluster,
            "cluster_size": self.cluster_size,
            "reserved_sectors": self.reserved_sectors,
            "num_fats": self.num_fats,
            "fat_size": self.fat_size,
            "total_sectors": self.total_sectors,
            "root_cluster": self.root_cluster,
            "data_start_sector": self.data_start_sector,
            "cluster_count": self.cluster_count,
            "volume_label": self.volume_label,
            "volume_id": f"{self.volume_id:08X}",
            "fs_type": self.fs_type,
            "signature_ok": self.signature_ok,
        }


@dataclass
class FileEntry:
    """A file or directory recovered from the directory tree."""

    name: str
    path: str
    short_name: str
    is_directory: bool
    is_deleted: bool
    size: int
    first_cluster: int
    attributes: int
    created: datetime | None
    modified: datetime | None
    clusters: list[int] = field(default_factory=list)
    damage: list[DamageReport] = field(default_factory=list)

    @property
    def allocated_bytes(self) -> int:
        return len(self.clusters)

    def to_dict(self, cluster_size: int = 0) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "short_name": self.short_name,
            "is_directory": self.is_directory,
            "is_deleted": self.is_deleted,
            "size": self.size,
            "first_cluster": self.first_cluster,
            "cluster_count": len(self.clusters),
            "allocated_bytes": len(self.clusters) * cluster_size,
            "created": self.created.isoformat() if self.created else None,
            "modified": self.modified.isoformat() if self.modified else None,
            "damage": [d.to_dict() for d in self.damage],
        }


def _decode_fat_datetime(date_word: int, time_word: int) -> datetime | None:
    """Convert the packed DOS date/time pair into a datetime, or None if absurd."""
    if date_word == 0:
        return None
    year = 1980 + ((date_word >> 9) & 0x7F)
    month = (date_word >> 5) & 0x0F
    day = date_word & 0x1F
    hour = (time_word >> 11) & 0x1F
    minute = (time_word >> 5) & 0x3F
    second = (time_word & 0x1F) * 2
    try:
        return datetime(year, month, day, hour, min(minute, 59), min(second, 59))
    except ValueError:
        return None


def _lfn_checksum(short_name: bytes) -> int:
    checksum = 0
    for byte in short_name:
        checksum = (((checksum & 1) << 7) + (checksum >> 1) + byte) & 0xFF
    return checksum


def _format_short_name(raw: bytes) -> str:
    stem = raw[:8].decode("ascii", errors="replace").rstrip()
    ext = raw[8:11].decode("ascii", errors="replace").rstrip()
    return f"{stem}.{ext}" if ext else stem


class Fat32Parser:
    """Damage-tolerant reader for a FAT32 volume."""

    def __init__(self, image: DiskImage, offset: int = 0):
        self.image = image
        self.offset = offset
        self.damage: list[DamageReport] = []
        self.boot: BootSector | None = None
        self._fat: list[int] | None = None
        self._fat_mirror_mismatches = 0

    @staticmethod
    def detect(image: DiskImage, offset: int = 0) -> bool:
        """Probe for FAT32, checking the backup boot sector when sector 0 is dead.

        Detection has to survive the exact damage the tool exists to handle. A
        card with a wiped sector 0 is still a FAT32 card, and answering "unknown
        filesystem" there would fail on the most common case in the field.
        """
        for probe in (offset, offset + 6 * DiskImage.DEFAULT_SECTOR_SIZE):
            head = image.read(probe, 512)
            if len(head) < 512 or head[510:512] != b"\x55\xAA":
                continue
            if head[3:11] == b"EXFAT   ":
                return False
            if head[0x52:0x5A].startswith(b"FAT32"):
                return True
            try:
                fat_size_16 = struct.unpack_from("<H", head, 0x16)[0]
                fat_size_32 = struct.unpack_from("<I", head, 0x24)[0]
            except struct.error:
                continue
            if fat_size_16 == 0 and fat_size_32 > 0:
                return True
        return False

    def parse_boot_sector(self) -> BootSector:
        """Read the BPB, falling back to the backup copy at sector 6 if needed.

        FAT32 keeps a spare boot sector precisely because sector 0 is the most
        commonly damaged sector on a card, so trying the backup is not a clever
        trick, it is what the specification intends.
        """
        boot = self._read_boot_at(self.offset)
        if boot is not None and boot.is_plausible():
            self.boot = boot
            self._apply_sector_size(boot)
            return boot

        self.damage.append(
            DamageReport(
                kind=DamageKind.BOOT_SECTOR_INVALID,
                detail="Primary boot sector failed validation, trying the backup at sector 6",
                sector=0,
            )
        )

        backup_offset = self.offset + 6 * DiskImage.DEFAULT_SECTOR_SIZE
        backup = self._read_boot_at(backup_offset)
        if backup is not None and backup.is_plausible():
            self.damage.append(
                DamageReport(
                    kind=DamageKind.BOOT_SECTOR_INVALID,
                    detail="Recovered volume geometry from the backup boot sector",
                    sector=6,
                )
            )
            self.boot = backup
            self._apply_sector_size(backup)
            return backup

        raise ValueError("no usable FAT32 boot sector in primary or backup location")

    def _apply_sector_size(self, boot: BootSector) -> None:
        if boot.bytes_per_sector != self.image.sector_size:
            self.image.set_sector_size(boot.bytes_per_sector)

    def _read_boot_at(self, offset: int) -> BootSector | None:
        raw = self.image.read(offset, 512)
        if len(raw) < 512:
            return None
        try:
            return BootSector(
                oem_name=raw[3:11].decode("ascii", errors="replace").strip(),
                bytes_per_sector=struct.unpack_from("<H", raw, 0x0B)[0],
                sectors_per_cluster=raw[0x0D],
                reserved_sectors=struct.unpack_from("<H", raw, 0x0E)[0],
                num_fats=raw[0x10],
                root_entry_count=struct.unpack_from("<H", raw, 0x11)[0],
                total_sectors_16=struct.unpack_from("<H", raw, 0x13)[0],
                media=raw[0x15],
                fat_size_16=struct.unpack_from("<H", raw, 0x16)[0],
                total_sectors_32=struct.unpack_from("<I", raw, 0x20)[0],
                fat_size_32=struct.unpack_from("<I", raw, 0x24)[0],
                ext_flags=struct.unpack_from("<H", raw, 0x28)[0],
                root_cluster=struct.unpack_from("<I", raw, 0x2C)[0],
                fsinfo_sector=struct.unpack_from("<H", raw, 0x30)[0],
                backup_boot_sector=struct.unpack_from("<H", raw, 0x32)[0],
                volume_id=struct.unpack_from("<I", raw, 0x43)[0],
                volume_label=raw[0x47:0x52].decode("ascii", errors="replace").strip(),
                fs_type=raw[0x52:0x5A].decode("ascii", errors="replace").strip(),
                signature_ok=raw[510:512] == b"\x55\xAA",
            )
        except struct.error:
            return None

    def load_fat(self) -> list[int]:
        """Load the allocation table, cross-checking the mirror when one exists.

        The second FAT is not decoration. When the primary table has been
        partially overwritten, disagreement between the two copies localises the
        damage to specific cluster ranges, and every entry the mirror still has
        is an entry we do not have to reconstruct by guessing.
        """
        if self._fat is not None:
            return self._fat

        boot = self.boot or self.parse_boot_sector()
        fat_bytes = boot.fat_size * boot.bytes_per_sector
        primary_offset = self.offset + boot.fat_start_sector * boot.bytes_per_sector
        primary = self.image.read(primary_offset, fat_bytes)

        entry_count = min(len(primary) // 4, boot.cluster_count + 2)
        table = list(struct.unpack_from(f"<{entry_count}I", primary, 0))
        table = [entry & CLUSTER_MASK for entry in table]

        if boot.num_fats > 1:
            mirror_offset = primary_offset + fat_bytes
            mirror = self.image.read(mirror_offset, fat_bytes)
            if len(mirror) >= entry_count * 4:
                mirror_table = [
                    value & CLUSTER_MASK
                    for value in struct.unpack_from(f"<{entry_count}I", mirror, 0)
                ]
                table = self._reconcile_fats(table, mirror_table)

        self._fat = table
        return table

    def _reconcile_fats(self, primary: list[int], mirror: list[int]) -> list[int]:
        """Prefer whichever copy still holds a live value for each cluster."""
        merged = list(primary)
        mismatch_ranges: list[tuple[int, int]] = []
        run_start: int | None = None

        for index in range(2, len(primary)):
            if primary[index] == mirror[index]:
                if run_start is not None:
                    mismatch_ranges.append((run_start, index - 1))
                    run_start = None
                continue

            self._fat_mirror_mismatches += 1
            if run_start is None:
                run_start = index
            if primary[index] in (FREE_CLUSTER, BAD_CLUSTER) and mirror[index] not in (
                FREE_CLUSTER,
                BAD_CLUSTER,
            ):
                merged[index] = mirror[index]

        if run_start is not None:
            mismatch_ranges.append((run_start, len(primary) - 1))

        for start, end in mismatch_ranges[:64]:
            self.damage.append(
                DamageReport(
                    kind=DamageKind.FAT_MIRROR_MISMATCH,
                    detail=(
                        f"FAT copies disagree across clusters {start}-{end}; "
                        f"took the live value where only one copy had one"
                    ),
                    cluster=start,
                )
            )
        return merged

    def cluster_to_offset(self, cluster: int) -> int:
        boot = self.boot or self.parse_boot_sector()
        sector = boot.data_start_sector + (cluster - 2) * boot.sectors_per_cluster
        return self.offset + sector * boot.bytes_per_sector

    def read_cluster(self, cluster: int) -> bytes:
        boot = self.boot or self.parse_boot_sector()
        return self.image.read(self.cluster_to_offset(cluster), boot.cluster_size)

    def follow_chain(self, start_cluster: int, path_hint: str = "") -> tuple[list[int], list[DamageReport]]:
        """Walk a cluster chain, stopping at the first thing that cannot be true.

        A healthy chain ends on an end-of-chain marker. A damaged one can point
        outside the volume, loop back on itself, or land on a free entry where a
        successor should be. Each of those is reported and the walk stops rather
        than following a pointer into nonsense.
        """
        boot = self.boot or self.parse_boot_sector()
        fat = self.load_fat()
        chain: list[int] = []
        problems: list[DamageReport] = []
        seen: set[int] = set()
        cluster = start_cluster

        while True:
            if cluster < 2 or cluster > boot.max_cluster or cluster >= len(fat):
                problems.append(
                    DamageReport(
                        kind=DamageKind.CLUSTER_OUT_OF_RANGE,
                        detail=f"Chain points at cluster {cluster}, outside the valid range 2-{boot.max_cluster}",
                        cluster=cluster,
                        path=path_hint or None,
                    )
                )
                break

            if cluster in seen:
                problems.append(
                    DamageReport(
                        kind=DamageKind.CIRCULAR_CHAIN,
                        detail=f"Chain loops back to cluster {cluster}; stopped to avoid an infinite walk",
                        cluster=cluster,
                        path=path_hint or None,
                    )
                )
                break

            seen.add(cluster)
            chain.append(cluster)

            if len(chain) > MAX_CHAIN_LENGTH:
                problems.append(
                    DamageReport(
                        kind=DamageKind.CHAIN_TRUNCATED,
                        detail="Chain exceeded the safety limit and was truncated",
                        cluster=cluster,
                        path=path_hint or None,
                    )
                )
                break

            nxt = fat[cluster]

            if nxt >= END_OF_CHAIN:
                break
            if nxt == BAD_CLUSTER:
                problems.append(
                    DamageReport(
                        kind=DamageKind.BAD_CLUSTER_MARKED,
                        detail=f"Cluster after {cluster} is flagged bad in the allocation table",
                        cluster=cluster,
                        path=path_hint or None,
                    )
                )
                break
            if nxt == FREE_CLUSTER:
                problems.append(
                    DamageReport(
                        kind=DamageKind.CHAIN_TRUNCATED,
                        detail=(
                            f"Chain dies at cluster {cluster}: the allocation table says the "
                            f"next cluster is free, so the tail of this file is unreachable"
                        ),
                        cluster=cluster,
                        path=path_hint or None,
                    )
                )
                break

            cluster = nxt

        return chain, problems

    def read_file(self, entry: FileEntry, limit: int | None = None) -> bytes:
        """Reassemble a file's bytes by concatenating its cluster chain."""
        cap = entry.size if limit is None else min(limit, entry.size or limit)
        buffer = bytearray()
        for cluster in entry.clusters:
            buffer.extend(self.read_cluster(cluster))
            if cap and len(buffer) >= cap:
                break
        return bytes(buffer[:cap]) if cap else bytes(buffer)

    def parse_directory(self, cluster: int, path: str = "/", depth: int = 0) -> list[FileEntry]:
        """Parse one directory cluster chain into entries, following LFN runs."""
        if depth > MAX_DIR_DEPTH:
            return []

        boot = self.boot or self.parse_boot_sector()
        chain, problems = self.follow_chain(cluster, path_hint=path)
        if problems:
            self.damage.extend(problems)
        if not chain:
            self.damage.append(
                DamageReport(
                    kind=DamageKind.DIRECTORY_UNREADABLE,
                    detail=f"Directory {path} has no readable clusters",
                    cluster=cluster,
                    path=path,
                )
            )
            return []

        raw = bytearray()
        for cluster_id in chain:
            raw.extend(self.read_cluster(cluster_id))

        entries: list[FileEntry] = []
        lfn_parts: dict[int, str] = {}
        lfn_checksum: int | None = None

        for position in range(0, len(raw) - DIR_ENTRY_SIZE + 1, DIR_ENTRY_SIZE):
            record = raw[position : position + DIR_ENTRY_SIZE]
            first = record[0]

            if first == ENTRY_END:
                break
            if first == ENTRY_FREE:
                lfn_parts.clear()
                lfn_checksum = None
                continue

            attributes = record[0x0B]

            if attributes == ATTR_LONG_NAME:
                sequence = first & 0x3F
                chunk = record[1:11] + record[14:26] + record[28:32]
                text = chunk.decode("utf-16-le", errors="ignore")
                for terminator in ("\x00", "￿"):
                    if terminator in text:
                        text = text.split(terminator)[0]
                lfn_parts[sequence] = text
                lfn_checksum = record[0x0D]
                continue

            if attributes & ATTR_VOLUME_ID:
                lfn_parts.clear()
                continue

            short_raw = bytes(record[0:11])
            short_name = _format_short_name(short_raw)

            long_name = ""
            if lfn_parts and lfn_checksum == _lfn_checksum(short_raw):
                long_name = "".join(lfn_parts[key] for key in sorted(lfn_parts))
            lfn_parts.clear()
            lfn_checksum = None

            name = long_name or short_name
            if name in (".", ".."):
                continue

            first_cluster = (
                struct.unpack_from("<H", record, 0x14)[0] << 16
            ) | struct.unpack_from("<H", record, 0x1A)[0]
            size = struct.unpack_from("<I", record, 0x1C)[0]
            is_directory = bool(attributes & ATTR_DIRECTORY)
            entry_path = f"{path.rstrip('/')}/{name}"

            entry = FileEntry(
                name=name,
                path=entry_path,
                short_name=short_name,
                is_directory=is_directory,
                is_deleted=False,
                size=size,
                first_cluster=first_cluster,
                attributes=attributes,
                created=_decode_fat_datetime(
                    struct.unpack_from("<H", record, 0x10)[0],
                    struct.unpack_from("<H", record, 0x0E)[0],
                ),
                modified=_decode_fat_datetime(
                    struct.unpack_from("<H", record, 0x18)[0],
                    struct.unpack_from("<H", record, 0x16)[0],
                ),
            )

            if first_cluster >= 2:
                entry.clusters, entry.damage = self.follow_chain(first_cluster, entry_path)

            if not is_directory and size and entry.clusters:
                expected = -(-size // boot.cluster_size)
                if len(entry.clusters) < expected:
                    entry.damage.append(
                        DamageReport(
                            kind=DamageKind.SIZE_CHAIN_MISMATCH,
                            detail=(
                                f"Directory entry claims {size} bytes which needs {expected} clusters, "
                                f"but only {len(entry.clusters)} are reachable"
                            ),
                            cluster=first_cluster,
                            path=entry_path,
                        )
                    )

            entries.append(entry)

            if is_directory and first_cluster >= 2:
                entries.extend(self.parse_directory(first_cluster, entry_path, depth + 1))

        return entries

    def walk(self) -> list[FileEntry]:
        """Parse the whole directory tree starting at the root cluster."""
        boot = self.boot or self.parse_boot_sector()
        return self.parse_directory(boot.root_cluster, "/")

    def allocated_clusters(self) -> set[int]:
        """Every cluster the allocation table considers in use."""
        boot = self.boot or self.parse_boot_sector()
        fat = self.load_fat()
        return {
            index
            for index in range(2, min(len(fat), boot.max_cluster + 1))
            if fat[index] not in (FREE_CLUSTER, BAD_CLUSTER)
        }

    def referenced_clusters(self, entries: list[FileEntry]) -> set[int]:
        """Every cluster some surviving directory entry still points at.

        The root directory has no parent entry describing it, so its own chain
        has to be added explicitly or it would show up as an orphan on a
        perfectly healthy volume.
        """
        boot = self.boot or self.parse_boot_sector()
        root_chain, _ = self.follow_chain(boot.root_cluster, "/")
        referenced: set[int] = set(root_chain)
        for entry in entries:
            referenced.update(entry.clusters)
        return referenced

    def orphaned_clusters(self, entries: list[FileEntry]) -> set[int]:
        """Clusters marked in use that no directory entry claims.

        This set is the whole reason carving is necessary. These clusters hold
        real data whose filename and chain head were destroyed, so the only way
        to identify them is to look at the bytes themselves.
        """
        orphans = self.allocated_clusters() - self.referenced_clusters(entries)
        if orphans:
            self.damage.append(
                DamageReport(
                    kind=DamageKind.ORPHANED_ALLOCATION,
                    detail=(
                        f"{len(orphans)} clusters are marked allocated but no directory entry "
                        f"references them; these are the carving targets"
                    ),
                    cluster=min(orphans),
                )
            )
        return orphans

    def cluster_runs(self, clusters: set[int]) -> list[tuple[int, int]]:
        """Collapse a cluster set into contiguous (start, end) runs."""
        if not clusters:
            return []
        ordered = sorted(clusters)
        runs: list[tuple[int, int]] = []
        start = previous = ordered[0]
        for cluster in ordered[1:]:
            if cluster == previous + 1:
                previous = cluster
                continue
            runs.append((start, previous))
            start = previous = cluster
        runs.append((start, previous))
        return runs

    def summary(self, entries: list[FileEntry]) -> dict:
        boot = self.boot or self.parse_boot_sector()
        allocated = self.allocated_clusters()
        referenced = self.referenced_clusters(entries)
        files = [entry for entry in entries if not entry.is_directory]
        return {
            "filesystem": "FAT32",
            "boot_sector": boot.to_dict(),
            "files_found": len(files),
            "directories_found": len(entries) - len(files),
            "clusters_allocated": len(allocated),
            "clusters_referenced": len(referenced),
            "clusters_orphaned": len(allocated - referenced),
            "fat_mirror_mismatches": self._fat_mirror_mismatches,
            "damage_events": len(self.damage),
            "damage": [d.to_dict() for d in self.damage[:200]],
        }
