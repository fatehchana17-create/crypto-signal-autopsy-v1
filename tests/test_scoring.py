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
        "buys_h1": 120,
        "sells_h1": 80,
        "unique_buyers_1h": 150,
        "raw_json": {"info": {"websites": [{"url": "https://example.test"}], "socials": []}},
    }


def test_strong_safe_token_becomes_pending_paper_candidate() -> None:
    result = score_token(_strong_metrics(), SecurityResult("ok", [], 0, 0, {"security_score": 95}))

    assert result.final_label == "Pending Paper Candidate"
    assert result.risk_score <= 35
    assert result.opportunity_score >= 75
    assert result.ten_x_label == "Strong 10x Research Setup"
    assert result.ten_x_score >= 75


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


def test_hot_launch_with_weak_buy_pressure_is_rejected() -> None:
    metrics = _strong_metrics()
    metrics.update(
        {
            "liquidity_usd": 60_000,
            "volume_h24_usd": 225_000,
            "volume_liquidity_ratio": 3.75,
            "pair_age_hours": 1.3,
            "pair_age_minutes": 78,
            "price_change_h1_pct": 95,
            "buys_h1": 118,
            "sells_h1": 255,
            "unique_buyers_1h": 118,
            "fdv_liquidity_ratio": 3,
        }
    )

    result = score_token(metrics, SecurityResult("ok", [], 0, 0, {"security_score": 85}))

    assert result.final_label == "Reject"
    assert "hot_launch_buy_pressure_below_45_percent" in result.reject_reasons


def test_clean_hot_momentum_routes_to_high_risk_watchlist() -> None:
    metrics = _strong_metrics()
    metrics.update(
        {
            "liquidity_usd": 90_000,
            "volume_h24_usd": 350_000,
            "volume_liquidity_ratio": 3.9,
            "pair_age_hours": 1.1,
            "pair_age_minutes": 66,
            "price_change_h1_pct": 157,
            "buys_h1": 600,
            "sells_h1": 380,
            "unique_buyers_15m": 80,
            "unique_buyers_1h": 600,
            "txns_15m": 130,
            "fdv_liquidity_ratio": 3,
        }
    )

    result = score_token(metrics, SecurityResult("ok", [], 0, 0, {"security_score": 85}))

    assert result.final_label == "High-Risk Momentum Watchlist"
    assert result.qualifies_high_risk_momentum


def test_too_early_extreme_pump_is_rejected() -> None:
    metrics = _strong_metrics()
    metrics.update(
        {
            "liquidity_usd": 110_000,
            "volume_h24_usd": 220_000,
            "volume_liquidity_ratio": 2,
            "pair_age_hours": 0.35,
            "pair_age_minutes": 21,
            "price_change_h1_pct": 1200,
            "buys_h1": 600,
            "sells_h1": 500,
            "unique_buyers_1h": 600,
            "fdv_liquidity_ratio": 3,
        }
    )

    result = score_token(metrics, SecurityResult("ok", [], 0, 0, {"security_score": 85}))

    assert result.final_label == "Reject"
    assert "early_extreme_overextension" in result.reject_reasons
    assert "too_early_after_large_pump" in result.reject_reasons
