"""Drive the MCP server over stdio the way a real client would.

Speaks the protocol properly rather than calling the handlers directly, because
the thing worth testing is that the tool schemas and responses are actually
usable by a client, not that the Python functions return dictionaries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", default="fixtures/card.img", type=Path)
    args = parser.parse_args()
    image = str(args.image.resolve())

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "flashforensics.mcp_server.server"],
        env=None,
    )

    failures: list[str] = []

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [tool.name for tool in tools.tools]
            print(f"tools exposed     {len(names)}: {', '.join(names)}")
            for expected in ("identify_filesystem", "read_sector", "entropy_map", "carve_region", "classify_fragment"):
                if expected not in names:
                    failures.append(f"missing tool {expected}")

            result = await session.call_tool("identify_filesystem", {"image_path": image})
            payload = json.loads(result.content[0].text)
            print(
                f"\nidentify_filesystem  {payload.get('filesystem')} "
                f"cluster_size={payload.get('boot_sector', {}).get('cluster_size')} "
                f"orphaned={payload.get('summary', {}).get('clusters_orphaned')}"
            )
            if payload.get("filesystem") != "FAT32":
                failures.append("filesystem was not identified as FAT32")

            result = await session.call_tool("read_sector", {"image_path": image, "lba": 6})
            payload = json.loads(result.content[0].text)
            print("\nread_sector lba=6 (backup boot sector):")
            for line in payload["hex_dump"].splitlines()[:4]:
                print("  " + line)
            if "FFORENSC" not in payload["hex_dump"]:
                failures.append("backup boot sector did not contain the expected OEM name")

            result = await session.call_tool("find_orphaned_regions", {"image_path": image})
            payload = json.loads(result.content[0].text)
            regions = payload["regions"]
            print(f"\nfind_orphaned_regions  {payload['orphaned_clusters']} clusters in {len(regions)} runs")
            if not regions:
                failures.append("no orphaned regions were reported on a damaged image")

            biggest = max(regions, key=lambda item: item["bytes"])
            result = await session.call_tool(
                "carve_region",
                {"image_path": image, "start": biggest["start"], "end": biggest["end"]},
            )
            payload = json.loads(result.content[0].text)
            print(f"\ncarve_region  {payload['carved']} fragments from the largest orphaned run")
            for fragment in payload["fragments"]:
                validation = fragment.get("validation") or {}
                print(
                    f"  offset={fragment['offset']:<9} len={fragment['length']:<8} "
                    f"format={validation.get('format_detected')} complete={validation.get('structure_complete')}"
                )
            if payload["carved"] == 0:
                failures.append("carving the largest orphaned run produced nothing")

            if payload["fragments"]:
                target = payload["fragments"][0]
                result = await session.call_tool(
                    "classify_fragment", {"image_path": image, "offset": target["offset"]}
                )
                payload = json.loads(result.content[0].text)
                best = payload["best_match"]
                print(
                    f"\nclassify_fragment  candidates={payload['magic_candidates']} "
                    f"ambiguous={payload['ambiguous']}"
                )
                print(f"  resolved to {best.get('format_detected')} at confidence {best.get('confidence')}")
                for line in (best.get("evidence") or [])[:3]:
                    print(f"    evidence: {line}")

            result = await session.call_tool("list_signatures", {"ambiguous_only": True})
            payload = json.loads(result.content[0].text)
            print(f"\nlist_signatures  {payload['count']} ambiguous signatures")
            for group, members in payload["ambiguity_groups"].items():
                print(f"  {group}: {', '.join(members)}")

            result = await session.call_tool("entropy_map", {"image_path": image, "points": 16})
            payload = json.loads(result.content[0].text)
            stats = payload["statistics"]
            print(
                f"\nentropy_map  blocks={stats['blocks']} occupancy={stats['occupancy_ratio']} "
                f"candidate_regions={len(payload['candidate_regions'])}"
            )

    print()
    if failures:
        for failure in failures:
            print(f"FAIL  {failure}")
        return 1
    print("PASS  MCP server answered every call correctly over stdio")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
