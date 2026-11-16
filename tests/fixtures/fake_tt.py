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
        if request.method != "GET":
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
        return httpx.Response(200, json=pages[index])

    def _account(self, account_id: int) -> httpx.Response:
        if account_id not in synthetic.SETTINGS:
            return httpx.Response(404, json={"status": "Not Found"})
        body = synthetic.account_detail(account_id)
        # Keep 19-digit ids exactly as text, the way TT sends them.
        return httpx.Response(
            200, content=json.dumps(body), headers={"Content-Type": "application/json"}
        )

    def _order_tag_defaults(self, params: dict[str, str]) -> httpx.Response:
        entries = sorted(synthetic.OTD_ENTRIES, key=lambda e: e["orderTagDefaultId"])
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
