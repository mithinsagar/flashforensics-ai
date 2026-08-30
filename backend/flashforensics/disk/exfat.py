"""exFAT filesystem parser.

exFAT is what every SD card above 32 GB ships with, so a recovery tool that only
handles FAT32 is missing most modern cards. The structures are different enough
from FAT32 to need their own parser: names are UTF-16 spread across chained
directory entries, and a file can opt out of the allocation table entirely with
the NoFatChain flag, which means a contiguous run is described by a start cluster
and a length with nothing in the FAT to follow.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime

from .fat32 import DamageKind, DamageReport, FileEntry
from .image import DiskImage

ENTRY_TYPE_FILE = 0x85
ENTRY_TYPE_STREAM = 0xC0
ENTRY_TYPE_NAME = 0xC1
ENTRY_TYPE_BITMAP = 0x81
ENTRY_TYPE_UPCASE = 0x82
ENTRY_TYPE_LABEL = 0x83
ENTRY_UNUSED = 0x00

EXFAT_END_OF_CHAIN = 0xFFFFFFFF
EXFAT_BAD_CLUSTER = 0xFFFFFFF7

ATTR_DIRECTORY = 0x10
FLAG_NO_FAT_CHAIN = 0x02

DIR_ENTRY_SIZE = 32
MAX_DIR_DEPTH = 32
MAX_CHAIN_LENGTH = 1_000_000


@dataclass
class ExfatBootSector:
    """Parsed exFAT volume boot record."""

    partition_offset: int
    volume_length: int
    fat_offset: int
    fat_length: int
    cluster_heap_offset: int
    cluster_count: int
    root_cluster: int
    volume_serial: int
    fs_revision: int
    volume_flags: int
    bytes_per_sector_shift: int
    sectors_per_cluster_shift: int
    num_fats: int
    percent_in_use: int
    signature_ok: bool

    @property
    def bytes_per_sector(self) -> int:
        return 1 << self.bytes_per_sector_shift

    @property
    def sectors_per_cluster(self) -> int:
        return 1 << self.sectors_per_cluster_shift

    @property
    def cluster_size(self) -> int:
        return self.bytes_per_sector * self.sectors_per_cluster

    @property
    def max_cluster(self) -> int:
        return self.cluster_count + 1

    def is_plausible(self) -> bool:
        return (
            9 <= self.bytes_per_sector_shift <= 12
            and 0 <= self.sectors_per_cluster_shift <= 25
            and self.fat_offset > 0
            and self.fat_length > 0
            and self.cluster_heap_offset > 0
            and self.cluster_count > 0
            and self.root_cluster >= 2
            and 1 <= self.num_fats <= 2
        )

    def to_dict(self) -> dict:
        return {
            "bytes_per_sector": self.bytes_per_sector,
            "sectors_per_cluster": self.sectors_per_cluster,
            "cluster_size": self.cluster_size,
            "fat_offset": self.fat_offset,
            "fat_length": self.fat_length,
            "cluster_heap_offset": self.cluster_heap_offset,
            "cluster_count": self.cluster_count,
            "root_cluster": self.root_cluster,
            "volume_serial": f"{self.volume_serial:08X}",
            "fs_revision": f"{self.fs_revision >> 8}.{self.fs_revision & 0xFF}",
            "percent_in_use": self.percent_in_use,
            "num_fats": self.num_fats,
            "signature_ok": self.signature_ok,
        }


def _decode_exfat_timestamp(value: int) -> datetime | None:
    if value == 0:
        return None
    second = (value & 0x1F) * 2
    minute = (value >> 5) & 0x3F
    hour = (value >> 11) & 0x1F
    day = (value >> 16) & 0x1F
    month = (value >> 21) & 0x0F
    year = 1980 + ((value >> 25) & 0x7F)
    try:
        return datetime(year, month, day, hour, min(minute, 59), min(second, 59))
    except ValueError:
        return None


class ExfatParser:
    """Damage-tolerant reader for an exFAT volume."""

    def __init__(self, image: DiskImage, offset: int = 0):
        self.image = image
        self.offset = offset
        self.damage: list[DamageReport] = []
        self.boot: ExfatBootSector | None = None
        self._fat: list[int] | None = None

    @staticmethod
    def detect(image: DiskImage, offset: int = 0) -> bool:
        head = image.read(offset, 512)
        return len(head) >= 512 and head[3:11] == b"EXFAT   "

    def parse_boot_sector(self) -> ExfatBootSector:
        """Read the VBR, falling back to the backup set at sector 12."""
        boot = self._read_boot_at(self.offset)
        if boot is not None and boot.is_plausible():
            self.boot = boot
            self._apply_sector_size(boot)
            return boot

        self.damage.append(
            DamageReport(
                kind=DamageKind.BOOT_SECTOR_INVALID,
                detail="Primary exFAT boot region failed validation, trying the backup at sector 12",
                sector=0,
            )
        )
        backup = self._read_boot_at(self.offset + 12 * DiskImage.DEFAULT_SECTOR_SIZE)
        if backup is not None and backup.is_plausible():
            self.damage.append(
                DamageReport(
                    kind=DamageKind.BOOT_SECTOR_INVALID,
                    detail="Recovered volume geometry from the backup exFAT boot region",
                    sector=12,
                )
            )
            self.boot = backup
            self._apply_sector_size(backup)
            return backup

        raise ValueError("no usable exFAT boot sector in primary or backup location")

    def _apply_sector_size(self, boot: ExfatBootSector) -> None:
        if boot.bytes_per_sector != self.image.sector_size:
            self.image.set_sector_size(boot.bytes_per_sector)

    def _read_boot_at(self, offset: int) -> ExfatBootSector | None:
        raw = self.image.read(offset, 512)
        if len(raw) < 512:
            return None
        try:
            return ExfatBootSector(
                partition_offset=struct.unpack_from("<Q", raw, 0x40)[0],
                volume_length=struct.unpack_from("<Q", raw, 0x48)[0],
                fat_offset=struct.unpack_from("<I", raw, 0x50)[0],
                fat_length=struct.unpack_from("<I", raw, 0x54)[0],
                cluster_heap_offset=struct.unpack_from("<I", raw, 0x58)[0],
                cluster_count=struct.unpack_from("<I", raw, 0x5C)[0],
                root_cluster=struct.unpack_from("<I", raw, 0x60)[0],
                volume_serial=struct.unpack_from("<I", raw, 0x64)[0],
                fs_revision=struct.unpack_from("<H", raw, 0x68)[0],
                volume_flags=struct.unpack_from("<H", raw, 0x6A)[0],
                bytes_per_sector_shift=raw[0x6C],
                sectors_per_cluster_shift=raw[0x6D],
                num_fats=raw[0x6E],
                percent_in_use=raw[0x70],
                signature_ok=raw[510:512] == b"\x55\xAA",
            )
        except struct.error:
            return None

    def load_fat(self) -> list[int]:
        if self._fat is not None:
            return self._fat
        boot = self.boot or self.parse_boot_sector()
        fat_offset = self.offset + boot.fat_offset * boot.bytes_per_sector
        fat_bytes = boot.fat_length * boot.bytes_per_sector
        raw = self.image.read(fat_offset, fat_bytes)
        entry_count = min(len(raw) // 4, boot.cluster_count + 2)
        self._fat = list(struct.unpack_from(f"<{entry_count}I", raw, 0))
        return self._fat

    def cluster_to_offset(self, cluster: int) -> int:
        boot = self.boot or self.parse_boot_sector()
        sector = boot.cluster_heap_offset + (cluster - 2) * boot.sectors_per_cluster
        return self.offset + sector * boot.bytes_per_sector

    def read_cluster(self, cluster: int) -> bytes:
        boot = self.boot or self.parse_boot_sector()
        return self.image.read(self.cluster_to_offset(cluster), boot.cluster_size)

    def follow_chain(
        self,
        start_cluster: int,
        length_bytes: int = 0,
        no_fat_chain: bool = False,
        path_hint: str = "",
    ) -> tuple[list[int], list[DamageReport]]:
        """Resolve an allocation, honouring the contiguous-run shortcut.

        When NoFatChain is set the file occupies a straight run of clusters and
        the allocation table holds nothing for it, so the run has to be derived
        from the declared data length instead of followed.
        """
        boot = self.boot or self.parse_boot_sector()
        problems: list[DamageReport] = []

        if no_fat_chain:
            needed = max(1, -(-length_bytes // boot.cluster_size)) if length_bytes else 1
            end = start_cluster + needed - 1
            if start_cluster < 2 or end > boot.max_cluster:
                problems.append(
                    DamageReport(
                        kind=DamageKind.CLUSTER_OUT_OF_RANGE,
                        detail=(
                            f"Contiguous run {start_cluster}-{end} leaves the cluster heap "
                            f"(valid range 2-{boot.max_cluster})"
                        ),
                        cluster=start_cluster,
                        path=path_hint or None,
                    )
                )
                end = min(end, boot.max_cluster)
            return list(range(start_cluster, max(start_cluster, end) + 1)), problems

        fat = self.load_fat()
        chain: list[int] = []
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
                        detail=f"Chain loops back to cluster {cluster}",
                        cluster=cluster,
                        path=path_hint or None,
                    )
                )
                break

            seen.add(cluster)
            chain.append(cluster)
            if len(chain) > MAX_CHAIN_LENGTH:
                break

            nxt = fat[cluster]
            if nxt == EXFAT_END_OF_CHAIN:
                break
            if nxt == EXFAT_BAD_CLUSTER:
                problems.append(
                    DamageReport(
                        kind=DamageKind.BAD_CLUSTER_MARKED,
                        detail=f"Cluster after {cluster} is flagged bad",
                        cluster=cluster,
                        path=path_hint or None,
                    )
                )
                break
            if nxt == 0:
                problems.append(
                    DamageReport(
                        kind=DamageKind.CHAIN_TRUNCATED,
                        detail=f"Chain dies at cluster {cluster}: successor entry is free",
                        cluster=cluster,
                        path=path_hint or None,
                    )
                )
                break
            cluster = nxt

        return chain, problems

    def parse_directory(self, cluster: int, path: str = "/", depth: int = 0) -> list[FileEntry]:
        """Parse a directory, assembling each file from its three-entry set."""
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
        position = 0

        while position <= len(raw) - DIR_ENTRY_SIZE:
            record = raw[position : position + DIR_ENTRY_SIZE]
            entry_type = record[0]

            if entry_type == ENTRY_UNUSED:
                break
            if entry_type != ENTRY_TYPE_FILE:
                position += DIR_ENTRY_SIZE
                continue

            secondary_count = record[1]
            attributes = struct.unpack_from("<H", record, 0x04)[0]
            created = _decode_exfat_timestamp(struct.unpack_from("<I", record, 0x08)[0])
            modified = _decode_exfat_timestamp(struct.unpack_from("<I", record, 0x0C)[0])

            stream_pos = position + DIR_ENTRY_SIZE
            if stream_pos + DIR_ENTRY_SIZE > len(raw):
                break
            stream = raw[stream_pos : stream_pos + DIR_ENTRY_SIZE]
            if stream[0] != ENTRY_TYPE_STREAM:
                position += DIR_ENTRY_SIZE
                continue

            secondary_flags = stream[1]
            name_length = stream[3]
            first_cluster = struct.unpack_from("<I", stream, 0x14)[0]
            data_length = struct.unpack_from("<Q", stream, 0x18)[0]
            no_fat_chain = bool(secondary_flags & FLAG_NO_FAT_CHAIN)

            name_chars: list[str] = []
            for index in range(1, secondary_count):
                name_pos = position + (index + 1) * DIR_ENTRY_SIZE
                if name_pos + DIR_ENTRY_SIZE > len(raw):
                    break
                name_record = raw[name_pos : name_pos + DIR_ENTRY_SIZE]
                if name_record[0] != ENTRY_TYPE_NAME:
                    continue
                name_chars.append(name_record[2:32].decode("utf-16-le", errors="ignore"))

            name = "".join(name_chars)[:name_length].rstrip("\x00")
            if not name:
                name = f"cluster_{first_cluster}"

            is_directory = bool(attributes & ATTR_DIRECTORY)
            entry_path = f"{path.rstrip('/')}/{name}"

            entry = FileEntry(
                name=name,
                path=entry_path,
                short_name=name[:12],
                is_directory=is_directory,
                is_deleted=False,
                size=data_length,
                first_cluster=first_cluster,
                attributes=attributes,
                created=created,
                modified=modified,
            )

            if first_cluster >= 2:
                entry.clusters, entry.damage = self.follow_chain(
                    first_cluster, data_length, no_fat_chain, entry_path
                )

            if not is_directory and data_length and entry.clusters:
                expected = -(-data_length // boot.cluster_size)
                if len(entry.clusters) < expected:
                    entry.damage.append(
                        DamageReport(
                            kind=DamageKind.SIZE_CHAIN_MISMATCH,
                            detail=(
                                f"Stream declares {data_length} bytes needing {expected} clusters, "
                                f"but only {len(entry.clusters)} are reachable"
                            ),
                            cluster=first_cluster,
                            path=entry_path,
                        )
                    )

            entries.append(entry)

            if is_directory and first_cluster >= 2:
                entries.extend(self.parse_directory(first_cluster, entry_path, depth + 1))

            position += (secondary_count + 1) * DIR_ENTRY_SIZE

        return entries

    def walk(self) -> list[FileEntry]:
        boot = self.boot or self.parse_boot_sector()
        return self.parse_directory(boot.root_cluster, "/")

    def allocated_clusters(self) -> set[int]:
        """Clusters in use according to the allocation bitmap, not the FAT.

        exFAT tracks free space in a bitmap rather than in the FAT, so a
        contiguous file can be allocated while its FAT entries stay zero. Reading
        the bitmap is the only way to see those clusters.
        """
        boot = self.boot or self.parse_boot_sector()
        bitmap_cluster, bitmap_size = self._find_allocation_bitmap()
        if bitmap_cluster is None:
            fat = self.load_fat()
            return {
                index
                for index in range(2, min(len(fat), boot.max_cluster + 1))
                if fat[index] not in (0, EXFAT_BAD_CLUSTER)
            }

        chain, _ = self.follow_chain(bitmap_cluster, bitmap_size, False, "/$Bitmap")
        raw = bytearray()
        for cluster_id in chain:
            raw.extend(self.read_cluster(cluster_id))
        raw = raw[:bitmap_size] if bitmap_size else raw

        allocated: set[int] = set()
        for byte_index, byte in enumerate(raw):
            if byte == 0:
                continue
            for bit in range(8):
                if byte & (1 << bit):
                    cluster = 2 + byte_index * 8 + bit
                    if cluster <= boot.max_cluster:
                        allocated.add(cluster)
        return allocated

    def _find_allocation_bitmap(self) -> tuple[int | None, int]:
        boot = self.boot or self.parse_boot_sector()
        chain, _ = self.follow_chain(boot.root_cluster, path_hint="/")
        for cluster_id in chain:
            data = self.read_cluster(cluster_id)
            for position in range(0, len(data) - DIR_ENTRY_SIZE + 1, DIR_ENTRY_SIZE):
                record = data[position : position + DIR_ENTRY_SIZE]
                if record[0] == ENTRY_UNUSED:
                    return None, 0
                if record[0] == ENTRY_TYPE_BITMAP:
                    return (
                        struct.unpack_from("<I", record, 0x14)[0],
                        struct.unpack_from("<Q", record, 0x18)[0],
                    )
        return None, 0

    def referenced_clusters(self, entries: list[FileEntry]) -> set[int]:
        referenced: set[int] = set()
        for entry in entries:
            referenced.update(entry.clusters)
        return referenced

    def orphaned_clusters(self, entries: list[FileEntry]) -> set[int]:
        orphans = self.allocated_clusters() - self.referenced_clusters(entries)
        if orphans:
            self.damage.append(
                DamageReport(
                    kind=DamageKind.ORPHANED_ALLOCATION,
                    detail=(
                        f"{len(orphans)} clusters are allocated in the bitmap but unreferenced "
                        f"by any directory entry"
                    ),
                    cluster=min(orphans),
                )
            )
        return orphans

    def cluster_runs(self, clusters: set[int]) -> list[tuple[int, int]]:
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

    def read_file(self, entry: FileEntry, limit: int | None = None) -> bytes:
        cap = entry.size if limit is None else min(limit, entry.size or limit)
        buffer = bytearray()
        for cluster in entry.clusters:
            buffer.extend(self.read_cluster(cluster))
            if cap and len(buffer) >= cap:
                break
        return bytes(buffer[:cap]) if cap else bytes(buffer)

    def summary(self, entries: list[FileEntry]) -> dict:
        boot = self.boot or self.parse_boot_sector()
        allocated = self.allocated_clusters()
        referenced = self.referenced_clusters(entries)
        files = [entry for entry in entries if not entry.is_directory]
        return {
            "filesystem": "exFAT",
            "boot_sector": boot.to_dict(),
            "files_found": len(files),
            "directories_found": len(entries) - len(files),
            "clusters_allocated": len(allocated),
            "clusters_referenced": len(referenced),
            "clusters_orphaned": len(allocated - referenced),
            "fat_mirror_mismatches": 0,
            "damage_events": len(self.damage),
            "damage": [d.to_dict() for d in self.damage[:200]],
        }
