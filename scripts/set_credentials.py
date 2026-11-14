"""Store the TT credentials on this PC. Run once per machine:

    uv run python scripts/set_credentials.py

Paste the APP_SECRET value (it looks like <app key>:<secret>). It is saved in
Windows Credential Manager and nowhere else. Nothing you type is shown or logged.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import credentials  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = sys.stdout.write
    if args == ["--delete"]:
        credentials.delete_secret()
        out("TT credentials removed from this PC.\n")
        return 0
    if args == ["--check"]:
        stored = credentials.has_credentials()
        out("TT credentials are stored on this PC.\n" if stored else "No TT credentials stored.\n")
        return 0 if stored else 1
    if args:
        out("Usage: set_credentials.py [--check | --delete]\n")
        return 2

    out("Paste the TT APP_SECRET value and press Enter. It will not be shown.\n")
    secret = getpass.getpass("APP_SECRET: ")
    try:
        credentials.store_secret(secret)
    except credentials.CredentialError as exc:
        out(f"Not saved. {exc}\n")
        return 1
    out("Saved. Lookout can now sign in to TT from this PC.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
