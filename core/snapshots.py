"""Snapshots: every TT pull is saved to disk, and reports are rebuilt from the saved files.

Layout::

    <base>/<client>/<YYYYMMDD_HHMMSS>/
        manifest.json    status, call count, tool_version, scope summary
        model.json       nodes, roots, raw_index, page counts
        pull_log.json    every call, errors, notes
        raw/<endpoint_id>__<raw_id>.json   TT response bodies
    <base>/<client>/LATEST                 id of the newest usable snapshot

Snapshots hold real client data. They live outside the repo and are never committed.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from core.readonly_http import LIVE_ENVIRONMENT
from core.version import __version__

Status = Literal["complete", "partial", "failed"]
STATUSES: tuple[Status, ...] = ("complete", "partial", "failed")

LATEST_FILE = "LATEST"
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_SNAPSHOT_ID = re.compile(r"\d{8}_\d{6}(?:_\d+)?")


class SnapshotError(Exception):
    """A snapshot could not be written or read."""


def _safe(name: str, what: str) -> str:
    if not isinstance(name, str) or not _SAFE_NAME.fullmatch(name) or ".." in name:
        raise SnapshotError(f"Unsafe {what}: {name!r}")
    return name


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SnapshotError(f"Missing snapshot file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"Corrupt snapshot file: {path} ({exc})") from exc


def _write_atomic(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def raw_file_name(endpoint_id: str, raw_id: str) -> str:
    return f"{_safe(endpoint_id, 'endpoint id')}__{_safe(raw_id, 'raw id')}.json"


@dataclass(frozen=True)
class Snapshot:
    """A saved pull, loaded from disk."""

    client: str
    id: str
    path: Path
    manifest: Mapping[str, Any]
    model: Mapping[str, Any]
    _raw_cache: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def status(self) -> Status:
        return self.manifest["status"]

    @property
    def is_complete(self) -> bool:
        return self.status == "complete"

    @property
    def raw_index(self) -> list[dict[str, Any]]:
        return list(self.model.get("raw_index", []))

    @property
    def pull_log(self) -> Mapping[str, Any]:
        return _read_json(self.path / "pull_log.json")

    def raw_file(self, file: str) -> Any:
        """Load one raw body by its file name, as listed in ``raw_index``."""
        if file not in self._raw_cache:
            self._raw_cache[file] = _read_json(self.path / "raw" / _safe(file, "raw file name"))
        return self._raw_cache[file]

    def raw(self, endpoint_id: str, raw_id: str) -> Any:
        return self.raw_file(raw_file_name(endpoint_id, raw_id))

    def entries(
        self, *, node_key: str | None = None, endpoint_id: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self.raw_index
            if (node_key is None or entry["node_key"] == node_key)
            and (endpoint_id is None or entry["endpoint_id"] == endpoint_id)
        ]

    def raw_for(self, node_key: str, endpoint_id: str) -> Any | None:
        """The saved body for a node and endpoint, or ``None`` if it was not read."""
        for entry in self.entries(node_key=node_key, endpoint_id=endpoint_id):
            if entry.get("file"):
                return self.raw_file(entry["file"])
        return None


class SnapshotWriter:
    """Collects one pull into a new snapshot folder. Call ``finish`` exactly once."""

    def __init__(
        self,
        base_dir: Path,
        client: str,
        *,
        now: datetime | None = None,
        tool_version: str = __version__,
        environment: str = LIVE_ENVIRONMENT,
    ) -> None:
        self.environment = environment  # the TT environment the data comes from
        self.client = _safe(client, "client name")
        self.base_dir = Path(base_dir)
        self.tool_version = tool_version
        self.created_at = now or datetime.now()
        self.id, self.path = self._create_folder()
        self._raw_index: list[dict[str, Any]] = []
        self._finished = False

    def _create_folder(self) -> tuple[str, Path]:
        client_dir = self.base_dir / self.client
        stamp = self.created_at.strftime("%Y%m%d_%H%M%S")
        for suffix in ["", *(f"_{n}" for n in range(2, 100))]:
            path = client_dir / (stamp + suffix)
            try:
                (path / "raw").mkdir(parents=True, exist_ok=False)
            except FileExistsError:
                continue
            return stamp + suffix, path
        raise SnapshotError(f"Could not create a snapshot folder under {client_dir}")

    def save_raw(
        self,
        endpoint_id: str,
        raw_id: str,
        body: Any,
        *,
        node_key: str,
        status: str = "ok",
    ) -> str:
        """Save one response body. A ``str`` is written exactly as TT sent it."""
        self._check_open()
        file = raw_file_name(endpoint_id, raw_id)
        target = self.path / "raw" / file
        if target.exists():
            raise SnapshotError(f"Raw file already saved: {file}")
        if isinstance(body, str):
            target.write_text(body, encoding="utf-8")
        else:
            _write_json(target, body)
        self._raw_index.append(
            {"node_key": node_key, "endpoint_id": endpoint_id, "file": file, "status": status}
        )
        return file

    def note_unread(self, endpoint_id: str, *, node_key: str, status: str = "error") -> None:
        """Record that something in scope could not be read, so reports show it as unknown."""
        self._check_open()
        self._raw_index.append(
            {"node_key": node_key, "endpoint_id": endpoint_id, "file": None, "status": status}
        )

    def finish(
        self,
        *,
        status: Status,
        nodes: Sequence[Mapping[str, Any]] = (),
        roots: Sequence[str] = (),
        calls: Sequence[Mapping[str, Any]] = (),
        errors: Sequence[str] = (),
        notes: Sequence[str] = (),
        scope: Mapping[str, Any] | None = None,
        page_counts: Mapping[str, int] | None = None,
    ) -> Snapshot:
        self._check_open()
        if status not in STATUSES:
            raise SnapshotError(f"Unknown snapshot status: {status!r}")
        self._finished = True

        _write_json(
            self.path / "model.json",
            {
                "nodes": list(nodes),
                "roots": list(roots),
                "raw_index": self._raw_index,
                "page_counts": dict(page_counts or {}),
            },
        )
        _write_json(
            self.path / "pull_log.json",
            {"calls": list(calls), "errors": list(errors), "notes": list(notes)},
        )
        # The manifest is written last: a folder without one is an interrupted pull.
        _write_json(
            self.path / "manifest.json",
            {
                "client": self.client,
                "snapshot_id": self.id,
                "created_at": self.created_at.isoformat(timespec="seconds"),
                "status": status,
                "tool_version": self.tool_version,
                "environment": self.environment,
                "calls": len(calls),
                "errors": len(errors),
                "scope": dict(scope or {}),
            },
        )
        if status != "failed":
            _write_atomic(self.base_dir / self.client / LATEST_FILE, self.id)
        return load_snapshot(self.base_dir, self.client, self.id, environment=self.environment)

    def _check_open(self) -> None:
        if self._finished:
            raise SnapshotError("This snapshot is already finished.")


def list_clients(base_dir: Path) -> list[str]:
    """Clients with at least one finished snapshot, alphabetically."""
    base = Path(base_dir)
    if not base.is_dir():
        return []
    return sorted(
        entry.name
        for entry in base.iterdir()
        if entry.is_dir() and _SAFE_NAME.fullmatch(entry.name) and list_snapshots(base, entry.name)
    )


def list_snapshots(base_dir: Path, client: str) -> list[str]:
    """Ids of finished snapshots (any status), newest first."""
    client_dir = Path(base_dir) / _safe(client, "client name")
    if not client_dir.is_dir():
        return []
    ids = [
        entry.name
        for entry in client_dir.iterdir()
        if entry.is_dir()
        and _SNAPSHOT_ID.fullmatch(entry.name)
        and (entry / "manifest.json").is_file()
    ]
    return sorted(ids, reverse=True)


def latest_id(base_dir: Path, client: str) -> str | None:
    """The id in ``LATEST`` if it points at a real snapshot, else the newest usable one."""
    client_dir = Path(base_dir) / _safe(client, "client name")
    available = list_snapshots(base_dir, client)
    pointer = client_dir / LATEST_FILE
    if pointer.is_file():
        named = pointer.read_text(encoding="utf-8").strip()
        if named in available:
            return named
    for snapshot_id in available:
        if _read_json(client_dir / snapshot_id / "manifest.json").get("status") != "failed":
            return snapshot_id
    return None


def load_snapshot(
    base_dir: Path,
    client: str,
    snapshot_id: str | None = None,
    *,
    environment: str | None = None,
) -> Snapshot:
    """Load a snapshot by id, or the latest one when no id is given.

    With ``environment``, a snapshot pulled from another TT environment is refused, so
    UAT data is never checked as if it were Live. Older snapshots carry no environment
    and are Live.
    """
    chosen = snapshot_id or latest_id(base_dir, client)
    if chosen is None:
        raise SnapshotError(f"No snapshots found for client {client!r} in {base_dir}")
    if not _SNAPSHOT_ID.fullmatch(chosen):
        raise SnapshotError(f"Not a snapshot id: {chosen!r}")
    path = Path(base_dir) / _safe(client, "client name") / chosen
    manifest = _read_json(path / "manifest.json")
    if manifest.get("status") not in STATUSES:
        raise SnapshotError(f"Snapshot {chosen} has an unknown status: {manifest.get('status')!r}")
    pulled_from = manifest.get("environment") or LIVE_ENVIRONMENT
    if environment is not None and pulled_from != environment:
        raise SnapshotError(
            f"Snapshot {chosen} was pulled from {pulled_from}, but Lookout is running "
            f"against {environment}. It is not shown here."
        )
    return Snapshot(
        client=client,
        id=chosen,
        path=path,
        manifest=manifest,
        model=_read_json(path / "model.json"),
    )
