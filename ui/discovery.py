"""Finds report plug-ins. Adding a report means adding a file to ``reports/``; nothing else.

Every ``reports/*.py`` whose name does not start with ``_`` must expose::

    TITLE: str
    DESCRIPTION: str
    ICON: str        # an emoji
    CATEGORY: str    # groups tiles on the landing page
    def render() -> None: ...
"""

from __future__ import annotations

import importlib.util
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"
_TEXT_FIELDS = ("TITLE", "DESCRIPTION", "ICON", "CATEGORY")
_SLUG = re.compile(r"[a-z][a-z0-9_]*")


@dataclass(frozen=True)
class ReportSpec:
    slug: str  # file name without .py; also the page's url_path
    title: str
    description: str
    icon: str
    category: str
    render: Callable[[], None]


@dataclass(frozen=True)
class ReportError:
    file: str
    problem: str


def _load(path: Path) -> ReportSpec:
    slug = path.stem
    if not _SLUG.fullmatch(slug):
        raise ValueError("file name must be lowercase letters, digits and underscores")

    module_name = f"lookout_reports.{slug}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise

    values: dict[str, str] = {}
    for name in _TEXT_FIELDS:
        value = getattr(module, name, None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
        values[name] = value.strip()
    render = getattr(module, "render", None)
    if not callable(render):
        raise ValueError("render() is missing")

    return ReportSpec(
        slug=slug,
        title=values["TITLE"],
        description=values["DESCRIPTION"],
        icon=values["ICON"],
        category=values["CATEGORY"],
        render=render,
    )


def discover_reports(directory: Path = REPORTS_DIR) -> tuple[list[ReportSpec], list[ReportError]]:
    """Load every report in the folder. A broken report is listed, not fatal."""
    specs: list[ReportSpec] = []
    errors: list[ReportError] = []
    for path in sorted(Path(directory).glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            specs.append(_load(path))
        except Exception as exc:  # noqa: BLE001 - one bad plug-in must not stop the app
            errors.append(ReportError(file=path.name, problem=f"{type(exc).__name__}: {exc}"))
    specs.sort(key=lambda s: (s.category.lower(), s.title.lower()))
    return specs, errors


def by_category(specs: list[ReportSpec]) -> dict[str, list[ReportSpec]]:
    grouped: dict[str, list[ReportSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.category, []).append(spec)
    return grouped
