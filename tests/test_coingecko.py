from __future__ import annotations

from typing import Any

from crypto_signal_autopsy.clients.coingecko import CoinGeckoClient
from crypto_signal_autopsy.config import load_settings


class FakeHttp:
    def __init__(self) -> None:
        self.pages: list[int] = []

    def get_json(
        self,
        provider: str,
        endpoint: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        params = params or {}
        page = int(params["page"])
        per_page = int(params["per_page"])
        self.pages.append(page)
        start = (page - 1) * per_page
        return [{"id": f"coin-{index}"} for index in range(start, start + per_page)]


def test_fetch_top_markets_paginates_past_250() -> None:
    http = FakeHttp()
    client = CoinGeckoClient(load_settings(), http)  # type: ignore[arg-type]

    markets = client.fetch_top_markets(300)

    assert len(markets) == 300
    assert http.pages == [1, 2]
    assert markets[-1]["id"] == "coin-299"
