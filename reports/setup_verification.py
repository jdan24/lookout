"""Setup Verification: a client's setup in TT against the levels and values in its profile.

UI only. The rules are in ``checks/setup`` and run on a saved snapshot. Client profiles
are TOML files kept outside the repo (see ``docs/example_client.toml``).
"""

from __future__ import annotations

import streamlit as st

import core.config as app_config
from checks.setup import rules, views
from checks.setup.levels import LevelError, levels_table
from checks.setup.profile import Profile, list_profiles
from checks.setup.verify import verify_snapshot
from core.hierarchy import HierarchyError
from core.snapshots import SnapshotError
from ui import components

TITLE = "Setup Verification"
DESCRIPTION = "Checks a client's account setup in TT against the levels and values in its profile."
ICON = "🧭"
CATEGORY = "Setup"


def _pick_profile() -> Profile | None:
    folder = app_config.load_config().clients.path
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
    snapshot_dir = app_config.load_config().snapshots.path
    snapshot = components.snapshot_picker(snapshot_dir, profile.client, key="sv")
    if snapshot is None:
        return

    try:
        result = verify_snapshot(snapshot, profile)
    except (LevelError, HierarchyError, SnapshotError) as exc:
        st.error(f"This snapshot could not be checked: {exc}")
        return
    for note in result.levels.notes:
        st.warning(note)

    tabs = st.tabs(["Results", "Levels", "Limits by level", "Settings by node", "Checked by hand"])
    results, levels, limits, settings, by_hand = tabs
    stem = f"{profile.client}_{snapshot.id}"
    with results:
        components.results_table(
            result.rows, key="sv_results", file_name=f"setup_verification_{stem}.csv"
        )
    with levels:
        st.caption(
            "Where each account sits and which profile level it got. Use this to tune the "
            "profile: an account that fits no level is not checked."
        )
        components.plain_table(
            levels_table(result.levels), key="sv_levels", file_name=f"levels_{stem}.csv"
        )
    with limits:
        st.caption("Product limit rows on every account in scope. Display only.")
        components.plain_table(
            views.limits_by_level(result.setup, result.levels, profile),
            key="sv_limits",
            file_name=f"limits_{stem}.csv",
        )
    with settings:
        st.caption("Every raw field of every account in scope. Display only.")
        components.plain_table(
            views.settings_by_node(result.setup, result.levels),
            key="sv_settings",
            file_name=f"settings_{stem}.csv",
        )
    with by_hand:
        st.caption(
            "Values the team checks by hand: account descriptions and the profile's "
            "display-only Order Tag Default fields. Shown to help; not judged."
        )
        components.plain_table(
            rules.manual_check_values(result.setup, result.levels, profile),
            key="sv_by_hand",
            file_name=f"hand_checks_{stem}.csv",
        )
