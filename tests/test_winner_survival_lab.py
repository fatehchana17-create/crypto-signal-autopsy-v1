from __future__ import annotations

import sqlite3

from crypto_signal_autopsy.config import load_settings
from crypto_signal_autopsy.db import init_db, to_json
from crypto_signal_autopsy.quant_analytics import refresh_quant_analytics


def _insert_filter_result(
    conn: sqlite3.Connection,
    *,
    label: str,
    scan_time: str,
    token_address: str = "0xwinner",
    pair_id: str = "base:0xpair",
    ten_x_score: float = 88,
) -> None:
    conn.execute(
        """
        INSERT INTO filter_results (
          token_address, pair_id, scan_time, hard_reject,
          qualifies_high_risk_momentum, reject_reasons, risk_score,
          opportunity_score, ten_x_score, ten_x_label, ten_x_reasons,
          final_label, risk_category, opportunity_category, model_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            token_address,
            pair_id,
            scan_time,
            0,
            1 if label == "High-Risk Momentum Watchlist" else 0,
            to_json([]),
            32,
            72,
            ten_x_score,
            "Strong 10x Research Setup",
            to_json(["strong_volume_vs_liquidity"]),
            label,
            "Medium Risk",
            "Strong Opportunity",
            "V2",
        ),
    )


def _insert_candidate(
    conn: sqlite3.Connection,
    *,
    scan_time: str,
    token_address: str = "0xwinner",
    pair_address: str = "0xpair",
) -> None:
    metrics = {
        "base_symbol": "WIN",
        "liquidity_usd": 90_000,
        "volume_h24_usd": 567_000,
        "volume_liquidity_ratio": 6.3,
        "pair_age_hours": 1.1,
        "price_change_h1_pct": 157,
        "buys_h1": 600,
        "sells_h1": 380,
        "unique_buyers_1h": 600,
    }
    conn.execute(
        """
        INSERT INTO candidate_evaluations (
          observed_at, chain_id, token_address, pair_address, accepted,
          signal_types, rejection_reasons, filter_version, security_status, raw_metrics
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scan_time,
            "base",
            token_address,
            pair_address,
            0,
            to_json([]),
            to_json([]),
            "V2",
            "ok",
            to_json(metrics),
        ),
    )


def _insert_outcome(
    conn: sqlite3.Connection,
    *,
    label: str,
    horizon: str,
    return_pct: float,
    scan_time: str,
    token_address: str = "0xwinner",
    pair_id: str = "base:0xpair",
) -> None:
    conn.execute(
        """
        INSERT INTO outcome_snapshots (
          token_address, pair_id, label_at_scan, scan_time, horizon,
          price_at_scan, price_at_horizon, return_pct,
          max_favorable_excursion_pct, max_adverse_excursion_pct,
          liquidity_at_scan, liquidity_at_horizon, volume_at_scan,
          volume_at_horizon, rugged, became_illiquid, volume_disappeared,
          snapshot_time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            token_address,
            pair_id,
            label,
            scan_time,
            horizon,
            1.0,
            1.0 + (return_pct / 100),
            return_pct,
            max(return_pct, 0),
            min(return_pct, 0),
            90_000,
            95_000,
            567_000,
            610_000,
            0,
            0,
            0,
            "2026-05-12T01:00:00+00:00",
        ),
    )


def test_winner_survival_lab_marks_missed_winner_study() -> None:
    settings = load_settings()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    scan_time = "2026-05-12T00:00:00+00:00"
    _insert_candidate(conn, scan_time=scan_time)
    _insert_filter_result(conn, label="Watchlist", scan_time=scan_time)
    _insert_outcome(conn, label="Watchlist", horizon="1h", return_pct=65, scan_time=scan_time)
    _insert_outcome(conn, label="Watchlist", horizon="4h", return_pct=120, scan_time=scan_time)

    counts = refresh_quant_analytics(conn, settings)

    assert counts["winner_survival_lab"] == 1
    row = conn.execute("SELECT * FROM winner_survival_lab").fetchone()
    assert row["symbol"] == "WIN"
    assert row["setup_type"] == "Missed Winner Study"
    assert row["pump_strength_score"] >= 65
    assert row["survival_score"] >= 50
    assert "strict filters blocked" in row["learning_note"]
    assert counts["tradable_candidates"] == 1
    assert counts["winner_loser_dna"] == 2
    assert counts["delayed_entry_simulator"] >= 1
    tradable = conn.execute("SELECT * FROM tradable_candidates").fetchone()
    assert tradable["symbol"] == "WIN"
    assert tradable["tradable_tier"] in {"Tradable Research Candidate", "Cautious Tradable Watch"}


def test_winner_survival_lab_marks_accepted_failure() -> None:
    settings = load_settings()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    scan_time = "2026-05-12T00:00:00+00:00"
    _insert_candidate(conn, scan_time=scan_time)
    _insert_filter_result(conn, label="Paper Trade Candidate", scan_time=scan_time)
    _insert_outcome(conn, label="Paper Trade Candidate", horizon="1h", return_pct=-35, scan_time=scan_time)
    _insert_outcome(conn, label="Paper Trade Candidate", horizon="4h", return_pct=-72, scan_time=scan_time)

    counts = refresh_quant_analytics(conn, settings)

    assert counts["winner_survival_lab"] == 1
    row = conn.execute("SELECT * FROM winner_survival_lab").fetchone()
    assert row["setup_type"] == "Accepted Failure"
    assert row["survival_label"] == "Fading Early"
    assert "survival evidence" in row["learning_note"]


def test_wallet_reputation_memory_summarizes_wallets() -> None:
    settings = load_settings()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.execute(
        """
        INSERT INTO wallets (
          wallet_address, chain, first_seen_at, last_seen_at, wallet_label,
          wallet_quality_score, wallet_risk_score, tokens_traded,
          realized_exits, win_rate, median_realized_return_pct,
          avg_realized_return_pct, rug_exposure_rate, quick_dump_rate,
          first_minute_entry_rate, avg_hold_time_minutes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "0xwallet",
            "base",
            "2026-05-01T00:00:00+00:00",
            "2026-05-12T00:00:00+00:00",
            "Useful Wallet",
            72,
            24,
            14,
            7,
            0.57,
            18,
            21,
            0.05,
            0.08,
            0.10,
            180,
            "2026-05-12T00:00:00+00:00",
        ),
    )

    counts = refresh_quant_analytics(conn, settings)

    assert counts["wallet_reputation_memory"] == 1
    row = conn.execute("SELECT * FROM wallet_reputation_memory").fetchone()
    assert row["memory_tier"] == "Useful Wallet Memory"
    assert "not a copy-trade signal" in row["reputation_note"]
