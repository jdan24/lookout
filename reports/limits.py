"""Limits: the Limits app's two tools (Account limit upload, Risk and periodic audit), one per tab.

The page shows the Limits app (``jdan24/limit_compare``) exactly as it is built there:
a compiled, self-contained copy lives in ``vendor/limits_app/index.html`` and is never
edited here (see that folder's README to refresh it). Each tab loads the app straight
on its own tool. The app runs in the browser only: no TT calls, nothing sent anywhere.

Team lists: when ``[limits] shared_folder`` is set in ``config.toml``, the page reads
``tt_symbol_mappings.csv``, ``unavailable_products.csv`` and ``tt_exchange_products.csv``
(TT's product list, cut down here to futures) from it (read only) and hands them to the
Account limit upload tab. Each user's own saved mappings, unavailable products and
dropped TT file sit on top. They are read once per session; "Reload team lists" reads
them again.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import streamlit as st

import core.config as app_config

TITLE = "Limits"
DESCRIPTION = (
    "Account limit upload: a client limit table matched to TT products (unavailable "
    "products greyed out), checked against the Risk System Export, with the risk-tool "
    "upload CSVs. Risk and periodic audit: a client's product universe against the "
    "risk system's limits, with the risk review and upload."
)
ICON = "📏"
CATEGORY = "Limits"

APP_FILE = Path(__file__).resolve().parent.parent / "vendor" / "limits_app" / "index.html"
ACCOUNT_UPLOAD_ROUTE = "/account-upload"
TOOLS: dict[str, str] = {
    "Account limit upload": "/account-upload",
    "Risk and periodic audit": "/risk-audit",
}
FRAME_HEIGHT = 1600
# Hides the app's "← Risk Tools" home link: each tab holds one tool.
HIDE_HOME_LINK = '<style>header a[href="#/"] { display: none; }</style>'


MAPPINGS_FILE = "tt_symbol_mappings.csv"
UNAVAILABLE_FILE = "unavailable_products.csv"
TT_FILE = "tt_exchange_products.csv"
MAX_SHARED_BYTES = 5_000_000  # a team list is a few KB; refuse anything absurd
MAX_TT_BYTES = 300_000_000  # TT's full product list is tens of MB
# Columns of TT's ExchangeProducts CSV that the app uses; only these are passed on.
TT_COLUMNS = ("Exchange", "Product Name", "Product Symbol", "Alternate Symbol", "Product Type")
SHARED_KEY = "lim_shared"


@dataclass(frozen=True)
class SharedLists:
    """The team's lists as CSV text (None when a file is absent), and any problems."""

    folder: str = ""
    mappings_csv: str | None = None
    unavailable_csv: str | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)
    # TT's product list, cut down to futures, with its file's last-modified time (ISO).
    tt_csv: str | None = None
    tt_modified: str | None = None
    tt_futures: int = 0


def futures_only(text: str) -> tuple[str, int]:
    """TT's ExchangeProducts CSV cut down to its futures and the columns the app uses.

    Raises ValueError when the file is not TT's product list.
    """
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    fields = [f.strip() for f in reader.fieldnames or []]
    missing = [c for c in ("Exchange", "Product Symbol", "Product Type") if c not in fields]
    if missing:
        raise ValueError(f"not TT's product list (no {', '.join(missing)} column)")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(TT_COLUMNS)
    count = 0
    for row in reader:
        clean = {(k or "").strip(): (v or "").strip() for k, v in row.items() if isinstance(v, str)}
        if clean.get("Product Type") != "Future" or clean.get("Exchange", "").endswith("_Delayed"):
            continue
        writer.writerow([clean.get(c, "") for c in TT_COLUMNS])
        count += 1
    return out.getvalue(), count


