"""Magic byte signature database used to locate file boundaries in raw sectors.

Carving works because most formats announce themselves in their first few bytes.
It fails to be useful on its own because those announcements are not unique: the
four bytes 50 4B 03 04 introduce ZIP, DOCX, XLSX, PPTX, APK, JAR, EPUB and ODT
alike, since all of them are zip containers with different things inside. The
same problem shows up with the ISO base media format, where MP4, MOV, M4A, 3GP
and HEIC share a common ftyp box.

So a signature match here produces a *candidate*, never a conclusion. The
`ambiguity_group` field is what tells the classifier that a fragment needs to be
disambiguated by looking at its internal structure rather than accepted at face
value.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Signature:
    """One magic byte pattern and everything known about the format behind it."""

    extension: str
    label: str
    header: bytes
    category: str
    footer: bytes | None = None
    header_offset: int = 0
    max_size: int = 64 * 1024 * 1024
    ambiguity_group: str | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_ambiguous(self) -> bool:
        return self.ambiguity_group is not None


ZIP_FAMILY = "zip_container"
ISOBMFF_FAMILY = "iso_base_media"
RIFF_FAMILY = "riff_container"
OLE_FAMILY = "ole_compound"
EBML_FAMILY = "ebml_container"

SIGNATURES: tuple[Signature, ...] = (
    Signature("jpg", "JPEG image", b"\xFF\xD8\xFF", "image", b"\xFF\xD9", max_size=32 * 1024 * 1024),
    Signature("png", "PNG image", b"\x89PNG\r\n\x1a\n", "image", b"IEND\xaeB`\x82"),
    Signature("gif", "GIF image", b"GIF89a", "image", b"\x00\x3B"),
    Signature("gif", "GIF image (87a)", b"GIF87a", "image", b"\x00\x3B"),
    Signature("bmp", "Bitmap image", b"BM", "image", max_size=64 * 1024 * 1024),
    Signature("tif", "TIFF image (little endian)", b"II\x2a\x00", "image"),
    Signature("tif", "TIFF image (big endian)", b"MM\x00\x2a", "image"),
    Signature("webp", "WebP image", b"RIFF", "image", header_offset=0, ambiguity_group=RIFF_FAMILY),
    Signature("heic", "HEIF image", b"ftypheic", "image", header_offset=4, ambiguity_group=ISOBMFF_FAMILY),
    Signature("cr2", "Canon raw image", b"II\x2a\x00\x10\x00\x00\x00CR", "image"),
    Signature("nef", "Nikon raw image", b"MM\x00\x2a\x00\x00\x00\x08\x00", "image"),
    Signature("dng", "Adobe digital negative", b"II\x2a\x00\x08\x00\x00\x00", "image"),
    Signature("psd", "Photoshop document", b"8BPS", "image"),
    Signature("ico", "Windows icon", b"\x00\x00\x01\x00", "image", max_size=1024 * 1024),
    Signature("avif", "AVIF image", b"ftypavif", "image", header_offset=4, ambiguity_group=ISOBMFF_FAMILY),
    Signature("pdf", "PDF document", b"%PDF-", "document", b"%%EOF"),
    Signature("rtf", "Rich text document", b"{\\rtf", "document", b"}"),
    Signature("doc", "Legacy Word document", b"\xD0\xCF\x11\xE0\xA1\xB1\x1a\xE1", "document", ambiguity_group=OLE_FAMILY),
    Signature("xls", "Legacy Excel workbook", b"\xD0\xCF\x11\xE0\xA1\xB1\x1a\xE1", "document", ambiguity_group=OLE_FAMILY),
    Signature("ppt", "Legacy PowerPoint deck", b"\xD0\xCF\x11\xE0\xA1\xB1\x1a\xE1", "document", ambiguity_group=OLE_FAMILY),
    Signature("msg", "Outlook message", b"\xD0\xCF\x11\xE0\xA1\xB1\x1a\xE1", "document", ambiguity_group=OLE_FAMILY),
    Signature("zip", "ZIP archive", b"PK\x03\x04", "archive", b"PK\x05\x06", ambiguity_group=ZIP_FAMILY),
    Signature("docx", "Word document", b"PK\x03\x04", "document", b"PK\x05\x06", ambiguity_group=ZIP_FAMILY),
    Signature("xlsx", "Excel workbook", b"PK\x03\x04", "document", b"PK\x05\x06", ambiguity_group=ZIP_FAMILY),
    Signature("pptx", "PowerPoint deck", b"PK\x03\x04", "document", b"PK\x05\x06", ambiguity_group=ZIP_FAMILY),
    Signature("apk", "Android package", b"PK\x03\x04", "application", b"PK\x05\x06", ambiguity_group=ZIP_FAMILY),
    Signature("jar", "Java archive", b"PK\x03\x04", "application", b"PK\x05\x06", ambiguity_group=ZIP_FAMILY),
    Signature("epub", "EPUB book", b"PK\x03\x04", "document", b"PK\x05\x06", ambiguity_group=ZIP_FAMILY),
    Signature("odt", "OpenDocument text", b"PK\x03\x04", "document", b"PK\x05\x06", ambiguity_group=ZIP_FAMILY),
    Signature("ods", "OpenDocument spreadsheet", b"PK\x03\x04", "document", b"PK\x05\x06", ambiguity_group=ZIP_FAMILY),
    Signature("ipa", "iOS application", b"PK\x03\x04", "application", b"PK\x05\x06", ambiguity_group=ZIP_FAMILY),
    Signature("rar", "RAR archive", b"Rar!\x1a\x07", "archive"),
    Signature("7z", "7-Zip archive", b"7z\xBC\xAF\x27\x1C", "archive"),
    Signature("gz", "Gzip stream", b"\x1F\x8B\x08", "archive"),
    Signature("bz2", "Bzip2 archive", b"BZh", "archive"),
    Signature("xz", "XZ archive", b"\xFD7zXZ\x00", "archive"),
    Signature("zst", "Zstandard archive", b"\x28\xB5\x2F\xFD", "archive"),
    Signature("tar", "Tar archive", b"ustar", "archive", header_offset=257),
    Signature("cab", "Windows cabinet", b"MSCF", "archive"),
    Signature("dmg", "Apple disk image", b"koly", "archive"),
    Signature("mp4", "MPEG-4 video", b"ftyp", "video", header_offset=4, max_size=2 * 1024 * 1024 * 1024, ambiguity_group=ISOBMFF_FAMILY),
    Signature("mov", "QuickTime video", b"ftypqt", "video", header_offset=4, ambiguity_group=ISOBMFF_FAMILY),
    Signature("m4a", "MPEG-4 audio", b"ftypM4A", "audio", header_offset=4, ambiguity_group=ISOBMFF_FAMILY),
    Signature("3gp", "3GPP video", b"ftyp3g", "video", header_offset=4, ambiguity_group=ISOBMFF_FAMILY),
    Signature("avi", "AVI video", b"RIFF", "video", ambiguity_group=RIFF_FAMILY),
    Signature("wav", "WAV audio", b"RIFF", "audio", ambiguity_group=RIFF_FAMILY),
    Signature("mkv", "Matroska video", b"\x1A\x45\xDF\xA3", "video", ambiguity_group=EBML_FAMILY),
    Signature("webm", "WebM video", b"\x1A\x45\xDF\xA3", "video", ambiguity_group=EBML_FAMILY),
    Signature("flv", "Flash video", b"FLV\x01", "video"),
    Signature("mpg", "MPEG program stream", b"\x00\x00\x01\xBA", "video"),
    Signature("mp3", "MP3 audio (ID3)", b"ID3", "audio"),
    Signature("flac", "FLAC audio", b"fLaC", "audio"),
    Signature("ogg", "Ogg container", b"OggS", "audio"),
    Signature("mid", "MIDI sequence", b"MThd", "audio"),
    Signature("aiff", "AIFF audio", b"FORM", "audio"),
    Signature("exe", "Windows executable", b"MZ", "application"),
    Signature("elf", "ELF binary", b"\x7FELF", "application"),
    Signature("class", "Java class file", b"\xCA\xFE\xBA\xBE", "application"),
    Signature("wasm", "WebAssembly module", b"\x00asm", "application"),
    Signature("dex", "Android Dalvik executable", b"dex\n", "application"),
    Signature("sqlite", "SQLite database", b"SQLite format 3\x00", "database"),
    Signature("pcap", "Packet capture", b"\xD4\xC3\xB2\xA1", "database"),
    Signature("pcapng", "Packet capture (next gen)", b"\x0A\x0D\x0D\x0A", "database"),
    Signature("ttf", "TrueType font", b"\x00\x01\x00\x00\x00", "font"),
    Signature("otf", "OpenType font", b"OTTO", "font"),
    Signature("woff", "Web font", b"wOFF", "font"),
    Signature("woff2", "Web font 2", b"wOF2", "font"),
    Signature("xml", "XML document", b"<?xml", "text"),
    Signature("html", "HTML document", b"<!DOCTYPE html", "text"),
    Signature("html", "HTML document (bare)", b"<html", "text"),
    Signature("json", "JSON document", b'{"', "text", max_size=16 * 1024 * 1024),
    Signature("svg", "SVG vector image", b"<svg", "image"),
    Signature("iso", "ISO 9660 disc image", b"CD001", "archive", header_offset=32769),
    Signature("vmdk", "VMware disk", b"KDMV", "archive"),
    Signature("vhd", "Virtual hard disk", b"conectix", "archive"),
    Signature("luks", "LUKS encrypted volume", b"LUKS\xBA\xBE", "encrypted"),
    Signature("kdbx", "KeePass database", b"\x03\xD9\xA2\x9A", "encrypted"),
)

MIN_RELIABLE_HEADER = 4
"""Headers shorter than this match by chance inside compressed data.

