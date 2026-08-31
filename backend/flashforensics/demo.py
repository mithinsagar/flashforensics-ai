"""The sample card that makes the app usable by someone with nothing to recover.

Most people who open this have no broken SD card in front of them, and a
forensics tool that cannot show what it does until you supply damaged hardware
demonstrates nothing. So the app can build its own patient: a small FAT32 volume
with real photos, documents, archives and audio written to it, then deliberately
damaged in six different ways, with a manifest recording exactly what was done.

That manifest is the reason this is not a canned demo. Every claim the dashboard
makes about the sample card can be checked against what was actually done to it,
which is the same data the accuracy benchmark scores against. The demo and the
test suite analyse the same volume.

The image is generated once and cached in the workspace, because building it
takes a couple of seconds and the result is byte-identical every time.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import sys
import threading
from pathlib import Path
from types import ModuleType

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

DEMO_NAME = "sample-card.img"
DEMO_SIZE_MB = 32
_LOCK = threading.Lock()


class DemoUnavailable(RuntimeError):
    """Raised when the sample card can neither be found nor built."""


def _load_sibling(module_name: str, filename: str) -> ModuleType:
    """Import the fixture generator whether or not `tools` is on the path.

    Installed as a package, `tools` is not importable by name, but the source
    tree still ships beside the package in every deployment of this project, so
    the file is loaded directly as a fallback rather than duplicating it.
    """
    cached = sys.modules.get(f"ff_{module_name}")
    if cached is not None:
        return cached

    try:
        return importlib.import_module(f"tools.{module_name}")
    except ImportError:
        pass

    candidate = Path(__file__).resolve().parents[1] / "tools" / filename
    if not candidate.is_file():
        raise DemoUnavailable(
            f"{filename} is not present in this install; upload a disk image instead"
        )

    spec = importlib.util.spec_from_file_location(f"ff_{module_name}", candidate)
    if spec is None or spec.loader is None:
        raise DemoUnavailable(f"could not load {candidate}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"ff_{module_name}"] = module
    spec.loader.exec_module(module)
    return module


def demo_image(settings: Settings | None = None) -> tuple[Path, dict]:
    """Return the sample card's path and its ground-truth manifest, building it once."""
    settings = settings or get_settings()
    directory = Path(settings.workspace) / "demo"
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / DEMO_NAME
    truth = image.with_suffix(".truth.json")

    with _LOCK:
        if image.is_file() and truth.is_file():
            return image, json.loads(truth.read_text())

        logger.info("building the sample card at %s", image)
        module = _load_sibling("make_fixture", "make_fixture.py")
        module.generate(output=image, size_mb=DEMO_SIZE_MB, sectors_per_cluster=2, damage=True)
        return image, json.loads(truth.read_text())


def score_run(truth: dict, state: dict) -> dict:
    """Grade a finished demo run against the record of what was done to the card.

    This is the same scorer the accuracy benchmark uses. Exposing it in the app
    means the demo does not ask to be believed: every row shows the expected
    format, size and damage verdict beside what the pipeline actually said.
    """
    module = _load_sibling("benchmark", "benchmark.py")
    return module.score(truth, state)


def demo_description(settings: Settings | None = None) -> dict:
    """A summary the UI can show before anything is analysed."""
    try:
        image, truth = demo_image(settings)
    except DemoUnavailable as error:
        return {"available": False, "reason": str(error)}

    scenarios: dict[str, int] = {}
    for item in truth.get("files", []):
        scenarios[item["scenario"]] = scenarios.get(item["scenario"], 0) + 1

    return {
        "available": True,
        "name": "Sample damaged card",
        "path": str(image),
        "size_bytes": image.stat().st_size,
        "filesystem": "FAT32",
        "planted_files": len(truth.get("files", [])),
        "scenarios": scenarios,
        "blurb": (
            "A 32 MB FAT32 card built for this demo: real photos, documents, archives and "
            "audio were written to it, then it was damaged on purpose. The boot sector was "
            "wiped, directory entries were erased, one file was truncated mid-write, another "
            "had its data corrupted, and one had its allocation chain severed. Everything the "
            "dashboard reports can be checked against the record of what was done."
        ),
    }
