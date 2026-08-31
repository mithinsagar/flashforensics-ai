"""Session-scoped RAG agent for asking questions about a completed recovery run.

The fragment list is the wrong interface for a person who has just lost their
photos. They do not want to page through four hundred rows, they want to ask
whether their wedding pictures are in there. This agent indexes every fragment as
a natural-language record at the end of the run and answers questions against
that index.

Retrieval is scoped to a single session so an answer can never leak across
analyses, and every claim carries the fragment id it came from, which means an
answer can be checked against the actual bytes rather than taken on trust.
"""

from __future__ import annotations

import logging
import re

from ..knowledge.vectorstore import FragmentIndex
from ..llm.prompts import RAG_SYSTEM
from ..llm.provider import LLMError, LLMProvider

logger = logging.getLogger(__name__)

CITATION = re.compile(r"\[([0-9a-f]{12})\]")

FILTER_HINTS: dict[str, dict] = {
    "recoverable": {"recoverable": True},
    "recovered": {"recoverable": True},
    "complete": {"verdict": "RECOVERABLE"},
    "intact": {"verdict": "RECOVERABLE"},
    "damaged": {"verdict": "PARTIAL"},
    "partial": {"verdict": "PARTIAL"},
    "broken": {"verdict": "PARTIAL"},
    "junk": {"verdict": "JUNK"},
}

CATEGORY_HINTS: dict[str, str] = {
    "photo": "image",
    "photos": "image",
    "picture": "image",
    "pictures": "image",
    "image": "image",
    "images": "image",
    "video": "video",
    "videos": "video",
    "movie": "video",
    "clip": "video",
    "audio": "audio",
    "music": "audio",
    "song": "audio",
    "recording": "audio",
    "document": "document",
    "documents": "document",
    "doc": "document",
    "pdf": "document",
    "spreadsheet": "document",
    "archive": "archive",
    "zip": "archive",
    "database": "database",
    "app": "application",
}


class RagAgent:
    """Answers plain-language questions over one session's carved fragments."""

    def __init__(self, session_id: str, provider: LLMProvider, embedding_provider: str = "auto"):
        self.session_id = session_id
        self.provider = provider
        self.index = FragmentIndex(session_id, embedding_provider=embedding_provider)

    def ingest(self, fragments: list[dict]) -> int:
        return self.index.add(fragments)

    @property
    def size(self) -> int:
        return self.index.count()

    def _infer_filter(self, question: str) -> dict | None:
        """Derive a metadata filter from the question's own words.

        Pure vector search answers "what did you find" well and "how many videos
        are recoverable" badly, because counting needs a filter rather than a
        similarity ranking. Reading the obvious nouns out of the question and
        turning them into a metadata constraint fixes the counting questions
        without making the agent worse at the open-ended ones.
        """
        lowered = question.lower()
        clauses: list[dict] = []

        for word, category in CATEGORY_HINTS.items():
            if re.search(rf"\b{word}\b", lowered):
                clauses.append({"category": category})
                break

        for word, condition in FILTER_HINTS.items():
            if re.search(rf"\b{word}\b", lowered):
                clauses.append(condition)
                break

        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    def ask(self, question: str, limit: int = 6) -> dict:
        """Answer a question with citations back to specific fragments."""
        if self.index.count() == 0:
            return {
                "answer": "No fragments have been indexed for this session yet. Run an analysis first.",
                "citations": [],
                "retrieved": 0,
            }

        where = self._infer_filter(question)
        hits = self.index.search(question, limit=limit, where=where)
        if not hits and where:
            hits = self.index.search(question, limit=limit)

        if not hits:
            return {
                "answer": (
                    "Nothing in this session's fragments matches that question. Try asking about a "
                    "format such as photos, documents or video, or about the recovery verdicts."
                ),
                "citations": [],
                "retrieved": 0,
            }

        context = "\n\n".join(hit["document"] for hit in hits)

        try:
            answer = self.provider.complete(
                RAG_SYSTEM.format(context=context),
                question,
                max_tokens=600,
            )
        except LLMError as error:
            logger.warning("rag answer failed: %s", error)
            answer = self._fallback_answer(question, hits)

        cited = CITATION.findall(answer)
        citations = [
            {
                "fragment_id": hit["metadata"].get("fragment_id"),
                "format": hit["metadata"].get("format"),
                "offset": hit["metadata"].get("offset"),
                "length": hit["metadata"].get("length"),
                "verdict": hit["metadata"].get("verdict"),
                "similarity": hit["similarity"],
                "cited_in_answer": hit["metadata"].get("fragment_id") in cited,
            }
            for hit in hits
        ]

        return {
            "answer": answer.strip(),
            "citations": citations,
            "retrieved": len(hits),
            "filter_applied": where,
        }

    def _fallback_answer(self, question: str, hits: list[dict]) -> str:
        """Compose an answer directly from retrieved metadata when no model answers."""
        lines = [f"{len(hits)} fragments in this session relate to that question:"]
        for hit in hits[:6]:
            metadata = hit["metadata"]
            size_kb = metadata.get("length", 0) / 1024
            size = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
            lines.append(
                f"  [{metadata.get('fragment_id')}] {metadata.get('format')}, {size}, "
                f"verdict {metadata.get('verdict')}, at offset {metadata.get('offset')}"
            )
        return "\n".join(lines)

    def close(self) -> None:
        self.index.reset()
