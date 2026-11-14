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
        {"snapshots": {"directory": str(snaps)}, "clients": {"directory": str(profiles)}}
    )
    monkeypatch.setattr(app_config, "load_config", lambda: settings)
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
    assert "No snapshots found for example" in app.warning[0].value
    assert len(app.dataframe) == 0


def test_results_from_the_latest_snapshot(folders: tuple[Path, Path]) -> None:
    synthetic.build_snapshot(folders[0], client="example")
    app = run()
    assert app.selectbox[1].value == "20261113_093000"
    assert "Complete" in app.success[0].value

    metrics = {m.label: int(m.value) for m in app.metric}
    assert metrics["Red"] > 0 and metrics["Amber"] > 0 and metrics["Green"] > 0
    table = app.dataframe[0].value
    assert set(table["colour"]) == {"red", "amber"}  # green is hidden by default
    assert "Tick Apply Limits on pod CLI-BETA." in set(table["message"])

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


def test_display_tabs(folders: tuple[Path, Path]) -> None:
    synthetic.build_snapshot(folders[0], client="example")
    app = run()
    results, levels, limits, settings, by_hand = (frame.value for frame in app.dataframe)
    assert "Does not fit any level" in set(levels["Level"])
    assert synthetic.BIG_FAMILY_ID in set(limits["Product family id"])
    assert {"node", "json_path", "value", "category"} <= set(settings.columns)
    assert "Description" in set(by_hand["item"])


def test_snapshot_that_does_not_match_the_profile_shows_an_error(
    folders: tuple[Path, Path],
) -> None:
    text = synthetic.EXAMPLE_PROFILE.read_text(encoding="utf-8")
    (folders[1] / "example.toml").write_text(
        text.replace('path = ["CBTOP", "CB-CLI"]', 'path = ["CBTOP", "NOPE"]'), encoding="utf-8"
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
    assert "Partial" in app.warning[0].value
    app.selectbox[1].set_value("20261113_093000")
    app.run()
    assert "Complete" in app.success[0].value
