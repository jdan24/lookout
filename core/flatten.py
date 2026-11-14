"""Flatten a nested TT record into (json_path, value) pairs for display."""

from __future__ import annotations

from typing import Any


def flatten(value: Any, path: str = "") -> list[tuple[str, Any]]:
    """Leaves of a JSON value with their paths, e.g. ``riskLimits.productLimits[0].maxPos``."""
    if isinstance(value, dict):
        if not value:
            return [(path, {})] if path else []
        pairs: list[tuple[str, Any]] = []
        for key, item in value.items():
            pairs.extend(flatten(item, f"{path}.{key}" if path else str(key)))
        return pairs
    if isinstance(value, list):
        if not value:
            return [(path, [])]
        pairs = []
        for position, item in enumerate(value):
            pairs.extend(flatten(item, f"{path}[{position}]"))
        return pairs
    return [(path, value)]


def category_of(json_path: str) -> str:
    """The top-level key of a path: ``restrictions.adl`` -> ``restrictions``."""
    head = json_path.split(".", 1)[0]
    return head.split("[", 1)[0]
