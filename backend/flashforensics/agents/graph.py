"""The LangGraph state machine that runs a recovery analysis.

    scan -> carve -> classify -> adjudicate -> report

Four analysis agents in sequence plus a reporting step. The pipeline is linear
because the dependencies genuinely are: nothing can be carved before the scanner
has isolated the orphaned regions, nothing can be classified before it has been
carved, and nothing can be judged before it has been identified. Branching here
would be decoration.

What the graph buys, over a plain function chain, is a typed state object every
node reads and writes, a conditional edge that routes a failure straight to the
end instead of letting a broken image cascade through four more stages, and the
ability to add a repair or reassembly branch later without restructuring the
callers.
"""

from __future__ import annotations

import logging
import time

from langgraph.graph import END, StateGraph

from ..knowledge.vectorstore import KnowledgeBase
from ..llm.prompts import TRIAGE_SYSTEM, TRIAGE_USER
from ..llm.provider import LLMError, build_provider
from .adjudicator import adjudicate_fragments
from .carver_agent import carve_fragments
from .classifier import classify_fragments
from .rag import RagAgent
from .scanner import scan_filesystem
from .state import RecoveryState, Stage, emit

logger = logging.getLogger(__name__)


def write_report(state: RecoveryState) -> RecoveryState:
    """Index the fragments for question answering and write the closing briefing."""
    if state.get("stage") == Stage.FAILED.value:
        return state

    emit(state, Stage.REPORTING, "Indexing fragments for question answering", 95, agent="reporter")

    provider = state["provider"]
    fragments = state.get("fragments", [])
    embedding_provider = getattr(state.get("settings"), "embedding_provider", "auto")

    rag = RagAgent(state["session_id"], provider, embedding_provider)
    indexed = rag.ingest(fragments)
    state["_rag"] = rag

    emit(state, Stage.REPORTING, f"Indexed {indexed} fragments, writing the summary", 97, agent="reporter")

    verdicts = state.get("verdict_stats", {})
    damage = state.get("damage", [])
    damage_text = "\n".join(f"- {item['detail']}" for item in damage[:12]) or "- none recorded"

    try:
        report = provider.complete(
            TRIAGE_SYSTEM,
            TRIAGE_USER.format(
                image_name=state.get("image_name", "image"),
                image_size=f"{state.get('image_size', 0) / (1024 * 1024):.0f} MB",
                filesystem=state.get("filesystem", "unknown"),
                filesystem_summary=_summary_lines(state.get("filesystem_summary", {})),
                damage=damage_text,
                fragment_count=len(fragments),
                recoverable=verdicts.get("recoverable", 0),
                partial=verdicts.get("partial", 0),
                metadata_only=verdicts.get("metadata_only", 0),
                junk=verdicts.get("junk", 0),
                formats=", ".join(verdicts.get("formats", {}).keys()) or "none",
            ),
            max_tokens=400,
        ).strip()
    except LLMError as error:
        logger.warning("report generation failed: %s", error)
        report = (
            f"Recovered {verdicts.get('recoverable', 0)} complete files and "
            f"{verdicts.get('partial', 0)} partial ones from {len(fragments)} carved fragments."
        )

    close_image(state)

    state.update(
        {
            "report": report,
            "stage": Stage.COMPLETE.value,
            "finished_at": time.time(),
            "provider_health": provider.health(),
        }
    )

    emit(
        state,
        Stage.COMPLETE,
        "Analysis complete",
        100,
        agent="reporter",
        report=report,
        elapsed_seconds=round(state["finished_at"] - state.get("started_at", state["finished_at"]), 2),
    )
    return state


def _summary_lines(summary: dict) -> str:
    if not summary:
        return "- no filesystem could be parsed"
    interesting = (
        "files_found",
        "directories_found",
        "clusters_allocated",
        "clusters_referenced",
        "clusters_orphaned",
        "fat_mirror_mismatches",
    )
    return "\n".join(f"- {key.replace('_', ' ')}: {summary[key]}" for key in interesting if key in summary)


def route_after_scan(state: RecoveryState) -> str:
    return "failed" if state.get("stage") == Stage.FAILED.value else "continue"


def build_graph():
    """Compile the recovery pipeline."""
    graph = StateGraph(RecoveryState)

    graph.add_node("scan", scan_filesystem)
    graph.add_node("carve", carve_fragments)
    graph.add_node("classify", classify_fragments)
    graph.add_node("adjudicate", adjudicate_fragments)
    graph.add_node("report", write_report)

    graph.set_entry_point("scan")
    graph.add_conditional_edges("scan", route_after_scan, {"continue": "carve", "failed": END})
    graph.add_edge("carve", "classify")
    graph.add_edge("classify", "adjudicate")
    graph.add_edge("adjudicate", "report")
    graph.add_edge("report", END)

    return graph.compile()


_compiled_graph = None
_knowledge_base: KnowledgeBase | None = None


def get_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def get_knowledge_base(settings) -> KnowledgeBase:
    """Build the format index once and share it across analyses.

    Loading the embedding model and indexing sixty-eight documents takes a few
    seconds, and it produces exactly the same index every time, so doing it per
    request would add that cost to every upload for no benefit.
    """
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = KnowledgeBase(settings.knowledge_dir, settings.embedding_provider)
    return _knowledge_base


def close_image(state: RecoveryState) -> None:
    image = state.get("_image")
    if image is not None:
        try:
            image.close()
        except Exception:
            logger.debug("image was already closed")
        state["_image"] = None


def run_analysis(
    session_id: str,
    image_path: str,
    image_name: str,
    image_size: int,
    settings,
    emitter=None,
) -> RecoveryState:
    """Execute the full pipeline for one image and return the final state."""
    provider = build_provider(settings)
    knowledge = get_knowledge_base(settings)

    initial: RecoveryState = {
        "session_id": session_id,
        "image_path": image_path,
        "image_name": image_name,
        "image_size": image_size,
        "emit": emitter,
        "settings": settings,
        "provider": provider,
        "knowledge": knowledge,
        "stage": Stage.QUEUED.value,
        "started_at": time.time(),
    }

    try:
        return get_graph().invoke(initial, {"recursion_limit": 24})
    except Exception as error:
        logger.exception("analysis failed")
        close_image(initial)
        initial.update(
            {"stage": Stage.FAILED.value, "error": str(error), "finished_at": time.time()}
        )
        emit(initial, Stage.FAILED, f"Analysis failed: {error}", 0, agent="system")
        return initial
