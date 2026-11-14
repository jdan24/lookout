"""App settings from ``config.toml``. No secrets live here; unknown keys are rejected."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import platformdirs
from pydantic import BaseModel, ConfigDict, Field, field_validator

from core.readonly_http import ALLOWED_ENVIRONMENT, ALLOWED_HOSTS

APP_NAME = "Lookout"
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TTSettings(_Section):
    host: str = "ttrestapi.trade.tt"
    environment: str = ALLOWED_ENVIRONMENT
    # Sent with every call as requestId=<prefix>--<random id>, so TT can trace our calls.
    request_id_prefix: str = Field(default="Lookout", pattern=r"^[A-Za-z0-9_-]{1,40}$")

    @field_validator("host")
    @classmethod
    def _host_is_allow_listed(cls, value: str) -> str:
        if value not in ALLOWED_HOSTS:
            raise ValueError(f"host must be one of {sorted(ALLOWED_HOSTS)}")
        return value

    @field_validator("environment")
    @classmethod
    def _environment_is_allowed(cls, value: str) -> str:
        if value != ALLOWED_ENVIRONMENT:
            raise ValueError(f"environment must be {ALLOWED_ENVIRONMENT}")
        return value


class PacingSettings(_Section):
    # The TT plan allows 5 requests/second and 15,000/day, shared by the whole team.
    requests_per_second: float = Field(default=2.0, gt=0, le=5)
    max_calls_per_pull: int = Field(default=1500, gt=0, le=15000)
    max_order_tag_default_pages: int = Field(default=1000, gt=0)


class SnapshotSettings(_Section):
    directory: str = ""

    @property
    def path(self) -> Path:
        """Where snapshots are stored. Defaults to a per-user folder outside the repo."""
        if self.directory.strip():
            return Path(os.path.expandvars(self.directory.strip())).expanduser()
        return default_snapshot_dir()


class ClientSettings(_Section):
    directory: str = ""

    @property
    def path(self) -> Path:
        """Where client profiles (``*.toml``) are read from. Defaults to a per-user folder."""
        if self.directory.strip():
            return Path(os.path.expandvars(self.directory.strip())).expanduser()
        return default_clients_dir()


class AppConfig(_Section):
    tt: TTSettings = TTSettings()
    pacing: PacingSettings = PacingSettings()
    snapshots: SnapshotSettings = SnapshotSettings()
    clients: ClientSettings = ClientSettings()


def default_snapshot_dir() -> Path:
    """``%LOCALAPPDATA%\\Lookout\\snapshots`` on Windows; the per-user data dir elsewhere."""
    return platformdirs.user_data_path(APP_NAME, appauthor=False) / "snapshots"


def default_clients_dir() -> Path:
    """``%LOCALAPPDATA%\\Lookout\\clients`` on Windows; the per-user data dir elsewhere."""
    return platformdirs.user_data_path(APP_NAME, appauthor=False) / "clients"


def load_config(path: Path | None = None) -> AppConfig:
    """Load settings; a missing file means all defaults."""
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        return AppConfig()
    with target.open("rb") as handle:
        return AppConfig.model_validate(tomllib.load(handle))
