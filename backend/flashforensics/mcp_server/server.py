"""MCP server exposing the disk primitives as tools any LLM client can call.

The pipeline in this repository is one opinionated way to use these primitives.
Exposing them over the Model Context Protocol makes them available to any client
that speaks it, so an analyst working in Claude Desktop or an agent framework can
drive an investigation conversationally, in an order the fixed pipeline does not
anticipate: read this sector, what is the entropy around cluster 9000, carve just
this range, what does this fragment look like structurally.

The tools are the primitives rather than the pipeline on purpose. `analyze_image`
is here for the one-shot case, but the value of the protocol surface is the
low-level operations, because those compose into investigations nobody wrote a
workflow for.

Every tool is read-only. Nothing in this server writes to the image under
examination, which matters because the images are evidence and a recovery attempt
that modifies its own input destroys the thing it was trying to save.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ..config import get_settings
from ..disk import signatures as sig
from ..disk.carver import Carver
from ..disk.entropy import EntropyMap, chi_square_uniformity, printable_ratio, shannon_entropy
from ..disk.exfat import ExfatParser
from ..disk.fat32 import Fat32Parser
from ..disk.image import DiskImage
from ..disk.validators import validate

logger = logging.getLogger(__name__)
server = Server("flashforensics")

MAX_HEX_DUMP = 4096
MAX_CARVE_SPAN = 512 * 1024 * 1024


def _open(path: str) -> DiskImage:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"no image at {resolved}")
    return DiskImage(resolved)


def _parser_for(image: DiskImage):
    if Fat32Parser.detect(image):
        parser = Fat32Parser(image)
        parser.parse_boot_sector()
        return parser, "FAT32"
    if ExfatParser.detect(image):
        parser = ExfatParser(image)
        parser.parse_boot_sector()
        return parser, "exFAT"
    return None, "unknown"


def _text(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, indent=2, default=str))]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="identify_filesystem",
            description=(
                "Detect and parse the filesystem on a disk image, returning volume geometry and "
                "every structural inconsistency found while parsing. Recovers geometry from the "
                "backup boot sector when sector 0 is destroyed. Start here."
            ),
            inputSchema={
                "type": "object",
                "properties": {"image_path": {"type": "string", "description": "Path to the disk image"}},
                "required": ["image_path"],
            },
        ),
        Tool(
            name="read_sector",
            description=(
                "Read raw sectors and return them as a hex dump with an ASCII gutter. Use to "
                "inspect a boot sector, a directory region, or any offset a previous tool flagged."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "lba": {"type": "integer", "description": "Logical block address of the first sector"},
                    "count": {"type": "integer", "default": 1, "description": "Sectors to read, max 8"},
                },
                "required": ["image_path", "lba"],
            },
        ),
        Tool(
            name="entropy_map",
            description=(
                "Measure Shannon entropy across the image and return a downsampled profile plus "
                "the regions worth carving. Near zero is unallocated space, 4 to 6.5 is text, "
                "above 7.5 is compressed or encrypted. Use to locate data before carving."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "block_size": {"type": "integer", "default": 4096},
                    "points": {"type": "integer", "default": 128, "description": "Profile resolution"},
                },
                "required": ["image_path"],
            },
        ),
        Tool(
            name="list_files",
            description=(
                "Walk the directory tree and list every file the filesystem can still describe, "
                "with its cluster chain and any damage recorded against it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "limit": {"type": "integer", "default": 100},
                },
                "required": ["image_path"],
            },
        ),
        Tool(
            name="find_orphaned_regions",
            description=(
                "Return byte ranges holding data no directory entry references: clusters marked "
                "allocated with nothing pointing at them. These are the lost files, and they are "
                "the regions carving should target."
            ),
            inputSchema={
                "type": "object",
                "properties": {"image_path": {"type": "string"}},
                "required": ["image_path"],
            },
        ),
        Tool(
            name="carve_region",
            description=(
                "Carve a byte range for file signatures, returning each fragment with its exact "
                "extent and a structural validation result. Extents come from parsing the format, "
                "not from a fixed dump size."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "start": {"type": "integer", "description": "Start byte offset"},
                    "end": {"type": "integer", "description": "End byte offset"},
                    "alignment": {
                        "type": "integer",
                        "default": 0,
                        "description": "0 uses the volume cluster size; 1 disables alignment filtering",
                    },
                },
                "required": ["image_path", "start", "end"],
            },
        ),
        Tool(
            name="classify_fragment",
            description=(
                "Read bytes at an offset and identify the format by walking its internal "
                "structure. Resolves headers shared by several formats: distinguishes DOCX from "
                "XLSX from APK by zip entry names, and MP4 from HEIC by ISO base media brand. "
                "Returns evidence and problems, not just a name."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "length": {"type": "integer", "default": 65536},
                },
                "required": ["image_path", "offset"],
            },
        ),
        Tool(
            name="analyze_image",
            description=(
                "Run the whole recovery pipeline: parse, map entropy, carve, classify and judge "
                "recoverability, returning a ranked summary. Use when a full triage is wanted "
                "rather than a targeted question."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "limit": {"type": "integer", "default": 25, "description": "Fragments to return"},
                },
                "required": ["image_path"],
            },
        ),
        Tool(
            name="list_signatures",
            description=(
                "List the file signatures the carver knows, including which share a header and "
                "therefore need structural disambiguation."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "Filter by category, optional"},
                    "ambiguous_only": {"type": "boolean", "default": False},
                },
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        handler = HANDLERS.get(name)
        if handler is None:
            return _text({"error": f"unknown tool: {name}"})
        return await asyncio.to_thread(handler, arguments)
    except Exception as error:
        logger.exception("tool %s failed", name)
        return _text({"error": str(error), "tool": name})


def handle_identify_filesystem(arguments: dict) -> list[TextContent]:
    with _open(arguments["image_path"]) as image:
        parser, filesystem = _parser_for(image)
        if parser is None:
            return _text(
                {
                    "filesystem": "unknown",
                    "image_size": image.size,
                    "note": (
                        "No FAT32 or exFAT boot sector in the primary or backup location. The "
                        "volume was reformatted or its metadata region was destroyed. Carve the "
                        "whole image instead of walking a directory tree."
                    ),
                }
            )
        entries = parser.walk()
        return _text(
            {
                "filesystem": filesystem,
                "image_size": image.size,
                "boot_sector": parser.boot.to_dict(),
                "summary": parser.summary(entries),
            }
        )


def handle_read_sector(arguments: dict) -> list[TextContent]:
    count = max(1, min(int(arguments.get("count", 1)), 8))
    lba = int(arguments["lba"])
    with _open(arguments["image_path"]) as image:
        parser, _ = _parser_for(image)
        data = image.read_sector(lba, count)[:MAX_HEX_DUMP]
        return _text(
            {
                "lba": lba,
                "sector_size": image.sector_size,
                "bytes_read": len(data),
                "entropy": round(shannon_entropy(data), 3),
                "hex_dump": _hex_dump(data, lba * image.sector_size),
            }
        )


def _hex_dump(data: bytes, base_offset: int = 0) -> str:
    """Classic 16-byte-per-line dump, which is how anyone reading sectors expects it."""
    lines = []
    for index in range(0, len(data), 16):
        chunk = data[index : index + 16]
        hex_part = " ".join(f"{byte:02X}" for byte in chunk).ljust(47)
        ascii_part = "".join(chr(byte) if 32 <= byte <= 126 else "." for byte in chunk)
        lines.append(f"{base_offset + index:010X}  {hex_part}  |{ascii_part}|")
    return "\n".join(lines)


def handle_entropy_map(arguments: dict) -> list[TextContent]:
    block_size = int(arguments.get("block_size", 4096))
    points = max(8, min(int(arguments.get("points", 128)), 1024))
    with _open(arguments["image_path"]) as image:
        entropy_map = EntropyMap(block_size).scan(image)
        entropy_map.find_anomalies([])
        candidates = entropy_map.candidate_regions()
        return _text(
            {
                "statistics": entropy_map.statistics(),
                "profile": entropy_map.downsample(points),
                "candidate_regions": [region.to_dict() for region in candidates[:64]],
                "anomalies": [anomaly.to_dict() for anomaly in entropy_map.anomalies[:64]],
            }
        )


def handle_list_files(arguments: dict) -> list[TextContent]:
    limit = max(1, min(int(arguments.get("limit", 100)), 2000))
    with _open(arguments["image_path"]) as image:
        parser, filesystem = _parser_for(image)
        if parser is None:
            return _text({"error": "no readable filesystem on this image", "filesystem": filesystem})
        entries = parser.walk()
        cluster_size = parser.boot.cluster_size
        return _text(
            {
                "filesystem": filesystem,
                "total": len(entries),
                "files": [entry.to_dict(cluster_size) for entry in entries[:limit]],
            }
        )


def handle_find_orphaned_regions(arguments: dict) -> list[TextContent]:
    with _open(arguments["image_path"]) as image:
        parser, filesystem = _parser_for(image)
        if parser is None:
            return _text(
                {
                    "filesystem": filesystem,
                    "regions": [{"start": 0, "end": image.size, "reason": "no filesystem, whole image is unallocated"}],
                }
            )
        entries = parser.walk()
        orphans = parser.orphaned_clusters(entries)
        runs = parser.cluster_runs(orphans)
        cluster_size = parser.boot.cluster_size
        return _text(
            {
                "filesystem": filesystem,
                "orphaned_clusters": len(orphans),
                "cluster_size": cluster_size,
                "regions": [
                    {
                        "cluster_start": start,
                        "cluster_end": end,
                        "start": parser.cluster_to_offset(start),
                        "end": parser.cluster_to_offset(end) + cluster_size,
                        "bytes": (end - start + 1) * cluster_size,
                    }
                    for start, end in runs[:200]
                ],
            }
        )


def handle_carve_region(arguments: dict) -> list[TextContent]:
    start = max(0, int(arguments["start"]))
    end = int(arguments["end"])
    if end <= start:
        return _text({"error": "end must be greater than start"})
    if end - start > MAX_CARVE_SPAN:
        return _text(
            {
                "error": f"span of {end - start} bytes exceeds the {MAX_CARVE_SPAN} byte limit for one call",
                "hint": "call entropy_map or find_orphaned_regions first and carve the ranges it returns",
            }
        )

    with _open(arguments["image_path"]) as image:
        parser, _ = _parser_for(image)
        requested = int(arguments.get("alignment", 0))
        alignment = requested or (parser.boot.cluster_size if parser else image.sector_size)
        carver = Carver(image, alignment=alignment)
        fragments = carver.carve_region(start, min(end, image.size))
        return _text(
            {
                "range": {"start": start, "end": end},
                "alignment": alignment,
                "carved": len(fragments),
                "rejected": carver.rejected,
                "fragments": [fragment.to_dict() for fragment in fragments[:100]],
            }
        )


def handle_classify_fragment(arguments: dict) -> list[TextContent]:
    offset = int(arguments["offset"])
    length = max(64, min(int(arguments.get("length", 65536)), 16 * 1024 * 1024))

    with _open(arguments["image_path"]) as image:
        data = image.read(offset, length)
        if not data:
            return _text({"error": f"no data at offset {offset}"})

        candidates: list[str] = []
        for header, header_offset, members in sig.distinct_headers():
            if data[header_offset : header_offset + len(header)] == header:
                candidates.extend(member.extension for member in members)
        candidates = list(dict.fromkeys(candidates))

        results = []
        for candidate in candidates or ["unknown"]:
            signature = sig.lookup(candidate)
            result = validate(data, candidate, signature.footer if signature else None)
            results.append({"candidate": candidate, **result.to_dict()})
        results.sort(key=lambda item: -item["confidence"])

        sample = data[: 64 * 1024]
        best = results[0] if results else {}
        return _text(
            {
                "offset": offset,
                "bytes_examined": len(data),
                "header_hex": data[:16].hex(),
                "magic_candidates": candidates,
                "ambiguous": len(candidates) > 1,
                "statistics": {
                    "entropy": round(shannon_entropy(sample), 3),
                    "chi_square": round(chi_square_uniformity(sample), 1),
                    "printable_ratio": round(printable_ratio(sample), 3),
                },
                "best_match": best,
                "all_candidates": results,
            }
        )


def handle_analyze_image(arguments: dict) -> list[TextContent]:
    from ..agents.graph import run_analysis

    limit = max(1, min(int(arguments.get("limit", 25)), 200))
    path = Path(arguments["image_path"]).expanduser().resolve()
    if not path.is_file():
        return _text({"error": f"no image at {path}"})

    state = run_analysis(
        session_id="mcp",
        image_path=str(path),
        image_name=path.name,
        image_size=path.stat().st_size,
        settings=get_settings(),
        emitter=None,
    )

    if state.get("stage") == "failed":
        return _text({"error": state.get("error", "analysis failed")})

    return _text(
        {
            "filesystem": state.get("filesystem"),
            "report": state.get("report"),
            "damage": state.get("damage", [])[:20],
            "carve_stats": state.get("carve_stats", {}),
            "verdict_stats": state.get("verdict_stats", {}),
            "fragments": [
                {
                    "rank": fragment.get("rank"),
                    "fragment_id": fragment["fragment_id"],
                    "format": (fragment.get("classification") or {}).get("format"),
                    "offset": fragment["offset"],
                    "length": fragment["length"],
                    "source_path": fragment.get("source_path"),
                    "status": (fragment.get("verdict") or {}).get("status"),
                    "explanation": (fragment.get("verdict") or {}).get("explanation"),
                }
                for fragment in state.get("fragments", [])[:limit]
            ],
        }
    )


def handle_list_signatures(arguments: dict) -> list[TextContent]:
    category = arguments.get("category")
    ambiguous_only = bool(arguments.get("ambiguous_only", False))

    selected = [
        signature
        for signature in sig.SIGNATURES
        if (not category or signature.category == category)
        and (not ambiguous_only or signature.is_ambiguous)
    ]

    groups: dict[str, list[str]] = {}
    for signature in sig.SIGNATURES:
        if signature.ambiguity_group:
            groups.setdefault(signature.ambiguity_group, []).append(signature.extension)

    return _text(
        {
            "count": len(selected),
            "ambiguity_groups": groups,
            "signatures": [
                {
                    "extension": signature.extension,
                    "label": signature.label,
                    "category": signature.category,
                    "header": signature.header.hex(),
                    "header_offset": signature.header_offset,
                    "has_footer": signature.footer is not None,
                    "ambiguity_group": signature.ambiguity_group,
                }
                for signature in selected
            ],
        }
    )


HANDLERS = {
    "identify_filesystem": handle_identify_filesystem,
    "read_sector": handle_read_sector,
    "entropy_map": handle_entropy_map,
    "list_files": handle_list_files,
    "find_orphaned_regions": handle_find_orphaned_regions,
    "carve_region": handle_carve_region,
    "classify_fragment": handle_classify_fragment,
    "analyze_image": handle_analyze_image,
    "list_signatures": handle_list_signatures,
}


async def run() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    main()
