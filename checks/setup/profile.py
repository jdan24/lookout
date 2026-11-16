"""Client profiles: which accounts a client lives under, its levels, and what each level expects.

A profile is a TOML file (see ``docs/example_client.toml``). Real profiles hold client
data, so they live outside the repo, in the folder from ``config.toml`` ``[clients]``.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from core.hierarchy import Account
from core.normalize import norm_id

OnOff = Literal["on", "off", "any"]
UsersRule = Literal["here", "inherited", "any"]

# The key fields of an Order Tag Default entry that apply to "any" (TT's defaults).
DEFAULT_OTD_KEY: dict[str, str] = {
    "profileId": "0",
    "userId": "0",
    "algoTypeId": "11",
    "exchangeProductGroupId": "0",
    "productTypeId": "0",
    "productId": "0",
}

_SAFE_NAME = r"^[A-Za-z0-9][A-Za-z0-9_.-]*$"


class ProfileError(Exception):
    """A profile file could not be read or is not valid."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _regex(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"not a valid regular expression: {exc}") from exc
    return value


def _ids(values: Iterable[Any]) -> list[str]:
    return [norm_id(v) for v in values]


class Level(_Model):
    depth: int = Field(ge=0)
    label: str = Field(min_length=1)
    # Fit: an account at this depth belongs to this level only if these match.
    account_type: int | None = None
    match_regex: str | None = None
    # Rules for accounts at this level.
    name_regex: str | None = None
    name_hint: str = ""
    apply_limits: OnOff = "any"
    limit_rows_required: bool = False
    apply_restrictions: OnOff = "any"
    users: UsersRule = "any"  # no longer used; accepted so older profiles still load
    may_be_empty: bool = False

    _check = field_validator("match_regex", "name_regex")(_regex)

    def fits(self, account: Account) -> bool:
        if self.account_type is not None and account.account_type != self.account_type:
            return False
        return self.match_regex is None or re.search(self.match_regex, account.name) is not None


class OrderTagDefaultRule(_Model):
    label: str = Field(min_length=1)
    level: str
    field_id: str | None = None  # the same field on every market in ``markets``
    markets: list[str] = Field(default_factory=list)
    fields: dict[str, str] = Field(default_factory=dict)  # market id -> field id
    value: str | None = None
    value_from_name: str | None = None  # regex with a named group "base"
    set_on: Literal["self", "inherited_ok"] = "inherited_ok"
    only_names: str | None = None  # regex: only accounts whose name matches
    except_names: str | None = None  # regex: skip accounts whose name matches

    _check = field_validator("only_names", "except_names")(_regex)

    @field_validator("markets", mode="before")
    @classmethod
    def _market_ids(cls, value: Any) -> Any:
        return _ids(value) if isinstance(value, list) else value

    @field_validator("value_from_name")
    @classmethod
    def _has_base_group(cls, value: str | None) -> str | None:
        if value is not None and "base" not in re.compile(_regex(value) or "").groupindex:
            raise ValueError("value_from_name needs a named group called 'base', like (?P<base>…)")
        return value

    @model_validator(mode="after")
    def _shape(self) -> OrderTagDefaultRule:
        if (self.value is None) == (self.value_from_name is None):
            raise ValueError("give exactly one of value or value_from_name")
        if self.fields and (self.field_id or self.markets):
            raise ValueError("give either fields, or field_id with markets, not both")
        if not self.fields and not (self.field_id and self.markets):
            raise ValueError("give fields, or field_id with markets")
        return self

    def field_by_market(self) -> dict[str, str]:
        if self.fields:
            return {norm_id(m): norm_id(f) for m, f in self.fields.items()}
        return {market: norm_id(self.field_id) for market in self.markets}

    def applies_to(self, name: str) -> bool:
        if self.only_names is not None and not re.search(self.only_names, name):
            return False
        return self.except_names is None or not re.search(self.except_names, name)


