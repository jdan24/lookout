"""Read a clone's source from TT, and carry a clone plan out in TT UAT.

Reads go through a normal ``TTClient`` (read-only guard). Writes go through a
``TTWriter`` (UAT-only write guard). A run stops at the first failure and says what was
and was not created; it never tries to undo anything.

The request shapes follow TT's public ttaccount and ttsetup references for UAT; the
first supervised UAT run confirms them.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from core.clone import (
    CLEARING_FIELD_NAME,
    ClearingChange,
    ClearingMode,
    ClonePlan,
    CloneStep,
    clone_params,
    find_source,
    is_default_entry,
)
from core.hierarchy import AccountTree
from core.normalize import norm_id
from core.readonly_http import ReadOnlyViolation
from core.tt_client import TTApiError, TTClient, TTResponse
from core.tt_writer import TTWriter

# Fields of an Order Tag Default that TT takes when creating one (ttsetup reference).
OTD_CREATE_FIELDS: tuple[str, ...] = (
    "accountId", "algoId", "algoTypeId", "applyToAllSubAccounts", "assetClassId",
    "clientCanOverride", "companyId", "exchangeProductGroupId", "fieldId", "marketId",
    "productId", "productTypeId", "profileId", "userId", "value", "valueEnumId",
)  # fmt: skip
OTD_UPDATE_FIELDS: tuple[str, ...] = (*OTD_CREATE_FIELDS, "orderTagDefaultId", "revision")
# What makes two Order Tag Defaults "the same slot" on an account.
OTD_SLOT: tuple[str, ...] = (
    "fieldId", "marketId", "productTypeId", "productId", "exchangeProductGroupId",
    "algoTypeId", "profileId",
)  # fmt: skip


@dataclass
class SourceData:
    tree: AccountTree
    clearing_field_ids: frozenset[str] = frozenset()
    clearing_entries: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    market_names: dict[str, str] = field(default_factory=dict)


def read_source(
    tt: TTClient, source_text: str, *, need_clearing: bool, max_pages: int
) -> SourceData:
    """The accounts list, and the source's Clearing Account Order Tag Defaults when needed."""
    accounts: list[dict[str, Any]] = []
    for page in tt.pages("ttaccount", "accounts"):
        accounts.extend(page.response.body.get("accounts") or [])
    data = SourceData(tree=AccountTree(accounts))
    source, _ = find_source(data.tree, source_text)
    if source is None or not need_clearing:
        return data

    fields = tt.get("ttsetup", "ordertagdefaults/fields").body.get("fields") or []
    data.clearing_field_ids = frozenset(
        norm_id(f.get("fieldId"))
        for f in fields
        if str(f.get("name") or "").strip().casefold() == CLEARING_FIELD_NAME.casefold()
    )
    if not data.clearing_field_ids:
        raise TTApiError(f"TT has no Order Tag Default field named {CLEARING_FIELD_NAME}.")
    markets = tt.get("ttpds", "markets").body.get("markets") or []
    data.market_names = {norm_id(m.get("id")): str(m.get("name") or "") for m in markets}
    for account in data.tree.subtree(source.id):
        found = account_entries(tt, account.id, data.clearing_field_ids, max_pages)
        if found:
            data.clearing_entries[account.id] = found
    return data


def account_entries(
    tt: TTClient, account_id: str, field_ids: frozenset[str], max_pages: int
) -> list[dict[str, Any]]:
    """The account's own (not per-user) Order Tag Defaults for ``field_ids``."""
    found: list[dict[str, Any]] = []
    reached_end = False
    for page in tt.pages(
        "ttsetup", "ordertagdefaults", {"accountId": account_id}, max_pages=max_pages
    ):
        for entry in page.response.body.get("orderTagDefaults") or []:
            if (
                norm_id(entry.get("accountId")) == account_id
                and norm_id(entry.get("fieldId")) in field_ids
                and is_default_entry(entry)
            ):
                found.append(entry)
        reached_end = page.reached_end
    if not reached_end:
        raise TTApiError(
            f"Could not read all Order Tag Defaults for account {account_id} within "
            f"{max_pages} pages; nothing was changed."
        )
    return sorted(found, key=lambda e: norm_id(e.get("marketId")).zfill(12))


