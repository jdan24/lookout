"""Shared test setup. Tests must never reach the network, and never TT."""

from __future__ import annotations

import socket
from typing import Any, NoReturn

import pytest


class NetworkBlockedError(RuntimeError):
    """Raised when a test tries to open a real network connection."""


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise NetworkBlockedError("Tests must not open network connections.")

    monkeypatch.delenv("LOOKOUT_ENVIRONMENT", raising=False)  # tests choose the mode themselves
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
