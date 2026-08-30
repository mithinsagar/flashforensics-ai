"""Adjudicator agent: decide what is worth the user's attention, and say why.

This is where the project earns its premise. Conventional carvers stop at
extraction and hand back a folder of unnamed files, leaving triage to a person
opening them one at a time. This agent does that triage: every fragment gets a
status, a plain-language explanation of what survived and what did not, and a
priority that determines where it lands in the ranked list.

Four statuses, and the boundaries between them are drawn from structural evidence
rather than from the format's reputation. A PNG whose chunks are all present but
whose CRCs fail is PARTIAL, not RECOVERABLE, because a checksum failure is proof
that stored bytes changed. Getting that ordering right is the difference between
a verdict and a guess dressed up as one.
"""

from __future__ import annotations

import logging

from ..llm.prompts import ADJUDICATOR_SYSTEM, ADJUDICATOR_USER
from ..llm.provider import LLMError
from .state import RecoveryState, Stage, emit

logger = logging.getLogger(__name__)

STATUS_ORDER = {"RECOVERABLE": 0, "PARTIAL": 1, "METADATA_ONLY": 2, "JUNK": 3}


def adjudicate_fragments(state: RecoveryState) -> RecoveryState:
    """Assign a recovery verdict to every fragment and rank the results."""
    if state.get("stage") == Stage.FAILED.value:
        return state

    fragments = state.get("fragments", [])
    if not fragments:
        state.update({"stage": Stage.REPORTING.value, "verdict_stats": _empty_stats()})
        return state

    provider = state["provider"]
    settings = state["settings"]
    budget = settings.llm_max_fragments

    for index, fragment in enumerate(fragments):
        percent = 86 + int((index / max(1, len(fragments))) * 8)
        validation = fragment.get("validation") or {}
        classification = fragment.get("classification") or {}

        if index % 10 == 0:
            emit(
                state,
                Stage.ADJUDICATING,
                f"Judging recoverability, {index} of {len(fragments)}",
                percent,
                agent="adjudicator",
            )

        provenance = (
            "clusters still marked allocated but with no directory entry pointing at them"
            if fragment.get("in_orphaned_region")
            else "space the filesystem reports as free but which still holds data"
        )

        if budget <= 0 or not provider.supports_reasoning:
            fragment["verdict"] = _rule_verdict(fragment, validation, classification)
            continue

        try:
            answer = provider.complete_json(
                ADJUDICATOR_SYSTEM,
                ADJUDICATOR_USER.format(
                    fragment_id=fragment["fragment_id"],
                    format=classification.get("format", fragment.get("format_guess", "unknown")),
                    category=fragment.get("category", "unknown"),
                    length=fragment["length"],
                    offset=fragment["offset"],
                    provenance=provenance,
                    header_valid=validation.get("header_valid"),
                    footer_present=validation.get("footer_present"),
                    structure_complete=validation.get("structure_complete"),
                    evidence="; ".join(validation.get("evidence") or []) or "none",
                    problems="; ".join(validation.get("problems") or []) or "none",
                    metadata=str(validation.get("metadata") or {})[:800],
                    entropy=fragment.get("entropy"),
                ),
                max_tokens=350,
            )
            status = str(answer.get("status", "JUNK")).upper()
            if status not in STATUS_ORDER:
                status = "JUNK"
            fragment["verdict"] = {
                "status": status,
                "recoverable": bool(answer.get("recoverable", status in ("RECOVERABLE", "PARTIAL"))),
                "confidence": float(answer.get("confidence", 0.5)),
                "explanation": answer.get("explanation", ""),
                "user_priority": int(answer.get("user_priority", 3)),
                "method": provider.name if provider.name != "heuristic" else "rules",
            }
            if provider.name != "heuristic":
                budget -= 1
        except (LLMError, ValueError, TypeError) as error:
            logger.warning("adjudication of %s failed: %s", fragment["fragment_id"], error)
            fragment["verdict"] = _rule_verdict(fragment, validation, classification)

    ranked = sorted(
        fragments,
        key=lambda item: (
            STATUS_ORDER.get((item.get("verdict") or {}).get("status", "JUNK"), 3),
            -int((item.get("verdict") or {}).get("user_priority", 1)),
            -item["length"],
        ),
    )
    for rank, fragment in enumerate(ranked, start=1):
        fragment["rank"] = rank

    stats = _summarise(ranked)
    emit(
        state,
        Stage.ADJUDICATING,
        (
            f"{stats['recoverable']} fully recoverable, {stats['partial']} partial, "
            f"{stats['junk']} discarded as chance matches"
        ),
        94,
        agent="adjudicator",
        **stats,
    )

    state.update({"fragments": ranked, "verdict_stats": stats, "stage": Stage.REPORTING.value})
    return state


