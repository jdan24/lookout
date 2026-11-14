"""Setup Verification: run every profile-driven rule on a snapshot. No Streamlit, no TT calls."""

from __future__ import annotations

from dataclasses import dataclass

from checks.setup import otd, rules
from checks.setup.levels import LevelMap, assign_levels
from checks.setup.profile import Profile
from core.results import ResultRow
from core.setup_model import SetupData, load_setup
from core.snapshots import Snapshot


@dataclass(frozen=True)
class Verification:
    rows: list[ResultRow]
    levels: LevelMap
    setup: SetupData
    profile: Profile


def run_checks(setup: SetupData, profile: Profile) -> Verification:
    levels = assign_levels(setup.tree, profile)
    rows = [
        *rules.check_levels(levels),
        *rules.check_apply_limits(setup, levels),
        *rules.check_apply_restrictions(setup, levels),
        *rules.check_markets(setup, levels, profile),
        *rules.check_naming(levels),
        *rules.check_companion(setup, levels, profile),
        *otd.check_order_tag_defaults(setup, levels, profile),
        *rules.check_users(setup, levels),
        *rules.check_legacy_groups(setup, levels, profile),
    ]
    return Verification(rows=rows, levels=levels, setup=setup, profile=profile)


def verify_snapshot(snapshot: Snapshot, profile: Profile) -> Verification:
    """Rebuild the setup from a saved snapshot and check it against the profile."""
    return run_checks(load_setup(snapshot), profile)