A two-byte pattern appears roughly once every 65 KB of random bytes, so on a
64 GB card a signature like PGP's 85 02 would produce a million hits and no
information. Short signatures stay in the table because they are real, but the
carver requires cluster alignment before it will accept one.
"""


def unique_extensions() -> list[str]:
    seen: list[str] = []
    for signature in SIGNATURES:
        if signature.extension not in seen:
            seen.append(signature.extension)
    return seen


def signatures_by_group(group: str) -> list[Signature]:
    return [signature for signature in SIGNATURES if signature.ambiguity_group == group]


def distinct_headers() -> list[tuple[bytes, int, list[Signature]]]:
    """Collapse the table into unique (header, offset) probes.

    Several formats share a header, so scanning once per signature would read the
    same bytes a dozen times. Grouping first means the carver does one pass per
    distinct pattern and carries the full candidate list forward.
    """
    grouped: dict[tuple[bytes, int], list[Signature]] = {}
    for signature in SIGNATURES:
        grouped.setdefault((signature.header, signature.header_offset), []).append(signature)
    return [(header, offset, members) for (header, offset), members in grouped.items()]


def lookup(extension: str) -> Signature | None:
    for signature in SIGNATURES:
        if signature.extension == extension:
            return signature
    return None


def category_of(extension: str) -> str:
    signature = lookup(extension)
    return signature.category if signature else "unknown"


MIME_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "webp": "image/webp",
    "heic": "image/heic",
    "avif": "image/avif",
    "svg": "image/svg+xml",
    "psd": "image/vnd.adobe.photoshop",
    "pdf": "application/pdf",
    "rtf": "application/rtf",
    "doc": "application/msword",
    "xls": "application/vnd.ms-excel",
    "ppt": "application/vnd.ms-powerpoint",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "zip": "application/zip",
    "apk": "application/vnd.android.package-archive",
    "jar": "application/java-archive",
    "epub": "application/epub+zip",
    "rar": "application/vnd.rar",
    "7z": "application/x-7z-compressed",
    "gz": "application/gzip",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
    "avi": "video/x-msvideo",
    "mkv": "video/x-matroska",
    "webm": "video/webm",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "m4a": "audio/mp4",
    "sqlite": "application/vnd.sqlite3",
    "exe": "application/vnd.microsoft.portable-executable",
    "elf": "application/x-elf",
    "json": "application/json",
    "xml": "application/xml",
    "html": "text/html",
}


def mime_for(extension: str) -> str:
    return MIME_TYPES.get(extension, "application/octet-stream")
