from __future__ import annotations

from typing import Any

from crypto_signal_autopsy.clients.dexscreener import DexScreenerClient


class FakeHttp:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def get_json(
        self,
        provider: str,
        endpoint: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.urls.append(url)
        addresses = url.rsplit("/", 1)[-1].split(",")
        return {
            "pairs": [
                {"chainId": "base", "pairAddress": address, "priceUsd": "1"}
                for address in addresses
            ]
        }


def test_fetch_pairs_by_addresses_batches_and_maps_pairs() -> None:
    http = FakeHttp()
    client = DexScreenerClient(http)  # type: ignore[arg-type]
    addresses = [f"0xpair{index:02d}" for index in range(31)]

    pairs = client.fetch_pairs_by_addresses("base", addresses)

    assert len(http.urls) == 2
    assert len(pairs) == 31
    assert pairs["0xpair00"]["pairAddress"] == "0xpair00"
    assert pairs["0xpair30"]["pairAddress"] == "0xpair30"
