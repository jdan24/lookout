"""A synthetic TT setup for tests. Every name, id and value here is invented.

It follows TT's response shapes and contains one
deliberate fault for each rule, so each check has something to find.

    TOPBANK (10) > DESK (11) > CLI (100)                 member-side client group
        CLI-ALPHA (200)   a correct pod
            ABCD1000ALP (300), ABCD1000ANX (301)
        CLI-BETA (210)    a pod with faults
            ABCD2000BET (310), BETA-ACCT (311), ABCD2001BNX (312)
        CLI-GAMMA (220)   Endex limit but no NX account
            ABCD3000GAM (320)
        CLI-EMPTY (230)   new, empty pod group
        LONE4000AAA (240) standalone account, no pod group
      CLI-OLDPOD (900)    legacy group outside the client group (under DESK)
    CBTOP (20) > CB-CLI (500)                            non-member client group
        CB-CLI-ALPHA (600)  a correct pod
            CB-ABCD1000ALP (700)
        CB-CLI-BETA (610)   a pod with faults
            CB-ABCD2000BET (710), ABCD2002BET (711)
    OTHERCO (50) > OTHER-ACCT (51)                       unrelated client
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from checks.setup.profile import Profile, load_profile
from core.snapshots import Snapshot, SnapshotWriter

COMPANY_ID = 999
BIG_FAMILY_ID = "7193248756120394857"

EXAMPLE_PROFILE = Path(__file__).resolve().parents[2] / "docs" / "example_client.toml"


def profile(client: str = "synthetic") -> Profile:
    """``docs/example_client.toml``, which describes this setup, saved under ``client``."""
    return load_profile(EXAMPLE_PROFILE).model_copy(update={"client": client})


def _list_item(id_: int, name: str, parent: int, type_: int) -> dict[str, Any]:
    return {
        "id": id_,
        "name": name,
        "parentId": parent,
        "parentAccountId": parent,
        "accountType": type_,
        "companyId": COMPANY_ID,
        "revision": 1,
    }


# (id, name, parent, accountType)
ACCOUNTS: list[tuple[int, str, int, int]] = [
    (10, "TOPBANK", 0, 3),
    (11, "DESK", 10, 3),
    (100, "CLI", 11, 3),
    (200, "CLI-ALPHA", 100, 3),
    (300, "ABCD1000ALP", 200, 1),
    (301, "ABCD1000ANX", 200, 1),
    (210, "CLI-BETA", 100, 3),
    (310, "ABCD2000BET", 210, 1),
    (311, "BETA-ACCT", 210, 1),
    (312, "ABCD2001BNX", 210, 1),
    (220, "CLI-GAMMA", 100, 3),
    (320, "ABCD3000GAM", 220, 1),
    (230, "CLI-EMPTY", 100, 3),
    (240, "LONE4000AAA", 100, 1),
    (900, "CLI-OLDPOD", 11, 3),
    (20, "CBTOP", 0, 3),
    (500, "CB-CLI", 20, 3),
    (600, "CB-CLI-ALPHA", 500, 3),
    (700, "CB-ABCD1000ALP", 600, 4),
    (610, "CB-CLI-BETA", 500, 3),
    (710, "CB-ABCD2000BET", 610, 4),
    (711, "ABCD2002BET", 610, 4),
    (50, "OTHERCO", 0, 3),
    (51, "OTHER-ACCT", 50, 1),
]

IN_SCOPE_ROOTS = (100, 500)


def limit(market: int, symbol: str, max_pos: int = 100) -> dict[str, Any]:
    return {
        "marketId": market,
        "productFamilyId": BIG_FAMILY_ID,
        "productTypeId": 34,
        "productId": "0",
        "productSymbol": symbol,
        "maxPos": max_pos,
        "maxLongPos": max_pos,
        "maxShortPos": max_pos,
        "outright_maxOrderQty": 10,
        "spread_maxOrderQty": 20,
        "outright_tradingAllowed": 1,
        "spread_tradingAllowed": 1,
        "tradeOutAllowed": 1,
    }


# account id -> (Apply Limits ticked, Apply Restrictions ticked, product limit rows)
SETTINGS: dict[int, tuple[bool, bool, list[dict[str, Any]]]] = {
    100: (False, False, []),
    200: (True, True, [limit(7, "ZN"), limit(2, "FGBL"), limit(32, "TFM")]),
    300: (False, False, []),
    301: (False, False, []),
    # faults: Apply Limits off; a non-member market (24); an unmapped market (216)
    210: (False, True, [limit(7, "ZN"), limit(24, "XT"), limit(216, "QQ"), limit(32, "BRN")]),
    310: (True, True, []),  # faults: both boxes ticked on a trading account
    311: (False, False, []),  # fault: name
    312: (False, False, []),  # NX account with no Endex limit on the pod
    220: (True, True, [limit(32, "TFM")]),  # fault: Endex limit, no NX account
    320: (False, False, []),
    230: (False, False, []),  # new empty pod
    240: (False, False, []),  # standalone
    500: (False, True, []),
    600: (True, False, [limit(24, "XT"), limit(64, "NK")]),
    700: (False, False, []),
    # faults: Apply Restrictions ticked below CB-CLI; a member market (7)
    610: (True, True, [limit(24, "XT"), limit(7, "ZN")]),
    710: (False, False, []),
    711: (False, False, []),  # fault: missing CB- prefix
}

USER_ANN = {"userId": 9001, "userName": "ann.trader", "orderPermissions": {}}
USER_BOB = {"userId": 9002, "userName": "bob.trader", "orderPermissions": {}}
USER_CAT = {"userId": 9003, "userName": "cat.trader", "orderPermissions": {}}

# account id -> users assigned directly on it (accounts not listed have none)
USERS: dict[int, list[dict[str, Any]]] = {
    100: [USER_ANN, USER_BOB],
    210: [USER_BOB, USER_CAT],  # faults: Bob duplicates the client group; Cat is pod-only
    500: [USER_ANN],
}


def otd(
    entry_id: int,
    account: int,
    market: int,
    field: int,
    value: str,
    **overrides: Any,
) -> dict[str, Any]:
    entry = {
        "orderTagDefaultId": entry_id,
        "accountId": account,
        "marketId": market,
        "fieldId": field,
        "value": value,
        "valueEnumId": 0,
        "profileId": 0,
        "userId": 0,
        "algoTypeId": 11,
        "exchangeProductGroupId": 0,
        "productTypeId": 0,
        "productId": "0",
        "assetClassId": 0,
        "applyToAllSubAccounts": "1",
        "clientCanOverride": "0",
        "revision": 1,
        "companyId": COMPANY_ID,
    }
    entry.update(overrides)
    return entry


CLEARING = 1859

OTD_ENTRIES: list[dict[str, Any]] = [
    # company defaults (account 0): per-user values, shown not judged
    otd(1, 0, 7, 501, "OPER1", userId=9001),
    # client group: ICE / ICE LIFFE exchange account and clearing firm, inherited by pods
    otd(10, 100, 32, 25, "EXCHCLIENT"),
    otd(11, 100, 92, 26, "EXCHCLIENT"),
    otd(12, 100, 32, 27, "55555"),
    otd(13, 100, 92, 28, "55555"),
    # ABCD1000ALP: clearing account on the account itself; CFE (93) has the wrong value
    otd(20, 300, 7, CLEARING, "ABCD1000"),
    otd(21, 300, 2, CLEARING, "ABCD1000"),
    otd(22, 300, 32, CLEARING, "ABCD1000"),
    otd(23, 300, 92, CLEARING, "ABCD1000"),
    otd(24, 300, 93, CLEARING, "ABCD9999"),
    otd(25, 300, 7, CLEARING, "IGNORED", userId=9001),  # not a default entry
    # ABCD1000ANX: Endex exchange account set on the NX account
    otd(30, 301, 32, 25, "EXCHENDEX"),
    # CLI-BETA pod: clearing account for CME set on the pod, not on the trading account
    otd(40, 210, 7, CLEARING, "ABCD2000"),
    # ABCD3000GAM: clearing account correct everywhere; ICE clearing firm overridden wrongly
    otd(50, 320, 7, CLEARING, "ABCD3000"),
    otd(51, 320, 2, CLEARING, "ABCD3000"),
    otd(52, 320, 32, CLEARING, "ABCD3000"),
    otd(53, 320, 92, CLEARING, "ABCD3000"),
    otd(54, 320, 93, CLEARING, "ABCD3000"),
    otd(55, 320, 32, 27, "99999"),
    # an unrelated client's entry, which a scoped read must not keep
    otd(90, 51, 7, CLEARING, "OTHER"),
]

OTD_FIELDS = [
    {"fieldId": 1859, "name": "Clearing Account", "marketId": 0, "valueTypeId": 1},
    {"fieldId": 25, "name": "Exchange Account", "marketId": 32, "valueTypeId": 1},
    {"fieldId": 26, "name": "Exchange Account", "marketId": 92, "valueTypeId": 1},
    {"fieldId": 27, "name": "Clearing Firm ID", "marketId": 32, "valueTypeId": 1},
    {"fieldId": 28, "name": "Clearing Firm ID", "marketId": 92, "valueTypeId": 1},
    {"fieldId": 501, "name": "Operator ID", "marketId": 7, "valueTypeId": 1},
]

MARKETS = [
    {"id": 7, "name": "CME"},
    {"id": 2, "name": "Eurex"},
    {"id": 32, "name": "ICE"},
    {"id": 92, "name": "ICE_L"},
    {"id": 93, "name": "CFE"},
    {"id": 24, "name": "ASX"},
    {"id": 64, "name": "SGX"},
]


# --- TT response bodies ----------------------------------------------------------


def accounts_list_pages(page_size: int = 10) -> list[dict[str, Any]]:
    items = [_list_item(*row) for row in ACCOUNTS]
    chunks = [items[i : i + page_size] for i in range(0, len(items), page_size)]
    pages = []
    for number, chunk in enumerate(chunks, start=1):
        last = number == len(chunks)
        pages.append(
            {
                "accounts": chunk,
                "lastPage": "true" if last else "false",
                "nextPageKey": "" if last else f"key{number + 1}",
                "requestVersion": "1",
                "status": "Ok",
            }
        )
    return pages


def in_scope_ids() -> list[int]:
    parents = {row[0]: row[2] for row in ACCOUNTS}

    def under_root(account_id: int) -> bool:
        current: int | None = account_id
        while current:
            if current in IN_SCOPE_ROOTS:
                return True
            current = parents.get(current)
        return False

    return [row[0] for row in ACCOUNTS if under_root(row[0])]


def account_detail(account_id: int) -> dict[str, Any]:
    id_, name, parent, type_ = next(row for row in ACCOUNTS if row[0] == account_id)
    apply_limits, apply_restrictions, limits = SETTINGS[account_id]
    record = {
        "id": id_,
        "name": name,
        "description": f"Synthetic account {name}",
        "accountType": type_,
        "brokerName": "SYNTH",
        "parentAccountId": parent,
        "revision": 1,
        "permissions": {"tradingAllowedSelf": 1, "tradingAllowedSubAccts": 1},
        "restrictions": {"applyRestrictions": int(apply_restrictions), "adl": 0, "blockOrders": 0},
        "effectiveRestrictions": {"adl": 0, "blockOrders": 0},
        "riskLimitSettings": {
            "id": id_,
            "noLimits": int(not apply_limits),  # inverted, as TT sends it
            "applyLimitsToWholesaleOrders": 0,
            "revision": 1,
        },
        "riskLimits": {"productLimits": limits, "contractLimits": [], "interProductLimits": []},
        "riskSettings": {"dailyCreditLimit": 0, "checkCredit": 0},
        "users": [],
    }
    return {"account": [record], "status": "Ok"}


def account_users(account_id: int) -> dict[str, Any]:
    return {"accountId": account_id, "users": USERS.get(account_id, []), "status": "Ok"}


def kept_order_tag_defaults(reached_end: bool = True) -> dict[str, Any]:
    scope = {0, *in_scope_ids()}
    kept = [entry for entry in OTD_ENTRIES if entry["accountId"] in scope]
    return {
        "orderTagDefaults": kept,
        "_tool_summary": {
            "mode": "full_list",
            "pages": 1,
            "entries_read": len(OTD_ENTRIES),
            "kept": len(kept),
            "reached_end": reached_end,
        },
    }


# --- snapshot --------------------------------------------------------------------


def build_snapshot(
    base_dir: Path,
    *,
    client: str = "synthetic",
    otd_reached_end: bool = True,
    skip_details: tuple[int, ...] = (),
    skip_users: tuple[int, ...] = (),
    when: datetime = datetime(2026, 11, 13, 9, 30, 0),
) -> Snapshot:
    """Write the synthetic setup as a snapshot, optionally with parts left unread."""
    writer = SnapshotWriter(base_dir, client, now=when)
    for number, page in enumerate(accounts_list_pages(), start=1):
        writer.save_raw("accounts_list", f"page{number}", page, node_key="global")
    for account_id in in_scope_ids():
        key = f"acct:{account_id}"
        if account_id in skip_details:
            writer.note_unread("account_detail", node_key=key)
        else:
            writer.save_raw(
                "account_detail", str(account_id), account_detail(account_id), node_key=key
            )
        if account_id in skip_users:
            writer.note_unread("account_users", node_key=key)
        else:
            writer.save_raw(
                "account_users", str(account_id), account_users(account_id), node_key=key
            )
    writer.save_raw(
        "ordertagdefaults_list",
        "global_kept",
        kept_order_tag_defaults(otd_reached_end),
        node_key="global",
    )
    writer.save_raw("ordertagdefaults_fields", "global", {"fields": OTD_FIELDS}, node_key="global")
    writer.save_raw("markets", "global", {"markets": MARKETS}, node_key="global")

    partial = bool(skip_details or skip_users or not otd_reached_end)
    return writer.finish(
        status="partial" if partial else "complete",
        roots=[f"acct:{root}" for root in IN_SCOPE_ROOTS],
        nodes=[{"key": f"acct:{account_id}"} for account_id in in_scope_ids()],
        notes=["synthetic fixture"],
        scope={"roots": [str(root) for root in IN_SCOPE_ROOTS]},
        page_counts={"accounts_list": len(accounts_list_pages())},
    )
