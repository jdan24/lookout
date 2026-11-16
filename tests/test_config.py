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
    assert cfg.snapshot_dir == Path("S:/Shared/Lookout/snapshots")


@pytest.mark.parametrize(
    "text",
    [
        '[tt]\nhost = "evil.example"',
        '[tt]\nhost = "ttrestapi.trade.tt.evil.example"',
        '[tt]\nenvironment = "ext_prod_sim"',
        '[tt]\nenvironment = "EXT_UAT_CERT"',
        "[pacing]\nrequests_per_second = 6",
        "[pacing]\nrequests_per_second = 0",
        "[pacing]\nmax_calls_per_pull = 20000",
        "[pacing]\nmax_calls_per_pull = 0",
        '[tt]\napp_secret = "key:secret"',
        '[secrets]\napp_secret = "key:secret"',
        "[pacing]\nrequests_per_secnd = 2",
        # TT only accepts <app>-<company> before the "--<guid>"
        '[tt]\nrequest_id_prefix = "Lookout"',
        '[tt]\nrequest_id_prefix = "Lookout--Acme"',
        '[tt]\nrequest_id_prefix = "Look out-Acme"',
        '[tt]\nrequest_id_prefix = "Lookout-Acme-Trading"',
        '[tt]\nrequest_id_prefix = "Lookout-Acme_Trading"',
        '[tt]\nrequest_id_prefix = "Lookout-"',
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
    assert cfg.snapshot_dir == tmp_path / "AppData" / "Local" / "Lookout" / "snapshots"
    assert calls == [("Lookout", False)]

    repo = Path(config.__file__).resolve().parent.parent
    assert repo not in config.default_snapshot_dir().parents


def test_snapshot_directory_expands_user_and_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LOOKOUT_TEST_DIR", str(tmp_path))
    cfg = AppConfig.model_validate({"snapshots": {"directory": "$LOOKOUT_TEST_DIR/snaps"}})
    assert cfg.snapshot_dir == tmp_path / "snaps"


def test_request_id_needs_a_real_company_name(tmp_path: Path) -> None:
    shipped = load_config()
    assert shipped.tt.request_id_prefix == "Lookout-CHANGEME"
    assert not shipped.tt.request_id_is_set

    cfg = load_config(write(tmp_path, '[tt]\nrequest_id_prefix = "Lookout-AcmeTrading"'))
    assert cfg.tt.request_id_prefix == "Lookout-AcmeTrading"
    assert cfg.tt.request_id_is_set


# --- Live / UAT --------------------------------------------------------------------


def test_live_is_the_mode_unless_the_launcher_says_uat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live = load_config()
    assert (live.tt.environment, live.tt.mode, live.tt.is_uat) == ("ext_prod_live", "LIVE", False)

    monkeypatch.setenv("LOOKOUT_ENVIRONMENT", "ext_uat_cert")
    uat = load_config()
    assert (uat.tt.environment, uat.tt.mode, uat.tt.is_uat) == ("ext_uat_cert", "UAT", True)
    assert uat.tt.request_id_prefix == live.tt.request_id_prefix  # the rest is unchanged
    # the launcher decides, whatever the file says, and also when there is no file
    file = write(tmp_path, '[tt]\nenvironment = "ext_prod_live"')
    assert load_config(file).tt.is_uat
    assert load_config(tmp_path / "absent.toml").tt.is_uat

    monkeypatch.setenv("LOOKOUT_ENVIRONMENT", "ext_prod_live")
    assert not load_config(write(tmp_path, '[tt]\nenvironment = "ext_uat_cert"')).tt.is_uat


@pytest.mark.parametrize("value", ["uat", "ext_prod_sim", "ext_uat_cert/../ext_prod_live"])
def test_an_unknown_mode_from_the_launcher_is_refused(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LOOKOUT_ENVIRONMENT", value)
    with pytest.raises(ValueError, match="LOOKOUT_ENVIRONMENT"):
        load_config()


def test_uat_has_its_own_snapshot_and_profile_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        config.platformdirs, "user_data_path", lambda appname, appauthor=None: tmp_path / appname
    )
    live = AppConfig()
    uat = AppConfig.model_validate({"tt": {"environment": "ext_uat_cert"}})
    assert live.snapshot_dir == tmp_path / "Lookout" / "snapshots"
    assert live.clients_dir == tmp_path / "Lookout" / "clients"
    assert uat.snapshot_dir == tmp_path / "Lookout" / "uat" / "snapshots"
    assert uat.clients_dir == tmp_path / "Lookout" / "uat" / "clients"

    shared = {"snapshots": {"directory": "S:/L/snaps"}, "clients": {"directory": "S:/L/clients"}}
    live = AppConfig.model_validate(shared)
    uat = AppConfig.model_validate({**shared, "tt": {"environment": "ext_uat_cert"}})
    assert (live.snapshot_dir, live.clients_dir) == (Path("S:/L/snaps"), Path("S:/L/clients"))
    assert (uat.snapshot_dir, uat.clients_dir) == (Path("S:/L/snaps/uat"), Path("S:/L/clients/uat"))
