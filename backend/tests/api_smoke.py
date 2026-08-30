"""End-to-end smoke test against a running API server.

Exercises the full HTTP surface the dashboard depends on: create a session,
start the analysis, follow the SSE stream to completion, read the results back,
filter fragments, ask a question, download a fragment and confirm the bytes
decode as a real file, then export.

Run with the server already up:

    uvicorn flashforensics.api.main:app --port 8811 &
    python tests/api_smoke.py --base http://127.0.0.1:8811 --image fixtures/card.img
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

FILTERED_MESSAGES = ("Entropy scan", "Free space", "Orphaned regions", "Judging recoverability")


def call(base: str, path: str, method: str = "GET", body: dict | None = None, raw: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = response.read()
    return payload if raw else json.loads(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8811")
    parser.add_argument("--image", default="fixtures/card.img", type=Path)
    args = parser.parse_args()

    image = args.image.resolve()
    failures: list[str] = []

    health = call(args.base, "/api/health")
    print(
        f"health           {health['status']}  signatures={health['signatures']}  "
        f"formats={health['knowledge_base']['formats_indexed']}  llm={health['llm']['provider']}"
    )
    if health["status"] != "ok":
        failures.append("health endpoint did not report ok")

    session = call(args.base, "/api/sessions/from-path", "POST", {"path": str(image)})
    session_id = session["session_id"]
    print(f"session          {session_id}  ({session['image_size'] / 1048576:.0f} MB)")

    call(args.base, f"/api/sessions/{session_id}/analyze", "POST")

    print("stream")
    stages_seen: set[str] = set()
    request = urllib.request.Request(f"{args.base}/api/sessions/{session_id}/stream")
    with urllib.request.urlopen(request, timeout=300) as response:
        for raw_line in response:
            line = raw_line.decode().strip()
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            stages_seen.add(event["stage"])
            if not any(token in event["message"] for token in FILTERED_MESSAGES):
                print(f"  [{event['agent']:11}] {event['percent']:3}% {event['message']}")
            if event["stage"] in ("complete", "failed"):
                break

    for expected in ("scanning", "carving", "classifying", "adjudicating", "complete"):
        if expected not in stages_seen:
            failures.append(f"stream never reported the {expected} stage")

    detail = call(args.base, f"/api/sessions/{session_id}")
    print(
        f"\nresult           status={detail['status']} fs={detail['filesystem']} "
        f"fragments={detail['fragments']} recoverable={detail['recoverable']} "
        f"partial={detail['partial']} elapsed={detail['elapsed_seconds']}s"
    )
    print(
        f"analysis         entropy_points={len(detail['entropy']['points'])} "
        f"anomalies={len(detail['entropy']['anomalies'])} damage={len(detail['damage'])}"
    )
    print(f"report           {detail['report'][:150]}")

    if detail["status"] != "complete":
        failures.append(f"analysis did not complete: {detail.get('error')}")
    if detail["fragments"] == 0:
        failures.append("no fragments were reported")
    if not detail["entropy"]["points"]:
        failures.append("entropy map was empty")

    partial = call(args.base, f"/api/sessions/{session_id}/fragments?status=PARTIAL")
    print(f"\npartial          {partial['total']} fragments")
    for fragment in partial["fragments"]:
        print(f"  {fragment['format_guess']:7} {fragment['length']:>7}B  {fragment['verdict']['explanation'][:72]}")

    answer = call(
        args.base,
        f"/api/sessions/{session_id}/ask",
        "POST",
        {"question": "which photos are fully recoverable"},
    )
    print(f"\nask              retrieved={answer['retrieved']} filter={answer.get('filter_applied')}")
    print("  " + answer["answer"].replace("\n", "\n  ")[:300])
    if answer["retrieved"] == 0:
        failures.append("rag agent retrieved nothing")

    listing = call(args.base, f"/api/sessions/{session_id}/fragments?limit=1")
    fragment_id = listing["fragments"][0]["fragment_id"]
    blob = call(args.base, f"/api/sessions/{session_id}/fragments/{fragment_id}/download", raw=True)
    Path("/tmp/ff_smoke_fragment").write_bytes(blob)

    try:
        from PIL import Image

        image_handle = Image.open("/tmp/ff_smoke_fragment")
        image_handle.load()
        print(f"\ndownload         decodes as {image_handle.format} {image_handle.size}, {len(blob)} bytes")
    except Exception as error:
        failures.append(f"top-ranked fragment did not decode as an image: {error}")

    export = call(args.base, f"/api/sessions/{session_id}/export?status=RECOVERABLE", "POST")
    print(f"export           {export['exported']} files, {export['bytes']} bytes -> {export['archive']}")

    call(args.base, f"/api/sessions/{session_id}", "DELETE")
    print("cleanup          session deleted")

    print()
    if failures:
        for failure in failures:
            print(f"FAIL  {failure}")
        return 1
    print("PASS  every endpoint behaved as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
