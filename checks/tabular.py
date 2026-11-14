"""Reading user-supplied tables (pasted text, CSV, XLSX) and finding their columns.

Ported from limit_compare (``shared/text.ts``, ``shared/parsers/autoDetect.ts``,
and its RIC tool's ``paste.ts``). Every value is kept as text.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

_NON_ALNUM = re.compile(r"[^a-z0-9]")


def canon(text: str | None) -> str:
    """Lowercase, letters and digits only: for loose matching of headers and keys."""
    return _NON_ALNUM.sub("", (text or "").lower())


@dataclass(frozen=True)
class RawTable:
    headers: list[str]
    rows: list[dict[str, str]]
    file_name: str = ""


class TableError(ValueError):
    """The text or file could not be read as a table."""


# --- column detection ------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    field: str
    label: str
    aliases: tuple[str, ...]  # the first is the canonical label
    required: bool
    # Also match a header that ends with one of these, when no alias matched exactly
    # (e.g. "<Firm> Supported" for "Supported").
    suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Detection:
    mapping: dict[str, str]  # field -> header
    confident: frozenset[str] = field(default_factory=frozenset)


def auto_detect(specs: Sequence[FieldSpec], headers: Sequence[str]) -> Detection:
    """Match each field to a header by exact canonical equality with one of its aliases.

    Exact-only matching keeps "Max order quantity" from grabbing the
    "Spreads: Max order quantity" column, and the other way round.
    """
    canon_headers = [(header, canon(header)) for header in headers]
    used: set[str] = set()
    mapping: dict[str, str] = {}
    for spec in specs:
        alias_canons = {canon(alias) for alias in spec.aliases}
        for header, c in canon_headers:
            if header not in used and c in alias_canons:
                mapping[spec.field] = header
                used.add(header)
                break
    for spec in specs:
        if spec.field in mapping or not spec.suffixes:
            continue
        endings = tuple(canon(suffix) for suffix in spec.suffixes)
        for header, c in canon_headers:
            if header not in used and c.endswith(endings):
                mapping[spec.field] = header
                used.add(header)
                break
    return Detection(mapping=mapping, confident=frozenset(mapping))


def required_fields(specs: Sequence[FieldSpec]) -> list[str]:
    return [spec.field for spec in specs if spec.required]


def missing_fields(specs: Sequence[FieldSpec], mapping: dict[str, str]) -> list[str]:
    return [spec.label for spec in specs if spec.required and not mapping.get(spec.field)]


def is_complete(specs: Sequence[FieldSpec], mapping: dict[str, str]) -> bool:
    return not missing_fields(specs, mapping)


# --- reading ---------------------------------------------------------------------


def _from_rows(records: list[list[str]], file_name: str) -> RawTable:
    if not records:
        raise TableError("Couldn't find a header row in the pasted text.")
    headers = [h.strip() for h in records[0]]
    positions = [(i, h) for i, h in enumerate(headers) if h]
    if not positions:
        raise TableError("Couldn't find a header row in the pasted text.")
    rows: list[dict[str, str]] = []
    for record in records[1:]:
        if not any(cell.strip() for cell in record):
            continue  # blank or whitespace-only lines are skipped
        rows.append({h: (record[i].strip() if i < len(record) else "") for i, h in positions})
    return RawTable(headers=[h for _, h in positions], rows=rows, file_name=file_name)


def _delimiter(first_line: str, sample: str) -> str:
    if "\t" in first_line:
        return "\t"
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;|\t").delimiter
    except csv.Error:
        return ","


def parse_pasted_table(text: str, file_name: str = "Pasted table") -> RawTable:
    """A table pasted from Excel / Outlook / a web page (tab-separated), or CSV text."""
    body = text.lstrip("﻿")
    body = re.sub(r"^\s*\n", "", body)
    first_line = re.split(r"\r?\n", body, maxsplit=1)[0]
    reader = csv.reader(
        io.StringIO(body, newline=""), delimiter=_delimiter(first_line, body[:4096])
    )
    return _from_rows(list(reader), file_name)


def read_csv_bytes(data: bytes, file_name: str) -> RawTable:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return parse_pasted_table(text, file_name)


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value).strip()


def read_xlsx_bytes(data: bytes, file_name: str) -> RawTable:
    """The first sheet; first row is the header. Unnamed headers become "Column N"."""
    from openpyxl import load_workbook

    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - any failure means "not a readable workbook"
        raise TableError(f"{file_name} could not be read as an Excel workbook.") from exc
    try:
        sheet = book.worksheets[0]
        grid = [[_cell_text(v) for v in row] for row in sheet.iter_rows(values_only=True)]
    finally:
        book.close()
    if not grid:
        raise TableError(f"{file_name} is empty.")
    headers = [h or f"Column {i + 1}" for i, h in enumerate(grid[0])]
    return _from_rows([headers, *grid[1:]], file_name)


def read_table_file(data: bytes, file_name: str) -> RawTable:
    if file_name.lower().endswith((".xlsx", ".xlsm")):
        return read_xlsx_bytes(data, file_name)
    return read_csv_bytes(data, file_name)
