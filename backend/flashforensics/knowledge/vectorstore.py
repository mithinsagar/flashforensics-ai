"""ChromaDB indexes: the file-type knowledge base and per-session fragment memory.

Two collections serve different jobs.

The file-type collection is built once from `filetypes.py` and never changes. The
classifier turns a fragment's measured properties into a sentence and retrieves
the nearest format descriptions, which gives the language model a shortlist
grounded in a fixed corpus instead of letting it free-associate a format name.

The fragment collection is created per analysis session and holds one document
per carved fragment, describing what was found and what the validators concluded.
That is what the RAG agent searches when the user asks a question in plain
language, and it is why an answer can cite the specific fragment it came from.

Embeddings use all-MiniLM-L6-v2 through Chroma's bundled ONNX runtime rather than
sentence-transformers, which keeps the install free of a PyTorch dependency while
using the same model.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

from .embeddings import build_embedding_function
from .filetypes import FILE_TYPES, as_documents

logger = logging.getLogger(__name__)

FILETYPE_COLLECTION = "filetype_knowledge"
EMBEDDING_MODEL = "all-MiniLM-L6-v2"


class KnowledgeBase:
    """Vector index over format descriptions, used to disambiguate fragments."""

    def __init__(self, persist_directory: str | Path | None = None):
        self.persist_directory = Path(persist_directory) if persist_directory else None
        if self.persist_directory:
            self.persist_directory.mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(
                path=str(self.persist_directory),
                settings=Settings(anonymized_telemetry=False, allow_reset=True),
            )
        else:
            self.client = chromadb.EphemeralClient(
                settings=Settings(anonymized_telemetry=False, allow_reset=True)
            )
        self.embedding_function, self.embedding_info = build_embedding_function()
        self.collection = self.client.get_or_create_collection(
            name=self._collection_name(),
            metadata={"hnsw:space": "cosine"},
            embedding_function=self.embedding_function,
        )
        self._ensure_indexed()

    def _collection_name(self) -> str:
        """Namespace the collection by embedder so two models never share vectors.

        A persisted index built with MiniLM and then queried with the hashing
        fallback would return confident nonsense, because the vectors live in
        unrelated spaces. Keying the collection name to the active embedder makes
        that mistake impossible rather than merely unlikely.
        """
        return f"{FILETYPE_COLLECTION}__{self.embedding_info['embedding_model'].replace('-', '_')}"

    def _ensure_indexed(self) -> None:
        if self.collection.count() >= len(FILE_TYPES):
            return
        ids, documents, metadatas = as_documents()
        self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        logger.info("indexed %d file type descriptions", len(ids))

    @property
    def size(self) -> int:
        return self.collection.count()

    def query(self, description: str, limit: int = 5) -> list[dict[str, Any]]:
        """Retrieve the format descriptions closest to an observation."""
        response = self.collection.query(query_texts=[description], n_results=limit)
        results: list[dict[str, Any]] = []
        documents = response.get("documents") or [[]]
        metadatas = response.get("metadatas") or [[]]
        distances = response.get("distances") or [[]]

        for index, document in enumerate(documents[0]):
            metadata = metadatas[0][index] if index < len(metadatas[0]) else {}
            distance = distances[0][index] if index < len(distances[0]) else 1.0
            results.append(
                {
                    "extension": metadata.get("extension", "unknown"),
                    "name": metadata.get("name", ""),
                    "category": metadata.get("category", "unknown"),
                    "ambiguous": metadata.get("ambiguous", False),
                    "document": document,
                    "distance": round(float(distance), 4),
                    "similarity": round(1.0 - float(distance), 4),
                }
            )
        return results

    def query_within(self, description: str, extensions: list[str], limit: int = 5) -> list[dict[str, Any]]:
        """Rank a known candidate set rather than searching the whole corpus.

        When a fragment's header narrows it to the zip family, searching all
        sixty-eight formats invites the model to consider answers the bytes have
        already ruled out. Restricting the query to the candidate set keeps
        retrieval inside what the evidence permits.
        """
        if not extensions:
            return self.query(description, limit)
        response = self.collection.query(
            query_texts=[description],
            n_results=min(limit, max(1, len(extensions))),
            where={"extension": {"$in": extensions}},
        )
        results: list[dict[str, Any]] = []
        documents = response.get("documents") or [[]]
        metadatas = response.get("metadatas") or [[]]
        distances = response.get("distances") or [[]]
        for index, document in enumerate(documents[0]):
            metadata = metadatas[0][index] if index < len(metadatas[0]) else {}
            distance = distances[0][index] if index < len(distances[0]) else 1.0
            results.append(
                {
                    "extension": metadata.get("extension", "unknown"),
                    "name": metadata.get("name", ""),
                    "category": metadata.get("category", "unknown"),
                    "document": document,
                    "distance": round(float(distance), 4),
                    "similarity": round(1.0 - float(distance), 4),
                }
            )
        return results


class FragmentIndex:
    """Per-session vector index over carved fragments, backing the RAG agent."""

    def __init__(self, session_id: str, client: chromadb.ClientAPI | None = None):
        self.session_id = session_id
        self.client = client or chromadb.EphemeralClient(
            settings=Settings(anonymized_telemetry=False, allow_reset=True)
        )
        self.embedding_function, self.embedding_info = build_embedding_function()
        self.collection = self.client.get_or_create_collection(
            name=f"fragments_{session_id}",
            metadata={"hnsw:space": "cosine"},
            embedding_function=self.embedding_function,
        )

    def add(self, fragments: list[dict]) -> int:
        """Index one document per fragment, phrased for natural-language search."""
        if not fragments:
            return 0

        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict] = []

        for fragment in fragments:
            ids.append(fragment["fragment_id"])
            documents.append(describe_fragment(fragment))
            metadatas.append(
                {
                    "fragment_id": fragment["fragment_id"],
                    "offset": fragment["offset"],
                    "length": fragment["length"],
                    "format": fragment.get("format_guess", "unknown"),
                    "category": fragment.get("category", "unknown"),
                    "verdict": (fragment.get("verdict") or {}).get("status", "unknown"),
                    "recoverable": bool((fragment.get("verdict") or {}).get("recoverable", False)),
                    "entropy": fragment.get("entropy", 0.0),
                }
            )

        self.collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        return len(ids)

    def search(self, question: str, limit: int = 6, where: dict | None = None) -> list[dict]:
        count = self.collection.count()
        if count == 0:
            return []
        response = self.collection.query(
            query_texts=[question],
            n_results=min(limit, count),
            where=where,
        )
        documents = response.get("documents") or [[]]
        metadatas = response.get("metadatas") or [[]]
        distances = response.get("distances") or [[]]

        results: list[dict] = []
        for index, document in enumerate(documents[0]):
            metadata = metadatas[0][index] if index < len(metadatas[0]) else {}
            distance = distances[0][index] if index < len(distances[0]) else 1.0
            results.append(
                {
                    "document": document,
                    "metadata": metadata,
                    "similarity": round(1.0 - float(distance), 4),
                }
            )
        return results

    def count(self) -> int:
        return self.collection.count()

    def reset(self) -> None:
        try:
            self.client.delete_collection(f"fragments_{self.session_id}")
        except Exception:
            logger.debug("fragment collection for %s was already gone", self.session_id)


def describe_fragment(fragment: dict) -> str:
    """Write the sentence that gets embedded for a fragment.

    Phrasing matters more than it looks. The user asks questions like "did you
    find any photos from a Canon camera" or "what is the biggest recoverable
    video", so the document has to carry the words a person would use: the format
    name, the category, the verdict in plain language, the size in human units,
    and any EXIF strings, document titles or archive entry names that were pulled
    out during validation.
    """
    validation = fragment.get("validation") or {}
    verdict = fragment.get("verdict") or {}
    metadata = validation.get("metadata") or {}
    classification = fragment.get("classification") or {}

    size_kb = fragment.get("length", 0) / 1024
    size_text = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"

    parts = [
        f"Fragment {fragment.get('fragment_id')} at byte offset {fragment.get('offset')} "
        f"on sector {fragment.get('sector_start')}.",
        f"Identified as {fragment.get('format_guess', 'unknown')} "
        f"({fragment.get('category', 'unknown')} file), {size_text}.",
    ]

    if classification.get("reasoning"):
        parts.append(f"Classification reasoning: {classification['reasoning']}")
    if verdict.get("status"):
        parts.append(f"Recovery verdict: {verdict['status']}.")
    if verdict.get("explanation"):
        parts.append(verdict["explanation"])

    if metadata.get("width") and metadata.get("height"):
        parts.append(f"Image dimensions {metadata['width']} by {metadata['height']} pixels.")
    if metadata.get("exif_strings"):
        parts.append("EXIF camera data: " + ", ".join(metadata["exif_strings"]) + ".")
    if metadata.get("title"):
        parts.append(f"Document title: {metadata['title']}.")
    if metadata.get("entry_names"):
        parts.append("Archive contains: " + ", ".join(metadata["entry_names"][:12]) + ".")
    if metadata.get("duration_seconds"):
        parts.append(f"Media duration {metadata['duration_seconds']} seconds.")
    if metadata.get("page_objects"):
        parts.append(f"Document has {metadata['page_objects']} pages.")
    if metadata.get("major_brand"):
        parts.append(f"Container brand {metadata['major_brand']}.")

    for item in (validation.get("evidence") or [])[:4]:
        parts.append(f"Evidence: {item}")
    for item in (validation.get("problems") or [])[:4]:
        parts.append(f"Problem: {item}")

    parts.append(f"Shannon entropy {fragment.get('entropy', 0)} bits per byte.")
    return " ".join(parts)
