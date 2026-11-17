"""Rules about the shape of the codebase, enforced by reading the source."""

from __future__ import annotations

import ast
import importlib
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PACKAGES = ["core", "reports", "checks", "checks.setup", "ui"]
SOURCE_DIRS = ["core", "reports", "checks", "ui", "scripts"]
STREAMLIT_FREE_DIRS = ["core", "checks"]

# The only files allowed to import an HTTP library: the read guard and the UAT write guard.
HTTP_GUARDS = {Path("core/readonly_http.py"), Path("core/write_http.py")}
HTTP_LIBRARIES = {"httpx", "httpcore", "requests", "urllib3", "aiohttp", "http.client"}
HTTP_MODULE_PREFIXES = ("urllib.request",)


def source_files(dirs: list[str]) -> list[Path]:
    files = [p for d in dirs for p in sorted((ROOT / d).rglob("*.py"))]
    if (ROOT / "app.py").exists() and dirs is SOURCE_DIRS:
        files.append(ROOT / "app.py")
    return files


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def is_http_import(module: str) -> bool:
    if module.startswith(HTTP_MODULE_PREFIXES):
        return True
    return any(module == lib or module.startswith(lib + ".") for lib in HTTP_LIBRARIES)


@pytest.mark.parametrize("package", PACKAGES)
def test_packages_import(package: str) -> None:
    importlib.import_module(package)


def test_core_and_checks_are_streamlit_free() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in source_files(STREAMLIT_FREE_DIRS)
        if any(m == "streamlit" or m.startswith("streamlit.") for m in imported_modules(path))
    ]
    assert offenders == [], f"Streamlit imported outside the UI layers: {offenders}"


def test_only_the_guard_imports_an_http_library() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in source_files(SOURCE_DIRS)
        if path.relative_to(ROOT) not in HTTP_GUARDS
        and any(is_http_import(m) for m in imported_modules(path))
    ]
    assert offenders == [], f"HTTP library imported outside {HTTP_GUARDS}: {offenders}"


def test_http_import_detection() -> None:
    assert is_http_import("httpx")
    assert is_http_import("urllib.request")
    assert is_http_import("requests.adapters")
    assert not is_http_import("urllib.parse")
    assert not is_http_import("http")


def test_network_is_blocked_in_tests() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(RuntimeError, match="must not open network"):
            sock.connect(("192.0.2.1", 443))
