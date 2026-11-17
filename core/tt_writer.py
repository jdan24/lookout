"""TT client for the Clone page's writes. UAT only.

It shares the token, pacing, call cap, request id and status checks of
``core.tt_client.TTClient``, but every request goes through ``core.write_http.WriteClient``,
which only allows the Clone page's few UAT calls. It cannot read: reads use a normal
``TTClient``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from core.credentials import TTCredentials
from core.tt_client import TTApiError, TTClient, TTResponse
from core.write_http import WriteClient


class TTWriter(TTClient):
    def __init__(
        self,
        http: WriteClient,
        credentials: TTCredentials | Callable[[], TTCredentials],
        **options: Any,
    ) -> None:
        if not isinstance(http, WriteClient):
            raise TypeError("TTWriter needs a WriteClient.")
        super().__init__(http, credentials, **options)  # type: ignore[arg-type]
        self._writer = http

    def get(self, service: str, resource: str, params: Mapping[str, Any] | None = None) -> Any:
        raise TTApiError("The write client cannot read; use a TTClient for reads.")

    def post(
        self,
        service: str,
        resource: str,
        body: Any,
        params: Mapping[str, Any] | None = None,
    ) -> TTResponse:
        """POST ``body`` as JSON to ``/<service>/<environment>/<resource>``."""
        path = self.path_for(service, resource)
        return self._call(
            "POST",
            path,
            lambda headers, query: self._writer.post(
                path, json=body, params=query, headers=headers
            ),
            params,
        )
