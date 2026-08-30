"""Classifier agent: decide what each fragment actually is.

The interesting cases are the ambiguous ones. A fragment starting with PK 03 04
could be any of eight formats and a fragment with an ftyp box could be any of
six, so the header alone is not an answer. This agent resolves them in three
steps, cheapest first.

Structural evidence comes first, because it is decisive when available: entry
names inside a zip container or the brand string in an ISO base media box settle
the question outright, and no model is consulted.

Retrieval comes second. The fragment's measured properties are written as a
sentence and matched against the format knowledge base, which narrows an
unresolved fragment to a shortlist drawn from a fixed corpus rather than from a
model's free association.

The model comes last and only for what the first two steps left open. That
ordering keeps the run cheap, keeps it deterministic where determinism is
available, and keeps the model doing the one job it is better at than a rule,
which is weighing partial evidence and saying so in words.
"""

from __future__ import annotations

import logging

from ..llm.prompts import CLASSIFIER_SYSTEM, CLASSIFIER_USER
from ..llm.provider import LLMError
from .state import RecoveryState, Stage, emit

logger = logging.getLogger(__name__)

STRUCTURAL_CONFIDENCE_FLOOR = 0.85


def classify_fragments(state: RecoveryState) -> RecoveryState:
    """Assign a format and a reason to every carved fragment."""
    if state.get("stage") == Stage.FAILED.value:
        return state

    fragments = state.get("fragments", [])
    if not fragments:
        state.update({"stage": Stage.ADJUDICATING.value, "classification_stats": {"classified": 0}})
        return state

    knowledge = state["knowledge"]
    provider = state["provider"]
    settings = state["settings"]

    resolved_structurally = 0
    resolved_by_model = 0
    resolved_by_retrieval = 0
    ambiguous_seen = 0
    model_budget = settings.llm_max_fragments

    for index, fragment in enumerate(fragments):
        percent = 76 + int((index / max(1, len(fragments))) * 9)
        validation = fragment.get("validation") or {}
        candidates = fragment.get("candidates", [])
        is_ambiguous = bool(fragment.get("ambiguity_group")) and len(set(candidates)) > 1
        ambiguous_seen += 1 if is_ambiguous else 0

        observation = _describe_observation(fragment)
        knowledge_hits = (
            knowledge.query_within(observation, list(set(candidates)), limit=4)
            if candidates
            else knowledge.query(observation, limit=4)
        )
        fragment["knowledge_matches"] = knowledge_hits[:3]

        detected = validation.get("format_detected")
        structural_confidence = validation.get("confidence", 0.0)

        if detected and structural_confidence >= STRUCTURAL_CONFIDENCE_FLOOR:
            evidence = (validation.get("evidence") or ["internal structure validated"])[-1]
            fragment["classification"] = {
                "format": detected,
                "confidence": round(min(0.99, structural_confidence), 3),
                "reasoning": f"Resolved by structural inspection: {evidence}.",
                "alternatives": [item for item in candidates if item != detected][:4],
                "method": "structural",
            }
            resolved_structurally += 1
            continue

        if not is_ambiguous and detected and structural_confidence >= 0.5:
            fragment["classification"] = {
                "format": detected,
                "confidence": round(structural_confidence, 3),
                "reasoning": (
                    f"Header matched a single format and the {detected} structure partially "
                    f"validated, so no disambiguation was needed."
                ),
                "alternatives": [],
                "method": "structural",
            }
            resolved_structurally += 1
            continue

        if model_budget <= 0 or not provider.supports_reasoning:
            fragment["classification"] = _retrieval_classification(
                fragment, validation, candidates, knowledge_hits, model_budget <= 0
            )
            resolved_by_retrieval += 1
            continue

        emit(
            state,
            Stage.CLASSIFYING,
            f"Disambiguating fragment {fragment['fragment_id']} across {len(set(candidates))} candidate formats",
            percent,
            agent="classifier",
        )

        try:
            answer = provider.complete_json(
                CLASSIFIER_SYSTEM,
                CLASSIFIER_USER.format(
                    fragment_id=fragment["fragment_id"],
                    offset=fragment["offset"],
                    length=fragment["length"],
                    candidates=", ".join(candidates) or "none",
                    ambiguity_group=fragment.get("ambiguity_group") or "none",
                    detected=detected or "none",
                    header_valid=validation.get("header_valid"),
                    footer_present=validation.get("footer_present"),
                    structure_complete=validation.get("structure_complete"),
                    validator_confidence=validation.get("confidence"),
                    evidence="; ".join(validation.get("evidence") or []) or "none",
                    problems="; ".join(validation.get("problems") or []) or "none",
                    metadata=_trim_metadata(validation.get("metadata") or {}),
                    entropy=fragment.get("entropy"),
                    chi_square=fragment.get("chi_square"),
                    printable_ratio=fragment.get("printable_ratio"),
                    header_hex=fragment.get("header_hex"),
                    knowledge=_format_knowledge(knowledge_hits),
                ),
                max_tokens=400,
            )
            fragment["classification"] = {
                "format": answer.get("format", detected or "unknown"),
                "confidence": float(answer.get("confidence", 0.5)),
                "reasoning": answer.get("reasoning", ""),
                "alternatives": answer.get("alternatives", [])[:4],
                "method": provider.name if provider.name != "heuristic" else "rules",
            }
            resolved_by_model += 1
            model_budget -= 1
        except (LLMError, ValueError, TypeError) as error:
            logger.warning("classification of %s failed: %s", fragment["fragment_id"], error)
            fragment["classification"] = _retrieval_classification(
                fragment, validation, candidates, knowledge_hits, False
            )
            resolved_by_retrieval += 1

    emit(
        state,
        Stage.CLASSIFYING,
        f"Identified {len(fragments)} fragments, {ambiguous_seen} needed disambiguation",
        85,
        agent="classifier",
        structural=resolved_structurally,
        adjudicated=resolved_by_model,
    )

    state.update(
        {
            "fragments": fragments,
            "classification_stats": {
                "classified": len(fragments),
                "ambiguous_headers": ambiguous_seen,
                "resolved_structurally": resolved_structurally,
                "resolved_by_adjudication": resolved_by_model,
                "resolved_by_retrieval": resolved_by_retrieval,
            },
            "stage": Stage.ADJUDICATING.value,
        }
    )
    return state


