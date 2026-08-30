"""Embedding functions, with a deterministic offline fallback.

The intended embedder is all-MiniLM-L6-v2, running through Chroma's bundled ONNX
runtime. It is small, fast on CPU, and does not drag PyTorch into the dependency
tree. It does have to be downloaded on first use.

`HashingEmbedding` is what runs when that download is impossible: an air-gapped
forensics workstation, a CI runner with no egress, a locked-down corporate
network. It is a hashed character and word n-gram model with sublinear term
weighting, which is a bag-of-words method with no learned semantics at all.

The honest framing matters here. On this corpus it does most of the job, because
retrieval is matching an observation like "archive entries include word/document
.xml" against sixty-eight format descriptions that use those exact terms, and
lexical overlap is genuinely most of the signal. What it loses is paraphrase:
MiniLM knows "photograph" and "camera image" are related and this does not. So it
is a fallback that keeps the system running offline, not an equivalent, and
`describe()` reports which one is active so a result never hides which was used.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from collections import Counter

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 384
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")


class HashingEmbedding:
    """Deterministic feature-hashing embedder with no external dependencies.

    Word unigrams and bigrams capture format vocabulary, and character 4-grams
    capture the fragments of magic-byte strings, extensions and path names that
    word tokenisation splits apart. Both are hashed into a fixed vector, weighted
    with sublinear term frequency, and L2 normalised so cosine distance behaves.
    """

    name_id = "hashing-ngram-384"

    def __init__(self, dimensions: int = EMBEDDING_DIMENSIONS):
        self.dimensions = dimensions

    @staticmethod
    def name() -> str:
        return HashingEmbedding.name_id

    def _tokens(self, text: str) -> list[str]:
        lowered = text.lower()
        words = TOKEN_PATTERN.findall(lowered)
        tokens = list(words)
        tokens.extend(f"{first}_{second}" for first, second in zip(words, words[1:], strict=False))
        compact = "".join(words)
        tokens.extend(compact[index : index + 4] for index in range(0, max(0, len(compact) - 3), 2))
        return tokens

    def _vector(self, text: str) -> list[float]:
        counts = Counter(self._tokens(text))
        vector = [0.0] * self.dimensions
        for token, count in counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]

    def __call__(self, input: list[str]) -> list[list[float]]:
        if isinstance(input, str):
            input = [input]
        return [self._vector(text) for text in input]


def build_embedding_function() -> tuple[object, dict]:
    """Return the best available embedder plus a description of what was chosen.

    MiniLM is attempted first and actually exercised on a probe string, because
    the model downloads lazily on first call: constructing the function proves
    nothing, and a failure that surfaces mid-analysis is far worse than one
    caught at startup.
    """
    try:
        from chromadb.utils import embedding_functions

        function = embedding_functions.ONNXMiniLM_L6_V2()
        function(["probe"])
        return function, {
            "embedding_model": "all-MiniLM-L6-v2",
            "backend": "onnxruntime",
            "semantic": True,
            "note": "384-dimensional sentence embeddings",
        }
    except Exception as error:
        logger.warning(
            "MiniLM unavailable (%s), falling back to offline hashing embeddings", error
        )
        return HashingEmbedding(), {
            "embedding_model": HashingEmbedding.name_id,
            "backend": "pure-python",
            "semantic": False,
            "note": (
                "MiniLM could not be downloaded, so retrieval is lexical rather than semantic. "
                "Matching on shared vocabulary still works; paraphrase matching does not."
            ),
            "reason": str(error)[:200],
        }
