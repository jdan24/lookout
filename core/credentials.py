"""TT credentials, kept only in the operating system's credential store.

On Windows this is Credential Manager, through ``keyring``. The secret is the TT
``APP_SECRET`` value in the form ``<app key>:<secret part>``; the app key is the
text before the colon. It is never written to a file, a log or a snapshot.

Live and UAT have separate entries, so one can never be used for the other.
"""

from __future__ import annotations

from dataclasses import dataclass

import keyring
import keyring.errors

from core.readonly_http import LIVE_ENVIRONMENT, UAT_ENVIRONMENT

SERVICE = "Lookout"
ENTRIES = {LIVE_ENVIRONMENT: "tt_app_secret", UAT_ENVIRONMENT: "tt_app_secret_uat"}

HOW_TO_SET = "Run scripts/set_credentials.py once on this PC to store the TT credentials."
HOW_TO_SET_UAT = (
    "Run scripts/set_credentials.py --uat once on this PC to store the TT UAT credentials."
)


def how_to_set(environment: str = LIVE_ENVIRONMENT) -> str:
    return HOW_TO_SET_UAT if environment == UAT_ENVIRONMENT else HOW_TO_SET


def _entry(environment: str) -> str:
    try:
        return ENTRIES[environment]
    except KeyError:
        raise CredentialError(f"Unknown TT environment: {environment!r}.") from None


def _missing(environment: str) -> str:
    which = "TT UAT" if environment == UAT_ENVIRONMENT else "TT"
    return f"No {which} credentials are stored on this PC. " + how_to_set(environment)


class CredentialError(Exception):
    """TT credentials are missing or malformed. The message never contains the secret."""


@dataclass(frozen=True, repr=False)
class TTCredentials:
    app_key: str  # the text before the colon
    app_secret: str  # the whole value, as TT expects it

    def __repr__(self) -> str:
        return "TTCredentials(app_key=***, app_secret=***)"

    __str__ = __repr__


def parse_secret(secret: str, environment: str = LIVE_ENVIRONMENT) -> TTCredentials:
    """Split the stored value into key and secret. Raises without echoing the value."""
    value = (secret or "").strip()
    key, colon, rest = value.partition(":")
    if not colon or not key.strip() or not rest.strip() or any(c.isspace() for c in value):
        raise CredentialError(
            "The TT secret must look like <app key>:<secret>, with no spaces. "
            + how_to_set(environment)
        )
    return TTCredentials(app_key=key, app_secret=value)


def store_secret(secret: str, environment: str = LIVE_ENVIRONMENT) -> None:
    """Validate and save the secret in the credential store, under its environment."""
    entry = _entry(environment)
    credentials = parse_secret(secret, environment)
    keyring.set_password(SERVICE, entry, credentials.app_secret)


def load_credentials(environment: str = LIVE_ENVIRONMENT) -> TTCredentials:
    entry = _entry(environment)
    try:
        stored = keyring.get_password(SERVICE, entry)
    except keyring.errors.KeyringError as exc:
        raise CredentialError(
            f"The credential store could not be read ({type(exc).__name__})."
        ) from None
    if not stored:
        raise CredentialError(_missing(environment))
    return parse_secret(stored, environment)


def has_credentials(environment: str = LIVE_ENVIRONMENT) -> bool:
    try:
        load_credentials(environment)
    except CredentialError:
        return False
    return True


def delete_secret(environment: str = LIVE_ENVIRONMENT) -> None:
    try:
        keyring.delete_password(SERVICE, _entry(environment))
    except keyring.errors.PasswordDeleteError:
        pass  # nothing stored
