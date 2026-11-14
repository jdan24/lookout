"""Pull a client's trees (the subtrees under its start accounts) from TT into a snapshot."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from checks.setup.levels import resolve_start
from checks.setup.profile import Profile
from core.hierarchy import AccountTree
from core.pull import pull_setup
from core.snapshots import Snapshot, SnapshotWriter
from core.tt_client import TTClient


def start_ids(tree: AccountTree, profile: Profile) -> list[str]:
    """Ids of the start accounts, found by walking each tree's path by name."""
    return [resolve_start(tree, spec)[0].id for spec in profile.trees]


def pull_client(
    tt: TTClient,
    snapshot_dir: Path,
    profile: Profile,
    *,
    otd_max_pages: int = 1000,
    now: datetime | None = None,
    progress: Callable[[str], None] | None = None,
) -> Snapshot:
    writer = SnapshotWriter(snapshot_dir, profile.client, now=now)
    return pull_setup(
        tt,
        writer,
        resolve_roots=lambda tree: start_ids(tree, profile),
        otd_max_pages=otd_max_pages,
        progress=progress,
    )
