"""LLM providers, plus a deterministic engine that replaces one when absent.

Three implementations sit behind one interface. Anthropic and OpenAI are called
over plain HTTP with httpx rather than through their SDKs, which keeps the
dependency list short and makes the request shape visible instead of hidden
behind a client object.

The third is the important one. `HeuristicProvider` answers the same questions
from the same evidence using explicit rules, so the pipeline runs end to end with
no API key and no network. That is not a degraded mode bolted on for demos, it is
a design requirement: the benchmark numbers in the README have to be reproducible
by anyone who clones this repository, and a number that moves when a model is
swapped is not a measurement of the recovery engine. The model layer earns its
place by explaining and by handling cases the rules do not cover, not by being
load-bearing for correctness.
"""

from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class LLMError(Exception):
    """Raised when a provider cannot produce a usable answer."""


def extract_json(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model response that may be wrapped in prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = JSON_BLOCK.search(text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as error:
                raise LLMError(f"model returned malformed JSON: {error}") from error
        raise LLMError("model response contained no JSON object") from None


class LLMProvider(ABC):
    """Interface every provider implements."""

    name: str = "base"
    supports_reasoning: bool = True

    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        """Return raw text for a single-turn prompt."""

    def complete_json(self, system: str, user: str, max_tokens: int = 700) -> dict[str, Any]:
        return extract_json(self.complete(system, user, max_tokens))

    def health(self) -> dict:
        return {"provider": self.name, "available": True}


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float = 45.0):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        try:
            response = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise LLMError(f"Anthropic request failed: {error}") from error

        payload = response.json()
        blocks = payload.get("content", [])
        return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")

    def health(self) -> dict:
        return {"provider": self.name, "model": self.model, "available": bool(self.api_key)}


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, timeout: float = 45.0):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        try:
            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise LLMError(f"OpenAI request failed: {error}") from error

        payload = response.json()
        choices = payload.get("choices", [])
        if not choices:
            raise LLMError("OpenAI returned no choices")
        return choices[0].get("message", {}).get("content", "")

    def health(self) -> dict:
        return {"provider": self.name, "model": self.model, "available": bool(self.api_key)}


class HeuristicProvider(LLMProvider):
    """Rule-based stand-in that answers from the same evidence, deterministically.

    Each branch mirrors the reasoning the prompts ask a model to perform, and the
    ordering matters: structural facts are consulted before statistical ones,
    exactly as the prompts require, so a swapped-in model and this engine agree on
    the clear cases and differ only on the genuinely ambiguous ones.
    """

    name = "heuristic"
    supports_reasoning = False

    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        if "Write the briefing." in user:
            return self._briefing(user)
        if "Identify the format." in user or "Give the recovery verdict." in user:
            raise LLMError(
                "no reasoning model configured; the calling agent owns the rule path for this task"
            )
        return self._answer_question(system, user)

    @staticmethod
    def _field(text: str, label: str) -> str:
        match = re.search(rf"^\s*{re.escape(label)}:\s*(.*)$", text, re.MULTILINE)
        return match.group(1).strip() if match else ""

    def _briefing(self, user: str) -> str:
        recoverable = self._to_int(self._field(user, "fully recoverable"), 0)
        partial = self._to_int(self._field(user, "partially recoverable"), 0)
        fragments = self._to_int(self._field(user, "fragments carved"), 0)
        formats = self._field(user, "formats found")
        damage_lines = [
            line.strip("- ").strip()
            for line in user.split("Damage recorded during parsing:")[-1].split("Carving results:")[0].splitlines()
            if line.strip().startswith("-")
        ]

        headline = (
            f"Recovered {recoverable} complete files and {partial} partial ones from "
            f"{fragments} carved fragments."
        )
        if formats:
            headline += f" Formats found: {formats}."

        cause = ""
        joined = " ".join(damage_lines).lower()
        if "backup boot sector" in joined:
            cause = (
                " The volume's first sector was destroyed, which is why the device asked to be "
                "reformatted, but the spare copy of that sector was intact and the data behind it "
                "was untouched."
            )
        elif "orphan" in joined:
            cause = (
                " Parts of the directory index were erased, so files that are physically present "
                "had lost their names and had to be found by scanning the raw data."
            )
        elif damage_lines:
            cause = f" The parser recorded {len(damage_lines)} structural problems on this volume."

        advice = (
            " Copy anything marked recoverable to another drive before doing anything else with "
            "this device."
        )
        return headline + cause + advice

    def _answer_question(self, system: str, user: str) -> str:
        ids = re.findall(r"Fragment ([0-9a-f]{12})", system)
        if not ids:
            return (
                "Nothing in this session's fragment index matches that question. Try asking about "
                "a specific format, such as photos or documents, or about the recovery verdicts."
            )
        citations = " ".join(f"[{identifier}]" for identifier in ids[:5])
        return (
            f"{len(ids)} fragments in this session relate to that question: {citations}. "
            "Open the fragment list for their verdicts and sizes, or ask about a specific format "
            "to narrow it down."
        )

    @staticmethod
    def _to_float(value: str, default: float) -> float:
        try:
            return float(re.sub(r"[^0-9.\-]", "", value) or default)
        except ValueError:
            return default

    @staticmethod
    def _to_int(value: str, default: int) -> int:
        try:
            return int(re.sub(r"[^0-9\-]", "", value) or default)
        except ValueError:
            return default

    def health(self) -> dict:
        return {
            "provider": self.name,
            "available": True,
            "note": "deterministic rule engine, no API key required",
        }


