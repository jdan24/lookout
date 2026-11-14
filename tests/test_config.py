from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from core import config
from core.config import AppConfig, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_when_file_is_missing(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "absent.toml")
    assert cfg == AppConfig()
    assert cfg.tt.host == "ttrestapi.trade.tt"
    assert cfg.tt.environment == "ext_prod_live"
    assert cfg.pacing.requests_per_second == 2
    assert cfg.pacing.max_calls_per_pull == 1500
    assert cfg.pacing.max_order_tag_default_pages == 1000


def test_shipped_config_matches_the_defaults() -> None:
    assert load_config() == AppConfig()


def test_overrides(tmp_path: Path) -> None:
    cfg = load_config(
        write(
            tmp_path,
            """
            [tt]
            host = "apigateway.trade.tt"
            [pacing]
            requests_per_second = 1
            max_calls_per_pull = 200
            [snapshots]
            directory = "S:/Shared/Lookout/snapshots"
            """,
        )
    )
    assert cfg.tt.host == "apigateway.trade.tt"
    assert cfg.pacing.requests_per_second == 1
    assert cfg.pacing.max_calls_per_pull == 200
    assert cfg.pacing.max_order_tag_default_pages == 1000
    assert cfg.snapshots.path == Path("S:/Shared/Lookout/snapshots")


@pytest.mark.parametrize(
    "text",
    [
        '[tt]\nhost = "evil.example"',
        '[tt]\nhost = "ttrestapi.trade.tt.evil.example"',
        '[tt]\nenvironment = "ext_uat_cert"',
        "[pacing]\nrequests_per_second = 6",
        "[pacing]\nrequests_per_second = 0",
        "[pacing]\nmax_calls_per_pull = 20000",
        "[pacing]\nmax_calls_per_pull = 0",
        '[tt]\napp_secret = "key:secret"',
        '[secrets]\napp_secret = "key:secret"',
        "[pacing]\nrequests_per_secnd = 2",
    ],
)
def test_invalid_settings_are_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, text))


def test_default_snapshot_dir_is_per_user_and_outside_the_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_user_data_path(appname: str, appauthor: object = None) -> Path:
        calls.append((appname, appauthor))
        return tmp_path / "AppData" / "Local" / appname

    monkeypatch.setattr(config.platformdirs, "user_data_path", fake_user_data_path)
    cfg = AppConfig()
    assert cfg.snapshots.path == tmp_path / "AppData" / "Local" / "Lookout" / "snapshots"
    assert calls == [("Lookout", False)]

    repo = Path(config.__file__).resolve().parent.parent
    assert repo not in config.default_snapshot_dir().parents


def test_snapshot_directory_expands_user_and_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOOKOUT_TEST_DIR", str(tmp_path))
    cfg = AppConfig.model_validate({"snapshots": {"directory": "$LOOKOUT_TEST_DIR/snaps"}})
    assert cfg.snapshots.path == tmp_path / "snaps"
