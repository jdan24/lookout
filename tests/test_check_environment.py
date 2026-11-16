"""The launcher's package check: it reports, and never installs."""

from __future__ import annotations

import importlib.util
from importlib import metadata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_environment.py"

spec = importlib.util.spec_from_file_location("check_environment", SCRIPT)
assert spec and spec.loader
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def fake_installed(versions: dict[str, str]):
    def lookup(name: str) -> str:
        if name not in versions:
            raise metadata.PackageNotFoundError(name)
        return versions[name]

    return lookup


def test_parse_requirements_skips_comments_blanks_and_includes() -> None:
    text = "# note\n\n-r other.txt\npandas>=2.2\n  streamlit >= 1.40  # ui\n"
    assert check.parse_requirements(text) == [("pandas", "2.2"), ("streamlit", "1.40")]


def test_requirements_file_lists_the_runtime_packages() -> None:
    names = [name for name, _ in check.parse_requirements(check.REQUIREMENTS.read_text())]
    assert names == [
        "httpx", "keyring", "openpyxl", "pandas",
        "platformdirs", "pydantic", "streamlit", "truststore",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.40.0", (1, 40, 0)), ("2.2.0rc1", (2, 2, 0)), ("25", (25,)), ("3.1.5+local", (3, 1, 5))],
)
def test_version_tuple(version: str, expected: tuple[int, ...]) -> None:
    assert check.version_tuple(version) == expected


def test_versions_compare_as_numbers_not_text() -> None:
    requirements = [("streamlit", "1.40")]
    assert check.find_problems(requirements, fake_installed({"streamlit": "1.9.0"})) == [
        "too old: streamlit 1.9.0 (needs 1.40 or newer)"
    ]
    assert check.find_problems(requirements, fake_installed({"streamlit": "1.40"})) == []
    assert check.find_problems(requirements, fake_installed({"streamlit": "1.100.2"})) == []
    assert check.find_problems([("keyring", "25")], fake_installed({"keyring": "25.0.0"})) == []


def test_missing_and_old_packages_are_each_listed() -> None:
    requirements = [("pandas", "2.2"), ("httpx", "0.27"), ("keyring", "25")]
    installed = fake_installed({"pandas": "2.1.4", "keyring": "25.7.0"})
    assert check.find_problems(requirements, installed) == [
        "too old: pandas 2.1.4 (needs 2.2 or newer)",
        "missing: httpx (needs 0.27 or newer)",
    ]


def test_old_python_is_reported() -> None:
    assert check.find_problems([], python_version=(3, 11, 9)) == [
        "too old: Python 3.11.9 (needs 3.12 or newer)"
    ]
    assert check.find_problems([], python_version=(3, 14, 0)) == []


def test_main_returns_0_when_clean_and_1_with_a_list(monkeypatch, capsys) -> None:
    assert check.main() == 0  # the development environment has everything
    assert "were found" in capsys.readouterr().out

    monkeypatch.setattr(check, "find_problems", lambda _: ["missing: pandas (needs 2.2 or newer)"])
    assert check.main() == 1
    out = capsys.readouterr().out
    assert "missing: pandas" in out and "Nothing was installed" in out


def test_check_uses_only_the_standard_library() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "subprocess" not in text and "pip" not in text and "urllib" not in text