class FallbackProvider(LLMProvider):
    """Wraps a remote provider and drops to the rule engine when it fails.

    An analysis that dies halfway because a rate limit was hit is worse than one
    that finishes with rule-based verdicts on the last few fragments, so a failed
    call degrades rather than propagates. The number of fallbacks is counted and
    surfaced in the run report, because a result that quietly stopped using the
    model should say so.
    """

    def __init__(self, primary: LLMProvider, fallback: LLMProvider):
        self.primary = primary
        self.fallback = fallback
        self.name = primary.name
        self.failures = 0

    def complete(self, system: str, user: str, max_tokens: int = 700) -> str:
        try:
            return self.primary.complete(system, user, max_tokens)
        except LLMError as error:
            self.failures += 1
            logger.warning("%s call failed, falling back: %s", self.primary.name, error)
            return self.fallback.complete(system, user, max_tokens)

    def complete_json(self, system: str, user: str, max_tokens: int = 700) -> dict[str, Any]:
        """Let a failure reach the caller so it can apply its own rules.

        The agents each own a deterministic rule path for their own task, and
        those paths see more context than a text-scraping fallback ever could.
        Routing a failed model call back through prompt parsing would mean two
        implementations of the same decision, which is how the two drift apart.
        """
        try:
            return extract_json(self.primary.complete(system, user, max_tokens))
        except LLMError as error:
            self.failures += 1
            logger.warning("%s call failed, caller will apply rules: %s", self.primary.name, error)
            raise

    def health(self) -> dict:
        info = self.primary.health()
        info["fallback"] = "heuristic"
        info["fallback_invocations"] = self.failures
        return info


def build_provider(settings: Settings | None = None) -> LLMProvider:
    """Construct the provider named by configuration, wrapped for graceful failure."""
    settings = settings or get_settings()
    choice = settings.resolved_provider()
    heuristic = HeuristicProvider()

    if choice == "anthropic" and settings.anthropic_api_key:
        return FallbackProvider(
            AnthropicProvider(
                settings.anthropic_api_key, settings.anthropic_model, settings.llm_timeout_seconds
            ),
            heuristic,
        )
    if choice == "openai" and settings.openai_api_key:
        return FallbackProvider(
            OpenAIProvider(
                settings.openai_api_key, settings.openai_model, settings.llm_timeout_seconds
            ),
            heuristic,
        )
    return heuristic
