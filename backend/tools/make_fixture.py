"""Generate a FAT32 disk image with real files, then damage it on purpose.

Every recovery tool claims to work. Almost none can tell you how well, because
nobody knows what was on a customer's broken SD card before it broke. This script
removes that excuse. It formats a FAT32 volume from scratch in pure Python,
writes genuine JPEGs, PNGs, PDFs, ZIP-family containers, SQLite databases and
MP4 files into it, records the SHA-256 of every one, and then applies a set of
named corruption scenarios drawn from how flash storage actually fails.

The output is an image plus a ground truth manifest. Because the manifest says
exactly which bytes went in and exactly what was done to them, the recovery rate
reported in the README is a measurement rather than a claim.

No mkfs, no loop mount, no root. Everything here is struct.pack, which is why the
whole test corpus regenerates identically on Linux, macOS and CI.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import random
import sqlite3
import struct
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

BYTES_PER_SECTOR = 512
RESERVED_SECTORS = 32
NUM_FATS = 2
ROOT_CLUSTER = 2
END_OF_CHAIN = 0x0FFFFFFF
DIR_ENTRY_SIZE = 32

ATTR_DIRECTORY = 0x10
ATTR_ARCHIVE = 0x20
ATTR_LONG_NAME = 0x0F


@dataclass
class PlantedFile:
    """One file written into the image, with the truth about what happened to it."""

    path: str
    name: str
    format: str
    size: int
    sha256: str
    first_cluster: int
    clusters: list[int]
    byte_offset: int
    scenario: str = "intact"
    expected_recoverable: bool = True
    expected_complete: bool = True
    notes: str = ""


@dataclass
class GroundTruth:
    """Everything the benchmark needs to score a recovery run."""

    image_path: str
    image_size: int
    filesystem: str
    bytes_per_sector: int
    sectors_per_cluster: int
    cluster_size: int
    data_start_sector: int
    total_clusters: int
    created_at: str
    scenarios: list[str]
    files: list[PlantedFile] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["files"] = [asdict(f) if not isinstance(f, dict) else f for f in self.files]
        return payload


def short_name_for(name: str, index: int) -> bytes:
    """Build the 8.3 short name that accompanies every long filename entry."""
    stem, _, extension = name.rpartition(".")
    stem = (stem or name).upper()
    cleaned = "".join(ch for ch in stem if ch.isalnum() or ch in "_-")[:6] or "FILE"
    tail = f"~{index}"
    base = (cleaned + tail)[:8].ljust(8)
    ext = "".join(ch for ch in extension.upper() if ch.isalnum())[:3].ljust(3)
    return (base + ext).encode("ascii", errors="replace")


def lfn_checksum(short: bytes) -> int:
    checksum = 0
    for byte in short:
        checksum = (((checksum & 1) << 7) + (checksum >> 1) + byte) & 0xFF
    return checksum


def pack_datetime(when: datetime) -> tuple[int, int]:
    date_word = ((when.year - 1980) << 9) | (when.month << 5) | when.day
    time_word = (when.hour << 11) | (when.minute << 5) | (when.second // 2)
    return date_word, time_word


def build_lfn_entries(name: str, short: bytes) -> bytes:
    """Emit the chain of long-filename entries that precedes a short entry."""
    checksum = lfn_checksum(short)
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    padding = (26 - (len(encoded) % 26)) % 26
    encoded += b"\xFF" * padding
    chunk_count = len(encoded) // 26

    entries = bytearray()
    for index in range(chunk_count, 0, -1):
        chunk = encoded[(index - 1) * 26 : index * 26]
        sequence = index | (0x40 if index == chunk_count else 0x00)
        entry = bytearray(DIR_ENTRY_SIZE)
        entry[0] = sequence
        entry[1:11] = chunk[0:10]
        entry[11] = ATTR_LONG_NAME
        entry[12] = 0
        entry[13] = checksum
        entry[14:26] = chunk[10:22]
        entry[26:28] = b"\x00\x00"
        entry[28:32] = chunk[22:26]
        entries.extend(entry)
    return bytes(entries)


def build_short_entry(
    short: bytes,
    attributes: int,
    first_cluster: int,
    size: int,
    when: datetime,
) -> bytes:
    date_word, time_word = pack_datetime(when)
    entry = bytearray(DIR_ENTRY_SIZE)
    entry[0:11] = short
    entry[11] = attributes
    entry[12] = 0
    entry[13] = 0
    struct.pack_into("<H", entry, 14, time_word)
    struct.pack_into("<H", entry, 16, date_word)
    struct.pack_into("<H", entry, 18, date_word)
    struct.pack_into("<H", entry, 20, (first_cluster >> 16) & 0xFFFF)
    struct.pack_into("<H", entry, 22, time_word)
    struct.pack_into("<H", entry, 24, date_word)
    struct.pack_into("<H", entry, 26, first_cluster & 0xFFFF)
    struct.pack_into("<I", entry, 28, size)
    return bytes(entry)


class Directory:
    """An in-memory directory node awaiting cluster assignment."""

    def __init__(self, name: str, path: str, parent: Directory | None = None):
        self.name = name
        self.path = path
        self.parent = parent
        self.files: list[tuple[str, bytes, str]] = []
        self.subdirs: list[Directory] = []
        self.clusters: list[int] = []
        self.first_cluster = 0

    def add_file(self, name: str, content: bytes, file_format: str) -> None:
        self.files.append((name, content, file_format))

    def add_dir(self, name: str) -> Directory:
        child = Directory(name, f"{self.path.rstrip('/')}/{name}", self)
        self.subdirs.append(child)
        return child

    def entry_bytes_needed(self) -> int:
        total = 0
        if self.parent is not None:
            total += 2 * DIR_ENTRY_SIZE
        for name, _content, _fmt in self.files:
            total += DIR_ENTRY_SIZE * (1 + max(1, -(-len(name) // 13)))
        for child in self.subdirs:
            total += DIR_ENTRY_SIZE * (1 + max(1, -(-len(child.name) // 13)))
        return total + DIR_ENTRY_SIZE


class Fat32Builder:
    """Formats a FAT32 volume and populates it, entirely in memory."""

    def __init__(self, size_bytes: int, sectors_per_cluster: int = 2, label: str = "FFTEST"):
        self.bytes_per_sector = BYTES_PER_SECTOR
        self.sectors_per_cluster = sectors_per_cluster
        self.cluster_size = self.bytes_per_sector * sectors_per_cluster
        self.total_sectors = size_bytes // self.bytes_per_sector
        self.label = label[:11].ljust(11)
        self.volume_id = random.getrandbits(32)

        self.fat_size, self.cluster_count = self._compute_geometry()
        self.data_start_sector = RESERVED_SECTORS + NUM_FATS * self.fat_size

        self.fat = [0] * (self.cluster_count + 2)
        self.fat[0] = 0x0FFFFFF8
        self.fat[1] = 0x0FFFFFFF
        self.data = bytearray(self.cluster_count * self.cluster_size)
        self.next_free = ROOT_CLUSTER
        self.root = Directory("", "/", None)
        self.planted: list[PlantedFile] = []

    def _compute_geometry(self) -> tuple[int, int]:
        """Solve the circular relationship between FAT size and cluster count."""
        fat_size = 1
        for _ in range(64):
            data_sectors = self.total_sectors - RESERVED_SECTORS - NUM_FATS * fat_size
            clusters = max(0, data_sectors // self.sectors_per_cluster)
            needed = -(-(clusters + 2) * 4 // self.bytes_per_sector)
            if needed <= fat_size:
                return fat_size, clusters
            fat_size = needed
        raise RuntimeError("FAT geometry failed to converge")

    def allocate(self, count: int) -> list[int]:
        if self.next_free + count > self.cluster_count + 2:
            raise RuntimeError("image is too small for the requested content")
        clusters = list(range(self.next_free, self.next_free + count))
        self.next_free += count
        for index, cluster in enumerate(clusters):
            self.fat[cluster] = END_OF_CHAIN if index == len(clusters) - 1 else clusters[index + 1]
        return clusters

    def cluster_offset(self, cluster: int) -> int:
        return (cluster - 2) * self.cluster_size

    def cluster_image_offset(self, cluster: int) -> int:
        return (self.data_start_sector + (cluster - 2) * self.sectors_per_cluster) * self.bytes_per_sector

    def write_clusters(self, clusters: list[int], content: bytes) -> None:
        for index, cluster in enumerate(clusters):
            chunk = content[index * self.cluster_size : (index + 1) * self.cluster_size]
            if not chunk:
                break
            start = self.cluster_offset(cluster)
            self.data[start : start + len(chunk)] = chunk

    def _assign_directory_clusters(self, directory: Directory) -> None:
        needed = max(1, -(-directory.entry_bytes_needed() // self.cluster_size))
        directory.clusters = self.allocate(needed)
        directory.first_cluster = directory.clusters[0]
        for child in directory.subdirs:
            self._assign_directory_clusters(child)

    def _write_files(self, directory: Directory) -> dict[str, tuple[int, list[int]]]:
        placements: dict[str, tuple[int, list[int]]] = {}
        for name, content, file_format in directory.files:
            needed = max(1, -(-len(content) // self.cluster_size))
            clusters = self.allocate(needed)
            self.write_clusters(clusters, content)
            full_path = f"{directory.path.rstrip('/')}/{name}"
            placements[full_path] = (clusters[0], clusters)
            self.planted.append(
                PlantedFile(
                    path=full_path,
                    name=name,
                    format=file_format,
                    size=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    first_cluster=clusters[0],
                    clusters=clusters,
                    byte_offset=self.cluster_image_offset(clusters[0]),
                )
            )
        for child in directory.subdirs:
            placements.update(self._write_files(child))
        return placements

    def _render_directory(self, directory: Directory, placements: dict[str, tuple[int, list[int]]]) -> None:
        buffer = bytearray()
        stamp = datetime(2026, 3, 14, 9, 30, 0)

        if directory.parent is not None:
            buffer.extend(
                build_short_entry(b".          ", ATTR_DIRECTORY, directory.first_cluster, 0, stamp)
            )
            parent_cluster = 0 if directory.parent.parent is None else directory.parent.first_cluster
            buffer.extend(
                build_short_entry(b"..         ", ATTR_DIRECTORY, parent_cluster, 0, stamp)
            )

        index = 1
        for child in directory.subdirs:
            short = short_name_for(child.name, index)
            index += 1
            buffer.extend(build_lfn_entries(child.name, short))
            buffer.extend(build_short_entry(short, ATTR_DIRECTORY, child.first_cluster, 0, stamp))

        for name, content, _fmt in directory.files:
            full_path = f"{directory.path.rstrip('/')}/{name}"
            first_cluster, _clusters = placements[full_path]
            short = short_name_for(name, index)
            index += 1
            buffer.extend(build_lfn_entries(name, short))
            buffer.extend(
                build_short_entry(short, ATTR_ARCHIVE, first_cluster, len(content), stamp)
            )

        self.write_clusters(directory.clusters, bytes(buffer))
        for child in directory.subdirs:
            self._render_directory(child, placements)

    def _boot_sector(self) -> bytes:
        sector = bytearray(self.bytes_per_sector)
        sector[0:3] = b"\xEB\x58\x90"
        sector[3:11] = b"FFORENSC"
        struct.pack_into("<H", sector, 0x0B, self.bytes_per_sector)
        sector[0x0D] = self.sectors_per_cluster
        struct.pack_into("<H", sector, 0x0E, RESERVED_SECTORS)
        sector[0x10] = NUM_FATS
        struct.pack_into("<H", sector, 0x11, 0)
        struct.pack_into("<H", sector, 0x13, 0)
        sector[0x15] = 0xF8
        struct.pack_into("<H", sector, 0x16, 0)
        struct.pack_into("<H", sector, 0x18, 63)
        struct.pack_into("<H", sector, 0x1A, 255)
        struct.pack_into("<I", sector, 0x1C, 0)
        struct.pack_into("<I", sector, 0x20, self.total_sectors)
        struct.pack_into("<I", sector, 0x24, self.fat_size)
        struct.pack_into("<H", sector, 0x28, 0)
        struct.pack_into("<H", sector, 0x2A, 0)
        struct.pack_into("<I", sector, 0x2C, ROOT_CLUSTER)
        struct.pack_into("<H", sector, 0x30, 1)
        struct.pack_into("<H", sector, 0x32, 6)
        sector[0x40] = 0x80
        sector[0x42] = 0x29
        struct.pack_into("<I", sector, 0x43, self.volume_id)
        sector[0x47:0x52] = self.label.encode("ascii")
        sector[0x52:0x5A] = b"FAT32   "
        sector[510:512] = b"\x55\xAA"
        return bytes(sector)

    def _fsinfo_sector(self) -> bytes:
        sector = bytearray(self.bytes_per_sector)
        sector[0:4] = b"RRaA"
        sector[484:488] = b"rrAa"
        free_count = self.cluster_count - (self.next_free - 2)
        struct.pack_into("<I", sector, 488, free_count)
        struct.pack_into("<I", sector, 492, self.next_free)
        sector[510:512] = b"\x55\xAA"
        return bytes(sector)

    def _fat_bytes(self) -> bytes:
        table = bytearray(self.fat_size * self.bytes_per_sector)
        struct.pack_into(f"<{len(self.fat)}I", table, 0, *self.fat)
        return bytes(table)

    def build(self) -> bytes:
        self._assign_directory_clusters(self.root)
        placements = self._write_files(self.root)
        self._render_directory(self.root, placements)

        image = bytearray(self.total_sectors * self.bytes_per_sector)
        boot = self._boot_sector()
        image[0 : len(boot)] = boot
        fsinfo = self._fsinfo_sector()
        image[self.bytes_per_sector : self.bytes_per_sector * 2] = fsinfo
        backup_offset = 6 * self.bytes_per_sector
        image[backup_offset : backup_offset + len(boot)] = boot
        image[backup_offset + self.bytes_per_sector : backup_offset + 2 * self.bytes_per_sector] = fsinfo

        fat_bytes = self._fat_bytes()
        first_fat = RESERVED_SECTORS * self.bytes_per_sector
        image[first_fat : first_fat + len(fat_bytes)] = fat_bytes
        second_fat = first_fat + len(fat_bytes)
        image[second_fat : second_fat + len(fat_bytes)] = fat_bytes

        data_offset = self.data_start_sector * self.bytes_per_sector
        image[data_offset : data_offset + len(self.data)] = self.data
        return bytes(image)


def make_jpeg(width: int, height: int, seed: int, label: str) -> bytes:
    """Render a real JPEG with EXIF so the validator has genuine structure to walk."""
    from PIL import Image, ImageDraw

    random.seed(seed)
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for _ in range(60):
        x0 = random.randint(0, width)
        y0 = random.randint(0, height)
        draw.ellipse(
            [x0, y0, x0 + random.randint(20, 160), y0 + random.randint(20, 160)],
            fill=(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)),
        )
    draw.text((10, 10), label, fill=(255, 255, 255))

    buffer = io.BytesIO()
    exif = image.getexif()
    exif[271] = "Canon"
    exif[272] = "Canon EOS R6"
    exif[305] = "FlashForensics fixture generator"
    exif[306] = "2026:03:14 09:30:00"
    image.save(buffer, format="JPEG", quality=88, exif=exif)
    return buffer.getvalue()


def make_png(width: int, height: int, seed: int) -> bytes:
    from PIL import Image, ImageDraw

    random.seed(seed)
    image = Image.new("RGBA", (width, height), (18, 20, 28, 255))
    draw = ImageDraw.Draw(image)
    for _ in range(40):
        x0 = random.randint(0, width)
        y0 = random.randint(0, height)
        draw.rectangle(
            [x0, y0, x0 + random.randint(10, 120), y0 + random.randint(10, 120)],
            outline=(random.randint(80, 255), random.randint(80, 255), random.randint(80, 255)),
            width=2,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_gif(seed: int) -> bytes:
    from PIL import Image

    random.seed(seed)
    frames = [
        Image.new("P", (120, 120), color=random.randint(0, 255)) for _ in range(4)
    ]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=120, loop=0)
    return buffer.getvalue()


def make_pdf(title: str, paragraphs: int) -> bytes:
    """Assemble a structurally valid PDF with a real xref table and trailer."""
    lines = [f"({title}) Tj"]
    body_text = []
    for index in range(paragraphs):
        body_text.append(
            f"BT /F1 11 Tf 72 {700 - index * 18} Td "
            f"(Section {index + 1}: recovered content sample for structural validation.) Tj ET"
        )
    content_stream = "BT /F1 18 Tf 72 740 Td " + lines[0] + " ET\n" + "\n".join(body_text)
    content_bytes = content_stream.encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content_bytes)).encode() + b" >>\nstream\n" + content_bytes + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    output = bytearray(b"%PDF-1.7\n%\xE2\xE3\xCF\xD3\n")
    offsets = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode()
    )
    return bytes(output)


def make_zip_family(kind: str) -> bytes:
    """Build real ZIP-family containers so the classifier has genuine entry names.

    The whole PK 03 04 ambiguity problem only exists because these formats are
    structurally identical on the outside, so the fixture has to build them the
    real way rather than faking a header.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        if kind == "docx":
            archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
            archive.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
            archive.writestr(
                "word/document.xml",
                '<?xml version="1.0"?><w:document><w:body><w:p><w:r><w:t>'
                "Quarterly recovery report</w:t></w:r></w:p></w:body></w:document>",
            )
            archive.writestr("word/styles.xml", '<?xml version="1.0"?><w:styles/>')
        elif kind == "xlsx":
            archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
            archive.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook><sheets/></workbook>')
            archive.writestr("xl/worksheets/sheet1.xml", '<?xml version="1.0"?><worksheet/>')
            archive.writestr("xl/sharedStrings.xml", '<?xml version="1.0"?><sst/>')
        elif kind == "pptx":
            archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
            archive.writestr("ppt/presentation.xml", '<?xml version="1.0"?><presentation/>')
            archive.writestr("ppt/slides/slide1.xml", '<?xml version="1.0"?><sld/>')
        elif kind == "apk":
            archive.writestr("AndroidManifest.xml", "\x03\x00\x08\x00manifest-binary-blob")
            archive.writestr("classes.dex", b"dex\n035\x00" + os.urandom(2048))
            archive.writestr("resources.arsc", os.urandom(1024))
            archive.writestr("META-INF/CERT.RSA", os.urandom(512))
        elif kind == "jar":
            archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\nMain-Class: app.Main\n")
            archive.writestr("app/Main.class", b"\xCA\xFE\xBA\xBE" + os.urandom(512))
        elif kind == "epub":
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", '<?xml version="1.0"?><container/>')
            archive.writestr("OEBPS/content.opf", '<?xml version="1.0"?><package/>')
            archive.writestr("OEBPS/chapter1.xhtml", "<html><body><h1>Chapter One</h1></body></html>")
        else:
            archive.writestr("readme.txt", "Plain archive with no application identity.\n")
            archive.writestr("data/values.csv", "id,value\n1,42\n2,73\n")
    return buffer.getvalue()