def _body(entry: Mapping[str, Any], names: Sequence[str], **changes: Any) -> dict[str, Any]:
    body = {name: entry[name] for name in names if name in entry}
    body.update(changes)
    return body


def _slot(entry: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(norm_id(entry.get(name)) if name != "productId" else str(entry.get(name) or "0")
                 for name in OTD_SLOT)  # fmt: skip


@dataclass
class CloneResult:
    rows: list[dict[str, str]] = field(default_factory=list)
    created: dict[int, str] = field(default_factory=dict)  # step number -> new account id
    stopped: str = ""  # why the run stopped early, if it did

    @property
    def ok(self) -> bool:
        return not self.stopped


ID_KEYS: tuple[str, ...] = ("id", "accountId", "newAccountId")
LOOKUP_TRIES = 3  # reads of the accounts list when the clone reply holds no id
LOOKUP_WAIT_SECONDS = 2.0


def id_in_reply(body: Any, name: str) -> str:
    """The new account's id from TT's clone reply, wherever in the reply it is.

    TT's reference says ``account: [{id, name, ...}]``, but the first UAT run did not
    match it, so any object carrying the new name and an id is accepted. Failing that,
    a lone id at the top of the reply. ``""`` when there is none.
    """
    named: list[str] = []
    loose: list[str] = []

    def walk(value: Any, depth: int) -> None:
        if isinstance(value, Mapping):
            ids = [norm_id(value.get(k)) for k in ID_KEYS if _is_id(value.get(k))]
            text = str(value.get("name") or value.get("accountName") or "")
            if ids and text.strip().casefold() == name.strip().casefold():
                named.append(ids[0])
            elif ids and depth == 0:
                loose.append(ids[0])
            for child in value.values():
                walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, depth + 1)

    walk(body, 0)
    if named:
        return named[0]
    return loose[0] if len(loose) == 1 else ""


def _is_id(value: Any) -> bool:
    try:
        return bool(norm_id(value)) and norm_id(value).isdigit()
    except ValueError:
        return False


def find_new_account(
    reader: TTClient, name: str, parent_id: str, *, wait: Callable[[float], None]
) -> str:
    """Look the new account up by name in TT's accounts list (under ``parent_id``)."""
    for attempt in range(LOOKUP_TRIES):
        if attempt:
            wait(LOOKUP_WAIT_SECONDS)  # TT may take a moment to list a new account
        for page in reader.pages("ttaccount", "accounts"):
            for item in page.response.body.get("accounts") or []:
                same_name = str(item.get("name") or "").casefold() == name.casefold()
                parent = norm_id(item.get("parentAccountId")) or norm_id(item.get("parentId"))
                if same_name and (not parent_id or parent == parent_id):
                    return norm_id(item.get("id"))
    return ""


def _new_account_id(
    reply: TTResponse, name: str, parent_id: str, reader: TTClient,
    wait: Callable[[float], None],
) -> tuple[str, str]:  # fmt: skip
    """(new id, how it was found). Raises when TT's reply and accounts list both lack it."""
    new_id = id_in_reply(reply.body, name)
    if new_id:
        return new_id, ""
    new_id = find_new_account(reader, name, parent_id, wait=wait)
    if new_id:
        return new_id, "id read from TT's accounts list (the clone reply had none)"
    shown = reply.text if len(reply.text) <= 500 else reply.text[:500] + "..."
    raise TTApiError(
        f"TT accepted the clone of {name} but its reply gave no new id, and {name} was not "
        f"found in TT's accounts list. The account may still have been created: check TT "
        f"Setup before trying again. TT's reply: {shown}"
    )