def _retrieval_classification(
    fragment: dict,
    validation: dict,
    candidates: list[str],
    knowledge_hits: list[dict],
    budget_exhausted: bool,
) -> dict:
    """Classify without a model, using retrieval ranking over the candidate set.

    This runs whenever no reasoning model is configured, a call failed, or the
    per-run budget is spent. It is the single rule path for the task, so the
    no-key install and the degraded-call case cannot drift apart the way two
    separate implementations would.

    The nearest reference entry is only trusted when retrieval actually
    discriminated. On a tie the header order wins, because an arbitrary pick from
    a flat similarity ranking is a coin flip dressed up as a decision.
    """
    detected = validation.get("format_detected")
    top = knowledge_hits[0] if knowledge_hits else None
    runner_up = knowledge_hits[1] if len(knowledge_hits) > 1 else None
    margin = (top or {}).get("similarity", 0.0) - (runner_up or {}).get("similarity", 0.0)
    discriminated = top is not None and (runner_up is None or margin > 0.02)

    if detected:
        chosen = detected
        confidence = max(0.5, float(validation.get("confidence", 0.5)))
        reason = f"Structural validation resolved this to {detected}, without needing disambiguation."
    elif discriminated:
        chosen = top["extension"]
        confidence = round(min(0.7, 0.45 + margin * 2), 3)
        reason = (
            f"Structure did not validate, so the format was ranked against the reference index; "
            f"{chosen} was the clearest match among {len(candidates) or 'all'} candidates."
        )
    else:
        chosen = candidates[0] if candidates else "unknown"
        confidence = 0.35
        reason = (
            "The header matches several formats and neither structure nor retrieval separated "
            "them, so this is unresolved rather than identified."
        )

    if budget_exhausted:
        reason += " The model adjudication budget for this run was already spent."

    return {
        "format": chosen,
        "confidence": confidence,
        "reasoning": reason,
        "alternatives": [item for item in candidates if item != chosen][:4]
        or [hit["extension"] for hit in knowledge_hits[1:4]],
        "method": "retrieval",
    }


def _describe_observation(fragment: dict) -> str:
    """Turn measurements into the sentence that gets embedded for retrieval."""
    validation = fragment.get("validation") or {}
    metadata = validation.get("metadata") or {}
    parts = [
        f"A {fragment['length']} byte fragment with entropy {fragment.get('entropy')} bits per byte,",
        f"printable ratio {fragment.get('printable_ratio')},",
        f"header bytes {fragment.get('header_hex', '')[:16]}.",
    ]
    if fragment.get("candidates"):
        parts.append("Magic bytes match " + ", ".join(fragment["candidates"]) + ".")
    if validation.get("evidence"):
        parts.append(" ".join(validation["evidence"][:3]))
    if metadata.get("entry_names"):
        parts.append("Archive entries include " + ", ".join(metadata["entry_names"][:10]) + ".")
    if metadata.get("boxes"):
        parts.append("Container boxes include " + ", ".join(metadata["boxes"][:8]) + ".")
    if metadata.get("major_brand"):
        parts.append(f"Brand string {metadata['major_brand']}.")
    return " ".join(parts)


def _format_knowledge(hits: list[dict]) -> str:
    if not hits:
        return "  (no close matches)"
    lines = []
    for hit in hits:
        lines.append(f"  [{hit['extension']}] similarity {hit['similarity']}: {hit['document'][:300]}")
    return "\n".join(lines)


def _trim_metadata(metadata: dict) -> str:
    """Keep the prompt small by dropping long lists down to what matters."""
    trimmed = {}
    for key, value in metadata.items():
        if isinstance(value, list):
            trimmed[key] = value[:10]
        elif isinstance(value, str) and len(value) > 200:
            trimmed[key] = value[:200]
        else:
            trimmed[key] = value
    return str(trimmed)[:1200]
