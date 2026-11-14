from __future__ import annotations

import importlib.util
from pathlib import Path

import keyring
import keyring.backend
import keyring.errors
import pytest

from core import credentials
from core.credentials import CredentialError, TTCredentials

ROOT = Path(__file__).resolve().parent.parent
SECRET = "appkey123:topsecretvalue"


class MemoryKeyring(keyring.backend.KeyringBackend):
    """Stands in for Windows Credential Manager."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self.store[(service, username)]


@pytest.fixture
def vault() -> MemoryKeyring:
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location(
        "set_credentials", ROOT / "scripts/set_credentials.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_key_is_the_text_before_the_colon() -> None:
    creds = credentials.parse_secret(f"  {SECRET}\n")
    assert creds.app_key == "appkey123"
    assert creds.app_secret == SECRET
    assert credentials.parse_secret("k:a:b").app_key == "k"
    assert credentials.parse_secret("k:a:b").app_secret == "k:a:b"


@pytest.mark.parametrize(
    "bad", ["", "   ", "nocolon", ":secret", "key:", "key: ", "ke y:secret", None]
)
def test_malformed_secret_is_rejected_without_echoing_it(bad: str | None) -> None:
    with pytest.raises(CredentialError) as info:
        credentials.parse_secret(bad)  # type: ignore[arg-type]
    if bad and bad.strip():
        assert bad.strip() not in str(info.value)


def test_credentials_never_print_themselves() -> None:
    creds = TTCredentials(app_key="appkey123", app_secret=SECRET)
    for text in (repr(creds), str(creds), f"{creds}", f"{[creds]}"):
        assert "appkey123" not in text and "topsecretvalue" not in text


def test_store_and_load(vault: MemoryKeyring) -> None:
    assert not credentials.has_credentials()
    with pytest.raises(CredentialError, match="set_credentials.py"):
        credentials.load_credentials()

    credentials.store_secret(SECRET)
    assert vault.store == {("Lookout", "tt_app_secret"): SECRET}
    assert credentials.has_credentials()
    assert credentials.load_credentials().app_key == "appkey123"

    credentials.delete_secret()
    credentials.delete_secret()  # deleting twice is fine
    assert vault.store == {}


def test_bad_secret_is_not_stored(vault: MemoryKeyring) -> None:
    with pytest.raises(CredentialError):
        credentials.store_secret("nocolon")
    assert vault.store == {}


def test_unreadable_store_gives_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(service: str, username: str) -> str:
        raise keyring.errors.KeyringError(f"backend exploded while reading {SECRET}")

    monkeypatch.setattr(keyring, "get_password", broken)
    with pytest.raises(CredentialError) as info:
        credentials.load_credentials()
    assert "topsecretvalue" not in str(info.value)
    assert info.value.__cause__ is None


def test_script_stores_what_is_typed_and_shows_nothing(
    vault: MemoryKeyring, script, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(script.getpass, "getpass", lambda prompt: SECRET)
    assert script.main([]) == 0
    assert vault.store == {("Lookout", "tt_app_secret"): SECRET}
    output = capsys.readouterr()
    assert "Saved" in output.out
    assert "topsecretvalue" not in output.out + output.err
    assert "appkey123" not in output.out + output.err


def test_script_rejects_a_malformed_value(
    vault: MemoryKeyring, script, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(script.getpass, "getpass", lambda prompt: "not-a-secret")
    assert script.main([]) == 1
    assert vault.store == {}
    output = capsys.readouterr().out
    assert "Not saved" in output and "not-a-secret" not in output


def test_script_check_and_delete(
    vault: MemoryKeyring, script, capsys: pytest.CaptureFixture
) -> None:
    assert script.main(["--check"]) == 1
    credentials.store_secret(SECRET)
    assert script.main(["--check"]) == 0
    assert script.main(["--delete"]) == 0
    assert vault.store == {}
    assert script.main(["--bogus"]) == 2
    assert "topsecretvalue" not in capsys.readouterr().out


def test_no_secret_shaped_values_in_tracked_config() -> None:
    for name in ("config.toml", "docs/example_client.toml"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        assert "app_secret" not in text and "password" not in text and "app_key" not in text


def test_launcher_is_a_windows_batch_file() -> None:
    raw = (ROOT / "scripts" / "Start-Lookout.bat").read_bytes()
    assert raw.isascii()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    text = raw.decode("ascii")
    assert "%LOCALAPPDATA%\\Lookout\\app" in text
    assert "streamlit run app.py --server.address localhost --server.headless true" in text
    assert "where uv" in text and "astral.sh/uv/install.ps1" in text
    assert "http://localhost:%PORT%" in text and 'set "PORT=8501"' in text
    assert "/XD .venv" in text  # updating must not wipe the local environment
