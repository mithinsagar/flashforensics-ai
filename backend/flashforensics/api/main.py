"""FastAPI application: upload, analyse, stream, download, ask.

The analysis runs on a worker thread and streams progress over Server-Sent
Events. SSE rather than WebSockets because the traffic is entirely one
directional, it survives proxies that mangle WebSocket upgrades, and browsers
reconnect on their own. The client sends one request and watches.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..agents.graph import get_knowledge_base, run_analysis
from ..agents.rag import RagAgent
from ..agents.state import STAGE_LABELS, AgentEvent, Stage
from ..config import get_settings
from ..demo import DemoUnavailable, demo_description, demo_image, score_run
from ..disk.devices import describe_environment, elevation_hint, imaging_hint, list_devices
from ..disk.image import DiskImage, DiskReadError
from ..disk.signatures import SIGNATURES, mime_for
from ..llm.provider import build_provider
from .store import Session, store

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="FlashForensics AI",
    version=__version__,
    description=(
        "Agentic recovery for corrupted flash storage. Parses FAT32 and exFAT volumes, maps "
        "entropy to locate damage, carves orphaned regions, and returns evidence-based "
        "recoverability verdicts."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins + ["http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=6, ge=1, le=20)


class AnalyzePathRequest(BaseModel):
    path: str = Field(min_length=1, description="Server-side path to an image already on disk")


class DeviceRequest(BaseModel):
    path: str = Field(min_length=1, description="Raw device path, e.g. /dev/disk4")


@app.get("/api/health")
def health() -> dict:
    """Report what the server can actually do right now.

    This is the endpoint to hit when something looks wrong. It names the active
    LLM provider and the active embedding model, which between them explain most
    behaviour differences between two installs of the same code.
    """
    provider = build_provider(settings)
    knowledge = get_knowledge_base(settings)
    return {
        "status": "ok",
        "version": __version__,
        "llm": provider.health(),
        "knowledge_base": {
            "formats_indexed": knowledge.size,
            **knowledge.embedding_info,
        },
        "signatures": len(SIGNATURES),
        "sessions_active": len(store.list()),
        "workspace": str(settings.workspace),
        "device_detection": describe_environment(),
    }


@app.get("/api/signatures")
def signatures() -> dict:
    """The signature table, so a client can show what the tool can look for."""
    return {
        "count": len(SIGNATURES),
        "signatures": [
            {
                "extension": signature.extension,
                "label": signature.label,
                "category": signature.category,
                "header": signature.header.hex(),
                "header_offset": signature.header_offset,
                "has_footer": signature.footer is not None,
                "ambiguity_group": signature.ambiguity_group,
                "mime": mime_for(signature.extension),
            }
            for signature in SIGNATURES
        ],
    }


@app.get("/api/devices")
def devices(removable_only: bool = Query(default=False)) -> dict:
    """Cards and drives currently attached to the machine running this server.

    The dashboard polls this, so a card inserted while the page is open appears
    without a refresh. Devices that cannot be read are still listed, with the
    reason and the command that fixes it, because a card the user can see in
    Finder but not here needs an explanation rather than an empty list.
    """
    found = list_devices(removable_only=removable_only)
    return {
        "environment": describe_environment(),
        "devices": [
            {
                **device.to_dict(),
                "elevation_hint": "" if device.readable else elevation_hint(device.path),
                "imaging_hint": imaging_hint(device),
            }
            for device in found
        ],
    }


@app.post("/api/sessions/from-device")
def create_from_device(request: DeviceRequest) -> dict:
    """Open an attached card directly, with no imaging step in between.

    Reading the device in place is both faster and safer than copying it first:
    nothing is written anywhere, and a 64 GB card does not need 64 GB of free
    space before the user can find out whether their photos survived.
    """
    known = {device.path: device for device in list_devices()}
    device = known.get(request.path)
    if device is None:
        raise HTTPException(404, f"no attached device at {request.path}")
    if not device.readable:
        raise HTTPException(
            403,
            f"{device.path} cannot be read: {device.reason}. {elevation_hint(device.path)}",
        )

    try:
        with DiskImage(device.path) as probe:
            size = probe.size
    except DiskReadError as error:
        raise HTTPException(400, str(error)) from error

    session = store.create(Path(device.path), device.label or device.identifier, size, owns_image=False)
    session.source = "device"
    return session.summary()


@app.get("/api/demo")
def demo_info() -> dict:
    """Describe the built-in sample card so the UI can offer it up front."""
    return demo_description(settings)


@app.post("/api/sessions/demo")
def create_demo_session() -> dict:
    """Build (or reuse) the sample damaged card and open a session on it."""
    try:
        image, truth = demo_image(settings)
    except DemoUnavailable as error:
        raise HTTPException(503, str(error)) from error

    session = store.create(image, "Sample damaged card", image.stat().st_size, owns_image=False)
    session.source = "demo"
    session.truth = truth
    return session.summary()


@app.post("/api/sessions")
async def upload(file: UploadFile = File(...)) -> dict:
    """Accept a disk image and create a session for it.

    Written to disk in chunks rather than read into memory, because these are
    disk images: a 64 GB card is a normal input and buffering one would take the
    server down.
    """
    if not file.filename:
        raise HTTPException(400, "no filename supplied")

    session_dir = settings.uploads_dir
    target = session_dir / f"{int(time.time())}_{Path(file.filename).name}"

    written = 0
    try:
        with target.open("wb") as handle:
            while chunk := await file.read(4 * 1024 * 1024):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    handle.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, f"image exceeds the {settings.max_upload_bytes} byte limit")
                handle.write(chunk)
    except HTTPException:
        raise
    except OSError as error:
        target.unlink(missing_ok=True)
        raise HTTPException(500, f"could not store the upload: {error}") from error

    if written == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "uploaded image was empty")

    session = store.create(target, file.filename, written)
    return session.summary()


@app.post("/api/sessions/from-path")
def create_from_path(request: AnalyzePathRequest) -> dict:
    """Register an image already present on the server, without copying it.

    Uploading a 64 GB card image through a browser to a tool running on the same
    machine is pure waste, and this is also the path a CLI or a batch job uses.
    """
    path = Path(request.path).expanduser().resolve()
    if not path.is_file():
        raise HTTPException(404, f"no file at {path}")
    session = store.create(path, path.name, path.stat().st_size, owns_image=False)
    return session.summary()


def _run_session(session: Session) -> None:
    """Execute the pipeline on a worker thread, publishing events as it goes."""
    session.status = "running"

    def emitter(event: AgentEvent) -> None:
        store.publish(session, event)

    try:
        state = run_analysis(
            session_id=session.session_id,
            image_path=str(session.image_path),
            image_name=session.image_name,
            image_size=session.image_size,
            settings=settings,
            emitter=emitter,
        )
        session.state = state
        session.rag = state.get("_rag")
        session.status = state.get("stage", "complete")
        session.error = state.get("error")
    except Exception as error:
        logger.exception("session %s failed", session.session_id)
        session.status = "failed"
        session.error = str(error)
        store.publish(
            session,
            AgentEvent(stage=Stage.FAILED.value, message=f"Analysis failed: {error}", agent="system"),
        )


@app.post("/api/sessions/{session_id}/analyze")
async def analyze(session_id: str) -> dict:
    """Start the pipeline on a worker thread and return immediately.

    This handler is async purely so it runs on the event loop rather than in the
    threadpool, which is the only place the loop reference the event bus needs to
    marshal events across can be captured. Progress is watched on the stream
    endpoint, not here.
    """
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "unknown session")
    if session.status == "running":
        raise HTTPException(409, "analysis already running for this session")

    session.loop = asyncio.get_running_loop()
    session.events.clear()
    thread = threading.Thread(target=_run_session, args=(session,), daemon=True)
    thread.start()
    return {"session_id": session_id, "status": "running"}


@app.get("/api/sessions/{session_id}/stream")
async def stream(session_id: str):
    """Stream analysis progress as Server-Sent Events.

    Buffered events replay first so a browser that connects late, or reconnects
    after a drop, still renders the full timeline instead of joining blank.
    """
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "unknown session")

    session.loop = asyncio.get_running_loop()
    queue = store.subscribe(session)

    async def generator():
        try:
            with session.lock:
                backlog = list(session.events)
            for event in backlog:
                yield f"data: {json.dumps(event.to_dict())}\n\n"

            if session.status in ("complete", "failed"):
                yield f"data: {json.dumps({'stage': session.status, 'message': 'done', 'percent': 100, 'agent': 'system', 'data': {}})}\n\n"
                return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue

                yield f"data: {json.dumps(event.to_dict())}\n\n"
                if event.stage in (Stage.COMPLETE.value, Stage.FAILED.value):
                    return
        finally:
            store.unsubscribe(session, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/sessions")
def list_sessions() -> dict:
    return {"sessions": [session.summary() for session in store.list()]}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "unknown session")

    state = session.state
    return {
        **session.summary(),
        "stage_label": STAGE_LABELS.get(Stage(session.status), session.status)
        if session.status in {stage.value for stage in Stage}
        else session.status,
        "boot_sector": state.get("boot_sector", {}),
        "filesystem_summary": state.get("filesystem_summary", {}),
        "damage": state.get("damage", []),
        "entropy": {
            "points": state.get("entropy_points", []),
            "detail": state.get("entropy_detail", {}),
            "stats": state.get("entropy_stats", {}),
            "anomalies": state.get("anomalies", [])[:200],
        },
        "carve_stats": state.get("carve_stats", {}),
        "classification_stats": state.get("classification_stats", {}),
        "verdict_stats": state.get("verdict_stats", {}),
        "report": state.get("report", ""),
        "provider": state.get("provider_health", {}),
    }


@app.get("/api/sessions/{session_id}/verification")
def verification(session_id: str) -> dict:
    """Grade a demo run against the record of what was done to the sample card.

    Only demo sessions can be scored, because only they come with a manifest of
    the truth. A real card has no answer key, which is the whole reason the
    sample card exists.
    """
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "unknown session")
    if session.truth is None:
        return {"available": False, "reason": "this session has no ground truth to check against"}
    if session.status != "complete":
        raise HTTPException(409, "analysis has not finished for this session")

    try:
        result = score_run(session.truth, session.state)
    except DemoUnavailable as error:
        return {"available": False, "reason": str(error)}
    return {"available": True, **result}


@app.get("/api/sessions/{session_id}/files")
def session_files(session_id: str) -> dict:
    """Files the filesystem itself can still describe, damage annotations included."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "unknown session")
    return {"files": session.state.get("files", [])}


