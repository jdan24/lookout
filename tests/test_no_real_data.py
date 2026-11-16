"""No real client detail from the local profiles appears in any tracked file.

The real client profiles live outside the repo (``[clients] directory`` in
``config.toml``). This test reads them and fails if a tracked file contains one of
their account names, start ids, client names or expected Order Tag Default values.
Extra terms (one per line, e.g. a firm or company id that is not in a profile) can
be listed in ``scrub_terms.txt`` in the same folder. Skipped when no profile exists.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from checks.setup.profile import Profile, list_profiles
from core.config import load_config
from core.readonly_http import LIVE_ENVIRONMENT, UAT_ENVIRONMENT

ROOT = Path(__file__).resolve().parent.parent
EXTRA_TERMS = "scrub_terms.txt"
MIN_LENGTH = 3


def terms_of(profile: Profile) -> set[str]:
    terms = {profile.client, profile.label}
    for tree in profile.trees:
        terms.update(tree.path)
        terms.add(tree.start_id or "")
        terms.update(rule.value or "" for rule in tree.order_tag_defaults)
    return {t.strip() for t in terms if len(t.strip()) >= MIN_LENGTH}


def real_terms(folder: Path) -> set[str]:
    terms: set[str] = set()
    for found in list_profiles(folder):
        if found.profile is not None:
            terms |= terms_of(found.profile)
    extra = folder / EXTRA_TERMS
    if extra.is_file():
        lines = extra.read_text(encoding="utf-8").splitlines()
        terms |= {line.strip() for line in lines if len(line.strip()) >= MIN_LENGTH}
    return terms


def tracked_files() -> list[Path]:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    listed = subprocess.run(
        [git, "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [ROOT / name for name in listed.split("\0") if name]


def pattern(term: str) -> re.Pattern[str]:
    # Whole words only, so "FUND" does not match inside "FUNDING" and ids not inside longer ids.
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", re.IGNORECASE)


def test_terms_are_found_as_whole_words() -> None:
    assert pattern("FUND").search("team FUND-ALPHA")
    assert not pattern("FUND").search("FUNDING")
    assert not pattern("1234").search("912345")


def test_no_tracked_file_contains_a_real_client_detail() -> None:
    settings = load_config()
    folder = settings.clients.path_for(LIVE_ENVIRONMENT)
    terms = real_terms(folder) | real_terms(settings.clients.path_for(UAT_ENVIRONMENT))
    if not terms:
        pytest.skip(f"no client profiles in {folder}")
    patterns = {term: pattern(term) for term in sorted(terms)}
    hits: list[str] = []
    for path in tracked_files():
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        hits += [
            f"{path.relative_to(ROOT)}: {term!r}"
            for term, rx in patterns.items()
            if rx.search(text)
        ]
    assert hits == [], "Real client details in tracked files:\n" + "\n".join(hits)
