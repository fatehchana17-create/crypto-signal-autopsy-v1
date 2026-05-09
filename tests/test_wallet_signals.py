from __future__ import annotations

import sqlite3

from crypto_signal_autopsy import db
from crypto_signal_autopsy.wallet_signals import (
    WalletSignalMetrics,
    apply_wallet_signals_to_filter_results,
    calculate_wallet_signal_score,
    label_wallet_signal,
)


def test_wallet_signal_score_penalizes_suspicious_wallets() -> None:
    clean = WalletSignalMetrics(useful_wallet_count=3, net_wallet_flow_usd=12_000)
    suspicious = WalletSignalMetrics(
        useful_wallet_count=3,
        net_wallet_flow_usd=12_000,
        suspicious_wallet_count=2,
        dump_wallet_count=1,
    )

    assert calculate_wallet_signal_score(clean) > calculate_wallet_signal_score(suspicious)
    assert label_wallet_signal(calculate_wallet_signal_score(clean)) == "Weak Wallet Signal"


def test_wallet_signal_does_not_override_hard_reject() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    conn.execute(
        """
        INSERT INTO filter_results (
          token_address, pair_id, scan_time, hard_reject,
          qualifies_high_risk_momentum, reject_reasons, risk_score,
          opportunity_score, final_label, risk_category, opportunity_category,
          model_version
        )
        VALUES ('0xtoken', 'base:0xpair', '2026-05-09T00:00:00+00:00', 1, 0,
                '["honeypot_detected"]', 20, 95, 'Reject', 'Lower Risk',
                'Strong Opportunity', 'V2')
        """
    )
    conn.execute(
        """
        INSERT INTO wallet_token_signals (
          token_address, pair_id, chain, scan_time, smart_wallet_count,
          useful_wallet_count, high_risk_wallet_count, suspicious_wallet_count,
          dump_wallet_count, unproven_wallet_count, total_wallet_buy_usd,
          total_wallet_sell_usd, net_wallet_flow_usd,
          smart_or_useful_wallets_entered_within_30m,
          low_rug_exposure_wallet_count, deployer_linked_wallet_involved,
          wallet_signal_score, wallet_signal_label
        )
        VALUES ('0xtoken', 'base:0xpair', 'base', '2026-05-09T00:00:00+00:00',
                2, 3, 0, 0, 0, 0, 20000, 0, 20000, 3, 3, 0, 95,
                'Strong Wallet Signal')
        """
    )
    conn.commit()

    apply_wallet_signals_to_filter_results(conn)

    row = conn.execute("SELECT final_label, final_research_score FROM filter_results").fetchone()
    assert row["final_label"] == "Reject"
    assert row["final_research_score"] is not None
