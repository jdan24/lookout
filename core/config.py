"""App settings from ``config.toml``. No secrets live here; unknown keys are rejected.

The TT environment (Live or UAT) is chosen by the launcher through the
``LOOKOUT_ENVIRONMENT`` variable and is fixed for the session. UAT keeps its snapshots,
client profiles and credentials apart from Live.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import platformdirs
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.readonly_http import (
    ALLOWED_ENVIRONMENTS,
    ALLOWED_HOSTS,
    LIVE_ENVIRONMENT,
    UAT_ENVIRONMENT,
)

APP_NAME = "Lookout"
# Stands in for the company name in the shipped config. No pull runs while it is there.
REQUEST_ID_PLACEHOLDER = "CHANGEME"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"
ENVIRONMENT_VARIABLE = "LOOKOUT_ENVIRONMENT"  # set by the launchers
UAT_FOLDER = "uat"  # UAT snapshots and profiles live in this subfolder
MODE_NAMES = {LIVE_ENVIRONMENT: "LIVE", UAT_ENVIRONMENT: "UAT"}


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TTSettings(_Section):
    host: str = "ttrestapi.trade.tt"
    environment: str = LIVE_ENVIRONMENT
    # Sent with every call as requestId=<app>-<company>--<random id>. TT rejects any
    # other form (HTTP 400), so the prefix must be exactly <app>-<company>: letters and
    # digits only, one dash, no spaces.
    request_id_prefix: str = Field(
        default=f"{APP_NAME}-{REQUEST_ID_PLACEHOLDER}",
        max_length=60,
        pattern=r"^[A-Za-z0-9]+-[A-Za-z0-9]+$",
    )

    @property
    def request_id_is_set(self) -> bool:
        """False while the company name is still the shipped placeholder."""
        return self.request_id_prefix.split("-")[1] != REQUEST_ID_PLACEHOLDER

    @field_validator("host")
    @classmethod
    def _host_is_allow_listed(cls, value: str) -> str:
        if value not in ALLOWED_HOSTS:
            raise ValueError(f"host must be one of {sorted(ALLOWED_HOSTS)}")
        return value

    @field_validator("environment")
    @classmethod
    def _environment_is_allowed(cls, value: str) -> str:
        if value not in ALLOWED_ENVIRONMENTS:
            raise ValueError(f"environment must be one of {sorted(ALLOWED_ENVIRONMENTS)}")
        return value

    @property
    def is_uat(self) -> bool:
        return self.environment == UAT_ENVIRONMENT

    @property
    def mode(self) -> str:
        """"LIVE" or "UAT", as shown to the user."""
        return MODE_NAMES[self.environment]


class PacingSettings(_Section):
    # The TT plan allows 5 requests/second and 15,000/day, shared by the whole team.
    requests_per_second: float = Field(default=2.0, gt=0, le=5)
    max_calls_per_pull: int = Field(default=1500, gt=0, le=15000)
    max_order_tag_default_pages: int = Field(default=1000, gt=0)


class SnapshotSettings(_Section):
    directory: str = ""

    def path_for(self, environment: str) -> Path:
        """Where snapshots are stored. Defaults to a per-user folder outside the repo."""
        return _folder(self.directory, "snapshots", environment)


class ClientSettings(_Section):
    directory: str = ""

    def path_for(self, environment: str) -> Path:
        """Where client profiles (``*.toml``) are read from. Defaults to a per-user folder."""
        return _folder(self.directory, "clients", environment)


class AppConfig(_Section):
    tt: TTSettings = TTSettings()
    pacing: PacingSettings = PacingSettings()
    snapshots: SnapshotSettings = SnapshotSettings()
    clients: ClientSettings = ClientSettings()

    @property
    def snapshot_dir(self) -> Path:
        """This session's snapshot folder: Live and UAT never share one."""
        return self.snapshots.path_for(self.tt.environment)

    @property
    def clients_dir(self) -> Path:
        """This session's client profiles folder: Live and UAT never share one."""
        return self.clients.path_for(self.tt.environment)

    @property
    def clone_log_dir(self) -> Path:
        """Where the Clone page records each run. Clone works in UAT only."""
        return _folder("", "clone_log", self.tt.environment)


def _folder(configured: str, name: str, environment: str) -> Path:
    """The Live folder, or its UAT counterpart.

    Default: ``<user data>/Lookout/<name>`` and ``<user data>/Lookout/uat/<name>``.
    Configured: the folder itself and its ``uat`` subfolder.
    """
    uat = environment == UAT_ENVIRONMENT
    if configured.strip():
        base = Path(os.path.expandvars(configured.strip())).expanduser()
        return base / UAT_FOLDER if uat else base
    base = platformdirs.user_data_path(APP_NAME, appauthor=False)
    return (base / UAT_FOLDER if uat else base) / name


def default_snapshot_dir() -> Path:
    """``%LOCALAPPDATA%\\Lookout\\snapshots`` on Windows; the per-user data dir elsewhere."""
    return _folder("", "snapshots", LIVE_ENVIRONMENT)


def default_clients_dir() -> Path:
    """``%LOCALAPPDATA%\\Lookout\\clients`` on Windows; the per-user data dir elsewhere."""
    return _folder("", "clients", LIVE_ENVIRONMENT)


def load_config(path: Path | None = None) -> AppConfig:
    """Load settings; a missing file means all defaults.

    ``LOOKOUT_ENVIRONMENT``, when set, decides the TT environment whatever the file says.
    """
    target = path or DEFAULT_CONFIG_PATH
    data: dict = {}
    if target.exists():
        with target.open("rb") as handle:
            data = tomllib.load(handle)
    chosen = os.environ.get(ENVIRONMENT_VARIABLE, "").strip()
    if chosen:
        if chosen not in ALLOWED_ENVIRONMENTS:
            raise ValueError(
                f"{ENVIRONMENT_VARIABLE} must be one of {sorted(ALLOWED_ENVIRONMENTS)}, "
                f"not {chosen!r}."
            )
        data = {**data, "tt": {**data.get("tt", {}), "environment": chosen}}
    return AppConfig.model_validate(data)
