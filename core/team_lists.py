"""The team's shared lists for the Limits page (a folder set by ``[limits] shared_folder``).

Read: ``tt_symbol_mappings.csv``, ``unavailable_products.csv`` and
``tt_exchange_products.csv`` (TT's product list, cut down to futures).

Write: only ``tt_symbol_mappings.csv``, and only when a user commits mappings from the
Account limit upload tab (``commit_mappings``). The write is merged into the current
file under a lock file, written to a temporary file and swapped in, and the previous
version is kept in ``history/``. Nothing else in the folder is ever written.

No Streamlit here; ``reports/limits.py`` is the UI.
"""

from __future__ import annotations

import csv
import io
import os
import re
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAPPINGS_FILE = "tt_symbol_mappings.csv"
UNAVAILABLE_FILE = "unavailable_products.csv"
TT_FILE = "tt_exchange_products.csv"
MAX_SHARED_BYTES = 5_000_000  # a team list is a few KB; refuse anything absurd
MAX_TT_BYTES = 300_000_000  # TT's full product list is tens of MB
# Columns of TT's ExchangeProducts CSV that the app uses; only these are passed on.
TT_COLUMNS = ("Exchange", "Product Name", "Product Symbol", "Alternate Symbol", "Product Type")


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


# --- committing mappings to the team file -------------------------------------------

MAPPING_HEADERS = ("ricRoot", "exchange", "ttExchange", "ttSymbol")
HISTORY_FOLDER = "history"
HISTORY_KEEP = 30  # previous versions kept in history/
MAX_COMMIT_ROWS = 5000
MAX_FIELD_CHARS = 100
LOCK_STALE_SECONDS = 120
LOCK_TRIES = 20
LOCK_WAIT_SECONDS = 0.25
_BAD_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class CommitError(Exception):
    """The commit was refused or failed; nothing was changed."""


@dataclass(frozen=True)
class CommitResult:
    added: int
    changed: int
    unchanged: int
    total: int  # mappings in the team file afterwards
    backup: str  # where the previous version was kept ("" when nothing was written)


def _canon(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def _key(row: Mapping[str, str]) -> tuple[str, str]:
    return (row["ricRoot"].strip().casefold(), row["exchange"].strip().casefold())


def parse_mappings(text: str) -> list[dict[str, str]]:
    """The team mapping file's rows (headers matched loosely, as the app does)."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    by_canon = {_canon(h or ""): h for h in reader.fieldnames or []}
    rows: list[dict[str, str]] = []
    for raw in reader:
        row = {
            h: str(raw.get(by_canon.get(_canon(h), ""), "") or "").strip() for h in MAPPING_HEADERS
        }
        if row["ricRoot"] and row["ttExchange"] and row["ttSymbol"]:
            rows.append(row)
    return rows


def mappings_csv(rows: Sequence[Mapping[str, str]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(MAPPING_HEADERS)
    for row in rows:
        writer.writerow([row[h] for h in MAPPING_HEADERS])
    return out.getvalue()


def clean_commit(rows: object) -> list[dict[str, str]]:
    """Validate mappings sent from the browser; raises CommitError on anything odd."""
    if not isinstance(rows, list) or not rows:
        raise CommitError("No mappings to commit.")
    if len(rows) > MAX_COMMIT_ROWS:
        raise CommitError(f"Too many mappings in one commit (over {MAX_COMMIT_ROWS}).")
    out: list[dict[str, str]] = []
    for item in rows:
        if not isinstance(item, dict):
            raise CommitError("A mapping was not in the expected form.")
        row: dict[str, str] = {}
        for h in MAPPING_HEADERS:
            value = item.get(h, "")
            if not isinstance(value, str):
                raise CommitError(f"A mapping's {h} was not text.")
            value = value.strip()
            if len(value) > MAX_FIELD_CHARS or _BAD_CHARS.search(value):
                raise CommitError(f"A mapping's {h} is too long or holds control characters.")
            row[h] = value
        if not (row["ricRoot"] and row["ttExchange"] and row["ttSymbol"]):
            raise CommitError("Every mapping needs a RIC root, a TT exchange and a TT symbol.")
        out.append(row)
    return out


def _lock(path: Path, sleep: Callable[[float], None]) -> int:
    for _ in range(LOCK_TRIES):
        try:
            return os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > LOCK_STALE_SECONDS:
                    path.unlink(missing_ok=True)  # left behind by a crash
                    continue
            except OSError:
                pass
            sleep(LOCK_WAIT_SECONDS)
    raise CommitError("Someone else is saving the team mappings right now; try again in a moment.")


def commit_mappings(
    folder: Path,
    rows: object,
    *,
    now: Callable[[], datetime] = datetime.now,
    sleep: Callable[[float], None] = time.sleep,
) -> CommitResult:
    """Merge ``rows`` into the team mapping file (a committed mapping replaces the team
    one for the same RIC root + exchange). Raises CommitError; never half-writes."""
    new_rows = clean_commit(rows)
    if not folder.is_dir():
        raise CommitError(f"The team lists folder {folder} can't be reached.")
    target = folder / MAPPINGS_FILE
    lock_path = folder / f"{MAPPINGS_FILE}.lock"
    try:
        fd = _lock(lock_path, sleep)
    except OSError as exc:
        raise CommitError(f"Can't write in {folder}: {exc}") from exc
    try:
        os.close(fd)
        current: list[dict[str, str]] = []
        if target.is_file():
            if target.stat().st_size > MAX_SHARED_BYTES:
                raise CommitError(f"{target} is too large to be a team list; not changed.")
            current = parse_mappings(target.read_bytes().decode("utf-8-sig", errors="replace"))
        merged = {_key(r): r for r in current}
        added = changed = unchanged = 0
        for row in new_rows:
            old = merged.get(_key(row))
            if old is None:
                added += 1
            elif all(old[h] == row[h] for h in MAPPING_HEADERS):
                unchanged += 1
                continue
            else:
                changed += 1
            merged[_key(row)] = row
        if not added and not changed:
            return CommitResult(0, 0, unchanged, len(merged), "")
        backup = ""
        if target.is_file():
            history = folder / HISTORY_FOLDER
            history.mkdir(exist_ok=True)
            stamp = now().strftime("%Y%m%d_%H%M%S_%f")
            backup_path = history / f"tt_symbol_mappings_{stamp}.csv"
            shutil.copy2(target, backup_path)
            backup = str(backup_path)
            old_versions = sorted(history.glob("tt_symbol_mappings_*.csv"))
            for extra in old_versions[:-HISTORY_KEEP]:
                extra.unlink(missing_ok=True)
        ordered = sorted(
            merged.values(), key=lambda r: (r["ricRoot"].casefold(), r["exchange"].casefold())
        )
        temp = folder / f".{MAPPINGS_FILE}.{os.getpid()}.tmp"
        temp.write_bytes(mappings_csv(ordered).encode("utf-8"))  # bytes: keep CRLF as is on Windows
        os.replace(temp, target)
        return CommitResult(added, changed, unchanged, len(merged), backup)
    except OSError as exc:
        raise CommitError(f"Could not save {target}: {exc}") from exc
    finally:
        lock_path.unlink(missing_ok=True)
