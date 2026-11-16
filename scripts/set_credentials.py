"""Store the TT credentials on this PC. Run once per machine:

    python scripts/set_credentials.py          (Live)
    python scripts/set_credentials.py --uat    (UAT, kept as a separate entry)

Paste the APP_SECRET value (it looks like <app key>:<secret>). It is saved in
Windows Credential Manager and nowhere else. Nothing you type is shown or logged.
Add --check to see whether credentials are stored, or --delete to remove them.
"""

from __future__ import annotations

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import credentials  # noqa: E402
from core.readonly_http import LIVE_ENVIRONMENT, UAT_ENVIRONMENT  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out = sys.stdout.write
    uat = "--uat" in args
    if uat:
        args.remove("--uat")
    environment = UAT_ENVIRONMENT if uat else LIVE_ENVIRONMENT
    which = "TT UAT" if uat else "TT"
    if args == ["--delete"]:
        credentials.delete_secret(environment)
        out(f"{which} credentials removed from this PC.\n")
        return 0
    if args == ["--check"]:
        stored = credentials.has_credentials(environment)
        out(
            f"{which} credentials are stored on this PC.\n"
            if stored
            else f"No {which} credentials stored.\n"
        )
        return 0 if stored else 1
    if args:
        out("Usage: set_credentials.py [--uat] [--check | --delete]\n")
        return 2

    out(f"Paste the {which} APP_SECRET value and press Enter. It will not be shown.\n")
    secret = getpass.getpass("APP_SECRET: ")
    try:
        credentials.store_secret(secret, environment)
    except credentials.CredentialError as exc:
        out(f"Not saved. {exc}\n")
        return 1
    out(f"Saved. Lookout can now sign in to {which} from this PC.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