def make_sqlite() -> bytes:
    """Create a genuine SQLite database so the page-count check has real numbers."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as handle:
        temp_path = handle.name
    try:
        connection = sqlite3.connect(temp_path)
        cursor = connection.cursor()
        cursor.execute("CREATE TABLE captures (id INTEGER PRIMARY KEY, device TEXT, taken_at TEXT, bytes INTEGER)")
        cursor.executemany(
            "INSERT INTO captures (device, taken_at, bytes) VALUES (?, ?, ?)",
            [(f"card-{index % 4}", f"2026-03-{(index % 28) + 1:02d}", index * 1024) for index in range(400)],
        )
        connection.commit()
        connection.close()
        return Path(temp_path).read_bytes()
    finally:
        os.unlink(temp_path)


def make_mp4(seconds: int = 2) -> bytes:
    """Assemble a minimal but structurally correct ISO base media file."""

    def box(box_type: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload) + 8) + box_type + payload

    ftyp = box(b"ftyp", b"isom" + struct.pack(">I", 512) + b"isomiso2avc1mp41")
    mvhd_payload = (
        struct.pack(">I", 0)
        + struct.pack(">II", 0, 0)
        + struct.pack(">II", 1000, seconds * 1000)
        + struct.pack(">i", 0x00010000)
        + struct.pack(">h", 0x0100)
        + b"\x00" * 10
        + struct.pack(">9i", 65536, 0, 0, 0, 65536, 0, 0, 0, 1073741824)
        + b"\x00" * 24
        + struct.pack(">I", 2)
    )
    mvhd = box(b"mvhd", mvhd_payload)
    trak = box(b"trak", box(b"tkhd", b"\x00" * 84))
    moov = box(b"moov", mvhd + trak)
    payload = bytes((index * 7 + 13) % 256 for index in range(seconds * 24 * 900))
    mdat = box(b"mdat", payload)
    return ftyp + moov + mdat


def make_mp3(seconds: int = 3) -> bytes:
    tag_body = b"TIT2" + struct.pack(">I", 20) + b"\x00\x00" + b"Recovered Audio\x00\x00\x00\x00"
    size = len(tag_body)
    synchsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    header = b"ID3\x03\x00\x00" + synchsafe
    frames = bytearray()
    frame_length = (144 * 128000 // 44100)
    for _ in range(int(seconds * 44100 / 1152)):
        frames.extend(b"\xFF\xFB\x90\x00")
        frames.extend(bytes((index * 31 + 7) % 256 for index in range(frame_length - 4)))
    return header + tag_body + bytes(frames)


def make_json(records: int) -> bytes:
    payload = {
        "device": "SDXC-128G",
        "captured": "2026-03-14T09:30:00",
        "readings": [
            {"id": index, "temperature_c": 20 + (index % 15), "wear_level": round(index / records, 4)}
            for index in range(records)
        ],
    }
    return json.dumps(payload, indent=2).encode()


def make_html() -> bytes:
    return (
        b"<!DOCTYPE html>\n<html><head><title>Field Notes</title></head>\n"
        b"<body><h1>Card inspection log</h1>"
        b"<p>Sector-level notes captured during acquisition.</p></body></html>\n"
    )


def make_text(paragraphs: int) -> bytes:
    lines = []
    for index in range(paragraphs):
        lines.append(
            f"Entry {index + 1}: acquisition notes recorded during the imaging pass. "
            f"Controller reported no read errors on this span."
        )
    return ("\n".join(lines) + "\n").encode()


def populate(builder: Fat32Builder) -> None:
    """Write a realistic card layout: photos in DCIM, documents, media, logs."""
    dcim = builder.root.add_dir("DCIM")
    camera = dcim.add_dir("100CANON")
    documents = builder.root.add_dir("Documents")
    media = builder.root.add_dir("Media")

    for index in range(1, 7):
        camera.add_file(f"IMG_{4800 + index}.JPG", make_jpeg(640, 480, index, f"IMG {4800 + index}"), "jpg")
    for index in range(1, 4):
        camera.add_file(f"screenshot_{index}.png", make_png(400, 300, index * 11), "png")
    camera.add_file("timelapse_preview.gif", make_gif(7), "gif")

    documents.add_file("field-report-q1.pdf", make_pdf("Field Report Q1 2026", 22), "pdf")
    documents.add_file("inspection-checklist.pdf", make_pdf("Inspection Checklist", 14), "pdf")
    documents.add_file("recovery-notes.docx", make_zip_family("docx"), "docx")
    documents.add_file("wear-analysis.xlsx", make_zip_family("xlsx"), "xlsx")
    documents.add_file("briefing-deck.pptx", make_zip_family("pptx"), "pptx")
    documents.add_file("handbook.epub", make_zip_family("epub"), "epub")
    documents.add_file("readings.json", make_json(240), "json")
    documents.add_file("notes.html", make_html(), "html")
    documents.add_file("acquisition.log", make_text(120), "txt")

    media.add_file("clip_0001.mp4", make_mp4(2), "mp4")
    media.add_file("voice_memo.mp3", make_mp3(4), "mp3")
    media.add_file("catalog.sqlite", make_sqlite(), "sqlite")

    builder.root.add_file("field-tools.apk", make_zip_family("apk"), "apk")
    builder.root.add_file("analyzer.jar", make_zip_family("jar"), "jar")
    builder.root.add_file("bundle.zip", make_zip_family("zip"), "zip")


class Corruptor:
    """Applies named damage scenarios drawn from how flash storage actually fails."""

    def __init__(self, builder: Fat32Builder, image: bytearray, planted: list[PlantedFile]):
        self.builder = builder
        self.image = image
        self.planted = {entry.path: entry for entry in planted}
        self.applied: list[str] = []

    def _cluster_range(self, cluster: int) -> tuple[int, int]:
        start = self.builder.cluster_image_offset(cluster)
        return start, start + self.builder.cluster_size

    def _fat_entry_offset(self, cluster: int, copy: int = 0) -> int:
        fat_start = (RESERVED_SECTORS + copy * self.builder.fat_size) * BYTES_PER_SECTOR
        return fat_start + cluster * 4

    def wipe_boot_sector(self) -> None:
        """Zero sector 0, which is the single most common SD card failure.

        A card interrupted during a metadata write loses its boot sector and the
        host then offers to reformat it. The volume is entirely intact behind it,
        and the backup boot sector at sector 6 still holds the geometry.
        """
        self.image[0:BYTES_PER_SECTOR] = b"\x00" * BYTES_PER_SECTOR
        self.applied.append("boot_sector_wiped")

    def corrupt_primary_fat(self, cluster_start: int, count: int) -> None:
        """Zero a span of the primary allocation table, leaving the mirror intact."""
        for cluster in range(cluster_start, cluster_start + count):
            offset = self._fat_entry_offset(cluster, copy=0)
            self.image[offset : offset + 4] = b"\x00\x00\x00\x00"
        self.applied.append(f"primary_fat_zeroed_{cluster_start}_{count}")

    def orphan_file(self, path: str) -> None:
        """Erase a file's directory entry while leaving its clusters allocated.

        This is what a damaged directory region looks like. The bytes are all
        still there and the allocation table still reserves them, but nothing
        names them any more, so only carving can find the file.
        """
        entry = self.planted.get(path)
        if entry is None:
            return
        self._erase_directory_entry(entry)
        entry.scenario = "orphaned"
        entry.expected_recoverable = True
        entry.expected_complete = True
        entry.notes = "directory entry erased, clusters still allocated, recoverable only by carving"
        self.applied.append(f"orphaned:{path}")

    def delete_file(self, path: str) -> None:
        """Erase the directory entry and free the clusters, as a normal delete does."""
        entry = self.planted.get(path)
        if entry is None:
            return
        self._erase_directory_entry(entry)
        for cluster in entry.clusters:
            for copy in range(NUM_FATS):
                offset = self._fat_entry_offset(cluster, copy)
                self.image[offset : offset + 4] = b"\x00\x00\x00\x00"
        entry.scenario = "deleted"
        entry.expected_recoverable = True
        entry.expected_complete = True
        entry.notes = "deleted normally, data intact in free space, recoverable by carving"
        self.applied.append(f"deleted:{path}")

    def truncate_file(self, path: str, keep_ratio: float = 0.55) -> None:
        """Zero the tail clusters, simulating a card pulled mid-write."""
        entry = self.planted.get(path)
        if entry is None:
            return
        keep = max(1, int(len(entry.clusters) * keep_ratio))
        for cluster in entry.clusters[keep:]:
            start, end = self._cluster_range(cluster)
            self.image[start:end] = b"\x00" * (end - start)
        entry.scenario = "truncated"
        entry.expected_recoverable = True
        entry.expected_complete = False
        entry.notes = f"tail zeroed after {keep} of {len(entry.clusters)} clusters, header survives"
        self.applied.append(f"truncated:{path}")

    def corrupt_payload(self, path: str, flips: int = 400) -> None:
        """Flip bytes inside a file's payload, leaving header and footer intact.

        This is the case a header check cannot catch. The file opens and its
        structure validates, but the pixels are wrong, which is exactly why PNG
        chunk CRCs matter.
        """
        entry = self.planted.get(path)
        if entry is None or len(entry.clusters) < 2:
            return
        random.seed(hash(path) & 0xFFFF)
        middle = entry.clusters[len(entry.clusters) // 2]
        start, end = self._cluster_range(middle)
        for _ in range(flips):
            position = random.randint(start, end - 1)
            self.image[position] ^= 0xFF
        entry.scenario = "payload_corrupted"
        entry.expected_recoverable = True
        entry.expected_complete = False
        entry.notes = "bytes flipped mid-payload, header and footer survive, integrity checks should fail"
        self.applied.append(f"payload_corrupted:{path}")

    def break_chain(self, path: str) -> None:
        """Zero a mid-chain FAT entry in both copies, severing the file."""
        entry = self.planted.get(path)
        if entry is None or len(entry.clusters) < 3:
            return
        break_at = entry.clusters[len(entry.clusters) // 2]
        for copy in range(NUM_FATS):
            offset = self._fat_entry_offset(break_at, copy)
            self.image[offset : offset + 4] = b"\x00\x00\x00\x00"
        entry.scenario = "chain_broken"
        entry.expected_recoverable = True
        entry.expected_complete = False
        entry.notes = f"allocation chain severed at cluster {break_at}, tail unreachable through the FAT"
        self.applied.append(f"chain_broken:{path}")

    def _erase_directory_entry(self, entry: PlantedFile) -> None:
        """Find and blank the 8.3 entry and its long-name run for a planted file."""
        target = struct.pack("<H", entry.first_cluster & 0xFFFF)
        target_high = struct.pack("<H", (entry.first_cluster >> 16) & 0xFFFF)
        size_bytes = struct.pack("<I", entry.size)

        data_start = self.builder.data_start_sector * BYTES_PER_SECTOR
        data_end = data_start + len(self.builder.data)

        for position in range(data_start, data_end - DIR_ENTRY_SIZE + 1, DIR_ENTRY_SIZE):
            record = self.image[position : position + DIR_ENTRY_SIZE]
            if record[11] == ATTR_LONG_NAME or record[0] in (0x00, 0xE5):
                continue
            if (
                record[26:28] == target
                and record[20:22] == target_high
                and record[28:32] == size_bytes
            ):
                self.image[position] = 0xE5
                self.image[position + 1 : position + 11] = b"\x00" * 10
                self.image[position + 20 : position + 22] = b"\x00\x00"
                self.image[position + 26 : position + 28] = b"\x00\x00"
                back = position - DIR_ENTRY_SIZE
                while back >= data_start and self.image[back + 11] == ATTR_LONG_NAME:
                    self.image[back] = 0xE5
                    back -= DIR_ENTRY_SIZE
                return


SCENARIO_PLAN = {
    "orphan": ["/DCIM/100CANON/IMG_4801.JPG", "/Documents/recovery-notes.docx", "/field-tools.apk"],
    "delete": ["/DCIM/100CANON/IMG_4802.JPG", "/Documents/handbook.epub", "/Media/voice_memo.mp3"],
    "truncate": ["/Media/clip_0001.mp4", "/Documents/field-report-q1.pdf"],
    "corrupt": ["/DCIM/100CANON/screenshot_1.png"],
    "break_chain": ["/Media/catalog.sqlite"],
}


def generate(
    output: Path,
    size_mb: int = 128,
    sectors_per_cluster: int = 2,
    damage: bool = True,
    seed: int = 20260314,
) -> GroundTruth:
    random.seed(seed)
    builder = Fat32Builder(size_mb * 1024 * 1024, sectors_per_cluster=sectors_per_cluster)
    populate(builder)
    image = bytearray(builder.build())

    applied: list[str] = []
    if damage:
        corruptor = Corruptor(builder, image, builder.planted)
        corruptor.wipe_boot_sector()
        corruptor.corrupt_primary_fat(cluster_start=8, count=64)
        for path in SCENARIO_PLAN["orphan"]:
            corruptor.orphan_file(path)
        for path in SCENARIO_PLAN["delete"]:
            corruptor.delete_file(path)
        for path in SCENARIO_PLAN["truncate"]:
            corruptor.truncate_file(path)
        for path in SCENARIO_PLAN["corrupt"]:
            corruptor.corrupt_payload(path)
        for path in SCENARIO_PLAN["break_chain"]:
            corruptor.break_chain(path)
        applied = corruptor.applied

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(bytes(image))

    truth = GroundTruth(
        image_path=str(output),
        image_size=len(image),
        filesystem="FAT32",
        bytes_per_sector=builder.bytes_per_sector,
        sectors_per_cluster=builder.sectors_per_cluster,
        cluster_size=builder.cluster_size,
        data_start_sector=builder.data_start_sector,
        total_clusters=builder.cluster_count,
        created_at=datetime.utcnow().isoformat(),
        scenarios=applied,
        files=builder.planted,
    )

    manifest_path = output.with_suffix(".truth.json")
    manifest_path.write_text(json.dumps(truth.to_dict(), indent=2))
    return truth


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a damaged FAT32 test image with ground truth")
    parser.add_argument("--output", default="fixtures/card.img", type=Path)
    parser.add_argument("--size-mb", type=int, default=128)
    parser.add_argument("--sectors-per-cluster", type=int, default=2)
    parser.add_argument("--clean", action="store_true", help="skip the damage pass")
    parser.add_argument("--seed", type=int, default=20260314)
    args = parser.parse_args()

    truth = generate(
        output=args.output,
        size_mb=args.size_mb,
        sectors_per_cluster=args.sectors_per_cluster,
        damage=not args.clean,
        seed=args.seed,
    )

    by_scenario: dict[str, int] = {}
    for entry in truth.files:
        by_scenario[entry.scenario] = by_scenario.get(entry.scenario, 0) + 1

    print(f"image      {truth.image_path} ({truth.image_size / (1024*1024):.0f} MB)")
    print(f"clusters   {truth.total_clusters} of {truth.cluster_size} bytes")
    print(f"files      {len(truth.files)} planted")
    for scenario, count in sorted(by_scenario.items()):
        print(f"  {scenario:<20} {count}")
    print(f"manifest   {Path(truth.image_path).with_suffix('.truth.json')}")


if __name__ == "__main__":
    main()
