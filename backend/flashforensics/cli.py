"""Command line interface.

The dashboard is the nice way to use this. The command line is the way it gets
used in practice by anyone scripting a triage over a directory of images, or
working on a machine with no browser, which describes most forensic workstations.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from pathlib import Path

from . import __version__

warnings.filterwarnings("ignore")
logging.getLogger("chromadb").setLevel(logging.ERROR)

STATUS_MARKS = {
    "RECOVERABLE": "OK  ",
    "PARTIAL": "PART",
    "METADATA_ONLY": "META",
    "JUNK": "JUNK",
}


def human(bytes_count: int) -> str:
    value = float(bytes_count)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}PB"


def command_analyze(args: argparse.Namespace) -> int:
    from .agents.graph import run_analysis
    from .config import get_settings

    path = Path(args.image).expanduser().resolve()
    if not path.is_file():
        print(f"error: no image at {path}", file=sys.stderr)
        return 2

    def emitter(event):
        if args.quiet or "Entropy scan" in event.message:
            return
        print(f"  [{event.agent:11}] {event.percent:3}% {event.message}", file=sys.stderr)

    state = run_analysis(
        session_id="cli",
        image_path=str(path),
        image_name=path.name,
        image_size=path.stat().st_size,
        settings=get_settings(),
        emitter=None if args.quiet else emitter,
    )

    if state.get("stage") == "failed":
        print(f"error: {state.get('error')}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({
            "filesystem": state.get("filesystem"),
            "report": state.get("report"),
            "damage": state.get("damage", []),
            "carve_stats": state.get("carve_stats", {}),
            "verdict_stats": state.get("verdict_stats", {}),
            "fragments": state.get("fragments", []),
        }, indent=2, default=str))
        return 0

    verdicts = state.get("verdict_stats", {})
    print()
    print(f"  {path.name}  {human(path.stat().st_size)}  {state.get('filesystem')}")
    print(f"  {state.get('report', '')}")
    print()
    print(
        f"  {verdicts.get('recoverable', 0)} recoverable · "
        f"{verdicts.get('partial', 0)} partial · "
        f"{verdicts.get('metadata_only', 0)} metadata only · "
        f"{verdicts.get('junk', 0)} junk"
    )
    print()

    for report in state.get("damage", [])[:8]:
        print(f"  ! {report['detail']}")
    if state.get("damage"):
        print()

    for fragment in state.get("fragments", [])[: args.limit]:
        verdict = fragment.get("verdict") or {}
        classification = fragment.get("classification") or {}
        name = fragment.get("source_path") or f"carved {classification.get('format')}"
        print(
            f"  {STATUS_MARKS.get(verdict.get('status'), '?   ')} "
            f"{human(fragment['length']):>8}  {classification.get('format', '?'):<7} {name}"
        )
        if verdict.get("status") != "RECOVERABLE" and verdict.get("explanation"):
            print(f"         {verdict['explanation']}")

    if args.export:
        from .disk.image import DiskImage

        export_dir = Path(args.export).expanduser().resolve()
        export_dir.mkdir(parents=True, exist_ok=True)
        written = 0
        with DiskImage(path) as image:
            for fragment in state.get("fragments", []):
                if not (fragment.get("verdict") or {}).get("recoverable"):
                    continue
                name = Path(fragment.get("source_path") or "").name or (
                    f"{fragment['offset']:012d}.{fragment.get('format_guess', 'bin')}"
                )
                (export_dir / name).write_bytes(image.read(fragment["offset"], fragment["length"]))
                written += 1
        print(f"\n  exported {written} recoverable files to {export_dir}")

    return 0


def command_fixture(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tools.make_fixture import generate

    truth = generate(
        output=Path(args.output),
        size_mb=args.size_mb,
        damage=not args.clean,
        seed=args.seed,
    )
    print(f"  wrote {truth.image_path} with {len(truth.files)} planted files")
    print(f"  ground truth at {Path(truth.image_path).with_suffix('.truth.json')}")
    return 0


def command_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import get_settings

    settings = get_settings()
    uvicorn.run(
        "flashforensics.api.main:app",
        host=args.host or settings.api_host,
        port=args.port or settings.api_port,
    )
    return 0


def command_mcp(_args: argparse.Namespace) -> int:
    from .mcp_server.server import main as mcp_main

    mcp_main()
    return 0


def command_health(_args: argparse.Namespace) -> int:
    from .agents.graph import get_knowledge_base
    from .config import get_settings
    from .disk.signatures import SIGNATURES
    from .llm.provider import build_provider

    settings = get_settings()
    provider = build_provider(settings)
    knowledge = get_knowledge_base(settings)

    print(f"  version          {__version__}")
    print(f"  signatures       {len(SIGNATURES)}")
    print(f"  formats indexed  {knowledge.size}")
    print(f"  embeddings       {knowledge.embedding_info.get('embedding_model')}")
    if not knowledge.embedding_info.get("semantic"):
        print(f"                   {knowledge.embedding_info.get('note')}")
    health = provider.health()
    print(f"  llm provider     {health.get('provider')} {health.get('model', '')}")
    if health.get("note"):
        print(f"                   {health['note']}")
    print(f"  workspace        {settings.workspace}")
    return 0


def command_devices(args: argparse.Namespace) -> int:
    """List attached cards and drives, and say which ones can be read."""
    from .disk.devices import describe_environment, elevation_hint, imaging_hint, list_devices

    environment = describe_environment()
    if not environment["detector_available"]:
        print(f"  device detection is not available on {environment['platform']}")
        return 1

    devices = list_devices(removable_only=args.removable)
    if not devices:
        print("  no disks detected")
        return 0

    for device in devices:
        marker = "card" if (device.removable and not device.internal) else "disk"
        state = "readable" if device.readable else f"NOT READABLE ({device.reason})"
        filesystems = ", ".join(device.filesystems) or "unknown"
        print(f"  [{marker}] {device.path}  {device.size_human:>10}  {device.label}")
        print(f"          filesystem {filesystems}   {state}")
        if not device.readable:
            print(f"          fix: {elevation_hint(device.path)}")
            print(f"          or:  {imaging_hint(device)}")
    print()
    print("  analyse one with:  flashforensics analyze <path>")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flashforensics",
        description="Agentic recovery for corrupted flash storage",
    )
    parser.add_argument("--version", action="version", version=f"flashforensics {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="run the full recovery pipeline on an image")
    analyze.add_argument("image", help="path to a disk image")
    analyze.add_argument("--json", action="store_true", help="emit machine-readable output")
    analyze.add_argument("--quiet", action="store_true", help="suppress progress on stderr")
    analyze.add_argument("--limit", type=int, default=40, help="fragments to print")
    analyze.add_argument("--export", metavar="DIR", help="write recoverable files to this directory")
    analyze.set_defaults(func=command_analyze)

    fixture = subparsers.add_parser("fixture", help="generate a damaged test image with ground truth")
    fixture.add_argument("--output", default="fixtures/card.img")
    fixture.add_argument("--size-mb", type=int, default=128)
    fixture.add_argument("--clean", action="store_true", help="skip the damage pass")
    fixture.add_argument("--seed", type=int, default=20260314)
    fixture.set_defaults(func=command_fixture)

    serve = subparsers.add_parser("serve", help="start the HTTP API")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.set_defaults(func=command_serve)

    mcp = subparsers.add_parser("mcp", help="start the MCP server on stdio")
    mcp.set_defaults(func=command_mcp)

    devices = subparsers.add_parser("devices", help="list attached cards and drives")
    devices.add_argument(
        "--removable", action="store_true", help="only show removable media"
    )
    devices.set_defaults(func=command_devices)

    health = subparsers.add_parser("health", help="report what this install can do")
    health.set_defaults(func=command_health)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
