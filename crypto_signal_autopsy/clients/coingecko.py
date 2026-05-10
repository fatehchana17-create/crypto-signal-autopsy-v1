from __future__ import annotations

from typing import Any

from crypto_signal_autopsy.clients.http import JsonHttpClient
from crypto_signal_autopsy.config import Settings


class CoinGeckoClient:
    provider = "coingecko"

    def __init__(self, settings: Settings, http: JsonHttpClient):
        self.settings = settings
        self.http = http
        self.base_url = "https://api.coingecko.com/api/v3"
        self.headers: dict[str, str] = {}
        if settings.coingecko_pro_api_key:
            self.base_url = "https://pro-api.coingecko.com/api/v3"
            self.headers["x-cg-pro-api-key"] = settings.coingecko_pro_api_key
        elif settings.coingecko_demo_api_key:
            self.headers["x-cg-demo-api-key"] = settings.coingecko_demo_api_key

    def fetch_top_markets(self, limit: int = 100) -> list[dict[str, Any]]:
        target = max(int(limit), 1)
        url = f"{self.base_url}/coins/markets"
        markets: list[dict[str, Any]] = []
        per_page = min(target, 250)
        page = 1
        while len(markets) < target:
            params = {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": per_page,
                "page": page,
                "sparkline": "false",
                "price_change_percentage": "1h,24h",
                "precision": "full",
            }
            data = self.http.get_json(
                provider=self.provider,
                endpoint="/coins/markets",
                url=url,
                params=params,
                headers=self.headers or None,
            )
            if not isinstance(data, list):
                raise ValueError("CoinGecko /coins/markets returned non-list data")
            if not data:
                break
            markets.extend(item for item in data if isinstance(item, dict))
            if len(data) < per_page:
                break
            page += 1
        return markets[:target]
