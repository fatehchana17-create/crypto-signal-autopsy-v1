from __future__ import annotations

from crypto_signal_autopsy.clients.goplus import SecurityResult
from crypto_signal_autopsy.scoring import score_token


def _strong_metrics() -> dict:
    return {
        "chain_id": "base",
        "token_address": "0xabc",
        "pair_address": "0xpair",
        "price_usd": 1.0,
        "liquidity_usd": 800_000,
        "volume_h24_usd": 1_200_000,
        "volume_liquidity_ratio": 1.5,
        "pair_age_hours": 12,
        "pair_age_minutes": 720,
        "price_change_h1_pct": 12,
        "fdv_liquidity_ratio": 8,
        "buys_h1": 150,
        "sells_h1": 50,
        "unique_buyers_1h": 150,
        "raw_json": {"info": {"websites": [{"url": "https://example.test"}], "socials": []}},
    }


def test_strong_safe_token_becomes_paper_trade_candidate() -> None:
    result = score_token(_strong_metrics(), SecurityResult("ok", [], 0, 0, {"security_score": 95}))

    assert result.final_label == "Paper Trade Candidate"
    assert result.risk_score <= 35
    assert result.opportunity_score >= 75


def test_low_liquidity_is_hard_reject() -> None:
    metrics = _strong_metrics()
    metrics["liquidity_usd"] = 5_000

    result = score_token(metrics, SecurityResult("ok", [], 0, 0, {"security_score": 95}))

    assert result.final_label == "Reject"
    assert result.hard_reject
    assert "liquidity_below_10000" in result.reject_reasons


def test_honeypot_is_rejected() -> None:
    result = score_token(_strong_metrics(), SecurityResult("ok", ["is_honeypot"], 0, 0, {}))

    assert result.final_label == "Reject"
    assert "honeypot_detected" in result.reject_reasons
