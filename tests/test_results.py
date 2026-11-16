from __future__ import annotations

import io

import pandas as pd
import pytest

from core.results import COLUMNS, Colour, Reason, ResultRow, summary, to_csv, to_frame, worst_first

GREEN = ResultRow(
    node="POD-A (2001)",
    node_label="Team",
    rule_id="A1",
    setting="Apply Limits",
    colour=Colour.GREEN,
    expected="ON",
    expected_level="Team",
    actual="ON",
    actual_level="Team",
)
RED = ResultRow(
    node="ABCD1234XYZ (3001)",
    node_label="Trading account",
    rule_id="B4",
    setting="Clearing Account (CME)",
    colour=Colour.RED,
    expected="ABCD1234",
    expected_level="Trading account",
    actual="ABC99999",
    actual_level="Trading account",
    reason=Reason.MISMATCH,
    message="Set Clearing Account for CME to ABCD1234 on ABCD1234XYZ.",
)
AMBER = ResultRow(
    node="LONE1234AAA (3009)",
    node_label="Team",
    rule_id="A1",
    setting="Placement",
    colour=Colour.AMBER,
    reason=Reason.NO_RULE,
    message="Standalone account: no rule yet. Check by hand.",
)


def test_columns_are_the_agreed_shape() -> None:
    assert COLUMNS == (
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


def test_colours_and_reasons_are_the_agreed_ones() -> None:
    assert {c.value for c in Colour} == {"green", "red", "amber", "grey"}
    assert {r.value for r in Reason} == {
        "MISSING",
        "WRONG_LEVEL",
        "MISMATCH",
        "UNKNOWN",
        "NO_RULE",
        "TO_BE_MIGRATED",
        "NOT_APPLICABLE",
    }


def test_row_as_dict() -> None:
    assert RED.as_dict() == {
        "node": "ABCD1234XYZ (3001)",
        "node_label": "Trading account",
        "rule_id": "B4",
        "setting": "Clearing Account (CME)",
        "expected": "ABCD1234",
        "expected_level": "Trading account",
        "actual": "ABC99999",
        "actual_level": "Trading account",
        "colour": "red",
        "reason": "MISMATCH",
        "message": "Set Clearing Account for CME to ABCD1234 on ABCD1234XYZ.",
    }
    assert GREEN.as_dict()["reason"] == ""


def test_plain_strings_are_accepted_for_colour_and_reason() -> None:
    row = ResultRow(
        node="n",
        node_label="Team",
        rule_id="B9",
        setting="Legacy group",
        colour="amber",  # type: ignore[arg-type]
        reason="TO_BE_MIGRATED",  # type: ignore[arg-type]
        message="To Be Migrated.",
    )
    assert row.colour is Colour.AMBER
    assert row.reason is Reason.TO_BE_MIGRATED


@pytest.mark.parametrize(
    "kwargs",
    [
        {"colour": Colour.GREEN, "reason": Reason.MISMATCH},
        {"colour": Colour.RED, "message": "Fix it."},
        {"colour": Colour.AMBER, "message": "Check it."},
        {"colour": Colour.RED, "reason": Reason.MISSING},
        {"colour": Colour.RED, "reason": Reason.MISSING, "message": "   "},
        {"colour": "purple"},
        {"colour": Colour.RED, "reason": "BROKEN", "message": "x"},
    ],
)
def test_invalid_rows_are_rejected(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        ResultRow(node="n", node_label="Team", rule_id="A1", setting="s", **kwargs)


def test_frame_has_standard_columns_and_text_only() -> None:
    frame = to_frame([GREEN, RED, AMBER])
    assert list(frame.columns) == list(COLUMNS)
    assert len(frame) == 3
    assert all(isinstance(v, str) for v in frame.to_numpy().ravel())
    assert frame.loc[1, "colour"] == "red"


def test_empty_frame_still_has_the_columns() -> None:
    frame = to_frame([])
    assert list(frame.columns) == list(COLUMNS)
    assert frame.empty


def test_csv_round_trip_keeps_long_ids_and_leading_zeros() -> None:
    row = ResultRow(
        node="X (1)",
        node_label="Team",
        rule_id="B1",
        setting="Product family",
        colour=Colour.GREEN,
        expected="7193248756120394857",
        actual="007",
    )
    data = to_csv([row, RED])
    assert data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in data
    back = pd.read_csv(io.BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    assert list(back.columns) == list(COLUMNS)
    assert back.loc[0, "expected"] == "7193248756120394857"
    assert back.loc[0, "actual"] == "007"
    assert back.loc[1].to_dict() == RED.as_dict()


def test_summary_and_ordering() -> None:
    grey = ResultRow(
        node="N (1)",
        node_label="Team",
        rule_id="OTD",
        setting="S",
        colour=Colour.GREY,
        reason=Reason.NOT_APPLICABLE,
        message="Not checked: no limits here.",
    )
    rows = [grey, GREEN, AMBER, RED, GREEN]
    assert summary(rows) == {Colour.GREEN: 2, Colour.RED: 1, Colour.AMBER: 1, Colour.GREY: 1}
    assert summary([]) == dict.fromkeys(Colour, 0)
    assert worst_first(rows) == [RED, AMBER, GREEN, GREEN, grey]  # grey is never a problem


def test_grey_and_not_applicable_only_go_together() -> None:
    base = {"node": "N (1)", "node_label": "Team", "rule_id": "X", "setting": "S", "message": "m"}
    with pytest.raises(ValueError, match="NOT_APPLICABLE"):
        ResultRow(**base, colour=Colour.GREY, reason=Reason.MISSING)
    with pytest.raises(ValueError, match="NOT_APPLICABLE"):
        ResultRow(**base, colour=Colour.RED, reason=Reason.NOT_APPLICABLE)
