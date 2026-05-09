from __future__ import annotations

from crypto_signal_autopsy.wallet_scoring import (
    WalletMetrics,
    calculate_wallet_quality_score,
    calculate_wallet_risk_score,
    label_wallet,
)


def test_wallet_with_too_little_history_stays_unproven() -> None:
    metrics = WalletMetrics(
        tokens_traded=4,
        realized_exits=4,
        win_rate=1,
        median_realized_return_pct=200,
        profitable_tokens=4,
        rug_exposure_rate=0,
        low_liquidity_trade_rate=0,
        realized_exit_rate=1,
        history_days=60,
    )

    quality = calculate_wallet_quality_score(metrics)
    risk = calculate_wallet_risk_score(metrics)

    assert label_wallet(metrics, quality, risk) == "Unproven Wallet"


def test_wallet_requires_enough_history_before_smart_label() -> None:
    metrics = WalletMetrics(
        tokens_traded=25,
        realized_exits=12,
        win_rate=0.75,
        median_realized_return_pct=55,
        profitable_tokens=12,
        successful_early_entries=8,
        rug_exposure_rate=0.02,
        low_liquidity_trade_rate=0.05,
        realized_exit_rate=0.80,
        avg_hold_time_minutes=120,
        suspicious_behavior_score=5,
        history_days=45,
    )

    quality = calculate_wallet_quality_score(metrics)
    risk = calculate_wallet_risk_score(metrics)

    assert 0 <= quality <= 100
    assert 0 <= risk <= 100
    assert label_wallet(metrics, quality, risk) == "Smart Wallet"


def test_quick_dump_wallet_can_be_labeled_dump_wallet() -> None:
    metrics = WalletMetrics(
        tokens_traded=12,
        realized_exits=10,
        win_rate=0.4,
        quick_dump_rate=0.70,
        avg_hold_time_minutes=3,
        first_minute_entry_rate=0.5,
        repeated_buy_sell_loop_count=8,
        rug_exposure_rate=0.3,
        low_liquidity_trade_rate=0.5,
    )

    quality = calculate_wallet_quality_score(metrics)
    risk = calculate_wallet_risk_score(metrics)

    assert risk >= 60
    assert label_wallet(metrics, quality, risk) == "Dump Wallet"
