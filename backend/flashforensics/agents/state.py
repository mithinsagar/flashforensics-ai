"""Shared state passed between agents in the recovery graph.

The state is deliberately flat and mostly plain data. Each agent reads the fields
its predecessors filled and writes its own, which keeps the pipeline inspectable:
a failure can be traced to the node that produced the bad field rather than to
the graph as a whole.

The one non-data field is the event emitter, which agents call to stream progress
to the browser. It lives in the state rather than in a global so that two analyses
running at once cannot write into each other's event stream.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypedDict


class Stage(str, Enum):
    QUEUED = "queued"
    SCANNING = "scanning"
    MAPPING = "mapping"
    CARVING = "carving"
    CLASSIFYING = "classifying"
    ADJUDICATING = "adjudicating"
    REPORTING = "reporting"
    COMPLETE = "complete"
    FAILED = "failed"


STAGE_LABELS: dict[Stage, str] = {
    Stage.QUEUED: "Waiting to start",
    Stage.SCANNING: "Parsing the filesystem",
    Stage.MAPPING: "Mapping entropy across the volume",
    Stage.CARVING: "Carving orphaned regions",
    Stage.CLASSIFYING: "Identifying carved fragments",
    Stage.ADJUDICATING: "Judging recoverability",
    Stage.REPORTING: "Writing the report",
    Stage.COMPLETE: "Analysis complete",
    Stage.FAILED: "Analysis failed",
}


@dataclass
class AgentEvent:
    """One progress event streamed to the dashboard."""

    stage: str
    message: str
    percent: int = 0
    agent: str = "system"
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "message": self.message,
            "percent": self.percent,
            "agent": self.agent,
            "data": self.data,
            "timestamp": self.timestamp,
        }


EventEmitter = Callable[[AgentEvent], None]


class RecoveryState(TypedDict, total=False):
    """Everything the graph accumulates during one analysis run."""

    session_id: str
    image_path: str
    image_name: str
    image_size: int

    emit: Any
    settings: Any
    provider: Any
    knowledge: Any

    stage: str
    started_at: float
    finished_at: float
    error: str

    filesystem: str
    boot_sector: dict
    files: list[dict]
    damage: list[dict]
    filesystem_summary: dict
    orphan_runs: list[list[int]]
    referenced_ranges: list[list[int]]
    cluster_size: int

    entropy_points: list[dict]
    entropy_detail: dict
    entropy_stats: dict
    anomalies: list[dict]

    fragments: list[dict]
    carve_stats: dict

    classification_stats: dict
    verdict_stats: dict
    report: str
    provider_health: dict

    _image: Any
    _entropy_map: Any
    _fragment_objects: Any
    _carver: Any
    _parser: Any
    _entries: Any
    _rag: Any


def emit(state: RecoveryState, stage: Stage, message: str, percent: int = 0, agent: str = "system", **data) -> None:
    """Send a progress event if the caller supplied an emitter."""
    emitter: EventEmitter | None = state.get("emit")
    if emitter is None:
        return
    emitter(AgentEvent(stage=stage.value, message=message, percent=percent, agent=agent, data=data))
