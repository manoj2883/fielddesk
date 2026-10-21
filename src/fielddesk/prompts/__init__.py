"""Versioned prompt files. A prompt's version is its file stem (``classify_v1``)."""

from functools import cache
from pathlib import Path

_DIR = Path(__file__).parent


@cache
def load_prompt(version: str) -> str:
    path = _DIR / f"{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no prompt file for version {version!r}")
    return path.read_text(encoding="utf-8").strip()
