"""The Clone page, run headless against the fake TT in UAT."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import core.config as app_config
from core import credentials
from core.config import AppConfig
from core.credentials import parse_secret
from core.readonly_http import ReadOnlyClient
from core.tt_client import TTClient
from core.tt_writer import TTWriter
from core.write_http import WriteClient
from tests.fixtures.fake_tt import SECRET, FakeTT
from ui.discovery import REPORTS_DIR, discover_reports

SCRIPT = "from reports import clone\nclone.render()\n"
UAT = "ext_uat_cert"
NO_WAIT = {"clock": lambda: 0.0, "sleep": lambda seconds: None}


def settings(environment: str = UAT) -> AppConfig:
    return AppConfig.model_validate(
        {"tt": {"request_id_prefix": "Lookout-Acme", "environment": environment}}
    )


@pytest.fixture
def tt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeTT:
    from reports import clone

    fake = FakeTT(environment=UAT)

    def make_reader(_: AppConfig):  # noqa: ANN202
        http = ReadOnlyClient(environment=UAT, transport=fake.transport)
        return TTClient(http, parse_secret(SECRET), **NO_WAIT), http.close

    def make_writer(_: AppConfig):  # noqa: ANN202
        http = WriteClient(environment=UAT, transport=fake.transport)
        return TTWriter(http, parse_secret(SECRET), **NO_WAIT), http.close

    monkeypatch.setattr(clone, "make_tt_client", make_reader)
    monkeypatch.setattr(clone, "make_tt_writer", make_writer)
    monkeypatch.setattr(app_config, "load_config", lambda: settings())
    monkeypatch.setattr(credentials, "has_credentials", lambda env: env == UAT)
    monkeypatch.setattr(AppConfig, "clone_log_dir", property(lambda self: tmp_path / "log"))
    return fake


def run() -> AppTest:
    app = AppTest.from_string(SCRIPT, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


def button(app: AppTest, key: str):  # noqa: ANN201
    return next(b for b in app.button if b.key == key)


def details(app: AppTest, source: str) -> AppTest:
    app.text_input(key="cl_source").set_value(source).run()
    button(app, "cl_details_button").click().run()
    assert not app.exception, app.exception
    return app


def choose(app: AppTest, mode: str) -> AppTest:
    from core.clone import ClearingMode

    app.radio(key="cl_clearing_mode").set_value(ClearingMode(mode)).run()
    return app


def fill(app: AppTest, source: str, names: str, mode: str = "source") -> AppTest:
    choose(details(app, source), mode)
    app.text_area(key="cl_names").set_value(names).run()
    return app


def test_report_is_discovered_with_its_contract() -> None:
    specs, errors = discover_reports(REPORTS_DIR)
    assert errors == []
    spec = next(s for s in specs if s.slug == "clone")
    assert (spec.title, spec.category) == ("Account Cloning Tool", "TT Setup Tools")


def test_live_shows_only_a_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_config, "load_config", lambda: settings("ext_prod_live"))
    app = run()
    assert "works in UAT only" in app.info[0].value
    assert not app.button and not app.text_input and not app.text_area


def test_uat_credentials_are_needed(tt: FakeTT, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "has_credentials", lambda env: False)
    app = run()
    assert "No TT UAT credentials" in app.warning[0].value and not app.button


def test_the_source_box_is_narrow_and_get_details_comes_first(tt: FakeTT) -> None:
    app = run()
    assert button(app, "cl_details_button").disabled
    assert not any(r.key == "cl_clearing_mode" for r in app.radio) and not app.text_area
    app = details(app, "ABC10001AL")
    table = app.dataframe[0].value
    assert list(table.columns[:2]) == ["Account", "Description"]
    assert table.iloc[0]["Account"] == "ABC10001AL" and table.iloc[0]["MX"] == "ABC99999"
    assert app.radio(key="cl_clearing_mode").value == "source"
    assert all(r.method == "GET" for r in tt.requests if "token" not in r.url.path)


def test_an_unknown_source_shows_why_and_no_options(tt: FakeTT) -> None:
    app = details(run(), "NOPE")
    assert "No account named NOPE" in app.error[0].value and not app.radio


def test_a_new_source_drops_the_details(tt: FakeTT) -> None:
    app = fill(run(), "ABC10001AL", "NEW10001AL")
    app.text_input(key="cl_source").set_value("OTHER").run()
    assert not app.radio and not app.dataframe


def test_next_shows_the_review_and_create_needs_a_tick(tt: FakeTT) -> None:
    app = fill(run(), "ABC10001AL", "  NEW10001AL  \n\n NEW20001AL\t\n")
    assert any("2 new account(s): NEW10001AL, NEW20001AL" in c.value for c in app.caption)
    button(app, "cl_next").click().run()
    table = app.data_editor[0].value
    assert list(table["New account"]) == ["NEW10001AL", "NEW20001AL"]
    assert set(table["Under"]) == {"FUND-ALPHA (200)"}
    create = button(app, "cl_create")
    assert create.disabled and create.label == "Create 2 account(s) in TT UAT"
    assert all(r.method == "GET" for r in tt.requests if "token" not in r.url.path)


def test_create_clones_and_shows_the_result(tt: FakeTT, tmp_path: Path) -> None:
    app = fill(run(), "ABC10001AL", "NEW10001AL")
    reads = len(tt.requests)
    button(app, "cl_next").click().run()
    assert len(tt.requests) == reads  # Next uses the details already read
    clearing = app.data_editor[1].value
    assert sorted(clearing["New value"]) == ["ABC10001"] * 4 + ["ABC99999"]
    app.checkbox(key="cl_ok_0_0").check().run()
    button(app, "cl_create").click().run()
    assert not app.exception, app.exception
    assert "Created 1 account(s) in TT UAT." in app.success[0].value
    assert len(tt.clones) == 1 and len(tt.otd_writes) == 1
    [log] = (tmp_path / "log").iterdir()
    assert json.loads(log.read_text(encoding="utf-8"))["result"][0]["New id"] == "5001"
    assert not app.radio  # TT has changed: Get details again before the next clone


def test_leaving_the_clearing_blank_shows_no_clearing_table(tt: FakeTT) -> None:
    app = fill(run(), "ABC10001AL", "NEW10001AL", "off")
    button(app, "cl_next").click().run()
    assert len(app.data_editor) == 1 and not app.error


def test_a_pasted_table_carries_per_exchange_clearing_values(tt: FakeTT) -> None:
    text = "Account\tExchange\tClearing\nNEW1\tCME\tCLR1\nNEW1\tASX\tCLR-AU\nNEW2\n"
    app = fill(run(), "ABC10001AL", text, "table")
    assert any("(header row skipped)" in c.value for c in app.caption)
    assert any("NEW1 (2 exchange value(s))" in c.value for c in app.caption)
    button(app, "cl_next").click().run()
    assert not app.exception, app.exception
    assert "Description" not in app.data_editor[0].value.columns
    clearing = app.data_editor[1].value
    rows = zip(clearing["New account"], clearing["Exchange"], clearing["New value"], strict=True)
    # Only the listed exchanges: the others, and NEW2, stay blank.
    assert set(rows) == {("NEW1", "CME", "CLR1"), ("NEW1", "ASX", "CLR-AU")}


def test_table_columns_need_the_table_choice(tt: FakeTT) -> None:
    app = fill(run(), "ABC10001AL", "NEW1,CME,CLR1")
    assert any("only used with 'Use the values from a table'" in e.value for e in app.error)
    assert button(app, "cl_next").disabled


def test_editing_the_review_tables_turns_into_plan_edits() -> None:
    from core.clone import PlanEdits
    from reports.clone import account_edits, clearing_edits

    frame = pd.DataFrame(
        {"Step": ["1", "2"], "New account": ["NEW1", "NEW2"]}, index=["k1", "k2"], dtype=str
    )
    out = frame.drop(index="k2").copy()
    out.at["k1", "New account"] = " NEW9 "
    edits = account_edits(frame, out, PlanEdits())
    assert edits == PlanEdits(names={"k1": "NEW9"}, removed=frozenset({"k2"}))
    assert account_edits(frame, frame.copy(), PlanEdits()) is None
    clr = pd.DataFrame({"New value": ["A", "B"]}, index=["c1", "c2"], dtype=str)
    out2 = clr.drop(index="c1").copy()
    out2.at["c2", "New value"] = "Z"
    assert clearing_edits(clr, out2, PlanEdits()) == PlanEdits(
        values={"c2": "Z"}, dropped=frozenset({"c1"})
    )


def test_a_bad_row_keeps_next_disabled(tt: FakeTT) -> None:
    app = fill(run(), "ABC10001AL", "NEW1\n\tno name\n")
    assert "Row 2 has no new account" in app.error[0].value
    assert button(app, "cl_next").disabled


def test_csv_files_are_read_as_utf8_or_windows_text() -> None:
    from reports.clone import _decode

    assert _decode("\ufeffNEW1,Café".encode()) == "NEW1,Café"
    assert _decode("NEW1,Café".encode("cp1252")) == "NEW1,Café"


def test_errors_keep_create_disabled(tt: FakeTT) -> None:
    app = fill(run(), "ABC10001AL", "ABC10001IX")
    button(app, "cl_next").click().run()
    assert "ABC10001IX already exists in TT." in [e.value for e in app.error]
    assert button(app, "cl_create").disabled
    assert next(c for c in app.checkbox if c.key == "cl_ok_0_0").disabled


def test_changing_the_inputs_drops_the_review(tt: FakeTT) -> None:
    app = fill(run(), "ABC10001AL", "NEW10001AL")
    button(app, "cl_next").click().run()
    assert any(b.key == "cl_create" for b in app.button)
    app.text_area(key="cl_names").set_value("OTHER1").run()
    assert not any(b.key == "cl_create" for b in app.button)
    button(app, "cl_next").click().run()
    choose(app, "off")
    assert not any(b.key == "cl_create" for b in app.button)
