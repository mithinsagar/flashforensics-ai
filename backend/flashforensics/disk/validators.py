"""Structural validators that turn a byte fragment into evidence.

This module is the reason a recoverability verdict here means something. A magic
byte match only says "something that starts like a JPEG begins at this offset".
It says nothing about whether the rest survived. To answer that you have to walk
the format's own internal structure: JPEG segment markers, PNG chunk CRCs, the
ZIP central directory, the PDF cross-reference trailer, ISO base media boxes.

Every validator returns the same shape, so the adjudicator agent reasons over a
uniform set of facts and the language model is asked to explain evidence rather
than to guess a format from a name. Walking the ZIP central directory is also
what resolves the PK 03 04 ambiguity, because the entry names inside a container
are what actually separate a DOCX from an XLSX from an APK.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from dataclasses import dataclass, field

MAX_VALIDATION_BYTES = 16 * 1024 * 1024
ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
ZIP_LOCAL_SIGNATURE = b"PK\x03\x04"
ZIP_CENTRAL_SIGNATURE = b"PK\x01\x02"


@dataclass
class ValidationResult:
    """Uniform structural verdict for one fragment."""

    format_detected: str | None
    header_valid: bool
    footer_present: bool
    structure_complete: bool
    confidence: float
    evidence: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    true_size: int | None = None

    def to_dict(self) -> dict:
        return {
            "format_detected": self.format_detected,
            "header_valid": self.header_valid,
            "footer_present": self.footer_present,
            "structure_complete": self.structure_complete,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence,
            "problems": self.problems,
            "metadata": self.metadata,
            "true_size": self.true_size,
        }


def _fail(reason: str, fmt: str | None = None) -> ValidationResult:
    return ValidationResult(
        format_detected=fmt,
        header_valid=False,
        footer_present=False,
        structure_complete=False,
        confidence=0.0,
        problems=[reason],
    )


def validate_jpeg(data: bytes) -> ValidationResult:
    """Walk JPEG segment markers from SOI to EOI.

    A JPEG is a chain of length-prefixed segments followed by entropy-coded scan
    data. Walking the chain proves the header region is intact, and reaching EOI
    proves the scan data was not cut short, which is the difference between a
    photo that opens and a grey half-image.
    """
    if len(data) < 4 or data[:3] != b"\xFF\xD8\xFF":
        return _fail("missing JPEG start-of-image marker", "jpg")

    result = ValidationResult(
        format_detected="jpg",
        header_valid=True,
        footer_present=False,
        structure_complete=False,
        confidence=0.4,
        evidence=["SOI marker FF D8 FF present"],
    )

    position = 2
    segments = 0
    saw_scan = False
    limit = min(len(data), MAX_VALIDATION_BYTES)

    while position < limit - 1:
        if data[position] != 0xFF:
            position += 1
            continue
        marker = data[position + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            position += 2
            continue
        if marker == 0xD9:
            result.footer_present = True
            result.true_size = position + 2
            result.evidence.append(f"EOI marker found at offset {position}")
            break
        if marker == 0xDA:
            saw_scan = True
            result.evidence.append(f"start-of-scan reached after {segments} header segments")
            if position + 4 > limit:
                break
            length = struct.unpack_from(">H", data, position + 2)[0]
            position += 2 + length
            while position < limit - 1:
                if data[position] == 0xFF and data[position + 1] not in (0x00,) and not (
                    0xD0 <= data[position + 1] <= 0xD7
                ):
                    break
                position += 1
            continue
        if position + 4 > limit:
            break
        length = struct.unpack_from(">H", data, position + 2)[0]
        if length < 2:
            result.problems.append(f"segment at {position} declares an impossible length {length}")
            break
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if position + 9 <= limit:
                height, width = struct.unpack_from(">HH", data, position + 5)
                result.metadata["width"] = width
                result.metadata["height"] = height
                result.metadata["components"] = data[position + 9] if position + 9 < limit else None
                result.evidence.append(f"frame header declares {width}x{height} pixels")
        if marker == 0xE1 and data[position + 4 : position + 8] == b"Exif":
            result.metadata["has_exif"] = True
            result.evidence.append("EXIF metadata block present")
            camera = _extract_exif_strings(data[position : position + length + 2])
            if camera:
                result.metadata["exif_strings"] = camera
        segments += 1
        position += 2 + length

    result.metadata["segments"] = segments
    result.structure_complete = result.footer_present and saw_scan

    if result.structure_complete:
        result.confidence = 0.97
        result.evidence.append("complete segment chain: SOI through scan data to EOI")
    elif saw_scan:
        result.confidence = 0.6
        result.problems.append("scan data begins but EOI never appears, so the image is truncated")
    else:
        result.confidence = 0.35
        result.problems.append("header segments end before start-of-scan, only metadata survived")

    return result


def _extract_exif_strings(segment: bytes) -> list[str]:
    """Pull readable ASCII runs out of an EXIF block for camera and lens hints."""
    found = re.findall(rb"[ -~]{4,32}", segment)
    interesting = []
    for candidate in found:
        text = candidate.decode("ascii", errors="ignore").strip()
        if text and text not in ("Exif", "MM", "II") and not text.startswith("http"):
            interesting.append(text)
    return interesting[:8]


def validate_png(data: bytes) -> ValidationResult:
    """Walk PNG chunks and verify each CRC32.

    PNG is the only common image format that checksums every chunk, so a CRC
    failure is proof of corruption rather than an inference from it. That makes
    PNG the most precisely gradeable format in the whole pipeline.
    """
    signature = b"\x89PNG\r\n\x1a\n"
    if len(data) < 8 or data[:8] != signature:
        return _fail("missing PNG signature", "png")

    result = ValidationResult(
        format_detected="png",
        header_valid=True,
        footer_present=False,
        structure_complete=False,
        confidence=0.4,
        evidence=["PNG signature intact"],
    )

    position = 8
    chunks = 0
    crc_failures = 0
    limit = min(len(data), MAX_VALIDATION_BYTES)

    while position + 8 <= limit:
        length = struct.unpack_from(">I", data, position)[0]
        chunk_type = data[position + 4 : position + 8]
        if length > limit:
            result.problems.append(f"chunk at {position} declares {length} bytes, beyond the fragment")
            break
        payload_end = position + 8 + length
        if payload_end + 4 > limit:
            result.problems.append(f"chunk {chunk_type.decode('ascii', 'replace')} is cut off mid-payload")
            break

        stored_crc = struct.unpack_from(">I", data, payload_end)[0]
        actual_crc = zlib.crc32(data[position + 4 : payload_end]) & 0xFFFFFFFF
        if stored_crc != actual_crc:
            crc_failures += 1
            result.problems.append(
                f"CRC mismatch in {chunk_type.decode('ascii', 'replace')} chunk at offset {position}"
            )

        if chunk_type == b"IHDR" and length >= 13:
            width, height = struct.unpack_from(">II", data, position + 8)
            result.metadata["width"] = width
            result.metadata["height"] = height
            result.metadata["bit_depth"] = data[position + 16]
            result.metadata["color_type"] = data[position + 17]
            result.evidence.append(f"IHDR declares {width}x{height}")

        chunks += 1
        if chunk_type == b"IEND":
            result.footer_present = True
            result.true_size = payload_end + 4
            result.evidence.append(f"IEND chunk closes the file at offset {payload_end + 4}")
            break

        position = payload_end + 4

    result.metadata["chunks"] = chunks
    result.metadata["crc_failures"] = crc_failures
    result.structure_complete = result.footer_present and crc_failures == 0

    if result.structure_complete:
        result.confidence = 0.99
        result.evidence.append(f"all {chunks} chunks passed CRC validation")
    elif result.footer_present:
        result.confidence = 0.5
        result.problems.append(f"file is complete but {crc_failures} chunks fail CRC, pixels are damaged")
    else:
        result.confidence = 0.4
        result.problems.append("no IEND chunk, the image is truncated")

    return result


def validate_gif(data: bytes) -> ValidationResult:
    if len(data) < 6 or data[:6] not in (b"GIF87a", b"GIF89a"):
        return _fail("missing GIF header", "gif")
    result = ValidationResult(
        format_detected="gif",
        header_valid=True,
        footer_present=False,
        structure_complete=False,
        confidence=0.45,
        evidence=[f"{data[:6].decode('ascii')} header present"],
    )
    if len(data) >= 10:
        width, height = struct.unpack_from("<HH", data, 6)
        result.metadata["width"] = width
        result.metadata["height"] = height
        result.evidence.append(f"logical screen is {width}x{height}")
    trailer = data.rfind(b"\x3B")
    if trailer > 10:
        result.footer_present = True
        result.structure_complete = True
        result.true_size = trailer + 1
        result.confidence = 0.85
        result.evidence.append("GIF trailer byte present")
    else:
        result.problems.append("no GIF trailer, animation data is truncated")
    return result


def validate_zip(data: bytes) -> ValidationResult:
    """Parse the ZIP structure and identify what the container actually holds.

    This is the function that resolves the biggest ambiguity in carving. Eight
    common formats share the PK 03 04 header because they are all zip files. The
    entry names inside are what separate them: a `word/document.xml` means DOCX,
    a `classes.dex` alongside `AndroidManifest.xml` means APK, a `mimetype`
    entry declaring `application/epub+zip` means EPUB.
    """
    if len(data) < 4 or data[:4] != ZIP_LOCAL_SIGNATURE:
        return _fail("missing ZIP local file header", "zip")

    result = ValidationResult(
        format_detected="zip",
        header_valid=True,
        footer_present=False,
        structure_complete=False,
        confidence=0.4,
        evidence=["ZIP local file header PK 03 04 present"],
    )

    names: list[str] = []
    position = 0
    limit = min(len(data), MAX_VALIDATION_BYTES)

    while position + 30 <= limit:
        if data[position : position + 4] != ZIP_LOCAL_SIGNATURE:
            break
        try:
            compressed_size = struct.unpack_from("<I", data, position + 18)[0]
            name_length = struct.unpack_from("<H", data, position + 26)[0]
            extra_length = struct.unpack_from("<H", data, position + 28)[0]
            flags = struct.unpack_from("<H", data, position + 6)[0]
        except struct.error:
            break

        name_start = position + 30
        name_end = name_start + name_length
        if name_end > limit or name_length > 4096:
            result.problems.append("entry name runs past the end of the fragment")
            break

        names.append(data[name_start:name_end].decode("utf-8", errors="replace"))

        if flags & 0x08 or compressed_size == 0:
            next_local = data.find(ZIP_LOCAL_SIGNATURE, name_end)
            next_central = data.find(ZIP_CENTRAL_SIGNATURE, name_end)
            candidates = [offset for offset in (next_local, next_central) if offset != -1]
            if not candidates:
                break
            position = min(candidates)
            if position == next_central:
                break
            continue

        position = name_end + extra_length + compressed_size
        if len(names) > 5000:
            break

    eocd = _find_matching_eocd(data, position, limit)
    if eocd != -1:
        result.footer_present = True
        result.structure_complete = True
        try:
            entry_count = struct.unpack_from("<H", data, eocd + 10)[0]
            comment_length = struct.unpack_from("<H", data, eocd + 20)[0]
            result.true_size = eocd + 22 + comment_length
            result.metadata["declared_entries"] = entry_count
            result.evidence.append(
                f"end-of-central-directory record declares {entry_count} entries"
            )
        except struct.error:
            result.true_size = eocd + 22
    else:
        result.problems.append("no end-of-central-directory record, the archive tail is missing")

    result.metadata["entry_names"] = names[:64]
    result.metadata["entries_seen"] = len(names)

    refined, reason, refine_confidence = identify_zip_container(names)
    if refined:
        result.format_detected = refined
        result.evidence.append(reason)
        result.confidence = refine_confidence if result.structure_complete else refine_confidence * 0.65
    else:
        result.confidence = 0.8 if result.structure_complete else 0.45
        if names:
            result.evidence.append(f"generic archive holding {len(names)} entries")

    return result


def _find_matching_eocd(data: bytes, entries_end: int, limit: int) -> int:
    """Locate the end-of-central-directory record belonging to *this* archive.

    Searching backwards from the end of the buffer is the obvious implementation
    and it is wrong here. A carve read runs well past the archive being examined,
    so the last EOCD in the window usually belongs to some later file, and taking
    it stretches a 4 KB archive into a 300 KB fragment with unrelated data inside.

    The correct anchor is where local entry parsing stopped, because a ZIP lays
    out local entries, then the central directory, then exactly one EOCD. The
    first EOCD at or after that point is this archive's own.
    """
    if 0 <= entries_end < limit:
        forward = data.find(ZIP_EOCD_SIGNATURE, entries_end, limit)
        if forward != -1:
            return forward
    return data.find(ZIP_EOCD_SIGNATURE, 0, limit)


def identify_zip_container(names: list[str]) -> tuple[str | None, str, float]:
    """Map a set of zip entry names onto a concrete format."""
    joined = set(names)
    lowered = {name.lower() for name in names}

    def has_prefix(prefix: str) -> bool:
        return any(name.lower().startswith(prefix) for name in names)

    if "AndroidManifest.xml" in joined and any(n.startswith("classes") and n.endswith(".dex") for n in names):
        return "apk", "contains AndroidManifest.xml and classes.dex, so this is an Android package", 0.97
    if "AndroidManifest.xml" in joined:
        return "apk", "contains AndroidManifest.xml, consistent with an Android package", 0.85
    if has_prefix("word/"):
        return "docx", "contains a word/ part, so this is a Word document", 0.97
    if has_prefix("xl/"):
        return "xlsx", "contains an xl/ part, so this is an Excel workbook", 0.97
    if has_prefix("ppt/"):
        return "pptx", "contains a ppt/ part, so this is a PowerPoint deck", 0.97
    if "mimetype" in lowered and has_prefix("epub"):
        return "epub", "declares an EPUB mimetype entry", 0.95
    if "meta-inf/container.xml" in lowered or has_prefix("oebps/"):
        return "epub", "contains OEBPS content and an EPUB container descriptor", 0.93
    if has_prefix("content.xml") or "content.xml" in lowered:
        return "odt", "contains content.xml, consistent with an OpenDocument file", 0.88
    if "meta-inf/manifest.mf" in lowered:
        return "jar", "contains META-INF/MANIFEST.MF, so this is a Java archive", 0.92
    if "[content_types].xml" in lowered:
        return "docx", "contains an Open Packaging Conventions content types part", 0.7
    return None, "", 0.0


def validate_pdf(data: bytes) -> ValidationResult:
    """Check the PDF header, object graph and cross-reference trailer."""
    if len(data) < 8 or not data.startswith(b"%PDF-"):
        return _fail("missing PDF header", "pdf")

    version = data[5:8].decode("ascii", errors="replace")
    result = ValidationResult(
        format_detected="pdf",
        header_valid=True,
        footer_present=False,
        structure_complete=False,
        confidence=0.4,
        evidence=[f"PDF header declares version {version}"],
        metadata={"version": version},
    )

    limit = min(len(data), MAX_VALIDATION_BYTES)
    window = data[:limit]

    obj_count = window.count(b" obj")
    endobj_count = window.count(b"endobj")
    result.metadata["objects"] = obj_count
    result.metadata["endobj"] = endobj_count
    if obj_count:
        result.evidence.append(f"{obj_count} indirect object definitions found")
    if obj_count and endobj_count < obj_count:
        result.problems.append(
            f"{obj_count - endobj_count} objects are opened but never closed, the body is cut short"
        )

    has_trailer = b"trailer" in window or b"/Root" in window
    startxref = window.rfind(b"startxref")
    eof = window.rfind(b"%%EOF")

    if eof != -1:
        result.footer_present = True
        result.true_size = eof + 5
        result.evidence.append(f"%%EOF marker at offset {eof}")
    else:
        result.problems.append("no %%EOF marker, the document is truncated")

    if startxref != -1:
        result.evidence.append("startxref pointer present, the cross-reference table is reachable")
    else:
        result.problems.append("no startxref pointer, the page tree cannot be located without repair")

    pages = window.count(b"/Type /Page") + window.count(b"/Type/Page")
    if pages:
        result.metadata["page_objects"] = pages
        result.evidence.append(f"{pages} page objects present")

    if b"/Encrypt" in window:
        result.metadata["encrypted"] = True
        result.problems.append("document declares an /Encrypt dictionary, content needs a password")

    result.structure_complete = result.footer_present and startxref != -1 and has_trailer
    if result.structure_complete:
        result.confidence = 0.95
    elif result.footer_present:
        result.confidence = 0.65
    elif obj_count > 2:
        result.confidence = 0.5
        result.problems.append("object body survives but the trailer is gone, partial text recovery only")
    else:
        result.confidence = 0.3

    return result


def validate_isobmff(data: bytes) -> ValidationResult:
    """Walk ISO base media boxes to separate MP4 from MOV, M4A, HEIC and 3GP.

    These formats all carry an `ftyp` box at offset 4 and differ only in the
    brand string inside it, so the brand is the disambiguator. The presence of a
    `moov` box is what decides playability, and on a card pulled mid-recording
    the `moov` is exactly what is missing, because most cameras write it last.
    """
    if len(data) < 12 or data[4:8] != b"ftyp":
        return _fail("no ftyp box at offset 4", "mp4")

    brand = data[8:12].decode("ascii", errors="replace").strip()
    brand_map = {
        "isom": "mp4",
        "mp41": "mp4",
        "mp42": "mp4",
        "avc1": "mp4",
        "dash": "mp4",
        "qt": "mov",
        "M4A": "m4a",
        "M4V": "mp4",
        "3gp": "3gp",
        "heic": "heic",
        "heix": "heic",
        "mif1": "heic",
        "avif": "avif",
    }
    detected = brand_map.get(brand, brand_map.get(brand[:3], "mp4"))

    result = ValidationResult(
        format_detected=detected,
        header_valid=True,
        footer_present=False,
        structure_complete=False,
        confidence=0.5,
        evidence=[f"ftyp box declares major brand '{brand}'"],
        metadata={"major_brand": brand},
    )

    position = 0
    boxes: list[str] = []
    limit = min(len(data), MAX_VALIDATION_BYTES)

    while position + 8 <= limit and len(boxes) < 256:
        size = struct.unpack_from(">I", data, position)[0]
        box_type = data[position + 4 : position + 8].decode("ascii", errors="replace")
        boxes.append(box_type)
        if size == 0:
            result.true_size = limit
            break
        if size == 1:
            if position + 16 > limit:
                break
            size = struct.unpack_from(">Q", data, position + 8)[0]
        if size < 8:
            result.problems.append(f"box '{box_type}' declares an impossible size {size}")
            break
        position += size

    result.metadata["boxes"] = boxes[:32]
    has_moov = "moov" in boxes
    has_mdat = "mdat" in boxes

    if has_moov:
        result.evidence.append("moov box present, so track and timing metadata survived")
    else:
        result.problems.append(
            "no moov box, the container has no index and will not play without reconstruction"
        )
    if has_mdat:
        result.evidence.append("mdat box present, media payload is on disk")

    payload_zeroed = _mdat_zero_ratio(data, limit)
    if payload_zeroed > 0.3:
        result.problems.append(
            f"{payload_zeroed:.0%} of the media payload is zero-filled, so the recording was cut "
            f"short and the box header still claims data that was never written"
        )
        result.metadata["payload_zero_ratio"] = round(payload_zeroed, 3)

    if position <= limit and has_moov and has_mdat and payload_zeroed <= 0.3:
        result.footer_present = True
        result.structure_complete = True
        result.true_size = min(position, limit)
        result.confidence = 0.93
    elif has_mdat and payload_zeroed > 0.3:
        result.confidence = 0.6
    elif has_mdat:
        result.confidence = 0.55
    else:
        result.confidence = 0.35

    return result


def _mdat_zero_ratio(data: bytes, limit: int) -> float:
    """Measure how much of the media payload was never written.

    Box walking alone cannot see this. A camera writes the box headers with their
    final declared sizes and then streams the samples in, so a card pulled
    mid-recording leaves a container whose structure validates perfectly while
    most of the mdat payload is still zeroes. The declared size is what the
    camera intended, not what reached the card, and only the payload itself shows
    the difference.
    """
    position = 0
    while position + 8 <= limit:
        size = struct.unpack_from(">I", data, position)[0]
        box_type = data[position + 4 : position + 8]
        if size == 1:
            if position + 16 > limit:
                return 0.0
            size = struct.unpack_from(">Q", data, position + 8)[0]
            header = 16
        else:
            header = 8
        if size < 8:
            return 0.0

        if box_type == b"mdat":
            payload_start = position + header
            payload_end = min(position + size, limit)
            payload = data[payload_start:payload_end]
            if len(payload) < 512:
                return 0.0
            return payload.count(0) / len(payload)

        if size == 0:
            return 0.0
        position += size
    return 0.0


MPEG1_LAYER3_BITRATES = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
MPEG1_SAMPLE_RATES = (44100, 48000, 32000, 0)


def validate_mp3(data: bytes) -> ValidationResult:
    """Walk MP3 frame headers to find where the audio actually ends.

    MP3 has no end-of-file marker, which is why carvers routinely emit a three
    minute song as a 64 MB blob with whatever followed it on the card attached to
    the end. The format is self-delimiting in a different way: every frame header
    encodes the bitrate and sample rate that determine that frame's exact length,
    so walking the chain gives a byte-precise size.
    """
    offset = 0
    result = ValidationResult(
        format_detected="mp3",
        header_valid=False,
        footer_present=False,
        structure_complete=False,
        confidence=0.0,
        evidence=[],
    )

    if data[:3] == b"ID3" and len(data) >= 10:
        size = 0
        for byte in data[6:10]:
            size = (size << 7) | (byte & 0x7F)
        offset = 10 + size
        result.header_valid = True
        result.evidence.append(f"ID3v2 tag of {size} bytes present")
        title = re.search(rb"TIT2.{0,8}([ -~]{3,60})", data[:offset] or b"")
        if title:
            result.metadata["title"] = title.group(1).decode("ascii", errors="ignore").strip()

    frames = 0
    total_ms = 0.0
    limit = min(len(data), MAX_VALIDATION_BYTES)

    while offset + 4 <= limit:
        if data[offset] != 0xFF or (data[offset + 1] & 0xE0) != 0xE0:
            break
        version = (data[offset + 1] >> 3) & 0x03
        layer = (data[offset + 1] >> 1) & 0x03
        bitrate_index = (data[offset + 2] >> 4) & 0x0F
        sample_index = (data[offset + 2] >> 2) & 0x03
        padding = (data[offset + 2] >> 1) & 0x01

        if version != 3 or layer != 1 or bitrate_index in (0, 15) or sample_index == 3:
            break

        bitrate = MPEG1_LAYER3_BITRATES[bitrate_index] * 1000
        sample_rate = MPEG1_SAMPLE_RATES[sample_index]
        frame_length = (144 * bitrate // sample_rate) + padding
        if frame_length < 24:
            break

        total_ms += 1152 * 1000 / sample_rate
        frames += 1
        offset += frame_length

    result.metadata["frames"] = frames
    result.metadata["duration_seconds"] = round(total_ms / 1000, 2)

    if frames == 0:
        result.problems.append("no decodable MPEG audio frames follow the header")
        result.confidence = 0.2
        return result

    result.header_valid = True
    result.true_size = offset
    result.footer_present = True
    result.structure_complete = True
    result.confidence = 0.9
    result.evidence.append(
        f"{frames} MPEG-1 Layer III frames walked cleanly, "
        f"{result.metadata['duration_seconds']}s of audio ending at byte {offset}"
    )
    return result


def validate_sqlite(data: bytes) -> ValidationResult:
    if len(data) < 100 or data[:16] != b"SQLite format 3\x00":
        return _fail("missing SQLite header", "sqlite")
    page_size = struct.unpack_from(">H", data, 16)[0]
    page_size = 65536 if page_size == 1 else page_size
    page_count = struct.unpack_from(">I", data, 28)[0]
    declared = page_size * page_count
    result = ValidationResult(
        format_detected="sqlite",
        header_valid=True,
        footer_present=declared > 0 and len(data) >= declared,
        structure_complete=False,
        confidence=0.6,
        evidence=[f"page size {page_size}, header declares {page_count} pages"],
        metadata={"page_size": page_size, "page_count": page_count, "declared_size": declared},
    )
    if declared and len(data) >= declared:
        result.structure_complete = True
        result.true_size = declared
        result.confidence = 0.95
        result.evidence.append("all declared pages are present in the fragment")
    elif declared:
        result.problems.append(
            f"header declares {declared} bytes but only {len(data)} were carved, tables at the tail are lost"
        )
        result.confidence = 0.5
    return result


def validate_text(data: bytes, fmt: str) -> ValidationResult:
    """Validate text-shaped formats by actually trying to parse them."""
    try:
        text = data.decode("utf-8")
        decoded_clean = True
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        decoded_clean = False

    result = ValidationResult(
        format_detected=fmt,
        header_valid=True,
        footer_present=False,
        structure_complete=False,
        confidence=0.4,
        evidence=[],
        metadata={"length": len(data), "utf8_clean": decoded_clean},
    )
    if not decoded_clean:
        result.problems.append("byte stream is not valid UTF-8, the fragment spans a boundary")

    if fmt == "json":
        try:
            parsed = json.loads(text)
            result.structure_complete = True
            result.footer_present = True
            result.confidence = 0.96
            result.evidence.append("parses as valid JSON end to end")
            if isinstance(parsed, dict):
                result.metadata["top_level_keys"] = list(parsed.keys())[:20]
        except json.JSONDecodeError as error:
            result.problems.append(f"JSON parse fails at position {error.pos}: {error.msg}")
            result.confidence = 0.35
    elif fmt in ("xml", "html", "svg"):
        opens = text.count("<")
        closes = text.count(">")
        result.metadata["tags_open"] = opens
        result.metadata["tags_close"] = closes
        closing_tag = {"xml": None, "html": "</html>", "svg": "</svg>"}.get(fmt)
        if closing_tag and closing_tag in text.lower():
            result.structure_complete = True
            result.footer_present = True
            result.confidence = 0.9
            result.evidence.append(f"document closes with {closing_tag}")
        elif abs(opens - closes) <= 2 and opens > 4:
            result.confidence = 0.6
            result.evidence.append("angle bracket counts balance, markup looks intact")
        else:
            result.problems.append("markup is unbalanced, the document is cut off")
            result.confidence = 0.4

    title = re.search(r"<title>(.{1,120}?)</title>", text, re.IGNORECASE | re.DOTALL)
    if title:
        result.metadata["title"] = title.group(1).strip()

    return result


def validate_plaintext(data: bytes, fmt: str = "txt") -> ValidationResult:
    """Validate signature-less text by its byte distribution.

    Plain text and CSV have no magic bytes at all, so a carver that only knows
    signatures cannot see them and a validator that only knows signatures reports
    them as unidentifiable. They are still perfectly recoverable files, and they
    are identifiable another way: a high ratio of printable characters plus clean
    UTF-8 decoding is strong evidence on its own.

    The detected format normalises to `txt` regardless of what the filename said,
    because .log, .md, .ini and .cfg are all the same thing to a recovery tool:
    readable text with no internal structure to validate. The original extension
    is kept in metadata rather than promoted to a format, since a filename is a
    claim about content and this module only reports what the bytes support.
    """
    if not data:
        return _fail("empty fragment", fmt)

    printable = sum(1 for byte in data[:8192] if 32 <= byte <= 126 or byte in (9, 10, 13))
    ratio = printable / min(len(data), 8192)

    result = ValidationResult(
        format_detected="txt",
        header_valid=ratio > 0.85,
        footer_present=False,
        structure_complete=False,
        confidence=0.0,
        metadata={"printable_ratio": round(ratio, 3), "length": len(data), "named_extension": fmt},
    )

    if ratio <= 0.85:
        result.problems.append(
            f"only {ratio:.0%} of bytes are printable, so this is not readable text"
        )
        result.confidence = 0.15
        return result

    try:
        text = data.decode("utf-8")
        result.metadata["utf8_clean"] = True
        result.metadata["lines"] = text.count("\n") + 1
        result.evidence.append(f"decodes cleanly as UTF-8, {result.metadata['lines']} lines")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        result.metadata["utf8_clean"] = False
        result.problems.append("not valid UTF-8 end to end, the fragment may span a file boundary")

    delimiters = text[:4096].count(",") + text[:4096].count("\t")
    if delimiters > 40 and text.count("\n") > 3:
        result.format_detected = "csv"
        result.evidence.append("regular delimiter rhythm across lines, consistent with tabular data")

    result.footer_present = True
    result.structure_complete = True
    result.true_size = len(data)
    result.confidence = 0.88 if result.metadata.get("utf8_clean") else 0.6
    result.evidence.append(f"{ratio:.0%} printable characters")
    return result


def validate_generic(data: bytes, fmt: str, footer: bytes | None) -> ValidationResult:
    """Fallback validator for formats without a dedicated structural walker."""
    result = ValidationResult(
        format_detected=fmt,
        header_valid=True,
        footer_present=False,
        structure_complete=False,
        confidence=0.45,
        evidence=[f"magic bytes match the {fmt} signature"],
        metadata={"length": len(data)},
    )
    if footer:
        position = data.rfind(footer)
        if position > 0:
            result.footer_present = True
            result.structure_complete = True
            result.true_size = position + len(footer)
            result.confidence = 0.75
            result.evidence.append(f"expected trailer bytes found at offset {position}")
        else:
            result.problems.append("expected trailer bytes are absent, the fragment is incomplete")
    else:
        result.problems.append("no structural validator for this format, verdict rests on the header alone")
    return result


VALIDATORS = {
    "jpg": validate_jpeg,
    "png": validate_png,
    "gif": validate_gif,
    "pdf": validate_pdf,
    "sqlite": validate_sqlite,
    "mp3": validate_mp3,
}

ZIP_FORMATS = {"zip", "docx", "xlsx", "pptx", "apk", "jar", "epub", "odt", "ods", "ipa"}
ISOBMFF_FORMATS = {"mp4", "mov", "m4a", "3gp", "heic", "avif"}
TEXT_FORMATS = {"json", "xml", "html", "svg"}
PLAINTEXT_FORMATS = {"txt", "csv", "log", "md", "ini", "cfg", "conf", "tsv", "unknown"}


def validate(data: bytes, fmt: str, footer: bytes | None = None) -> ValidationResult:
    """Dispatch a fragment to the right structural validator."""
    if not data:
        return _fail("empty fragment", fmt)
    if fmt in VALIDATORS:
        return VALIDATORS[fmt](data)
    if fmt in ZIP_FORMATS:
        return validate_zip(data)
    if fmt in ISOBMFF_FORMATS:
        return validate_isobmff(data)
    if fmt in TEXT_FORMATS:
        return validate_text(data, fmt)
    if fmt in PLAINTEXT_FORMATS:
        return validate_plaintext(data, "txt" if fmt == "unknown" else fmt)
    return validate_generic(data, fmt, footer)
