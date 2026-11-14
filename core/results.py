"""The one result shape every check returns."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from enum import StrEnum

import pandas as pd


class Colour(StrEnum):
    GREEN = "green"  # as expected
    RED = "red"  # not as expected
    AMBER = "amber"  # needs a rule, couldn't be read, or legacy / to be migrated


class Reason(StrEnum):
    MISSING = "MISSING"  # not set anywhere
    WRONG_LEVEL = "WRONG_LEVEL"  # set, but at the wrong level or place
    MISMATCH = "MISMATCH"  # right place, wrong value
    UNKNOWN = "UNKNOWN"  # data incomplete, e.g. a list not read to the end
    NO_RULE = "NO_RULE"  # nothing defined to judge this by yet
    TO_BE_MIGRATED = "TO_BE_MIGRATED"  # legacy setup


COLUMNS: tuple[str, ...] = (
    "node",
    "node_label",
    "rule_id",
    "setting",
    "expected",
    "expected_level",
    "actual",
    "actual_level",
    "colour",
    "reason",
    "message",
)


@dataclass(frozen=True)
class ResultRow:
    node: str  # the account or group the row is about, e.g. "FUND-ALPHA (200)"
    node_label: str  # Client group / Team / Trading account / Sub-account
    rule_id: str  # e.g. "A1", "B4"
    setting: str  # what was checked
    colour: Colour
    expected: str = ""
    expected_level: str = ""
    actual: str = ""
    actual_level: str = ""
    reason: Reason | None = None
    message: str = ""  # tells the team exactly what to fix in TT

    def __post_init__(self) -> None:
        object.__setattr__(self, "colour", Colour(self.colour))
        if self.reason is not None:
            object.__setattr__(self, "reason", Reason(self.reason))
        if self.colour is Colour.GREEN and self.reason is not None:
            raise ValueError("A green row has no reason.")
        if self.colour is not Colour.GREEN and self.reason is None:
            raise ValueError(f"A {self.colour} row needs a reason.")
        if self.colour is not Colour.GREEN and not self.message.strip():
            raise ValueError(f"A {self.colour} row needs a message saying what to do.")

    def as_dict(self) -> dict[str, str]:
        data = asdict(self)
        data["colour"] = self.colour.value
        data["reason"] = self.reason.value if self.reason else ""
        return {column: str(data[column]) for column in COLUMNS}


def to_frame(rows: Iterable[ResultRow]) -> pd.DataFrame:
    """All columns as text, in the standard order. Ids must never become numbers."""
    return pd.DataFrame([row.as_dict() for row in rows], columns=list(COLUMNS), dtype=str)


def to_csv(rows: Iterable[ResultRow]) -> bytes:
    """CSV for download. UTF-8 with a BOM so Excel on Windows opens it correctly."""
    return to_frame(rows).to_csv(index=False, lineterminator="\r\n").encode("utf-8-sig")


def summary(rows: Iterable[ResultRow]) -> dict[Colour, int]:
    counts = dict.fromkeys(Colour, 0)
    for row in rows:
        counts[row.colour] += 1
    return counts


_SEVERITY = {Colour.RED: 0, Colour.AMBER: 1, Colour.GREEN: 2}


def worst_first(rows: Iterable[ResultRow]) -> list[ResultRow]:
    """Red, then amber, then green; original order kept within each colour."""
    return sorted(rows, key=lambda row: _SEVERITY[row.colour])
