"""The landing page, run headless with Streamlit's test harness (no browser, no network)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui import discovery

ROOT = Path(__file__).resolve().parent.parent

REPORT = """
import streamlit as st

TITLE = "{title}"
DESCRIPTION = "{description}"
ICON = "📋"
CATEGORY = "{category}"

def render() -> None:
    st.title(TITLE)
"""


def add(directory: Path, name: str, title: str, category: str, description: str = "Desc.") -> None:
    (directory / name).write_text(
        REPORT.format(title=title, category=category, description=description), encoding="utf-8"
    )


def run_app(monkeypatch: pytest.MonkeyPatch, reports_dir: Path) -> AppTest:
    original = discovery.discover_reports
    monkeypatch.setattr(discovery, "discover_reports", lambda: original(reports_dir))
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


def all_text(app: AppTest) -> str:
    return "\n".join(str(element.value) for element in [*app.markdown, *app.subheader, *app.title])


def test_landing_page_with_no_reports(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app = run_app(monkeypatch, tmp_path)
    assert "Lookout" in app.title[0].value
    assert "No reports are installed yet" in app.info[0].value


def test_tiles_appear_for_dropped_in_reports_without_touching_app_py(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    add(tmp_path, "first_check.py", "First check", "Clients", "Checks the first thing.")
    add(tmp_path, "second_check.py", "Second check", "Limits", "Checks the second thing.")
    add(tmp_path, "_hidden.py", "Hidden", "Clients")

    app = run_app(monkeypatch, tmp_path)
    text = all_text(app)
    assert [s.value for s in app.subheader] == ["Clients", "Limits"]
    assert "First check" in text and "Checks the first thing." in text
    assert "Second check" in text and "Checks the second thing." in text
    assert "Hidden" not in text
    assert len(app.info) == 0

    assert "first_check" not in app_source and "second_check" not in app_source
    assert (ROOT / "app.py").read_text(encoding="utf-8") == app_source


def test_a_broken_report_does_not_take_the_app_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    add(tmp_path, "good.py", "Good one", "Clients")
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')", encoding="utf-8")
    app = run_app(monkeypatch, tmp_path)
    text = all_text(app)
    assert "Good one" in text
    assert "broken.py" in text and "boom" in text


def test_app_py_names_no_report() -> None:
    """app.py must stay generic: it may not import or mention any report module."""
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    for path in (ROOT / "reports").glob("*.py"):
        if not path.name.startswith("_"):
            assert path.stem not in source


def test_template_follows_the_report_contract(tmp_path: Path) -> None:
    """A copy of the template, renamed, is a working report."""
    copy = tmp_path / "copied_template.py"
    copy.write_text((ROOT / "reports" / "_template.py").read_text(encoding="utf-8"), "utf-8")
    specs, errors = discovery.discover_reports(tmp_path)
    assert errors == []
    assert [s.slug for s in specs] == ["copied_template"]

    script = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('copied_template', r'{copy}')\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "module.render()\n"
    )
    app = AppTest.from_string(script, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    assert "Report name" in app.title[0].value


def test_real_reports_folder_shows_the_report_tiles() -> None:
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    assert [s.value for s in app.subheader] == ["Limits", "Setup"]
    assert "Setup Verification" in all_text(app)


def test_live_mode_is_labelled_on_the_page_and_in_the_sidebar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = run_app(monkeypatch, tmp_path)
    assert "LIVE - TT production (ext_prod_live)" in app.markdown[0].value
    assert "LIVE - TT production (ext_prod_live)" in app.sidebar.markdown[0].value
    assert "UAT" not in all_text(app)


def test_uat_mode_has_a_banner_on_the_page_and_in_the_sidebar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOOKOUT_ENVIRONMENT", "ext_uat_cert")
    app = run_app(monkeypatch, tmp_path)
    banner = "UAT MODE - TT test environment (ext_uat_cert)"
    assert banner in app.markdown[0].value and "not production" in app.markdown[0].value
    assert banner in app.sidebar.markdown[0].value
    assert app.title[0].value == "🔭 Lookout UAT"
    assert "LIVE" not in all_text(app)