@app.get("/api/sessions/{session_id}/fragments")
def fragments(
    session_id: str,
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "unknown session")

    items = session.state.get("fragments", [])
    if status:
        wanted = status.upper()
        items = [item for item in items if (item.get("verdict") or {}).get("status") == wanted]
    if category:
        items = [item for item in items if item.get("category") == category]

    return {
        "total": len(items),
        "limit": limit,
        "offset": offset,
        "fragments": items[offset : offset + limit],
    }


@app.get("/api/sessions/{session_id}/fragments/{fragment_id}")
def fragment_detail(session_id: str, fragment_id: str) -> dict:
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "unknown session")
    for fragment in session.state.get("fragments", []):
        if fragment["fragment_id"] == fragment_id:
            return fragment
    raise HTTPException(404, "unknown fragment")


@app.get("/api/sessions/{session_id}/fragments/{fragment_id}/download")
def download_fragment(session_id: str, fragment_id: str):
    """Extract one fragment from the image and return it as a file.

    Fragments are cut on demand rather than written out during analysis, because
    a run that finds four hundred fragments should not write four hundred files
    the user may never open.
    """
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "unknown session")

    target = next(
        (item for item in session.state.get("fragments", []) if item["fragment_id"] == fragment_id),
        None,
    )
    if target is None:
        raise HTTPException(404, "unknown fragment")

    export_dir = settings.exports_dir / session_id
    export_dir.mkdir(parents=True, exist_ok=True)
    extension = target.get("format_guess", "bin")
    name = Path(target.get("source_path") or "").name or f"fragment_{target['offset']}.{extension}"
    output = export_dir / name

    with DiskImage(session.image_path) as image:
        data = image.read(target["offset"], target["length"])
    output.write_bytes(data)

    return FileResponse(output, media_type=target.get("mime", "application/octet-stream"), filename=name)


