"""Loads the invented Ridgeline reference data in ``data/domain/``."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DOMAIN_DIR = Path(__file__).resolve().parents[1] / "domain"


@dataclass(frozen=True)
class Machine:
    model: str
    type: str
    description: str
    hour_range: tuple[int, int]


@dataclass(frozen=True)
class ErrorCode:
    code: str
    subsystem: str
    severity: str
    safety: bool
    applies_to: tuple[str, ...]
    meaning: str


@dataclass(frozen=True)
class Dealer:
    name: str
    region: str
    voice: str
    notes: str


@dataclass(frozen=True)
class Domain:
    oem: str
    machines: tuple[Machine, ...]
    codes: tuple[ErrorCode, ...]
    dealers: tuple[Dealer, ...]

    def code(self, code: str) -> ErrorCode:
        return next(c for c in self.codes if c.code == code)

    def codes_for(self, machine_type: str, *, safety: bool | None = None) -> list[ErrorCode]:
        return [
            c
            for c in self.codes
            if machine_type in c.applies_to and (safety is None or c.safety == safety)
        ]


def _load(name: str) -> dict:  # type: ignore[type-arg]
    with (DOMAIN_DIR / name).open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)  # type: ignore[no-any-return]


@lru_cache(maxsize=1)
def load_domain() -> Domain:
    machines = _load("machines.yaml")
    codes = _load("error_codes.yaml")["codes"]
    dealers = _load("dealers.yaml")["dealers"]
    return Domain(
        oem=machines["oem"],
        machines=tuple(
            Machine(m["model"], m["type"], m["description"], tuple(m["hour_range"]))
            for m in machines["machines"]
        ),
        codes=tuple(
            ErrorCode(
                c["code"],
                c["subsystem"],
                c["severity"],
                c["safety"],
                tuple(c["applies_to"]),
                c["meaning"],
            )
            for c in codes
        ),
        dealers=tuple(Dealer(d["name"], d["region"], d["voice"], d["notes"]) for d in dealers),
    )
