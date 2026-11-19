"""The Limits page: the vendored Limits app, one tool per tab."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import streamlit as st_module
from streamlit.testing.v1 import AppTest

import core.config as app_config
from core.config import AppConfig
from reports import limits
from ui.discovery import REPORTS_DIR, discover_reports

SCRIPT = "from reports import limits\nlimits.render()\n"


def test_report_is_discovered_with_its_contract() -> None:
    specs, errors = discover_reports(REPORTS_DIR)
    assert errors == []
    spec = next(s for s in specs if s.slug == "limits")
    assert (spec.title, spec.category) == ("Limits", "Limits")
    assert not any(s.slug == "limit_verification" for s in specs)


def test_the_vendored_app_is_the_self_contained_build() -> None:
    html = limits.load_app()
    assert html.startswith("<!doctype html>") and "<head>" in html
    assert 'src="http' not in html and 'href="http' not in html  # nothing loaded from outside


@pytest.mark.parametrize("route", ["/risk-audit", "/account-upload"])
def test_each_tab_opens_on_its_tool_without_the_home_link(route: str) -> None:
    html = limits.load_app()
    page = limits.app_for(html, route)
    snippet = f'<script>window.location.hash = "{route}";</script>'
    assert page.index(snippet) < page.index('<script type="module"')
    assert limits.HIDE_HOME_LINK in page
    assert page.replace(snippet, "").replace(limits.HIDE_HOME_LINK, "") == html


def test_the_page_shows_two_tabs() -> None:
    app = AppTest.from_string(SCRIPT, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    assert [t.label for t in app.tabs] == ["Account limit upload", "Risk and periodic audit"]


def test_a_missing_app_file_is_explained(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(limits, "APP_FILE", tmp_path / "index.html")
    app = AppTest.from_string(SCRIPT, default_timeout=30)
    app.run()
    assert "The Limits app is missing" in app.error[0].value and not app.tabs


# --- team lists (shared folder) ---------------------------------------------------

MAPPINGS = "ricRoot,exchange,ttExchange,ttSymbol\r\nTY,,CME,ZN\r\n"
UNAVAILABLE = "TT Exchange,TT Product Symbol\r\nCME,CL\r\n"


def test_read_shared_reads_both_files(tmp_path: Path) -> None:
    (tmp_path / limits.MAPPINGS_FILE).write_text("\ufeff" + MAPPINGS, encoding="utf-8")
    (tmp_path / limits.UNAVAILABLE_FILE).write_text(UNAVAILABLE, encoding="utf-8")
    shared = limits.read_shared(tmp_path)
    assert shared.mappings_csv == MAPPINGS and shared.unavailable_csv == UNAVAILABLE
    assert shared.folder == str(tmp_path) and shared.problems == ()


def test_read_shared_tolerates_missing_files_and_folders(tmp_path: Path) -> None:
    assert limits.read_shared(None) == limits.SharedLists()
    only = limits.read_shared(tmp_path)
    assert only.mappings_csv is None and only.unavailable_csv is None and only.problems == ()
    gone = limits.read_shared(tmp_path / "nope")
    assert "can't be reached" in gone.problems[0]


def test_read_shared_skips_a_huge_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(limits, "MAX_SHARED_BYTES", 10)
    (tmp_path / limits.MAPPINGS_FILE).write_text(MAPPINGS, encoding="utf-8")
    shared = limits.read_shared(tmp_path)
    assert shared.mappings_csv is None and "too large" in shared.problems[0]


def test_team_lists_go_in_before_the_app_and_cannot_close_the_script() -> None:
    html = limits.load_app()
    shared = limits.SharedLists("X:/team", "a,b\r\n</script><script>alert(1)</script>", None)
    page = limits.app_for(html, "/account-upload", shared)
    script = limits.shared_script(shared)
    assert script in page and page.index(script) < page.index('<script type="module"')
    assert "</script><script>alert" not in script and script.count("</script>") == 1
    payload = json.loads(
        script.removeprefix("<script>window.__LIMITS_SHARED__ = ").removesuffix(";</script>")
    )
    assert payload == {
        "mappingsCsv": shared.mappings_csv,
        "unavailableCsv": None,
        "ttCsv": None,
        "ttFile": None,
        "ttModified": None,
        "source": "X:/team",
    }
    assert limits.app_for(html, "/account-upload", limits.SharedLists()) == limits.app_for(
        html, "/account-upload"
    )


def with_config(monkeypatch: pytest.MonkeyPatch, **limits_settings: str) -> None:
    cfg = AppConfig.model_validate({"limits": limits_settings})
    monkeypatch.setattr(app_config, "load_config", lambda: cfg)


def test_the_page_names_the_team_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / limits.UNAVAILABLE_FILE).write_text(UNAVAILABLE, encoding="utf-8")
    with_config(monkeypatch, shared_folder=str(tmp_path))
    app = AppTest.from_string(SCRIPT, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    caption = next(c.value for c in app.caption if "Team lists from" in c.value)
    assert str(tmp_path) in caption and limits.UNAVAILABLE_FILE in caption
    assert limits.MAPPINGS_FILE not in caption
    assert any(b.key == "lim_reload" for b in app.button)


def test_no_team_folder_means_no_team_line(monkeypatch: pytest.MonkeyPatch) -> None:
    with_config(monkeypatch)
    app = AppTest.from_string(SCRIPT, default_timeout=30)
    app.run()
    assert not any("Team lists from" in c.value for c in app.caption) and not app.button


TT_CSV = (
    "﻿Exchange,Product Name,Product Symbol,Alternate Symbol,Product Type,Asset Class\r\n"
    "CME,10-Year T-Note Futures,ZN,,Future,Interest Rate\r\n"
    "CME,Options on ZN,OZN,,Option,Interest Rate\r\n"
    "CME_Delayed,10-Year T-Note Futures,ZN,,Future,Interest Rate\r\n"
    'ICE_L,"Long Gilt, Future",R,,Future,Interest Rate\r\n'
)


def test_futures_only_keeps_futures_and_the_needed_columns() -> None:
    text, count = limits.futures_only(TT_CSV)
    assert count == 2
    assert text.splitlines() == [
        "Exchange,Product Name,Product Symbol,Alternate Symbol,Product Type",
        "CME,10-Year T-Note Futures,ZN,,Future",
        'ICE_L,"Long Gilt, Future",R,,Future',
    ]
    with pytest.raises(ValueError, match="not TT's product list"):
        limits.futures_only("a,b\r\n1,2\r\n")


def test_read_shared_reads_the_tt_file(tmp_path: Path) -> None:
    (tmp_path / limits.TT_FILE).write_text(TT_CSV, encoding="utf-8")
    shared = limits.read_shared(tmp_path)
    assert shared.tt_futures == 2 and shared.tt_csv and "OZN" not in shared.tt_csv
    assert shared.tt_modified and shared.problems == ()
    payload = limits.shared_script(shared)
    assert '"ttFile": "tt_exchange_products.csv"' in payload


def test_a_wrong_tt_file_is_reported(tmp_path: Path) -> None:
    (tmp_path / limits.TT_FILE).write_text("a,b\r\n1,2\r\n", encoding="utf-8")
    shared = limits.read_shared(tmp_path)
    assert shared.tt_csv is None and "not TT's product list" in shared.problems[0]


def test_only_the_upload_tab_gets_the_team_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / limits.TT_FILE).write_text(TT_CSV, encoding="utf-8")
    with_config(monkeypatch, shared_folder=str(tmp_path))
    sent: list[str] = []
    monkeypatch.setattr(st_module, "iframe", lambda src, **_: sent.append(src))
    app = AppTest.from_string(SCRIPT, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    assert len(sent) == 2
    injected = "window.__LIMITS_SHARED__ = "
    assert injected in sent[0] and injected not in sent[1]
    caption = next(c.value for c in app.caption if "Team lists from" in c.value)
    assert "tt_exchange_products.csv (2 futures)" in caption
