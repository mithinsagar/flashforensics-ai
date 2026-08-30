"""In-process session store and event bus.

An analysis takes seconds to minutes and streams progress the whole time, so the
HTTP layer needs somewhere to keep a run's state and a way to hand events to
however many browsers are watching. This is that place.

It is deliberately in-memory. A forensics tool that persisted disk images and
their carved contents to a shared database would be creating a second copy of
exactly the data the user is most anxious about, and the failure mode of losing
a session on restart is far better than the failure mode of leaking one. Swapping
this for Redis is a contained change if a multi-worker deployment ever needs it.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agents.state import AgentEvent

MAX_BUFFERED_EVENTS = 2000
SESSION_TTL_SECONDS = 6 * 60 * 60


@dataclass
class Session:
    """One uploaded image and everything produced from it."""

    session_id: str
    image_path: Path
    image_name: str
    image_size: int
    owns_image: bool = True
    created_at: float = field(default_factory=time.time)
    status: str = "queued"
    error: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    events: list[AgentEvent] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    loop: asyncio.AbstractEventLoop | None = None
    rag: Any = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def summary(self) -> dict:
        verdicts = self.state.get("verdict_stats", {})
        return {
            "session_id": self.session_id,
            "image_name": self.image_name,
            "image_size": self.image_size,
            "created_at": self.created_at,
            "status": self.status,
            "error": self.error,
            "filesystem": self.state.get("filesystem"),
            "fragments": len(self.state.get("fragments", [])),
            "recoverable": verdicts.get("recoverable", 0),
            "partial": verdicts.get("partial", 0),
            "elapsed_seconds": (
                round(self.state["finished_at"] - self.state["started_at"], 2)
                if self.state.get("finished_at") and self.state.get("started_at")
                else None
            ),
        }


class SessionStore:
    """Thread-safe registry of active analysis sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, image_path: Path, image_name: str, image_size: int, owns_image: bool = True) -> Session:
        """Register a session.

        `owns_image` records whether this server put the file there. An upload is
        ours to delete; an image registered by path belongs to whoever pointed us
        at it, and deleting it would destroy the evidence the user asked us to
        examine.
        """
        session = Session(
            session_id=uuid.uuid4().hex[:16],
            image_path=image_path,
            image_name=image_name,
            image_size=image_size,
            owns_image=owns_image,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def list(self) -> list[Session]:
        with self._lock:
            return sorted(self._sessions.values(), key=lambda item: -item.created_at)

    def delete(self, session_id: str) -> bool:
        """Remove a session, and the image too when this server owns it.

        Deleting an upload matters more than tidiness: it is somebody's disk
        contents, and leaving it on the server after the analysis has been read
        turns a recovery tool into a liability. Deleting an image that was merely
        referenced by path would be far worse, since that is the user's only copy
        of a device they are trying to recover, so ownership is checked first.
        """
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        if session.rag is not None:
            try:
                session.rag.close()
            except Exception:
                pass
        if session.owns_image:
            try:
                session.image_path.unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def prune(self, ttl: int = SESSION_TTL_SECONDS) -> int:
        cutoff = time.time() - ttl
        stale = [
            session.session_id
            for session in self.list()
            if session.created_at < cutoff and session.status in ("complete", "failed")
        ]
        for session_id in stale:
            self.delete(session_id)
        return len(stale)

    def publish(self, session: Session, event: AgentEvent) -> None:
        """Fan an event out to every subscriber watching this session.

        The analysis runs on a worker thread while subscribers live on the event
        loop, so delivery is marshalled across with `call_soon_threadsafe`. Events
        are also buffered, because a browser that connects mid-run should see the
        stages that already happened rather than an empty timeline.
        """
        with session.lock:
            session.events.append(event)
            if len(session.events) > MAX_BUFFERED_EVENTS:
                del session.events[: len(session.events) - MAX_BUFFERED_EVENTS]
            subscribers = list(session.subscribers)
            loop = session.loop

        if loop is None:
            return
        for queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:
                continue

    def subscribe(self, session: Session) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with session.lock:
            session.subscribers.append(queue)
        return queue

    def unsubscribe(self, session: Session, queue: asyncio.Queue) -> None:
        with session.lock:
            if queue in session.subscribers:
                session.subscribers.remove(queue)


store = SessionStore()
