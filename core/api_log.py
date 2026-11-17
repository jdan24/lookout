"""A local log of every TT API call, for troubleshooting.

One JSON object per line, one file per day: ``api_YYYYMMDD.jsonl`` in the session's
log folder (``%LOCALAPPDATA%\\Lookout\\api_log``, or ``...\\uat\\api_log`` in UAT).

Each line holds the time, method, path, query (with the requestId TT support asks for),
status, duration, the request body of a POST and TT's reply. Headers are never logged,
so the API key and the access token never appear. The sign-in call logs no bodies at
all, since its form holds the secret and its reply holds the token.

The log holds account data like a snapshot does, so it stays on this PC: never commit
or share it without checking it first.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

MAX_BODY_CHARS = 200_000  # longer replies are cut, with a note saying so


def _cut(text: str) -> str:
    if len(text) <= MAX_BODY_CHARS:
        return text
    return text[:MAX_BODY_CHARS] + f"... [cut: {len(text):,} characters in all]"


class ApiLog:
    def __init__(self, folder: Path, *, now: Callable[[], datetime] = datetime.now) -> None:
        self.folder = folder
        self._now = now
        self.failed = ""  # why writing the log failed, if it did; the call goes on anyway

    def path_for(self, when: datetime) -> Path:
        return self.folder / f"api_{when:%Y%m%d}.jsonl"

    def record(
        self,
        *,
        environment: str,
        method: str,
        path: str,
        query: Mapping[str, Any] | None,
        status: int | str | None,
        ms: int | None,
        request_body: Any = None,
        response_text: str | None = None,
    ) -> None:
        when = self._now()
        entry: dict[str, Any] = {
            "time": when.isoformat(timespec="milliseconds"),
            "environment": environment,
            "method": method,
            "path": path,
            "query": dict(query or {}),
            "status": status,
            "ms": ms,
        }
        if request_body is not None:
            entry["request_body"] = request_body
        if response_text is not None:
            entry["response"] = _cut(response_text)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with self.path_for(when).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, default=str) + "\n")
        except OSError as exc:  # a full disk must not stop a pull or a clone
            self.failed = f"Could not write the API log in {self.folder}: {exc}"
