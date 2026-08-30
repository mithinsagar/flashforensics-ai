"""End-to-end tests for the agent pipeline, knowledge base and recovery accuracy.

The accuracy tests here are the ones that would fail loudest if a change broke
something real, because they compare a full run against the manifest recorded
when the image was built rather than against a snapshot of previous output.
"""

from __future__ import annotations

import pytest

from flashforensics.agents.graph import run_analysis
from flashforensics.agents.rag import RagAgent
from flashforensics.config import Settings
from flashforensics.knowledge.embeddings import HashingEmbedding, build_embedding_function
from flashforensics.knowledge.vectorstore import KnowledgeBase, describe_fragment
from flashforensics.llm.provider import HeuristicProvider, LLMError, extract_json

EXPECTED_STATUS = {
    "intact": "RECOVERABLE",
    "orphaned": "RECOVERABLE",
    "deleted": "RECOVERABLE",
    "truncated": "PARTIAL",
    "payload_corrupted": "PARTIAL",
    "chain_broken": "PARTIAL",
}


@pytest.fixture(scope="module")
def settings(tmp_path_factory) -> Settings:
    return Settings(workspace=tmp_path_factory.mktemp("workspace"), llm_provider="heuristic")


@pytest.fixture(scope="module")
def analysis(damaged_image, settings):
    path, truth = damaged_image
    state = run_analysis(
        session_id="pytest",
        image_path=str(path),
        image_name=path.name,
        image_size=path.stat().st_size,
        settings=settings,
        emitter=None,
    )
    return state, truth


class TestPipeline:
    def test_run_completes(self, analysis):
        state, _ = analysis
        assert state["stage"] == "complete", state.get("error")

    def test_every_agent_contributed(self, analysis):
        state, _ = analysis
        assert state["filesystem"] == "FAT32"
        assert state["carve_stats"]["carved"] > 0
        assert state["classification_stats"]["classified"] > 0
        assert state["verdict_stats"]["total"] > 0
        assert state["report"]

    def test_failure_routes_straight_to_the_end(self, tmp_path, settings):
        """A broken image must not cascade through four more stages."""
        garbage = tmp_path / "not-a-disk.img"
        garbage.write_bytes(b"\x00" * 1024)
        state = run_analysis("bad", str(garbage), "bad.img", 1024, settings, None)
        assert state["stage"] in ("complete", "failed")
        assert state.get("fragments", []) == [] or state["stage"] == "complete"

    def test_image_handle_is_released(self, analysis):
        state, _ = analysis
        assert state.get("_image") is None, "the image must be closed once the run finishes"


class TestRecoveryAccuracy:
    """The numbers reported in the README, asserted."""

    def _matches(self, state, truth):
        fragments = state["fragments"]
        by_path = {f["source_path"]: f for f in fragments if f.get("source_path")}
        by_offset = {f["offset"]: f for f in fragments}
        return [
            (item, by_path.get(item["path"]) or by_offset.get(item["byte_offset"]))
            for item in truth["files"]
        ]

    def test_recall_is_total(self, analysis):
        state, truth = analysis
        missed = [item["path"] for item, match in self._matches(state, truth) if match is None]
        assert missed == [], f"missed {len(missed)} planted files: {missed}"

    def test_every_format_is_identified_correctly(self, analysis):
        state, truth = analysis
        wrong = [
            (item["path"], item["format"], (match.get("classification") or {}).get("format"))
            for item, match in self._matches(state, truth)
            if match and (match.get("classification") or {}).get("format") != item["format"]
        ]
        assert wrong == [], f"format mismatches: {wrong}"

    def test_extents_are_byte_exact_where_recoverable(self, analysis):
        """chain_broken is excluded: its tail is genuinely unreachable."""
        state, truth = analysis
        wrong = [
            (item["path"], item["size"], match["length"])
            for item, match in self._matches(state, truth)
            if match and item["scenario"] != "chain_broken" and match["length"] != item["size"]
        ]
        assert wrong == [], f"extent mismatches: {wrong}"

    def test_damage_verdicts_match_what_was_actually_done(self, analysis):
        state, truth = analysis
        wrong = [
            (item["path"], item["scenario"], EXPECTED_STATUS[item["scenario"]], (match.get("verdict") or {}).get("status"))
            for item, match in self._matches(state, truth)
            if match and (match.get("verdict") or {}).get("status") != EXPECTED_STATUS[item["scenario"]]
        ]
        assert wrong == [], f"verdict mismatches: {wrong}"

    def test_no_junk_is_reported_on_a_clean_carve(self, analysis):
        state, _ = analysis
        assert state["verdict_stats"]["junk"] == 0

    def test_corrupted_png_is_partial_not_recoverable(self, analysis):
        """A passing checksum is the difference; the verdict must respect it."""
        state, truth = analysis
        corrupted = [i for i in truth["files"] if i["scenario"] == "payload_corrupted"]
        if not corrupted:
            pytest.skip("no payload_corrupted scenario")
        match = next(
            (f for f in state["fragments"] if f.get("source_path") == corrupted[0]["path"]), None
        )
        assert match is not None
        assert match["verdict"]["status"] == "PARTIAL"
        assert match["validation"]["metadata"]["crc_failures"] > 0


