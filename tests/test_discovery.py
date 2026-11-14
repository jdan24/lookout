from __future__ import annotations

from pathlib import Path

import pytest

from ui.discovery import REPORTS_DIR, by_category, discover_reports

GOOD = """
TITLE = "{title}"
DESCRIPTION = "Checks things."
ICON = "📋"
CATEGORY = "{category}"

CALLS = []

def render() -> None:
    CALLS.append("rendered")
"""


def add(directory: Path, name: str, title: str = "Sample", category: str = "General") -> None:
    (directory / name).write_text(GOOD.format(title=title, category=category), encoding="utf-8")


def test_a_dropped_in_file_is_discovered(tmp_path: Path) -> None:
    add(tmp_path, "sample_report.py", title="Sample report", category="Clients")
    specs, errors = discover_reports(tmp_path)
    assert errors == []
    (spec,) = specs
    assert spec.slug == "sample_report"
    assert spec.title == "Sample report"
    assert spec.description == "Checks things."
    assert spec.icon == "📋"
    assert spec.category == "Clients"
    spec.render()  # callable


def test_underscore_files_and_non_python_files_are_skipped(tmp_path: Path) -> None:
    add(tmp_path, "_template.py")
    add(tmp_path, "__init__.py")
    (tmp_path / "notes.txt").write_text("not a report")
    (tmp_path / "sub").mkdir()
    add(tmp_path / "sub", "nested.py")
    assert discover_reports(tmp_path) == ([], [])


def test_reports_are_sorted_and_grouped(tmp_path: Path) -> None:
    add(tmp_path, "zeta.py", title="Zeta", category="Limits")
    add(tmp_path, "alpha.py", title="Alpha", category="Limits")
    add(tmp_path, "mid.py", title="Mid", category="Clients")
    specs, _ = discover_reports(tmp_path)
    assert [s.title for s in specs] == ["Mid", "Alpha", "Zeta"]
    grouped = by_category(specs)
    assert list(grouped) == ["Clients", "Limits"]
    assert [s.slug for s in grouped["Limits"]] == ["alpha", "zeta"]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("TITLE = 'x'\nDESCRIPTION = 'x'\nICON = 'x'\ndef render(): pass", "CATEGORY"),
        ("TITLE = ''\nDESCRIPTION = 'x'\nICON = 'x'\nCATEGORY = 'x'\ndef render(): pass", "TITLE"),
        ("TITLE = 5\nDESCRIPTION = 'x'\nICON = 'x'\nCATEGORY = 'x'\ndef render(): pass", "TITLE"),
        ("TITLE = 'x'\nDESCRIPTION = 'x'\nICON = 'x'\nCATEGORY = 'x'", "render"),
        ("TITLE = 'x'\nDESCRIPTION = 'x'\nICON = 'x'\nCATEGORY = 'x'\nrender = 3", "render"),
        ("raise RuntimeError('boom')", "boom"),
        ("def broken(:", "SyntaxError"),
        ("import module_that_does_not_exist_xyz", "ModuleNotFoundError"),
    ],
)
def test_a_broken_report_is_reported_and_the_rest_still_load(
    tmp_path: Path, source: str, expected: str
) -> None:
    add(tmp_path, "good.py")
    (tmp_path / "broken.py").write_text(source, encoding="utf-8")
    specs, errors = discover_reports(tmp_path)
    assert [s.slug for s in specs] == ["good"]
    (error,) = errors
    assert error.file == "broken.py"
    assert expected in error.problem


def test_bad_file_names_are_reported(tmp_path: Path) -> None:
    add(tmp_path, "Has-Dash.py")
    specs, errors = discover_reports(tmp_path)
    assert specs == []
    assert errors[0].file == "Has-Dash.py"


def test_missing_folder_means_no_reports(tmp_path: Path) -> None:
    assert discover_reports(tmp_path / "absent") == ([], [])


def test_the_real_reports_folder_loads_cleanly() -> None:
    specs, errors = discover_reports(REPORTS_DIR)
    assert errors == []
    assert "_template" not in {s.slug for s in specs}
    assert len({s.slug for s in specs}) == len(specs)
