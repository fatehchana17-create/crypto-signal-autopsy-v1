from __future__ import annotations

from datetime import datetime, timezone

from crypto_signal_autopsy.normalize import normalize_pair
from crypto_signal_autopsy.timeutils import iso_utc


def test_normalize_pair_extracts_metrics() -> None:
    now = datetime(2026, 5, 7, 12, tzinfo=timezone.utc)
    pair = {
        "chainId": "base",
        "dexId": "uniswap",
        "pairAddress": "0xpair",
        "baseToken": {"address": "0xabc", "symbol": "ABC", "name": "ABC Token"},
        "quoteToken": {"symbol": "WETH"},
        "priceUsd": "1.25",
        "liquidity": {"usd": 800000},
        "volume": {"h1": 50000, "h6": 120000, "h24": 250000},
        "priceChange": {"h1": 5, "h24": 9},
        "txns": {"h1": {"buys": 20, "sells": 7}},
        "pairCreatedAt": 1778140800000,
    }
    source = {"token_address": "0xabc", "source_tags": ["boost_latest"], "boost_total_amount": 100}
    metrics = normalize_pair(pair, iso_utc(now), source, now)
    assert metrics is not None
    assert metrics["price_usd"] == 1.25
    assert metrics["liquidity_usd"] == 800000
    assert metrics["base_symbol"] == "ABC"
    assert metrics["source_tags"] == ["boost_latest"]
