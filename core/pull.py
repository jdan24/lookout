"""Pull a client's setup from TT into a new snapshot. Read-only, and scope-limited.

Only the client's own subtrees are read in detail. The company-wide accounts list
is read once (it is lightweight) to find those subtrees. Order Tag Defaults are
read last, because that list is long and may run into the call cap.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from core.hierarchy import GLOBAL_KEY, Account, AccountTree, HierarchyError
from core.normalize import norm_id
from core.setup_model import (
    ACCOUNT_DETAIL,
    ACCOUNT_USERS,
    ACCOUNTS_LIST,
    MARKETS,
    OTD_FIELDS,
    OTD_LIST,
)
from core.snapshots import Snapshot, SnapshotWriter, Status
from core.tt_client import CallCapReached, TTApiError, TTClient

KEPT_RAW_ID = "global_kept"


def pull_setup(
    tt: TTClient,
    writer: SnapshotWriter,
    *,
    resolve_roots: Callable[[AccountTree], list[str]],
    otd_max_pages: int = 1000,
) -> Snapshot:
    """Read the setup under the roots chosen by ``resolve_roots`` and save it."""
    errors: list[str] = []
    notes: list[str] = []
    page_counts: dict[str, int] = {}

    def finish(
        status: Status, scope: Sequence[Account] = (), roots: Sequence[str] = ()
    ) -> Snapshot:
        return writer.finish(
            status=status,
            nodes=[
                {
                    "key": a.key,
                    "id": a.id,
                    "name": a.name,
                    "parent": a.parent_id,
                    "account_type": a.account_type,
                }
                for a in scope
            ],
            roots=[f"acct:{root}" for root in roots],
            calls=tt.calls,
            errors=errors,
            notes=notes,
            scope={"roots": list(roots), "accounts": len(scope)},
            page_counts=page_counts,
        )

    # 1. The company-wide accounts list, to find the client's subtrees.
    accounts: list[dict[str, Any]] = []
    try:
        for page in tt.pages("ttaccount", "accounts"):
            writer.save_raw(
                ACCOUNTS_LIST, f"page{page.number}", page.response.text, node_key=GLOBAL_KEY
            )
            accounts.extend(page.response.body.get("accounts") or [])
            page_counts[ACCOUNTS_LIST] = page.number
        tree = AccountTree(accounts)
    except (TTApiError, HierarchyError) as exc:
        errors.append(f"Accounts list: {exc}")
        return finish("failed")
    try:
        roots = [norm_id(root) for root in resolve_roots(tree)]
    except Exception as exc:  # noqa: BLE001 - whatever the resolver raises, the pull has failed
        errors.append(f"Could not find the client's start accounts in TT: {exc}")
        return finish("failed")

    scope: list[Account] = []
    for root in roots:
        scope.extend(tree.subtree(root))

    # 2. Detail and users for each in-scope account; fields and markets once.
    capped = False

    def read(service: str, resource: str, endpoint: str, raw_id: str, node_key: str) -> None:
        nonlocal capped
        if capped:
            writer.note_unread(endpoint, node_key=node_key, status="not read")
            return
        try:
            response = tt.get(service, resource)
        except CallCapReached as exc:
            capped = True
            errors.append(str(exc))
            writer.note_unread(endpoint, node_key=node_key, status="not read")
        except TTApiError as exc:
            errors.append(f"{endpoint} {node_key}: {exc}")
            writer.note_unread(endpoint, node_key=node_key, status="error")
        else:
            writer.save_raw(endpoint, raw_id, response.text, node_key=node_key)

    for account in scope:
        read("ttaccount", f"account/{account.id}", ACCOUNT_DETAIL, account.id, account.key)
        read("ttaccount", f"account/{account.id}/users", ACCOUNT_USERS, account.id, account.key)
    read("ttsetup", "ordertagdefaults/fields", OTD_FIELDS, "global", GLOBAL_KEY)
    read("ttpds", "markets", MARKETS, "global", GLOBAL_KEY)

    # 3. Order Tag Defaults, kept only for the client's accounts and the company default.
    reached_end = False
    if capped:
        writer.note_unread(OTD_LIST, node_key=GLOBAL_KEY, status="not read")
    else:
        reached_end = _pull_order_tag_defaults(
            tt, writer, scope, roots, otd_max_pages, errors, notes, page_counts
        )

    complete = not errors and reached_end
    return finish("complete" if complete else "partial", scope, roots)


def _pull_order_tag_defaults(
    tt: TTClient,
    writer: SnapshotWriter,
    scope: list[Account],
    roots: list[str],
    max_pages: int,
    errors: list[str],
    notes: list[str],
    page_counts: dict[str, int],
) -> bool:
    """Returns True when everything relevant was read to the end."""
    wanted = {account.id for account in scope} | {""}  # "" is account 0: the company default
    kept: list[dict[str, Any]] = []
    pages = entries_read = 0
    reached_end = True
    mode = "full_list"

    def collect(params: dict[str, str] | None) -> bool:
        nonlocal pages, entries_read
        at_end = False
        for page in tt.pages("ttsetup", "ordertagdefaults", params, max_pages=max_pages - pages):
            pages += 1
            entries = page.response.body.get("orderTagDefaults") or []
            entries_read += len(entries)
            kept.extend(e for e in entries if norm_id(e.get("accountId")) in wanted)
            at_end = page.reached_end
            if pages >= max_pages:
                break
        return at_end

    try:
        # Does TT honour ?accountId= ? If so, every entry returned is for that account.
        probe = tt.get("ttsetup", "ordertagdefaults", {"accountId": roots[0]})
        probe_entries = probe.body.get("orderTagDefaults") or []
        honoured = all(norm_id(e.get("accountId")) == roots[0] for e in probe_entries)
        if honoured:
            mode = "per_account"
            notes.append("Order Tag Defaults: TT honoured the accountId filter.")
            for account_id in ["0", *(a.id for a in scope)]:
                if pages >= max_pages:
                    reached_end = False
                    break
                reached_end = collect({"accountId": account_id}) and reached_end
        else:
            notes.append("Order Tag Defaults: TT ignored the accountId filter; read the full list.")
            reached_end = collect(None)
    except TTApiError as exc:
        reached_end = False
        errors.append(f"Order Tag Defaults: {exc}")

    if not reached_end:
        notes.append(
            "Order Tag Defaults were not read to the end; values not found show as unknown."
        )
    page_counts[OTD_LIST] = pages
    body = {
        "orderTagDefaults": kept,
        "_tool_summary": {
            "mode": mode,
            "pages": pages,
            "entries_read": entries_read,
            "kept": len(kept),
            "reached_end": reached_end,
        },
    }
    # Dumped compactly as text so 19-digit ids inside entries stay exactly as TT sent them.
    writer.save_raw(OTD_LIST, KEPT_RAW_ID, json.dumps(body), node_key=GLOBAL_KEY)
    return reached_end
