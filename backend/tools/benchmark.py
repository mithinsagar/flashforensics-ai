"""Score a recovery run against the fixture's ground truth manifest.

This is the script that produces the numbers in the README. It runs the full
pipeline over a generated image and compares the result against the manifest
written when that image was built, which records exactly what went in and exactly
what was done to it.

Four things get measured, and they are deliberately separate because a tool can
be good at one and bad at another:

  recall            of the files planted, how many were found at all
  format accuracy   of the files found, how many were identified correctly
  extent accuracy   how many were sized to the byte, which is what decides
                    whether a recovered file actually opens
  verdict accuracy  how many damage assessments matched what was really done

Extent accuracy is the one most carvers quietly skip. Finding a JPEG is easy;
knowing where it stops is the hard part, and a fragment with the next file's
bytes stapled to the end is not a recovered photo.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import warnings
from pathlib import Path

from flashforensics.agents.graph import run_analysis
from flashforensics.config import get_settings

warnings.filterwarnings("ignore")
logging.getLogger("chromadb").setLevel(logging.ERROR)

EXTENT_MEASURABLE = {"intact", "orphaned", "deleted", "truncated", "payload_corrupted"}
"""Scenarios where recovering the original byte length is actually possible.

`chain_broken` is excluded on purpose. Severing a mid-chain allocation entry
makes the tail of the file physically unreachable through the filesystem, so
reading fewer bytes than the directory entry claims is the correct result, not a
sizing error. Scoring it as a miss would reward a tool for inventing bytes it
cannot reach.
"""

EXPECTED_STATUS = {
    "intact": "RECOVERABLE",
    "orphaned": "RECOVERABLE",
    "deleted": "RECOVERABLE",
    "truncated": "PARTIAL",
    "payload_corrupted": "PARTIAL",
    "chain_broken": "PARTIAL",
}


def score(truth: dict, state: dict, offset_tolerance: int = 0) -> dict:
    """Compare a completed run against what was actually planted."""
    planted = truth["files"]
    fragments = state.get("fragments", [])

    by_offset: dict[int, dict] = {fragment["offset"]: fragment for fragment in fragments}
    by_path: dict[str, dict] = {
        fragment["source_path"]: fragment for fragment in fragments if fragment.get("source_path")
    }

    rows = []
    found = 0
    format_correct = 0
    extent_exact = 0
    extent_scored = 0
    verdict_correct = 0

    for entry in planted:
        match = by_path.get(entry["path"]) or by_offset.get(entry["byte_offset"])
        if match is None and offset_tolerance:
            for offset, fragment in by_offset.items():
                if abs(offset - entry["byte_offset"]) <= offset_tolerance:
                    match = fragment
                    break

        if match is None:
            rows.append(
                {
                    "path": entry["path"],
                    "scenario": entry["scenario"],
                    "found": False,
                    "expected_format": entry["format"],
                    "detected_format": None,
                    "expected_size": entry["size"],
                    "detected_size": None,
                    "expected_status": EXPECTED_STATUS.get(entry["scenario"], "RECOVERABLE"),
                    "detected_status": None,
                    "format_ok": False,
                    "extent_ok": False if entry["scenario"] in EXTENT_MEASURABLE else None,
                    "verdict_ok": False,
                }
            )
            continue

        found += 1
        detected_format = (match.get("classification") or {}).get("format") or match.get("format_guess")
        detected_status = (match.get("verdict") or {}).get("status")
        expected_status = EXPECTED_STATUS.get(entry["scenario"], "RECOVERABLE")

        measurable = entry["scenario"] in EXTENT_MEASURABLE
        format_ok = detected_format == entry["format"]
        extent_ok = match["length"] == entry["size"] if measurable else None
        verdict_ok = detected_status == expected_status

        format_correct += format_ok
        extent_exact += bool(extent_ok)
        extent_scored += measurable
        verdict_correct += verdict_ok

        rows.append(
            {
                "path": entry["path"],
                "scenario": entry["scenario"],
                "found": True,
                "expected_format": entry["format"],
                "detected_format": detected_format,
                "expected_size": entry["size"],
                "detected_size": match["length"],
                "expected_status": expected_status,
                "detected_status": detected_status,
                "format_ok": format_ok,
                "extent_ok": extent_ok,
                "verdict_ok": verdict_ok,
            }
        )

    total = len(planted)
    matched_offsets = {row["path"] for row in rows if row["found"]}
    false_positives = [
        fragment
        for fragment in fragments
        if (fragment.get("source_path") or "") not in matched_offsets
        and fragment["offset"] not in {entry["byte_offset"] for entry in planted}
    ]

    by_scenario: dict[str, dict] = {}
    for row in rows:
        bucket = by_scenario.setdefault(
            row["scenario"], {"total": 0, "found": 0, "format_ok": 0, "extent_ok": 0, "verdict_ok": 0}
        )
        bucket["total"] += 1
        bucket["found"] += row["found"]
        bucket["format_ok"] += row["format_ok"]
        bucket["extent_ok"] += bool(row["extent_ok"])
        bucket["verdict_ok"] += row["verdict_ok"]

    return {
        "planted": total,
        "found": found,
        "recall": round(found / total, 4) if total else 0.0,
        "format_correct": format_correct,
        "format_accuracy": round(format_correct / found, 4) if found else 0.0,
        "extent_exact": extent_exact,
        "extent_scored": extent_scored,
        "extent_accuracy": round(extent_exact / extent_scored, 4) if extent_scored else 0.0,
        "verdict_correct": verdict_correct,
        "verdict_accuracy": round(verdict_correct / found, 4) if found else 0.0,
        "false_positives": len(false_positives),
        "fragments_reported": len(fragments),
        "by_scenario": by_scenario,
        "rows": rows,
    }


def print_report(result: dict, elapsed: float, state: dict) -> None:
    print()
    print("=" * 78)
    print("FlashForensics benchmark")
    print("=" * 78)
    print(f"  files planted        {result['planted']}")
    print(f"  files found          {result['found']}   recall {result['recall']:.1%}")
    print(f"  format correct       {result['format_correct']}   accuracy {result['format_accuracy']:.1%}")
    print(
        f"  extent byte-exact    {result['extent_exact']}/{result['extent_scored']}"
        f"   accuracy {result['extent_accuracy']:.1%}"
    )
    print(f"  verdict correct      {result['verdict_correct']}   accuracy {result['verdict_accuracy']:.1%}")
    print(f"  false positives      {result['false_positives']}")
    print(f"  elapsed              {elapsed:.1f}s")
    print()

    print(f"  {'scenario':<20} {'n':>3} {'found':>6} {'format':>7} {'extent':>7} {'verdict':>8}")
    print("  " + "-" * 60)
    for scenario, bucket in sorted(result["by_scenario"].items()):
        print(
            f"  {scenario:<20} {bucket['total']:>3} {bucket['found']:>6} "
            f"{bucket['format_ok']:>7} {bucket['extent_ok']:>7} {bucket['verdict_ok']:>8}"
        )
    print()

    failures = [
        row
        for row in result["rows"]
        if not (
            row["found"]
            and row["format_ok"]
            and row["extent_ok"] is not False
            and row["verdict_ok"]
        )
    ]
    if failures:
        print("  imperfect rows:")
        for row in failures:
            if not row["found"]:
                print(f"    MISSED   {row['path']} ({row['scenario']})")
                continue
            problems = []
            if not row["format_ok"]:
                problems.append(f"format {row['detected_format']} != {row['expected_format']}")
            if row["extent_ok"] is False:
                problems.append(f"size {row['detected_size']} != {row['expected_size']}")
            if not row["verdict_ok"]:
                problems.append(f"verdict {row['detected_status']} != {row['expected_status']}")
            print(f"    PARTIAL  {row['path']}: {'; '.join(problems)}")
        print()

    provider = state.get("provider_health", {})
    print(f"  provider             {provider.get('provider')} ({provider.get('note', 'model backed')})")
    print("=" * 78)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Score recovery against ground truth")
    parser.add_argument("--image", default="fixtures/card.img", type=Path)
    parser.add_argument("--truth", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    truth_path = args.truth or args.image.with_suffix(".truth.json")
    if not args.image.exists():
        raise SystemExit(f"image not found: {args.image}. Run tools/make_fixture.py first.")
    if not truth_path.exists():
        raise SystemExit(f"ground truth not found: {truth_path}")

    truth = json.loads(truth_path.read_text())
    settings = get_settings()

    emitter = None
    if args.verbose:
        def emitter(event):
            if "Entropy scan" not in event.message:
                print(f"  [{event.agent:11}] {event.percent:3}% {event.message}")

    start = time.time()
    state = run_analysis(
        session_id="benchmark",
        image_path=str(args.image),
        image_name=args.image.name,
        image_size=args.image.stat().st_size,
        settings=settings,
        emitter=emitter,
    )
    elapsed = time.time() - start

    if state.get("stage") == "failed":
        raise SystemExit(f"analysis failed: {state.get('error')}")

    result = score(truth, state)
    result["elapsed_seconds"] = round(elapsed, 2)
    result["provider"] = state.get("provider_health", {})
    print_report(result, elapsed, state)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2))
        print(f"  wrote {args.json_out}")


if __name__ == "__main__":
    main()
