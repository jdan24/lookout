"""TT credentials, kept only in the operating system's credential store.

On Windows this is Credential Manager, through ``keyring``. The secret is the TT
``APP_SECRET`` value in the form ``<app key>:<secret part>``; the app key is the
text before the colon. It is never written to a file, a log or a snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass

import keyring
import keyring.errors

SERVICE = "Lookout"
ENTRY = "tt_app_secret"

HOW_TO_SET = "Run scripts/set_credentials.py once on this PC to store the TT credentials."


class CredentialError(Exception):
    """TT credentials are missing or malformed. The message never contains the secret."""


@dataclass(frozen=True, repr=False)
class TTCredentials:
    app_key: str  # the text before the colon
    app_secret: str  # the whole value, as TT expects it

    def __repr__(self) -> str:
        return "TTCredentials(app_key=***, app_secret=***)"

    __str__ = __repr__


def parse_secret(secret: str) -> TTCredentials:
    """Split the stored value into key and secret. Raises without echoing the value."""
    value = (secret or "").strip()
    key, colon, rest = value.partition(":")
    if not colon or not key.strip() or not rest.strip() or any(c.isspace() for c in value):
        raise CredentialError(
            "The TT secret must look like <app key>:<secret>, with no spaces. " + HOW_TO_SET
        )
    return TTCredentials(app_key=key, app_secret=value)


def store_secret(secret: str) -> None:
    """Validate and save the secret in the credential store."""
    credentials = parse_secret(secret)
    keyring.set_password(SERVICE, ENTRY, credentials.app_secret)


def load_credentials() -> TTCredentials:
    try:
        stored = keyring.get_password(SERVICE, ENTRY)
    except keyring.errors.KeyringError as exc:
        raise CredentialError(
            f"The credential store could not be read ({type(exc).__name__})."
        ) from None
    if not stored:
        raise CredentialError("No TT credentials are stored on this PC. " + HOW_TO_SET)
    return parse_secret(stored)


def has_credentials() -> bool:
    try:
        load_credentials()
    except CredentialError:
        return False
    return True


def delete_secret() -> None:
    try:
        keyring.delete_password(SERVICE, ENTRY)
    except keyring.errors.PasswordDeleteError:
        pass  # nothing stored
