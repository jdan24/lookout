"""The Limits page: the vendored Limits app, one tool per tab."""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

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


def test_a_missing_app_file_is_explained() -> None:
    app = AppTest.from_string(
        "from reports import limits\n"
        "def missing():\n    raise FileNotFoundError('index.html')\n"
        "limits.load_app = missing\n"
        "limits.render()\n",
        default_timeout=30,
    )
    app.run()
    assert "The Limits app is missing" in app.error[0].value and not app.tabs