def _rule_verdict(fragment: dict, validation: dict, classification: dict) -> dict:
    """Deterministic verdict used when no model is available or the budget ran out."""
    problems = " ".join(validation.get("problems") or []).lower()
    metadata = validation.get("metadata") or {}
    complete = validation.get("structure_complete", False)
    header_valid = validation.get("header_valid", False)
    footer = validation.get("footer_present", False)
    crc_failures = metadata.get("crc_failures", 0)
    fmt = classification.get("format", fragment.get("format_guess", "unknown"))

    priority_by_category = {
        "image": 5, "video": 5, "document": 4, "audio": 4,
        "archive": 3, "database": 3, "application": 2, "text": 2, "font": 1,
    }
    priority = priority_by_category.get(fragment.get("category", "unknown"), 2)

    if complete and not crc_failures:
        return {
            "status": "RECOVERABLE",
            "recoverable": True,
            "confidence": 0.92,
            "explanation": (
                f"The {fmt} structure runs intact from its header to its end marker and every "
                f"integrity check passed, so this file should open normally."
            ),
            "user_priority": priority,
            "method": "rules",
        }

    if crc_failures:
        return {
            "status": "PARTIAL",
            "recoverable": True,
            "confidence": 0.8,
            "explanation": (
                f"The {fmt} file is whole but {crc_failures} checksum checks fail, so some stored "
                f"content was altered. It will open with visible damage."
            ),
            "user_priority": max(1, priority - 1),
            "method": "rules",
        }

    if header_valid and ("truncat" in problems or "no iend" in problems or "%%eof" in problems or "zero-filled" in problems or "tail are lost" in problems or not footer):
        content_survives = (
            validation.get("confidence", 0.0) >= 0.5
            or any(
                key in metadata
                for key in (
                    "width", "height", "title", "page_objects", "duration_seconds",
                    "page_count", "entries_seen", "frames", "objects", "lines",
                )
            )
        )
        if content_survives:
            return {
                "status": "PARTIAL",
                "recoverable": True,
                "confidence": 0.7,
                "explanation": (
                    f"The start of this {fmt} survived and its properties are readable, but the end "
                    f"is missing, so the tail of the content is gone."
                ),
                "user_priority": max(1, priority - 1),
                "method": "rules",
            }
        return {
            "status": "METADATA_ONLY",
            "recoverable": False,
            "confidence": 0.6,
            "explanation": (
                f"Only the {fmt} header region survived. The file will not open, though its recorded "
                f"properties can still be read."
            ),
            "user_priority": 1,
            "method": "rules",
        }

    return {
        "status": "JUNK",
        "recoverable": False,
        "confidence": 0.7,
        "explanation": (
            "No coherent structure follows the signature, so this is a chance byte pattern inside "
            "unrelated data rather than a real file."
        ),
        "user_priority": 1,
        "method": "rules",
    }


def _summarise(fragments: list[dict]) -> dict:
    stats = _empty_stats()
    formats: dict[str, int] = {}
    for fragment in fragments:
        status = (fragment.get("verdict") or {}).get("status", "JUNK")
        key = {
            "RECOVERABLE": "recoverable",
            "PARTIAL": "partial",
            "METADATA_ONLY": "metadata_only",
            "JUNK": "junk",
        }.get(status, "junk")
        stats[key] += 1
        if status != "JUNK":
            fmt = (fragment.get("classification") or {}).get("format", "unknown")
            formats[fmt] = formats.get(fmt, 0) + 1
    stats["total"] = len(fragments)
    stats["formats"] = dict(sorted(formats.items(), key=lambda item: -item[1]))
    stats["bytes_recoverable"] = sum(
        fragment["length"]
        for fragment in fragments
        if (fragment.get("verdict") or {}).get("recoverable")
    )
    return stats


def _empty_stats() -> dict:
    return {
        "recoverable": 0,
        "partial": 0,
        "metadata_only": 0,
        "junk": 0,
        "total": 0,
        "formats": {},
        "bytes_recoverable": 0,
    }
