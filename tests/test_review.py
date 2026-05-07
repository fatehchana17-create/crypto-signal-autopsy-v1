from __future__ import annotations

from crypto_signal_autopsy.config import load_settings
from crypto_signal_autopsy.db import to_json
from crypto_signal_autopsy.review import build_rejection_record, build_requirement_rows


def test_rejection_record_shows_gaps_and_link() -> None:
    settings = load_settings()
    row = {
        "id": 1,
        "observed_at": "2026-05-07T12:00:00+00:00",
        "chain_id": "base",
        "token_address": "0x1234567890abcdef",
        "pair_address": "0xabcdef1234567890",
        "accepted": 0,
        "rejection_reasons": to_json(["liquidity_below_min", "volume_24h_below_min"]),
        "security_status": "skipped_no_token",
        "raw_metrics": to_json(
            {
                "base_symbol": "TEST",
                "price_usd": 0.01,
                "liquidity_usd": 250_000,
                "volume_h24_usd": 20_000,
                "volume_h1_usd": 2_000,
                "pair_age_hours": 9,
                "price_change_h1_pct": 4,
                "source_tags": ["profile_latest"],
            }
        ),
    }
    record = build_rejection_record(row, settings)
    assert record["symbol"] == "TEST"
    assert record["liquidity_gap_usd"] == 250_000
    assert record["volume_24h_gap_usd"] == 80_000
    assert record["dexscreener_url"] == "https://dexscreener.com/base/0xabcdef1234567890"


def test_requirement_rows_include_actual_vs_required() -> None:
    settings = load_settings()
    row = {
        "id": 1,
        "observed_at": "2026-05-07T12:00:00+00:00",
        "chain_id": "base",
        "token_address": "0x1234567890abcdef",
        "pair_address": "0xabcdef1234567890",
        "rejection_reasons": to_json(["pair_too_young"]),
        "security_status": "skipped_no_token",
        "raw_metrics": to_json(
            {
                "base_symbol": "TEST",
                "price_usd": 0.01,
                "liquidity_usd": 600_000,
                "volume_h24_usd": 120_000,
                "volume_h1_usd": 20_000,
                "pair_age_hours": 2,
                "price_change_h1_pct": 4,
                "source_tags": ["boost_latest"],
            }
        ),
    }
    rows = build_requirement_rows(build_rejection_record(row, settings), settings)
    pair_age = next(item for item in rows if item["Check"] == "Pair age")
    boosted = next(item for item in rows if item["Check"] == "Boosted token source")
    assert pair_age["Status"] == "Fail"
    assert "Needs" in pair_age["Gap"]
    assert boosted["Status"] == "Pass"
