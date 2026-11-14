from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd
import pytest

from core.normalize import NO_ID, is_on, norm_id, same_id, tt_bool

BIG = "7193248756120394857"  # 19 digits, as product and family ids are


@pytest.mark.parametrize("value", [None, 0, "0", "", "  ", " 0 ", 0.0, float("nan"), "0.0"])
def test_no_id_values(value: Any) -> None:
    assert norm_id(value) == NO_ID


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (7654321, "7654321"),
        ("7654321", "7654321"),
        (" 7654321 ", "7654321"),
        (7654321.0, "7654321"),
        ("7654321.0", "7654321"),
        (BIG, BIG),
        (int(BIG), BIG),
        ("007", "007"),
    ],
)
def test_ids_become_strings(value: Any, expected: str) -> None:
    assert norm_id(value) == expected


@pytest.mark.parametrize("value", [float(BIG), 1.5, "7.193248756120395e+18", True, False, [], {}])
def test_values_that_are_not_safe_ids_raise(value: Any) -> None:
    with pytest.raises(ValueError):
        norm_id(value)


def test_same_id() -> None:
    assert same_id(7654321, "7654321")
    assert same_id(BIG, int(BIG))
    assert not same_id("7654321", "7654320")
    assert not same_id(0, "")
    assert not same_id(None, None)


def test_big_ids_survive_json_and_pandas_as_strings() -> None:
    raw = json.dumps({"productLimits": [{"productFamilyId": BIG, "productId": BIG[::-1]}]})
    rows = json.loads(raw)["productLimits"]
    frame = pd.DataFrame(rows, dtype=str)
    assert frame.loc[0, "productFamilyId"] == BIG

    csv = frame.to_csv(index=False)
    back = pd.read_csv(io.StringIO(csv), dtype=str)
    assert norm_id(back.loc[0, "productFamilyId"]) == BIG
    assert norm_id(back.loc[0, "productId"]) == BIG[::-1]


@pytest.mark.parametrize("value", [True, 1, 1.0, "1", "true", "True", " TRUE "])
def test_true_values(value: Any) -> None:
    assert tt_bool(value) is True
    assert is_on(value)


@pytest.mark.parametrize("value", [False, 0, 0.0, "0", "false", "False"])
def test_false_values(value: Any) -> None:
    assert tt_bool(value) is False
    assert not is_on(value)


@pytest.mark.parametrize("value", [None, "", "  "])
def test_unset_values(value: Any) -> None:
    assert tt_bool(value) is None
    assert not is_on(value)


@pytest.mark.parametrize("value", [2, -1, "yes", "2", 0.5, [], {}])
def test_values_that_are_not_booleans_raise(value: Any) -> None:
    with pytest.raises(ValueError):
        tt_bool(value)
