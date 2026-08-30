"""Runtime configuration, read from the environment with usable defaults.

Every setting has a default that produces a working system, and the LLM provider
defaults to `auto`, which uses whichever API key is present and falls back to the
deterministic rule engine when none is. Anyone can clone this repository and run
a complete analysis without holding an account anywhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FF_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    workspace: Path = Field(default=Path.home() / ".flashforensics")
    max_upload_bytes: int = Field(default=8 * 1024 * 1024 * 1024)
    entropy_block_size: int = Field(default=4096)
    max_fragment_bytes: int = Field(default=64 * 1024 * 1024)
    carve_alignment: int = Field(default=0, description="0 means follow the volume cluster size")
    min_carve_confidence: float = Field(default=0.35)

    llm_provider: Literal["auto", "anthropic", "openai", "heuristic"] = "auto"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    openai_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 45.0
    llm_max_fragments: int = Field(
        default=60,
        description="cap on fragments sent for model adjudication in one run",
    )

    chroma_path: Path | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    @property
    def uploads_dir(self) -> Path:
        path = self.workspace / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def exports_dir(self) -> Path:
        path = self.workspace / "exports"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def knowledge_dir(self) -> Path:
        path = self.chroma_path or (self.workspace / "chroma")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def resolved_provider(self) -> str:
        if self.llm_provider != "auto":
            return self.llm_provider
        if self.anthropic_api_key:
            return "anthropic"
        if self.openai_api_key:
            return "openai"
        return "heuristic"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
        _settings.workspace.mkdir(parents=True, exist_ok=True)
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
