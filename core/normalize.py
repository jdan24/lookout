"""The one place where TT ids and booleans are normalised.

* Ids are strings everywhere. ``0``, ``"0"``, ``""`` and ``None`` all mean "no id".
  Product and family ids are 19-digit numbers, which a float cannot hold exactly.
* TT sends booleans as ``0/1`` integers and sometimes as ``"0"/"1"`` or
  ``"true"/"false"`` strings.
"""

from __future__ import annotations

import math
import re
from typing import Any

NO_ID = ""

_INTEGRAL_TEXT = re.compile(r"(\d+)\.0+")
_FLOAT_SAFE_MAX = 2**53

_TRUE = {"1", "true"}
_FALSE = {"0", "false"}


def norm_id(value: Any) -> str:
    """Return the id as a string, or ``""`` when the value means "no id"."""
    if value is None:
        return NO_ID
    if isinstance(value, bool):
        raise ValueError(f"A boolean is not an id: {value!r}")
    if isinstance(value, int):
        return NO_ID if value == 0 else str(value)
    if isinstance(value, float):
        if math.isnan(value):
            return NO_ID
        if not value.is_integer() or abs(value) >= _FLOAT_SAFE_MAX:
            raise ValueError(f"Id was parsed as a float and may have lost digits: {value!r}")
        return norm_id(int(value))
    if isinstance(value, str):
        text = value.strip()
        match = _INTEGRAL_TEXT.fullmatch(text)
        if match:
            text = match.group(1)
        elif re.fullmatch(r"[\d.]+[eE][+-]?\d+", text):
            raise ValueError(f"Id was parsed as a float and may have lost digits: {value!r}")
        return NO_ID if text in {"", "0"} else text
    raise ValueError(f"Not an id: {value!r}")


def same_id(a: Any, b: Any) -> bool:
    """True when both values are the same real id. "No id" never equals anything."""
    left, right = norm_id(a), norm_id(b)
    return left != NO_ID and left == right


def tt_bool(value: Any) -> bool | None:
    """Normalise a TT boolean. ``None`` and ``""`` mean "not set" and return ``None``."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "":
            return None
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    raise ValueError(f"Not a TT boolean: {value!r}")


def is_on(value: Any) -> bool:
    """True only when the value is a set, true TT boolean."""
    return tt_bool(value) is True