def run_plan(
    plan: ClonePlan,
    writer: TTWriter,
    reader: TTClient,
    *,
    clearing_field_ids: frozenset[str],
    max_pages: int,
    wait: Callable[[float], None] = time.sleep,
) -> CloneResult:
    """Create every account in the plan, in order, then set its Clearing Account values."""
    result = CloneResult()
    if not plan.ok:
        result.stopped = "The plan has errors; nothing was sent to TT."
        return result
    params = clone_params(plan.flags)
    for step in plan.steps:
        try:
            parent_id = result.created[step.parent_step] if step.parent_step else step.parent_id
            body: dict[str, Any] = {
                "sourceAccountId": int(step.source.id),
                "accountName": step.new_name,
                "cloneChildren": "False",
            }
            if parent_id:
                body["parentId"] = int(parent_id)
            reply = writer.post("ttaccount", "account/clone", body, params)
            new_id, how = _new_account_id(reply, step.new_name, parent_id, reader, wait)
            result.created[step.number] = new_id
            detail = f"from {step.source.display}" + (f"; {how}" if how else "")
            result.rows.append(_row(step, new_id, "Account", "Created", detail))
            changes = [c for c in plan.clearing if c.step == step.number]
            if changes:
                _set_clearing(plan, step, new_id, changes, writer, reader, result,
                              clearing_field_ids, max_pages)  # fmt: skip
        except (TTApiError, ReadOnlyViolation) as exc:
            result.stopped = f"Stopped at step {step.number} ({step.new_name}): {exc}"
            result.rows.append(
                _row(step, result.created.get(step.number, ""), "", "Failed", str(exc))
            )
            break
    done = set(result.created)
    for step in plan.steps:
        if step.number not in done and not any(r["Step"] == str(step.number) for r in result.rows):
            result.rows.append(_row(step, "", "Account", "Not run", ""))
    return result


def _row(step: CloneStep, new_id: str, what: str, status: str, detail: str) -> dict[str, str]:
    return {
        "Step": str(step.number),
        "New account": step.new_name,
        "New id": new_id,
        "What": what,
        "Status": status,
        "Detail": detail,
    }


def _set_clearing(
    plan: ClonePlan,
    step: CloneStep,
    new_id: str,
    changes: list[ClearingChange],
    writer: TTWriter,
    reader: TTClient,
    result: CloneResult,
    field_ids: frozenset[str],
    max_pages: int,
) -> None:
    copied_by_tt = bool(plan.flags.get("orderTagDefaults"))
    to_create: list[ClearingChange] = []
    if copied_by_tt and plan.clearing_mode is ClearingMode.SOURCE:
        for change in changes:
            result.rows.append(_row(step, new_id, f"Clearing Account, {change.market}",
                                    "Copied by TT", change.value))  # fmt: skip
        return
    if copied_by_tt:
        current = {_slot(e): e for e in account_entries(reader, new_id, field_ids, max_pages)}
        for change in changes:
            entry = current.get(_slot(change.entry))
            what = f"Clearing Account, {change.market}"
            if entry is None:
                to_create.append(change)
            elif str(entry.get("value") or "") == change.value:
                result.rows.append(_row(step, new_id, what, "Already set", change.value))
            else:
                entry_id = norm_id(entry.get("orderTagDefaultId"))
                writer.post(
                    "ttsetup",
                    f"ordertagdefaults/{entry_id}",
                    _body(entry, OTD_UPDATE_FIELDS, value=change.value),
                )
                result.rows.append(_row(step, new_id, what, "Updated", change.value))
    else:
        to_create = changes
    if to_create:
        writer.post(
            "ttsetup",
            "ordertagdefaults",
            [
                _body(c.entry, OTD_CREATE_FIELDS, accountId=int(new_id), value=c.value)
                for c in to_create
            ],
        )
        for change in to_create:
            result.rows.append(_row(step, new_id, f"Clearing Account, {change.market}",
                                    "Created", change.value))  # fmt: skip


def write_log(
    folder: Path,
    plan: ClonePlan,
    result: CloneResult,
    calls: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> Path:
    """Save what was asked and what happened as JSON. No secrets: calls hold paths only."""
    when = now or datetime.now()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"clone_{when:%Y%m%d_%H%M%S_%f}.json"
    record = {
        "when": when.isoformat(timespec="seconds"),
        "source": plan.source.display if plan.source else "",
        "flags": dict(plan.flags),
        "clearing_mode": plan.clearing_mode.value,
        "plan": plan.rows(),
        "clearing": plan.clearing_rows(),
        "result": result.rows,
        "stopped": result.stopped,
        "calls": list(calls),
    }
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return path
