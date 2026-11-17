"""Setup Verification: a client's setup in TT against the levels and values in its profile.

UI only. The rules are in ``checks/setup`` and run on a saved snapshot. Client profiles
are TOML files kept outside the repo (see ``docs/example_client.toml``).
"""

from __future__ import annotations

import streamlit as st

import core.config as app_config
from checks.setup.levels import LevelError
from checks.setup.profile import Profile, list_profiles
from checks.setup.verify import verify_snapshot
from core.hierarchy import HierarchyError
from core.snapshots import SnapshotError, list_snapshots
from ui import components
from ui.pull import pull_panel

TITLE = "Setup Verification"
DESCRIPTION = "Checks a client's account setup in TT against the levels and values in its profile."
ICON = "🧭"
CATEGORY = "Setup"


def _pick_profile() -> Profile | None:
    folder = app_config.load_config().clients_dir
    found = list_profiles(folder)
    for broken in (f for f in found if f.profile is None):
        st.error(f"Could not read the profile {broken.path.name}: {broken.error}")
    profiles = [f.profile for f in found if f.profile is not None]
    if not profiles:
        st.warning(
            f"No client profiles found in {folder}. Put a profile (.toml) there; "
            "docs/example_client.toml in the Lookout folder shows the format."
        )
        return None
    titles = [p.title for p in profiles]
    chosen = st.selectbox("Client", titles, key="sv_client")
    return profiles[titles.index(chosen)]


def render() -> None:
    st.title(f"{ICON} {TITLE}")
    st.caption(DESCRIPTION + " Read-only: fixes are made by hand in TT.")

    profile = _pick_profile()
    if profile is None:
        return
    settings = app_config.load_config()
    snapshot_dir = settings.snapshot_dir
    pull_panel(
        profile,
        key="sv",
        select_key="sv_snapshot",
        expanded=not list_snapshots(snapshot_dir, profile.client),
    )
    snapshot = components.snapshot_picker(
        snapshot_dir, profile.client, key="sv", environment=settings.tt.environment
    )
    if snapshot is None:
        return

    try:
        result = verify_snapshot(snapshot, profile)
    except (LevelError, HierarchyError, SnapshotError) as exc:
        st.error(f"This snapshot could not be checked: {exc}")
        return
    except (ValueError, TypeError, KeyError) as exc:  # data TT sent in a shape we did not expect
        st.error(
            "This snapshot holds data Lookout did not expect, so it could not be checked "
            f"({type(exc).__name__}: {exc}). Please report this with the snapshot id."
        )
        return
    for note in result.levels.notes:
        st.warning(note)

    stem = f"{profile.client}_{snapshot.id}"
    components.results_table(
        result.rows, key="sv_results", file_name=f"setup_verification_{stem}.csv"
    )