class Tree(_Model):
    name: str = Field(pattern=_SAFE_NAME)
    label: str
    path: list[str] = Field(min_length=1)  # top-level account down to the start account
    start_id: str | None = None
    id_verified: bool = True
    markets: dict[str, str] = Field(default_factory=dict)  # market id -> exchange name
    # User ids every lowest-level account must have, on itself or on an account above it.
    required_users: list[str] = Field(default_factory=list)
    levels: list[Level] = Field(min_length=1)
    order_tag_defaults: list[OrderTagDefaultRule] = Field(default_factory=list)

    @field_validator("markets")
    @classmethod
    def _market_ids(cls, value: dict[str, str]) -> dict[str, str]:
        return {norm_id(k): v for k, v in value.items()}

    @field_validator("required_users", mode="before")
    @classmethod
    def _user_ids(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        ids = _ids(value)
        if "" in ids or len(set(ids)) != len(ids):
            raise ValueError("required_users must be different, non-zero user ids")
        return ids

    @model_validator(mode="after")
    def _levels(self) -> Tree:
        if not any(level.depth == 0 for level in self.levels):
            raise ValueError("needs a level with depth = 0 (the start account)")
        labels = {level.label for level in self.levels}
        for rule in self.order_tag_defaults:
            if rule.level not in labels:
                raise ValueError(
                    f"order_tag_defaults {rule.label!r} names level {rule.level!r}, "
                    f"which is not one of {sorted(labels)}"
                )
        return self

    @property
    def start_name(self) -> str:
        return self.path[-1]

    @property
    def max_depth(self) -> int:
        return max(level.depth for level in self.levels)

    def levels_at(self, depth: int) -> list[Level]:
        return [level for level in self.levels if level.depth == min(depth, self.max_depth)]

    def level_labels(self, setting: str, want: str) -> list[str]:
        """Labels of the levels where ``setting`` (a Level attribute) equals ``want``."""
        return [level.label for level in self.levels if getattr(level, setting) == want]


class CompanionAccountRule(_Model):
    label: str = "Companion account"
    level: str
    market: str
    products: list[str] = Field(min_length=1)
    suffix: str = Field(min_length=1)
    unnecessary: Literal["amber", "red"] = "amber"

    @field_validator("market", mode="before")
    @classmethod
    def _market_id(cls, value: Any) -> str:
        return norm_id(value)


class LegacyGroupsRule(_Model):
    name_regex: str

    _check = field_validator("name_regex")(_regex)


class OptionalRules(_Model):
    companion_account: CompanionAccountRule | None = None
    legacy_groups: LegacyGroupsRule | None = None


class Profile(_Model):
    client: str = Field(pattern=_SAFE_NAME)
    label: str = ""
    trees: list[Tree] = Field(min_length=1)
    display_only_fields: list[str] = Field(default_factory=list)
    otd_default_key: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_OTD_KEY))
    rules: OptionalRules = OptionalRules()

    @model_validator(mode="after")
    def _unique(self) -> Profile:
        names = [tree.name for tree in self.trees]
        if len(set(names)) != len(names):
            raise ValueError(f"tree names must be different: {names}")
        seen: dict[str, str] = {}
        for tree in self.trees:
            for market in tree.markets:
                if market in seen:
                    raise ValueError(f"market {market} is listed on {seen[market]} and {tree.name}")
                seen[market] = tree.name
        return self

    @property
    def title(self) -> str:
        return self.label or self.client

    def tree(self, name: str) -> Tree:
        return next(tree for tree in self.trees if tree.name == name)

    def tree_of_market(self, market_id: str) -> Tree | None:
        return next((t for t in self.trees if norm_id(market_id) in t.markets), None)

    def market_names(self) -> dict[str, str]:
        return {m: n for tree in self.trees for m, n in tree.markets.items()}


# --- loading -----------------------------------------------------------------------


def _describe(exc: ValidationError) -> str:
    lines = []
    for error in exc.errors():
        where = ".".join(str(part) for part in error["loc"]) or "(top)"
        lines.append(f"{where}: {error['msg']}")
    return "; ".join(lines)


def parse_profile(data: dict[str, Any], source: str = "profile") -> Profile:
    try:
        return Profile.model_validate(data)
    except ValidationError as exc:
        raise ProfileError(f"{source}: {_describe(exc)}") from exc


def load_profile(path: Path) -> Profile:
    try:
        with Path(path).open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ProfileError(f"No profile file at {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"{Path(path).name}: not valid TOML ({exc})") from exc
    return parse_profile(data, Path(path).name)


@dataclass(frozen=True)
class ProfileFile:
    path: Path
    profile: Profile | None
    error: str = ""


def list_profiles(directory: Path) -> list[ProfileFile]:
    """Every ``*.toml`` in the folder, loaded; broken files are kept with their error."""
    folder = Path(directory)
    if not folder.is_dir():
        return []
    found: list[ProfileFile] = []
    for path in sorted(folder.glob("*.toml")):
        try:
            found.append(ProfileFile(path, load_profile(path)))
        except ProfileError as exc:
            found.append(ProfileFile(path, None, str(exc)))
    return found
