"""A fake TT server for tests, serving the synthetic setup through httpx.MockTransport.

It records every request, so tests can assert exactly what was asked of "TT".
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx

from tests.fixtures import synthetic

ENV = "ext_prod_live"
SECRET = "fakekey:fakesecretvalue"
TOKEN = "fake-access-token"
# TT's rule for the requestId query parameter: <app>-<company>--<guid>, no spaces.
REQUEST_ID = re.compile(r"^[A-Za-z0-9]+-[A-Za-z0-9]+--[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


class FakeTT:
    def __init__(
        self,
        *,
        honour_otd_filter: bool = False,
        otd_page_size: int = 8,
        token_lifetime: int = 3600,
        environment: str = ENV,
    ) -> None:
        self.environment = environment  # like TT, it only answers for its own environment
        self.honour_otd_filter = honour_otd_filter
        self.otd_page_size = otd_page_size
        self.token_lifetime = token_lifetime
        self.requests: list[httpx.Request] = []
        self.token_requests = 0
        # path -> (status code, body): a canned reply, used to simulate failures
        self.fail: dict[str, tuple[int, Any]] = {}
        self.hook: Callable[[httpx.Request], httpx.Response | None] | None = None
        # What the Clone page's writes changed (UAT only, like the real write guard).
        self.clones: list[tuple[dict[str, str], dict[str, Any]]] = []  # (query, body)
        self.otd_writes: list[tuple[str, Any]] = []  # (path, body)
        self.new_otds: list[dict[str, Any]] = []
        self.new_accounts: list[dict[str, Any]] = []  # listed by GET accounts once cloned
        self.clone_reply_has_id = True  # False: answer like the first real UAT clone did
        self._next_account_id = 5000
        self._next_otd_id = 9000
        self.transport = httpx.MockTransport(self._handle)

    # --- helpers for assertions -------------------------------------------------

    def paths(self, method: str = "GET") -> list[str]:
        return [r.url.path for r in self.requests if r.method == method]

    def data_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "GET"]

    # --- server -----------------------------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.hook is not None:
            custom = self.hook(request)
            if custom is not None:
                return custom
        path = request.url.path
        if path in self.fail:
            code, body = self.fail[path]
            return httpx.Response(code, json=body)

        if request.method == "POST" and path == f"/ttid/{self.environment}/token":
            return self._token(request)
        if request.method not in ("GET", "POST"):
            return httpx.Response(405, json={"status": "Error"})
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"status": "Unauthorized"})
        if request.headers.get("x-api-key") != SECRET.split(":")[0]:
            return httpx.Response(403, json={"status": "Forbidden"})

        params = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        request_id = params.get("requestId", "")
        if not REQUEST_ID.match(request_id):
            message = (
                f"requestId query parameter [{request_id}] is in incorrect form. The value "
                "must be in the given format: <app_name><company_name>--<new_guid>."
            )
            return httpx.Response(400, json={"status": "StatusFail", "status_message": message})
        parts = path.strip("/").split("/")
        if len(parts) < 3 or parts[1] != self.environment:
            return httpx.Response(404, json={"status": "Not Found"})
        service, resource = parts[0], parts[2:]
        if request.method == "POST":
            return self._write(service, resource, params, json.loads(request.content or b"null"))

        if service == "ttaccount" and resource == ["accounts"]:
            return self._accounts(params)
        if service == "ttaccount" and len(resource) == 2 and resource[0] == "account":
            return self._account(int(resource[1]))
        if service == "ttaccount" and len(resource) == 3 and resource[2] == "users":
            return httpx.Response(200, json=synthetic.account_users(int(resource[1])))
        if service == "ttsetup" and resource == ["ordertagdefaults"]:
            return self._order_tag_defaults(params)
        if service == "ttsetup" and resource == ["ordertagdefaults", "fields"]:
            return httpx.Response(200, json={"fields": synthetic.OTD_FIELDS, "status": "Ok"})
        if service == "ttpds" and resource == ["markets"]:
            return httpx.Response(200, json={"markets": synthetic.MARKETS, "status": "Ok"})
        return httpx.Response(404, json={"status": "Not Found"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.token_requests += 1
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        key_ok = request.headers.get("x-api-key") == SECRET.split(":")[0]
        if not key_ok or form != {"grant_type": "user_app", "app_key": SECRET}:
            return httpx.Response(400, json={"status": "Bad Request", "echo": form})
        return httpx.Response(
            200,
            json={
                "access_token": TOKEN,
                "token_type": "bearer",
                "seconds_until_expiry": self.token_lifetime,
                "status": "Ok",
            },
        )

    def _accounts(self, params: dict[str, str]) -> httpx.Response:
        pages = synthetic.accounts_list_pages()
        key = params.get("nextPageKey")
        index = 0 if not key else int(key.removeprefix("key")) - 1
        page = pages[index]
        if index == len(pages) - 1 and self.new_accounts:
            page = {**page, "accounts": [*page["accounts"], *self.new_accounts]}
        return httpx.Response(200, json=page)

    def _account(self, account_id: int) -> httpx.Response:
        for made in self.new_accounts:
            if made["id"] == account_id:
                return httpx.Response(200, json={"account": [made], "status": "Ok"})
        if account_id not in synthetic.SETTINGS:
            return httpx.Response(404, json={"status": "Not Found"})
        body = synthetic.account_detail(account_id)
        # Keep 19-digit ids exactly as text, the way TT sends them.
        return httpx.Response(
            200, content=json.dumps(body), headers={"Content-Type": "application/json"}
        )

    def _write(
        self, service: str, resource: list[str], params: dict[str, str], body: Any
    ) -> httpx.Response:
        if service == "ttaccount" and resource == ["account", "clone"]:
            return self._clone(params, body)
        if service == "ttsetup" and resource == ["ordertagdefaults"]:
            self.otd_writes.append(("create", body))
            made = []
            for entry in body:
                self._next_otd_id += 1
                made.append({**entry, "orderTagDefaultId": self._next_otd_id, "revision": 1})
            self.new_otds.extend(made)
            return httpx.Response(200, json={"orderTagDefaults": made, "status": "Ok"})
        if service == "ttsetup" and len(resource) == 2 and resource[0] == "ordertagdefaults":
            self.otd_writes.append((resource[1], body))
            for entry in self.new_otds:
                if str(entry["orderTagDefaultId"]) == resource[1]:
                    entry.update(body)
                    return httpx.Response(200, json={"orderTagDefaults": [entry], "status": "Ok"})
            return httpx.Response(400, json={"status": "Failed", "status_message": "no such id"})
        return httpx.Response(404, json={"status": "Not Found"})

    def _clone(self, params: dict[str, str], body: dict[str, Any]) -> httpx.Response:
        self.clones.append((params, body))
        self._next_account_id += 1
        new_id = self._next_account_id
        if params.get("orderTagDefaults") == "true":
            source = body["sourceAccountId"]
            for entry in [*synthetic.OTD_ENTRIES, *self.new_otds]:
                if entry["accountId"] == source:
                    self._next_otd_id += 1
                    copied = {**entry, "accountId": new_id, "orderTagDefaultId": self._next_otd_id}
                    self.new_otds.append(copied)
        account = {
            "id": new_id,
            "name": body["accountName"],
            "parentAccountId": body.get("parentId", 0),
            "accountType": 1,
            "revision": 1,
        }
        self.new_accounts.append(account)
        if not self.clone_reply_has_id:
            return httpx.Response(200, json={"status": "Ok", "statusCode": 200})
        return httpx.Response(200, json={"account": [account], "status": "Ok"})

    def _order_tag_defaults(self, params: dict[str, str]) -> httpx.Response:
        entries = sorted(
            [*synthetic.OTD_ENTRIES, *self.new_otds], key=lambda e: e["orderTagDefaultId"]
        )
        if self.honour_otd_filter and "accountId" in params:
            entries = [e for e in entries if str(e["accountId"]) == params["accountId"]]
        start = int(params.get("nextPageKey", "0"))
        chunk = entries[start : start + self.otd_page_size]
        end = start + self.otd_page_size
        last = end >= len(entries)
        return httpx.Response(
            200,
            json={
                "orderTagDefaults": chunk,
                "lastPage": "true" if last else "false",
                "nextPageKey": "" if last else str(end),
                "requestVersion": "1",
                "status": "Ok",
            },
        )