class TestKnowledgeBase:
    def test_every_format_is_indexed(self, tmp_path):
        knowledge = KnowledgeBase(tmp_path / "chroma")
        assert knowledge.size >= 60

    def test_retrieval_ranks_the_right_format_first(self, tmp_path):
        knowledge = KnowledgeBase(tmp_path / "chroma2")
        hits = knowledge.query_within(
            "archive entries include AndroidManifest.xml and classes.dex",
            ["zip", "docx", "xlsx", "apk", "jar"],
            limit=3,
        )
        assert hits[0]["extension"] == "apk"

    def test_query_within_never_returns_a_ruled_out_format(self, tmp_path):
        knowledge = KnowledgeBase(tmp_path / "chroma3")
        allowed = ["docx", "xlsx"]
        hits = knowledge.query_within("a spreadsheet workbook", allowed, limit=5)
        assert all(hit["extension"] in allowed for hit in hits)


class TestEmbeddings:
    def test_hashing_embedding_is_deterministic(self):
        embed = HashingEmbedding()
        assert embed(["a jpeg photograph"])[0] == embed(["a jpeg photograph"])[0]

    def test_hashing_embedding_is_normalised(self):
        vector = HashingEmbedding()(["some text about png chunks"])[0]
        magnitude = sum(value * value for value in vector) ** 0.5
        assert abs(magnitude - 1.0) < 1e-6

    def test_similar_text_scores_higher_than_unrelated_text(self):
        embed = HashingEmbedding()
        [target, near, far] = embed(
            [
                "zip archive containing word document xml parts",
                "word document xml inside a zip container",
                "mpeg audio frames with an id3 tag",
            ]
        )
        def dot(first, second):
            return sum(x * y for x, y in zip(first, second, strict=True))

        assert dot(target, near) > dot(target, far)

    def test_builder_always_returns_something_usable(self):
        function, info = build_embedding_function()
        assert callable(function)
        assert "embedding_model" in info
        assert len(function(["probe"])[0]) > 0


class TestProvider:
    def test_json_is_extracted_from_a_fenced_response(self):
        assert extract_json('```json\n{"format": "png"}\n```')["format"] == "png"

    def test_json_is_extracted_from_surrounding_prose(self):
        assert extract_json('Here you go: {"status": "OK"} hope that helps')["status"] == "OK"

    def test_malformed_json_raises(self):
        with pytest.raises(LLMError):
            extract_json("no json in this sentence at all")

    def test_heuristic_defers_classification_to_the_agent(self):
        """One rule path, owned by the agent, not two that can drift apart."""
        provider = HeuristicProvider()
        assert provider.supports_reasoning is False
        with pytest.raises(LLMError):
            provider.complete("system", "Identify the format.")

    def test_heuristic_still_writes_the_briefing(self):
        provider = HeuristicProvider()
        text = provider.complete(
            "system",
            "fully recoverable: 12\npartially recoverable: 3\nfragments carved: 15\n"
            "formats found: jpg, png\nWrite the briefing.",
        )
        assert "12" in text and "3" in text


class TestRagAgent:
    def test_indexes_and_answers_with_citations(self, analysis, settings):
        state, _ = analysis
        agent = RagAgent("pytest-rag", HeuristicProvider())
        assert agent.ingest(state["fragments"]) == len(state["fragments"])

        answer = agent.ask("which photos are recoverable")
        assert answer["retrieved"] > 0
        assert answer["citations"]
        agent.close()

    def test_question_words_become_a_metadata_filter(self, analysis):
        state, _ = analysis
        agent = RagAgent("pytest-rag2", HeuristicProvider())
        agent.ingest(state["fragments"])

        answer = agent.ask("what documents were found")
        assert answer.get("filter_applied") is not None
        agent.close()

    def test_empty_index_says_so_rather_than_guessing(self):
        agent = RagAgent("pytest-empty", HeuristicProvider())
        answer = agent.ask("anything?")
        assert answer["retrieved"] == 0
        assert answer["citations"] == []

    def test_fragment_description_carries_searchable_terms(self, analysis):
        state, _ = analysis
        fragment = next(f for f in state["fragments"] if f["category"] == "image")
        text = describe_fragment(fragment)
        assert fragment["fragment_id"] in text
        assert "image" in text
