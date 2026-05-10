from __future__ import annotations

from crypto_signal_autopsy.clients.goplus import SecurityResult
from crypto_signal_autopsy.config import load_settings
from crypto_signal_autopsy.filters import evaluate_candidate


def _base_metrics() -> dict:
    return {
        "chain_id": "base",
        "token_address": "0xabc",
        "pair_address": "0xpair",
        "price_usd": 1.0,
        "liquidity_usd": 750_000,
        "volume_h1_usd": 250_000,
        "volume_h24_usd": 1_000_000,
        "fdv_liquidity_ratio": 8,
        "volume_liquidity_ratio": 1.33,
        "buys_h1": 120,
        "sells_h1": 80,
        "unique_buyers_1h": 120,
        "price_change_h1_pct": 8,
        "pair_age_hours": 12,
        "source_tags": ["profile_latest"],
        "raw_json": {"info": {"websites": [{"url": "https://example.test"}], "socials": []}},
    }


def test_clean_momentum_passes_with_volume_spike() -> None:
    settings = load_settings()
    security = SecurityResult("ok", [], None, None, {"security_score": 95})
    evaluation = evaluate_candidate(_base_metrics(), security, None, settings)
    assert evaluation.accepted
    assert evaluation.final_label == "Pending Paper Candidate"
    assert evaluation.signal_types == []


def test_low_liquidity_rejected() -> None:
    settings = load_settings()
    metrics = _base_metrics()
    metrics["liquidity_usd"] = 5_000
    security = SecurityResult("ok", [], None, None, {"security_score": 95})
    evaluation = evaluate_candidate(metrics, security, None, settings)
    assert not evaluation.accepted
    assert "liquidity_below_10000" in evaluation.rejection_reasons


def test_goplus_risk_rejected() -> None:
    settings = load_settings()
    security = SecurityResult("ok", ["is_honeypot"], None, None, {})
    evaluation = evaluate_candidate(_base_metrics(), security, None, settings)
    assert not evaluation.accepted
    assert "honeypot_detected" in evaluation.rejection_reasons
