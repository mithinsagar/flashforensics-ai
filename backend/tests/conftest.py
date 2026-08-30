"""Shared fixtures.

The disk image is built once per test session and reused. Generating it takes a
couple of seconds and every test that needs a filesystem needs the same one, so
rebuilding per test would multiply the suite runtime for no isolation benefit:
nothing here writes to the image.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.make_fixture import generate  # noqa: E402


@pytest.fixture(scope="session")
def damaged_image(tmp_path_factory) -> tuple[Path, dict]:
    """A 32 MB FAT32 image with every damage scenario applied, plus ground truth."""
    directory = tmp_path_factory.mktemp("fixtures")
    output = directory / "card.img"
    generate(output=output, size_mb=32, sectors_per_cluster=2, damage=True)
    return output, json.loads(output.with_suffix(".truth.json").read_text())


@pytest.fixture(scope="session")
def clean_image(tmp_path_factory) -> tuple[Path, dict]:
    """The same volume with no damage applied, as a control."""
    directory = tmp_path_factory.mktemp("fixtures_clean")
    output = directory / "clean.img"
    generate(output=output, size_mb=32, sectors_per_cluster=2, damage=False)
    return output, json.loads(output.with_suffix(".truth.json").read_text())
