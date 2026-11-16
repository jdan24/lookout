"""Check that this PC's Python has the packages Lookout needs:

    python scripts/check_environment.py

It only looks. It never installs, downloads or changes anything. It lists what is
missing or older than requirements.txt asks for and returns 1 if it listed anything.
Standard library only, so it runs even when no package is installed.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
MIN_PYTHON = (3, 12)

_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*>=\s*([0-9][^\s;#]*)")


def parse_requirements(text: str) -> list[tuple[str, str]]:
    """(package, minimum version) for each name>=version line; other lines are skipped."""
    found = []
    for line in text.splitlines():
        match = _LINE.match(line.strip())
        if match:
            found.append((match.group(1), match.group(2)))
    return found


def version_tuple(version: str) -> tuple[int, ...]:
    """The leading numeric parts of a version: "2.2.0rc1" -> (2, 2, 0)."""
    parts: list[int] = []
    for piece in version.split("."):
        digits = re.match(r"\d+", piece)
        if not digits:
            break
        parts.append(int(digits.group()))
        if digits.group() != piece:
            break
    return tuple(parts)


def _is_older(installed: str, minimum: str) -> bool:
    have, need = version_tuple(installed), version_tuple(minimum)
    width = max(len(have), len(need))
    have += (0,) * (width - len(have))
    need += (0,) * (width - len(need))
    return have < need


def find_problems(
    requirements: list[tuple[str, str]],
    installed_version: Callable[[str], str] = metadata.version,
    python_version: tuple[int, ...] = tuple(sys.version_info[:3]),
) -> list[str]:
    """One plain line per thing that is missing or too old. Empty when all is well."""
    problems = []
    if tuple(python_version[:2]) < MIN_PYTHON:
        have = ".".join(str(part) for part in python_version)
        need = ".".join(str(part) for part in MIN_PYTHON)
        problems.append(f"too old: Python {have} (needs {need} or newer)")
    for name, minimum in requirements:
        try:
            installed = installed_version(name)
        except metadata.PackageNotFoundError:
            problems.append(f"missing: {name} (needs {minimum} or newer)")
            continue
        if _is_older(installed, minimum):
            problems.append(f"too old: {name} {installed} (needs {minimum} or newer)")
    return problems


def main() -> int:
    out = sys.stdout.write
    requirements = parse_requirements(REQUIREMENTS.read_text(encoding="utf-8"))
    problems = find_problems(requirements)
    if not problems:
        out(f"All {len(requirements)} packages Lookout needs were found.\n")
        return 0
    out("\n This Python does not have everything Lookout needs:\n\n")
    for problem in problems:
        out(f"   {problem}\n")
    out(f"\n Python used: {sys.executable}\n")
    out(" Nothing was installed or downloaded. Ask for these packages through your\n")
    out(" usual process, then start Lookout again.\n\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
