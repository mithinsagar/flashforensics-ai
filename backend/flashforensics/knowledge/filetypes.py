"""Natural-language descriptions of file formats, indexed for retrieval.

The classifier does not match on these strings. It embeds them and retrieves the
nearest neighbours to a description of the fragment's observed characteristics,
which is what lets an unfamiliar or partially damaged fragment still land near
the right family. The text of each entry is written the way it is on purpose:
it names the structural markers a validator would find and the situations the
format turns up in, because those are the terms an observation gets phrased in.

Sixty-eight formats are described here, weighted toward what actually appears on
consumer flash media: camera output, phone media, office documents and the
zip-container family that causes most misidentification.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FileTypeDoc:
    """One indexed description of a format."""

    extension: str
    name: str
    category: str
    description: str
    header_note: str
    typical_size: str
    ambiguity_note: str = ""

    def to_document(self) -> str:
        parts = [
            f"{self.name} ({self.extension}). Category: {self.category}.",
            self.description,
            f"Header: {self.header_note}",
            f"Typical size: {self.typical_size}",
        ]
        if self.ambiguity_note:
            parts.append(f"Disambiguation: {self.ambiguity_note}")
        return " ".join(parts)

    def to_metadata(self) -> dict:
        return {
            "extension": self.extension,
            "name": self.name,
            "category": self.category,
            "ambiguous": bool(self.ambiguity_note),
        }


FILE_TYPES: tuple[FileTypeDoc, ...] = (
    FileTypeDoc(
        "jpg", "JPEG image", "image",
        "Lossy compressed photograph, the default output of every consumer camera and phone. "
        "Content is a chain of length-prefixed segments followed by entropy-coded scan data. "
        "Entropy sits between 7.5 and 8.0 because the DCT coefficients are Huffman coded.",
        "FF D8 FF at offset 0, ending with FF D9",
        "0.5 MB to 12 MB for a modern camera, 40 KB to 400 KB for a thumbnail or web image",
    ),
    FileTypeDoc(
        "png", "PNG image", "image",
        "Lossless compressed raster image used for screenshots, UI assets and diagrams. "
        "Built from length-prefixed chunks each carrying a CRC32, so corruption is provable "
        "rather than inferred. Screenshots dominate this format on phone storage.",
        "89 50 4E 47 0D 0A 1A 0A, ending with an IEND chunk",
        "20 KB to 4 MB, screenshots usually under 1 MB",
    ),
    FileTypeDoc(
        "gif", "GIF image", "image",
        "Palette-indexed image format, almost always animated in modern use. Carries a global "
        "colour table and a stream of frame blocks terminated by a single trailer byte.",
        "GIF87a or GIF89a, terminated by 0x3B",
        "50 KB to 5 MB",
    ),
    FileTypeDoc(
        "bmp", "Windows bitmap", "image",
        "Uncompressed raster image. Entropy is unusually low for an image format because pixel "
        "data is stored raw, which makes it easy to confuse with structured binary data.",
        "BM followed by a 32-bit file size",
        "500 KB to 20 MB, large for its pixel dimensions",
    ),
    FileTypeDoc(
        "tif", "TIFF image", "image",
        "Tag-based container for high bit depth images, used by scanners, medical equipment and "
        "professional photography. Directory entries can point anywhere in the file.",
        "II 2A 00 little endian or MM 00 2A big endian",
        "5 MB to 200 MB",
        "Shares its opening bytes with camera raw formats like CR2, NEF and DNG, which are TIFF "
        "variants with vendor-specific tags.",
    ),
    FileTypeDoc(
        "webp", "WebP image", "image",
        "Modern web image format carried inside a RIFF container, supporting both lossy and "
        "lossless modes plus animation.",
        "RIFF then a WEBP fourcc at offset 8",
        "30 KB to 2 MB",
        "The RIFF header alone is shared with AVI video and WAV audio; the fourcc at offset 8 "
        "is what separates them.",
    ),
    FileTypeDoc(
        "heic", "HEIF image", "image",
        "High efficiency image container used by iPhone photography since iOS 11. Wraps HEVC "
        "encoded stills in the ISO base media box structure used by MP4.",
        "ftyp box at offset 4 with a heic, heix or mif1 brand",
        "1 MB to 5 MB",
        "Shares the ISO base media ftyp box with MP4, MOV, M4A and AVIF; only the brand differs.",
    ),
    FileTypeDoc(
        "avif", "AVIF image", "image",
        "AV1-encoded still image in an ISO base media container, increasingly common as a web "
        "delivery format.",
        "ftyp box at offset 4 with an avif brand",
        "20 KB to 1 MB",
        "Another ISO base media brand, distinguishable from HEIC and MP4 only by the ftyp brand.",
    ),
    FileTypeDoc(
        "cr2", "Canon raw image", "image",
        "Canon's raw sensor capture format, a TIFF variant carrying an uncompressed or lightly "
        "compressed sensor dump plus an embedded JPEG preview.",
        "II 2A 00 10 00 00 00 CR",
        "20 MB to 40 MB",
        "The embedded JPEG preview inside a raw file frequently gets carved as a separate JPEG.",
    ),
    FileTypeDoc(
        "nef", "Nikon raw image", "image",
        "Nikon's raw sensor format, another TIFF derivative with proprietary maker note tags.",
        "MM 00 2A big endian TIFF opening",
        "20 MB to 50 MB",
        "Indistinguishable from generic TIFF without inspecting the maker note tags.",
    ),
    FileTypeDoc(
        "dng", "Adobe digital negative", "image",
        "Open raw format, a documented TIFF profile intended as an archival container for "
        "sensor data from any camera.",
        "II 2A 00 08 00 00 00",
        "15 MB to 60 MB",
    ),
    FileTypeDoc(
        "psd", "Photoshop document", "image",
        "Layered image document holding per-layer pixel data, masks and adjustment metadata. "
        "Very large relative to its visible output.",
        "8BPS",
        "10 MB to 500 MB",
    ),
    FileTypeDoc(
        "svg", "SVG vector image", "image",
        "XML-based vector graphic. Plain text, so entropy is in the text band rather than the "
        "compressed band, and it is readable directly in a fragment preview.",
        "an <svg element, often after an XML declaration",
        "2 KB to 500 KB",
    ),
    FileTypeDoc(
        "ico", "Windows icon", "image",
        "Container holding several small bitmap or PNG images at different resolutions.",
        "00 00 01 00",
        "5 KB to 300 KB",
    ),
    FileTypeDoc(
        "pdf", "PDF document", "document",
        "Page description document with an object graph, a cross-reference table and a trailer. "
        "Text and fonts are usually compressed while structure markers stay readable, producing "
        "a distinctive mixed entropy profile.",
        "%PDF- followed by a version, ending with %%EOF",
        "100 KB to 20 MB",
    ),
    FileTypeDoc(
        "docx", "Word document", "document",
        "Modern Word document, technically a ZIP archive of XML parts under the Open Packaging "
        "Conventions. The document body lives at word/document.xml.",
        "PK 03 04, the standard ZIP local file header",
        "20 KB to 5 MB",
        "Identified only by the presence of a word/ directory inside the archive. Without reading "
        "entry names it is indistinguishable from XLSX, PPTX, APK, JAR, EPUB and plain ZIP.",
    ),
    FileTypeDoc(
        "xlsx", "Excel workbook", "document",
        "Spreadsheet workbook stored as a ZIP of XML parts. Sheet data lives under xl/worksheets "
        "and shared text under xl/sharedStrings.xml.",
        "PK 03 04",
        "15 KB to 50 MB",
        "Identified by an xl/ directory inside the archive, otherwise identical to any other "
        "Open Packaging Conventions container.",
    ),
    FileTypeDoc(
        "pptx", "PowerPoint presentation", "document",
        "Slide deck stored as a ZIP of XML parts with embedded media. Slides live under "
        "ppt/slides and the deck manifest at ppt/presentation.xml.",
        "PK 03 04",
        "500 KB to 100 MB, dominated by embedded images",
        "Identified by a ppt/ directory inside the archive.",
    ),
    FileTypeDoc(
        "doc", "Legacy Word document", "document",
        "Pre-2007 Word document stored in the OLE compound binary format, a small FAT-like "
        "filesystem inside a single file.",
        "D0 CF 11 E0 A1 B1 1A E1",
        "30 KB to 10 MB",
        "The OLE compound header is shared byte for byte with legacy XLS, PPT and Outlook MSG "
        "files; only the internal stream names separate them.",
    ),
    FileTypeDoc(
        "xls", "Legacy Excel workbook", "document",
        "Pre-2007 spreadsheet in the OLE compound binary format with a Workbook stream inside.",
        "D0 CF 11 E0 A1 B1 1A E1",
        "20 KB to 20 MB",
        "Shares the OLE compound header with DOC, PPT and MSG.",
    ),
    FileTypeDoc(
        "ppt", "Legacy PowerPoint deck", "document",
        "Pre-2007 presentation in the OLE compound binary format.",
        "D0 CF 11 E0 A1 B1 1A E1",
        "500 KB to 50 MB",
        "Shares the OLE compound header with DOC, XLS and MSG.",
    ),
    FileTypeDoc(
        "rtf", "Rich text document", "document",
        "Plain text markup format for formatted documents, readable directly without decoding.",
        "{\\rtf1",
        "10 KB to 5 MB",
    ),
    FileTypeDoc(
        "epub", "EPUB book", "document",
        "Electronic book, a ZIP archive holding XHTML chapters and an OPF manifest, with a "
        "mimetype entry stored uncompressed as the first member.",
        "PK 03 04 with a mimetype entry declaring application/epub+zip",
        "300 KB to 20 MB",
        "Another ZIP container. The uncompressed mimetype entry at the start is the reliable tell.",
    ),
    FileTypeDoc(
        "odt", "OpenDocument text", "document",
        "LibreOffice text document, a ZIP archive with content.xml, styles.xml and a mimetype "
        "entry.",
        "PK 03 04",
        "20 KB to 5 MB",
        "Distinguished from OOXML documents by content.xml rather than word/document.xml.",
    ),
    FileTypeDoc(
        "zip", "ZIP archive", "archive",
        "General purpose compressed archive with local file headers, a central directory and an "
        "end-of-central-directory record. The base format for a whole family of document types.",
        "PK 03 04, with PK 05 06 marking the end of the central directory",
        "any size",
        "A generic ZIP is what remains after ruling out every application-specific container "
        "that uses the same structure.",
    ),
    FileTypeDoc(
        "apk", "Android application package", "application",
        "Android app bundle: a ZIP holding compiled Dalvik bytecode, a binary XML manifest, "
        "compiled resources and a signature block.",
        "PK 03 04",
        "5 MB to 150 MB",
        "Identified by AndroidManifest.xml together with one or more classes.dex entries. This "
        "is the classic false positive for a plain ZIP.",
    ),
    FileTypeDoc(
        "jar", "Java archive", "application",
        "Java class bundle, a ZIP with a META-INF/MANIFEST.MF describing the entry point.",
        "PK 03 04",
        "10 KB to 100 MB",
        "Identified by META-INF/MANIFEST.MF. An APK is technically also a JAR, so manifest "
        "checks must run in the right order.",
    ),
    FileTypeDoc(
        "ipa", "iOS application archive", "application",
        "iOS app bundle, a ZIP containing a Payload directory with a signed .app bundle inside.",
        "PK 03 04",
        "20 MB to 500 MB",
        "Identified by a Payload/ prefix on the archive entries.",
    ),
    FileTypeDoc(
        "rar", "RAR archive", "archive",
        "Proprietary compressed archive supporting solid compression and recovery records.",
        "Rar! 1A 07",
        "any size",
    ),
    FileTypeDoc(
        "7z", "7-Zip archive", "archive",
        "High ratio LZMA compressed archive with the file index stored at the end, which means a "
        "truncated 7z loses its listing entirely.",
        "37 7A BC AF 27 1C",
        "any size",
    ),
    FileTypeDoc(
        "gz", "Gzip stream", "archive",
        "Single-member DEFLATE stream, usually wrapping a tar archive. Carries the original "
        "filename in its header when one was set.",
        "1F 8B 08",
        "any size",
    ),
    FileTypeDoc(
        "bz2", "Bzip2 archive", "archive",
        "Block-sorting compressed stream, slower and denser than gzip.",
        "BZh followed by a block size digit",
        "any size",
    ),
    FileTypeDoc(
        "xz", "XZ archive", "archive",
        "LZMA2 container with integrity checks, common for source distributions.",
        "FD 37 7A 58 5A 00",
        "any size",
    ),
    FileTypeDoc(
        "zst", "Zstandard archive", "archive",
        "Fast modern compression format increasingly used in place of gzip.",
        "28 B5 2F FD",
        "any size",
    ),
    FileTypeDoc(
        "tar", "Tar archive", "archive",
        "Uncompressed sequential archive of 512-byte records. Entropy matches whatever it "
        "contains rather than the container.",
        "ustar at offset 257 inside the first header record",
        "any size",
    ),
    FileTypeDoc(
        "cab", "Windows cabinet", "archive",
        "Microsoft installer archive format used by Windows setup packages.",
        "MSCF",
        "100 KB to 500 MB",
    ),
    FileTypeDoc(
        "mp4", "MPEG-4 video", "video",
        "The dominant consumer video container. Built from nested boxes: ftyp declares the brand, "
        "moov carries the track index and mdat holds the encoded media.",
        "an ftyp box at offset 4 with an isom, mp41, mp42 or avc1 brand",
        "10 MB to 4 GB",
        "Cameras write the moov box last, so a recording interrupted by card removal has an mdat "
        "full of video and no index, which is the single most common unplayable-video case.",
    ),
    FileTypeDoc(
        "mov", "QuickTime video", "video",
        "Apple's container, structurally the same box format as MP4 with a qt brand.",
        "ftyp box at offset 4 with a qt brand",
        "10 MB to 4 GB",
        "Separated from MP4 only by the ftyp brand string.",
    ),
    FileTypeDoc(
        "m4a", "MPEG-4 audio", "audio",
        "AAC audio in an ISO base media container, the format of iTunes purchases and voice memos.",
        "ftyp box at offset 4 with an M4A brand",
        "2 MB to 100 MB",
        "Same box structure as MP4 video, so the brand is the only distinguishing marker.",
    ),
    FileTypeDoc(
        "3gp", "3GPP video", "video",
        "Mobile video container from the feature phone era, still produced by some devices.",
        "ftyp box at offset 4 with a 3gp brand",
        "1 MB to 100 MB",
        "Another ISO base media brand variant.",
    ),
    FileTypeDoc(
        "avi", "AVI video", "video",
        "Legacy RIFF-based video container, still emitted by dashcams and older action cameras.",
        "RIFF with an AVI fourcc at offset 8",
        "50 MB to 4 GB",
        "The bare RIFF header is shared with WAV audio and WebP images.",
    ),
    FileTypeDoc(
        "mkv", "Matroska video", "video",
        "Flexible EBML-based container able to hold effectively any codec plus subtitles and "
        "chapters.",
        "1A 45 DF A3",
        "100 MB to 20 GB",
        "Shares the EBML header with WebM, which is a constrained Matroska profile.",
    ),
    FileTypeDoc(
        "webm", "WebM video", "video",
        "Web-oriented subset of Matroska restricted to royalty-free codecs.",
        "1A 45 DF A3",
        "5 MB to 500 MB",
        "Structurally a Matroska file; the codec IDs inside are the difference.",
    ),
    FileTypeDoc(
        "flv", "Flash video", "video",
        "Legacy streaming container occasionally still found in archived downloads.",
        "FLV 01",
        "5 MB to 500 MB",
    ),
    FileTypeDoc(
        "mp3", "MP3 audio", "audio",
        "MPEG-1 Layer III audio, a sequence of self-describing frames. No end marker exists, so "
        "the only precise way to size one is to walk the frame headers.",
        "ID3 tag, or a frame sync of FF followed by three set bits",
        "3 MB to 15 MB for a song",
        "Without frame walking, carvers overshoot MP3 extents badly because nothing marks the end.",
    ),
    FileTypeDoc(
        "wav", "WAV audio", "audio",
        "Uncompressed PCM audio in a RIFF container. Entropy is moderate rather than high because "
        "samples are not compressed.",
        "RIFF with a WAVE fourcc at offset 8",
        "10 MB to 500 MB",
        "Shares the RIFF header with AVI and WebP.",
    ),
    FileTypeDoc(
        "flac", "FLAC audio", "audio",
        "Lossless compressed audio with per-frame CRCs and an MD5 of the decoded stream in its "
        "header, which makes integrity verifiable.",
        "fLaC",
        "20 MB to 60 MB per album track",
    ),
    FileTypeDoc(
        "ogg", "Ogg container", "audio",
        "Page-oriented streaming container usually carrying Vorbis or Opus audio.",
        "OggS",
        "3 MB to 20 MB",
    ),
    FileTypeDoc(
        "mid", "MIDI sequence", "audio",
        "Musical note event data rather than sampled audio. Extremely small and low entropy.",
        "MThd",
        "2 KB to 100 KB",
    ),
    FileTypeDoc(
        "exe", "Windows executable", "application",
        "Portable Executable binary opening with a DOS stub. Sections have sharply different "
        "entropy, and a packed or encrypted section stands out clearly.",
        "MZ, with a PE signature at the offset stored at 0x3C",
        "50 KB to 200 MB",
    ),
    FileTypeDoc(
        "elf", "ELF binary", "application",
        "Executable and Linkable Format, the native binary format on Linux and Android.",
        "7F 45 4C 46",
        "10 KB to 100 MB",
    ),
    FileTypeDoc(
        "dex", "Dalvik executable", "application",
        "Compiled Android bytecode, normally found inside an APK rather than standing alone.",
        "dex 0A followed by a version",
        "500 KB to 20 MB",
        "Finding a bare classes.dex usually means an APK was fragmented across the volume.",
    ),
    FileTypeDoc(
        "class", "Java class file", "application",
        "Single compiled Java class, normally packaged inside a JAR.",
        "CA FE BA BE",
        "1 KB to 500 KB",
    ),
    FileTypeDoc(
        "wasm", "WebAssembly module", "application",
        "Portable binary module for browser and edge runtimes.",
        "00 61 73 6D",
        "10 KB to 20 MB",
    ),
    FileTypeDoc(
        "sqlite", "SQLite database", "database",
        "Self-contained relational database in a single file. The header declares a page size "
        "and page count, so its true length is verifiable arithmetic rather than a guess. Phone "
        "messages, browser history and app state all live in these.",
        "SQLite format 3 followed by a null byte",
        "100 KB to 2 GB",
    ),
    FileTypeDoc(
        "pcap", "Packet capture", "database",
        "Network traffic capture with a global header followed by timestamped packet records.",
        "D4 C3 B2 A1 or its byte-swapped form",
        "1 MB to 10 GB",
    ),
    FileTypeDoc(
        "json", "JSON document", "text",
        "Structured text data. Validity is decidable by parsing, so a truncated fragment is "
        "provably incomplete rather than probably incomplete.",
        "an opening brace or bracket, often with a quoted key",
        "1 KB to 100 MB",
    ),
    FileTypeDoc(
        "xml", "XML document", "text",
        "Structured markup used for configuration, feeds and document formats.",
        "<?xml declaration",
        "1 KB to 50 MB",
    ),
    FileTypeDoc(
        "html", "HTML document", "text",
        "Web page markup, often saved by browsers alongside an assets directory.",
        "<!DOCTYPE html or a bare <html element",
        "5 KB to 5 MB",
    ),
    FileTypeDoc(
        "csv", "Comma separated values", "text",
        "Tabular plain text. Highly regular, with a repeating delimiter rhythm that distinguishes "
        "it from prose at the same entropy.",
        "no magic bytes at all, identified by structure",
        "1 KB to 1 GB",
        "Has no signature, so it is only ever found by structural inspection of text regions.",
    ),
    FileTypeDoc(
        "txt", "Plain text", "text",
        "Unstructured text. Entropy between 4 and 5 bits per byte with a very high printable "
        "character ratio.",
        "no magic bytes",
        "1 KB to 10 MB",
        "Like CSV, has no signature and can only be recognised by its byte distribution.",
    ),
    FileTypeDoc(
        "ttf", "TrueType font", "font",
        "Outline font with a table directory. Frequently embedded inside documents and apps.",
        "00 01 00 00 00",
        "50 KB to 20 MB",
    ),
    FileTypeDoc(
        "otf", "OpenType font", "font",
        "PostScript-flavoured outline font sharing TrueType's table structure.",
        "OTTO",
        "50 KB to 10 MB",
    ),
    FileTypeDoc(
        "woff2", "Web font", "font",
        "Brotli-compressed font for web delivery.",
        "wOF2",
        "10 KB to 2 MB",
    ),
    FileTypeDoc(
        "iso", "ISO 9660 disc image", "archive",
        "Optical disc filesystem image with its identifier deep inside the volume descriptor "
        "rather than at the start.",
        "CD001 at offset 32769",
        "100 MB to 8 GB",
    ),
    FileTypeDoc(
        "vmdk", "VMware virtual disk", "archive",
        "Virtual machine disk image, often sparse and mostly empty.",
        "KDMV",
        "1 GB to 500 GB",
    ),
    FileTypeDoc(
        "luks", "LUKS encrypted volume", "encrypted",
        "Linux disk encryption header. Payload is indistinguishable from random data, so entropy "
        "sits at the theoretical maximum and chi-square uniformity is near perfect.",
        "LUKS BA BE",
        "any size",
        "High entropy alone cannot separate encrypted data from compressed data; the chi-square "
        "residual is what does it, because compression leaves structure that encryption does not.",
    ),
    FileTypeDoc(
        "kdbx", "KeePass database", "encrypted",
        "Encrypted password vault. Like any encrypted blob its payload is statistically uniform.",
        "03 D9 A2 9A",
        "10 KB to 10 MB",
    ),
    FileTypeDoc(
        "unknown", "Unrecognised binary", "unknown",
        "A fragment whose byte distribution matches no known format. High entropy with no header "
        "usually means the middle of a compressed file whose beginning was lost, which is common "
        "when a file is fragmented across non-adjacent clusters.",
        "no recognisable header",
        "any size",
        "Middle-of-file fragments are the largest single category in real recovery work and the "
        "reason a header-only carver produces so much noise.",
    ),
)


def by_extension(extension: str) -> FileTypeDoc | None:
    for entry in FILE_TYPES:
        if entry.extension == extension:
            return entry
    return None


def ambiguous_types() -> list[FileTypeDoc]:
    return [entry for entry in FILE_TYPES if entry.ambiguity_note]


def as_documents() -> tuple[list[str], list[str], list[dict]]:
    """Return (ids, documents, metadatas) ready for a vector store."""
    ids = [f"filetype::{entry.extension}::{index}" for index, entry in enumerate(FILE_TYPES)]
    documents = [entry.to_document() for entry in FILE_TYPES]
    metadatas = [entry.to_metadata() for entry in FILE_TYPES]
    return ids, documents, metadatas