def read_shared(folder: Path | None) -> SharedLists:
    """Read the two team files from ``folder``. A missing file is fine; never raises."""
    if folder is None:
        return SharedLists()
    if not folder.is_dir():
        return SharedLists(
            str(folder), problems=(f"The team lists folder {folder} can't be reached.",)
        )
    texts: dict[str, str | None] = {}
    problems: list[str] = []
    for name in (MAPPINGS_FILE, UNAVAILABLE_FILE):
        path = folder / name
        texts[name] = None
        try:
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_SHARED_BYTES:
                problems.append(f"{path} is too large to be a team list; it was skipped.")
                continue
            texts[name] = path.read_bytes().decode("utf-8-sig", errors="replace")
        except OSError as exc:
            problems.append(f"Could not read {path}: {exc}")
    tt_csv, tt_modified, tt_futures = None, None, 0
    tt_path = folder / TT_FILE
    try:
        if tt_path.is_file():
            stat = tt_path.stat()
            if stat.st_size > MAX_TT_BYTES:
                problems.append(f"{tt_path} is too large to be TT's product list; it was skipped.")
            else:
                text = tt_path.read_bytes().decode("utf-8-sig", errors="replace")
                tt_csv, tt_futures = futures_only(text)
                tt_modified = datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat()
    except (OSError, ValueError, csv.Error) as exc:
        tt_csv = None
        problems.append(f"Could not use {tt_path}: {exc}")
    return SharedLists(
        str(folder),
        texts[MAPPINGS_FILE],
        texts[UNAVAILABLE_FILE],
        tuple(problems),
        tt_csv,
        tt_modified,
        tt_futures,
    )


def shared_script(shared: SharedLists) -> str:
    """The script that hands the team lists to the app (``window.__LIMITS_SHARED__``).

    JSON is written so that no text in the files can close the script element.
    """
    payload = json.dumps(
        {
            "mappingsCsv": shared.mappings_csv,
            "unavailableCsv": shared.unavailable_csv,
            "ttCsv": shared.tt_csv,
            "ttFile": TT_FILE if shared.tt_csv else None,
            "ttModified": shared.tt_modified,
            "source": shared.folder,
        }
    ).replace("</", "<\\/")
    return f"<script>window.__LIMITS_SHARED__ = {payload};</script>"


@st.cache_resource(show_spinner=False)
def load_app(path: Path = APP_FILE) -> str:
    return path.read_text(encoding="utf-8")


def app_for(html: str, route: str, shared: SharedLists | None = None) -> str:
    """The app opening on ``route`` (and given the team lists, if any): both are set
    before the app's own script runs."""
    snippet = f"<script>window.location.hash = {json.dumps(route)};</script>{HIDE_HOME_LINK}"
    if shared is not None and shared.folder:
        snippet = shared_script(shared) + snippet
    head = html.find("<head>")
    if head < 0:
        raise ValueError("The Limits app file has no <head>; restore it from the Limits repo.")
    at = head + len("<head>")
    return html[:at] + snippet + html[at:]


def render() -> None:
    st.title(f"{ICON} {TITLE}")
    st.caption(DESCRIPTION + " Everything runs in this browser; nothing is sent to TT.")
    try:
        html = load_app(APP_FILE)
    except OSError:
        st.error(
            f"The Limits app is missing ({APP_FILE}). Copy spa/dist/index.html from the "
            "Limits project there (see vendor/limits_app/README.md)."
        )
        return
    shared = _team_lists()
    for tab, route in zip(st.tabs(list(TOOLS)), TOOLS.values(), strict=True):
        with tab:
            # Only Account limit upload uses the team lists (and the TT list is large).
            team = shared if route == ACCOUNT_UPLOAD_ROUTE else None
            st.iframe(app_for(html, route, team), height=FRAME_HEIGHT)


def _team_lists() -> SharedLists:
    """The team lists for this session (read once; the button reads them again)."""
    folder = app_config.load_config().limits.shared_path
    if folder is None:
        return SharedLists()
    cached: SharedLists | None = st.session_state.get(SHARED_KEY)
    left, right = st.columns([5, 1])
    if (
        right.button("Reload team lists", key="lim_reload")
        or cached is None
        or cached.folder != str(folder)
    ):
        cached = read_shared(folder)
        st.session_state[SHARED_KEY] = cached
    found = [
        n
        for n, t in (
            (MAPPINGS_FILE, cached.mappings_csv),
            (UNAVAILABLE_FILE, cached.unavailable_csv),
        )
        if t
    ]
    if cached.tt_csv:
        found.append(f"{TT_FILE} ({cached.tt_futures:,} futures)")
    left.caption(
        f"Team lists from {cached.folder}: "
        + (", ".join(found) if found else "no list files there yet")
        + "."
    )
    for problem in cached.problems:
        st.warning(problem)
    return cached
