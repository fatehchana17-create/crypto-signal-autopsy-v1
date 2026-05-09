from __future__ import annotations

from typing import Any

from crypto_signal_autopsy.clients.http import JsonHttpClient


class GeckoTerminalClient:
    provider = "geckoterminal"
    base_url = "https://api.geckoterminal.com/api/v2"

    def __init__(self, http: JsonHttpClient):
        self.http = http

    def fetch_pool_ohlcv(
        self,
        network: str,
        pool_address: str,
        timeframe: str = "minute",
        aggregate: int = 1,
        limit: int = 100,
    ) -> list[list[Any]]:
        data = self.http.get_json(
            self.provider,
            "/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}",
            f"{self.base_url}/networks/{network}/pools/{pool_address}/ohlcv/{timeframe}",
            params={"aggregate": aggregate, "limit": limit},
        )
        attributes = (((data or {}).get("data") or {}).get("attributes") or {})
        ohlcv = attributes.get("ohlcv_list")
        return ohlcv if isinstance(ohlcv, list) else []
