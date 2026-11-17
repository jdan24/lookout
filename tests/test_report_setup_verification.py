"""The Setup Verification page, run headless on the synthetic snapshot."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import core.config as app_config
from core.config import AppConfig
from tests.fixtures import synthetic
from ui.discovery import REPORTS_DIR, discover_reports

SCRIPT = "from reports import setup_verification\nsetup_verification.render()\n"


@pytest.fixture
def folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Temp snapshot and profile folders, with the example profile in place."""
    snaps, profiles = tmp_path / "snaps", tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "example.toml").write_bytes(synthetic.EXAMPLE_PROFILE.read_bytes())
    settings = AppConfig.model_validate(
        {
            "tt": {"request_id_prefix": "Lookout-Acme"},
            "snapshots": {"directory": str(snaps)},
            "clients": {"directory": str(profiles)},
        }
    )
    monkeypatch.setattr(app_config, "load_config", lambda: settings)
    from core import credentials

    monkeypatch.setattr(credentials, "has_credentials", lambda env: False)  # never the real store
    return snaps, profiles


def run() -> AppTest:
    app = AppTest.from_string(SCRIPT, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    return app


def test_report_is_discovered_with_its_contract() -> None:
    specs, errors = discover_reports(REPORTS_DIR)
    assert errors == []
    spec = next(s for s in specs if s.slug == "setup_verification")
    assert (spec.title, spec.category) == ("Setup Verification", "Setup")


def test_no_profiles(folders: tuple[Path, Path]) -> None:
    (folders[1] / "example.toml").unlink()
    app = run()
    assert "No client profiles found" in app.warning[0].value


def test_broken_profile_is_reported(folders: tuple[Path, Path]) -> None:
    (folders[1] / "broken.toml").write_text("client = [", encoding="utf-8")
    app = run()
    assert "broken.toml" in app.error[0].value
    assert app.selectbox[0].options == ["Example client"]


def test_no_snapshots_yet(folders: tuple[Path, Path]) -> None:
    app = run()
    assert any("No snapshots found for example" in w.value for w in app.warning)
    assert len(app.dataframe) == 0


def test_results_from_the_latest_snapshot(folders: tuple[Path, Path]) -> None:
    synthetic.build_snapshot(folders[0], client="example")
    app = run()
    assert app.selectbox[1].value == "20261113_093000"
    assert "Complete" in app.success[0].value

    metrics = {m.label: int(m.value) for m in app.metric}
    assert metrics["Red"] > 0 and metrics["Amber"] > 0 and metrics["Green"] > 0
    assert metrics["Not checked"] > 0  # account defaults on markets without limits
    table = app.dataframe[0].value
    assert set(table["colour"]) == {"red", "amber"}  # green is hidden by default
    assert "Tick Apply Limits on team FUND-BETA." in set(table["message"])

    rules = app.multiselect[1]
    assert set(rules.options) == {
        "LEVEL",
        "LIMITS",
        "RESTRICTIONS",
        "MARKETS",
        "NAMING",
        "COMPANION",
        "OTD",
        "USERS",
        "LEGACY",
    }


def test_snapshot_that_does_not_match_the_profile_shows_an_error(
    folders: tuple[Path, Path],
) -> None:
    text = synthetic.EXAMPLE_PROFILE.read_text(encoding="utf-8")
    (folders[1] / "example.toml").write_text(
        text.replace('path = ["SOUTHCO", "S-FUND"]', 'path = ["SOUTHCO", "NOPE"]'), encoding="utf-8"
    )
    synthetic.build_snapshot(folders[0], client="example")
    app = run()
    assert "could not be checked" in app.error[0].value and "NOPE" in app.error[0].value


def test_older_snapshot_can_be_picked(folders: tuple[Path, Path]) -> None:
    synthetic.build_snapshot(folders[0], client="example")
    synthetic.build_snapshot(
        folders[0], client="example", when=datetime(2026, 11, 14, 8, 0, 0), skip_details=(220,)
    )
    app = run()
    assert app.selectbox[1].options == ["20261114_080000 (latest)", "20261113_093000"]
    assert any("Partial" in w.value for w in app.warning)
    app.selectbox[1].set_value("20261113_093000")
    app.run()
    assert "Complete" in app.success[0].value


def test_unexpected_data_shows_a_message_not_a_traceback(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    synthetic.build_snapshot(folders[0], client="example")
    from checks.setup import verify

    def broken(*args: object) -> None:
        raise ValueError("Not an id: {}")

    monkeypatch.setattr(verify, "run_checks", broken)
    app = run()
    assert "did not expect" in app.error[0].value


# --- pulling -------------------------------------------------------------------


def _fake_tt(monkeypatch: pytest.MonkeyPatch, *, stored: bool = True):  # noqa: ANN202
    from core import credentials
    from core.credentials import parse_secret
    from core.readonly_http import ReadOnlyClient
    from core.tt_client import TTClient
    from tests.fixtures.fake_tt import SECRET, FakeTT
    from ui import pull

    tt = FakeTT()

    def make(settings: AppConfig):  # noqa: ANN202
        http = ReadOnlyClient(transport=tt.transport)
        client = TTClient(http, parse_secret(SECRET), clock=lambda: 0.0, sleep=lambda seconds: None)
        return client, http.close

    monkeypatch.setattr(pull, "make_tt_client", make)
    monkeypatch.setattr(credentials, "has_credentials", lambda env: stored)
    return tt


def test_pull_needs_stored_credentials(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    tt = _fake_tt(monkeypatch, stored=False)
    app = run()
    assert any("No TT credentials" in w.value for w in app.warning)
    assert not [b for b in app.button if b.key == "sv_pull"]
    assert tt.requests == []


def test_pull_needs_the_company_name_in_the_request_id(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ui import pull

    real_make_tt_client = pull.make_tt_client
    tt = _fake_tt(monkeypatch)
    placeholder = AppConfig.model_validate(
        {"snapshots": {"directory": str(folders[0])}, "clients": {"directory": str(folders[1])}}
    )
    monkeypatch.setattr(app_config, "load_config", lambda: placeholder)
    app = run()
    assert any("Lookout-CHANGEME" in w.value for w in app.warning)
    assert not [b for b in app.button if b.key == "sv_pull"]
    assert tt.requests == []

    # The real client factory refuses too, before any connection is prepared.
    with pytest.raises(ValueError, match="company name"):
        real_make_tt_client(placeholder)


def test_uat_pull_says_uat_and_uses_uat_credentials_and_folders(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from core import credentials

    uat = AppConfig.model_validate(
        {
            "tt": {"request_id_prefix": "Lookout-Acme", "environment": "ext_uat_cert"},
            "snapshots": {"directory": str(folders[0])},
            "clients": {"directory": str(folders[1])},
        }
    )
    monkeypatch.setattr(app_config, "load_config", lambda: uat)
    asked: list[str] = []
    monkeypatch.setattr(credentials, "has_credentials", lambda env: asked.append(env) or False)

    # Live profiles are not offered in UAT: UAT has its own profiles folder
    app = run()
    assert any("No client profiles" in str(e.value) for e in [*app.warning, *app.info, *app.error])

    (folders[1] / "uat").mkdir()
    (folders[1] / "uat" / "example.toml").write_bytes(synthetic.EXAMPLE_PROFILE.read_bytes())
    synthetic.build_snapshot(folders[0], client="example")  # a Live snapshot: not shown in UAT
    app = run()
    assert asked and set(asked) == {"ext_uat_cert"}
    assert any("No TT UAT credentials" in w.value and "--uat" in w.value for w in app.warning)
    assert any("No snapshots found" in w.value and "uat" in w.value for w in app.warning)

    monkeypatch.setattr(credentials, "has_credentials", lambda env: True)
    app = run()
    assert "from TT UAT now" in app.checkbox(key="sv_pull_ok_0").label


def test_pull_only_after_confirming_then_new_snapshot_is_shown(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    tt = _fake_tt(monkeypatch)
    app = run()
    assert app.button(key="sv_pull").disabled  # not confirmed yet
    assert tt.requests == []

    app.checkbox(key="sv_pull_ok_0").check().run()
    app.button(key="sv_pull").click().run()
    assert not app.exception, app.exception
    assert tt.requests and {r.method for r in tt.requests} <= {"GET", "POST"}
    assert any("saved" in s.value for s in app.success)
    from core.snapshots import list_snapshots

    (new_id,) = list_snapshots(folders[0], "example")
    assert app.selectbox(key="sv_snapshot").value == new_id
    assert app.dataframe  # the new snapshot was checked
    # on the next run the confirmation starts unticked again, and the new snapshot stays
    app.run()
    assert app.checkbox(key="sv_pull_ok_1").value is False
    assert app.selectbox(key="sv_snapshot").value == new_id


def test_pull_that_cannot_find_the_client_says_so(
    folders: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    text = synthetic.EXAMPLE_PROFILE.read_text(encoding="utf-8")
    (folders[1] / "example.toml").write_text(
        text.replace('path = ["SOUTHCO", "S-FUND"]', 'path = ["SOUTHCO", "NOPE"]'), encoding="utf-8"
    )
    _fake_tt(monkeypatch)
    app = run()
    app.checkbox(key="sv_pull_ok_0").check().run()
    app.button(key="sv_pull").click().run()
    assert not app.exception, app.exception
    assert any("pull failed" in e.value for e in app.error)