@app.post("/api/sessions/{session_id}/export")
def export_all(session_id: str, status: str = Query(default="RECOVERABLE")) -> dict:
    """Write every fragment matching a verdict to a zip the user can download."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "unknown session")

    wanted = status.upper()
    selected = [
        item
        for item in session.state.get("fragments", [])
        if wanted == "ALL" or (item.get("verdict") or {}).get("status") == wanted
    ]
    if not selected:
        raise HTTPException(404, f"no fragments with status {wanted}")

    export_dir = settings.exports_dir / session_id / wanted.lower()
    export_dir.mkdir(parents=True, exist_ok=True)

    with DiskImage(session.image_path) as image:
        for item in selected:
            name = Path(item.get("source_path") or "").name or (
                f"{item['offset']:012d}_{item['fragment_id']}.{item.get('format_guess', 'bin')}"
            )
            (export_dir / name).write_bytes(image.read(item["offset"], item["length"]))

    archive = shutil.make_archive(str(export_dir), "zip", root_dir=export_dir)
    return {
        "exported": len(selected),
        "status": wanted,
        "archive": archive,
        "bytes": sum(item["length"] for item in selected),
    }


@app.post("/api/sessions/{session_id}/ask")
def ask(session_id: str, request: AskRequest) -> dict:
    """Answer a natural-language question about this session's fragments."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(404, "unknown session")
    if session.status != "complete":
        raise HTTPException(409, "analysis has not finished for this session")

    agent = session.rag
    if agent is None:
        agent = RagAgent(session_id, build_provider(settings))
        agent.ingest(session.state.get("fragments", []))
        session.rag = agent

    return agent.ask(request.question, limit=request.limit)


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    if not store.delete(session_id):
        raise HTTPException(404, "unknown session")
    return {"deleted": session_id}


@app.on_event("startup")
def warm_knowledge_base() -> None:
    """Build the format index at boot so the first upload is not slow."""
    try:
        knowledge = get_knowledge_base(settings)
        logger.info(
            "knowledge base ready: %d formats via %s",
            knowledge.size,
            knowledge.embedding_info.get("embedding_model"),
        )
    except Exception as error:
        logger.warning("knowledge base warm-up failed: %s", error)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "flashforensics.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
