"""The TT setup as read back from a snapshot: the tree plus the raw records the rules need.

Generic: knows TT field names and snapshot endpoint ids, but no client names or rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.hierarchy import AccountTree, parse_node_key
from core.normalize import NO_ID, is_on, norm_id, tt_bool
from core.snapshots import Snapshot

# Endpoint ids used in raw file names and in model.json's raw_index.
ACCOUNTS_LIST = "accounts_list"
ACCOUNT_DETAIL = "account_detail"
ACCOUNT_USERS = "account_users"
OTD_LIST = "ordertagdefaults_list"
OTD_FIELDS = "ordertagdefaults_fields"
MARKETS = "markets"

LIMIT_KINDS = ("productLimits", "contractLimits", "interProductLimits")


@dataclass
class SetupData:
    tree: AccountTree
    details: dict[str, dict[str, Any]] = field(default_factory=dict)  # account id -> record
    users: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # only where read
    otd_entries: list[dict[str, Any]] = field(default_factory=list)
    otd_reached_end: bool = False  # False: a value that isn't found is unknown, not missing
    otd_fields: dict[str, dict[str, Any]] = field(default_factory=dict)  # field id -> field
    markets: dict[str, str] = field(default_factory=dict)  # market id -> name
    snapshot_status: str = "complete"

    def detail(self, account_id: str) -> dict[str, Any] | None:
        return self.details.get(norm_id(account_id))

    def apply_limits(self, account_id: str) -> bool | None:
        """The Apply Limits checkbox. TT stores the reverse, ``noLimits``. None = not read."""
        record = self.detail(account_id)
        if record is None:
            return None
        no_limits = tt_bool((record.get("riskLimitSettings") or {}).get("noLimits"))
        return None if no_limits is None else not no_limits

    def apply_restrictions(self, account_id: str) -> bool | None:
        """The Apply Restrictions checkbox. None = not read."""
        record = self.detail(account_id)
        if record is None:
            return None
        return tt_bool((record.get("restrictions") or {}).get("applyRestrictions"))

    def limit_rows(self, account_id: str, kind: str | None = None) -> list[dict[str, Any]]:
        """Limit rows on the account; all three kinds unless one is named."""
        record = self.detail(account_id) or {}
        limits = record.get("riskLimits") or {}
        kinds = (kind,) if kind else LIMIT_KINDS
        return [row for k in kinds for row in (limits.get(k) or [])]

    def direct_users(self, account_id: str) -> list[dict[str, str]] | None:
        """Users assigned on this account itself, as ``{id, name}``. None = not read."""
        key = norm_id(account_id)
        if key in self.users:
            source = self.users[key]
        elif key in self.details and "users" in self.details[key]:
            source = self.details[key]["users"] or []
        else:
            return None
        found = []
        for user in source:
            user_id = norm_id(user.get("userId")) or norm_id(user.get("id"))
            if user_id != NO_ID:
                found.append({"id": user_id, "name": str(user.get("userName") or "")})
        return found

    def market_name(self, market_id: Any, known: dict[str, str] | None = None) -> str:
        key = norm_id(market_id)
        return (known or {}).get(key) or self.markets.get(key) or f"market {key or '?'}"


def _otd_file_reached_end(body: dict[str, Any]) -> bool:
    summary = body.get("_tool_summary")
    if isinstance(summary, dict):
        return bool(summary.get("reached_end"))
    return is_on(body.get("lastPage"))


def load_setup(snapshot: Snapshot) -> SetupData:
    """Rebuild the setup from a snapshot's raw files. Never calls TT."""
    accounts: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    users: dict[str, list[dict[str, Any]]] = {}
    otd_entries: list[dict[str, Any]] = []
    otd_files = 0
    otd_complete = True
    otd_fields: dict[str, dict[str, Any]] = {}
    markets: dict[str, str] = {}

    for entry in snapshot.raw_index:
        endpoint = entry["endpoint_id"]
        if not entry.get("file"):
            if endpoint == OTD_LIST:
                otd_complete = False
            continue
        body = snapshot.raw_file(entry["file"])

        if endpoint == ACCOUNTS_LIST:
            accounts.extend(body.get("accounts") or [])
        elif endpoint == ACCOUNT_DETAIL:
            for record in body.get("account") or []:
                details[norm_id(record.get("id"))] = record
        elif endpoint == ACCOUNT_USERS:
            _, account_id = parse_node_key(entry["node_key"])
            users[norm_id(body.get("accountId")) or account_id] = list(body.get("users") or [])
        elif endpoint == OTD_LIST:
            otd_files += 1
            otd_entries.extend(body.get("orderTagDefaults") or [])
            otd_complete = otd_complete and _otd_file_reached_end(body)
        elif endpoint == OTD_FIELDS:
            for item in body.get("fields") or body.get("orderTagDefaultFields") or []:
                otd_fields[norm_id(item.get("fieldId"))] = item
        elif endpoint == MARKETS:
            for item in body.get("markets") or []:
                markets[norm_id(item.get("id"))] = str(item.get("name") or "")

    return SetupData(
        tree=AccountTree(accounts),
        details=details,
        users=users,
        otd_entries=otd_entries,
        otd_reached_end=otd_files > 0 and otd_complete,
        otd_fields=otd_fields,
        markets=markets,
        snapshot_status=snapshot.status,
    )
