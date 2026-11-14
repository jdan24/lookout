"""The Limit Verification page, run headless on the synthetic snapshot."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import core.config as app_config
from core.config import AppConfig
from tests.fixtures import synthetic
from ui.discovery import REPORTS_DIR, discover_reports

SCRIPT = "from reports import limit_verification\nlimit_verification.render()\n"
SHEET = "Exch\tRIC\tMax Order\tMax Net\nCBT\tTY\t10\t100\nEUX\tFGBL\t10\t200\n"


@pytest.fixture
def snapshot_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    settings = AppConfig.model_validate({"snapshots": {"directory": str(tmp_path)}})
    monkeypatch.setattr(app_config, "load_config", lambda: settings)
    return tmp_path


def run() -> AppTest:
    app = AppTest.from_string(SCRIPT, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


def test_report_is_discovered() -> None:
    specs, errors = discover_reports(REPORTS_DIR)
    assert errors == []
    spec = next(s for s in specs if s.slug == "limit_verification")
    assert (spec.title, spec.category) == ("Limit Verification", "Limits")


def test_no_snapshots(snapshot_dir: Path) -> None:
    assert "No snapshots found" in run().warning[0].value


def test_compare_a_pasted_sheet(snapshot_dir: Path) -> None:
    synthetic.build_snapshot(snapshot_dir)
    app = run()
    assert app.selectbox[0].value == "synthetic"
    account = app.selectbox(key="lv_account")
    assert account.value == "200"  # FUND-ALPHA, first by name
    assert "Paste or upload" in app.info[0].value

    app.text_area(key="lv_paste").set_value(SHEET).run()
    assert not app.exception, app.exception
    assert app.radio(key="lv_format").value == "ric"
    metrics = {m.label: int(m.value) for m in app.metric}
    assert metrics == {"Red": 1, "Amber": 1, "Green": 3}
    results = app.dataframe[0].value
    assert list(results["colour"]) == ["red", "amber"]
    assert results.iloc[0]["setting"] == "FGBL → Eurex FGBL: Max Position Product (net)"
    assert results.iloc[1]["setting"] == "jFCE (Euronext, Future)"
    current = app.dataframe[1].value
    assert list(current["Product"]) == ["ZN", "FGBL", "jFCE"]
    matching = app.dataframe[2].value
    assert list(matching["Match"]) == ["known", "known"]


def test_incomplete_columns_ask_for_a_choice(snapshot_dir: Path) -> None:
    synthetic.build_snapshot(snapshot_dir)
    app = run()
    app.text_area(key="lv_paste").set_value("Code\tLimit\nTY\t5\n").run()
    assert not app.exception
    assert "Choose the column for" in app.warning[0].value
    app.selectbox(key="lv_col_ric_ricRoot").set_value("Code").run()
    app.selectbox(key="lv_col_ric_maxOrder").set_value("Limit").run()
    app.selectbox(key="lv_col_ric_maxNet").set_value("Limit").run()
    assert not app.exception, app.exception
    assert len(app.metric) == 3
