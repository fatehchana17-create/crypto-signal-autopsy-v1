from __future__ import annotations

import sqlite3

from crypto_signal_autopsy.db import archive_completed_rejected_audits, init_db, to_json


def test_archive_completed_rejected_audit_keeps_summary_and_removes_active_rows() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    metrics = {
        "base_symbol": "BAD",
        "price_usd": 1.0,
        "liquidity_usd": 20_000,
        "volume_h24_usd": 30_000,
        "pair_age_hours": 1,
    }
    cursor = conn.execute(
        """
        INSERT INTO candidate_evaluations (
          observed_at, chain_id, token_address, pair_address, accepted,
          signal_types, rejection_reasons, filter_version, security_status, raw_metrics
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-05-09T00:00:00+00:00",
            "base",
            "0xbad",
            "0xpair",
            0,
            to_json([]),
            to_json(["liquidity_below_10000"]),
            "V2",
            "ok",
            to_json(metrics),
        ),
    )
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
            "0xbad",
            "base:0xpair",
            "2026-05-09T00:00:00+00:00",
            1,
            0,
            to_json(["liquidity_below_10000"]),
            70,
            20,
            25,
            "No 10x Setup",
            to_json(["risk_score_too_high"]),
            "Reject",
            "High Risk",
            "Low Opportunity",
            "V2",
        ),
    )
    conn.execute(
        """
        INSERT INTO outcome_snapshots (
          token_address, pair_id, label_at_scan, scan_time, horizon,
          price_at_scan, price_at_horizon, return_pct,
          rugged, became_illiquid, volume_disappeared, snapshot_time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "0xbad",
            "base:0xpair",
            "Reject",
            "2026-05-09T00:00:00+00:00",
            "24h",
            1.0,
            0.4,
            -60.0,
            0,
            0,
            0,
            "2026-05-10T00:02:00+00:00",
        ),
    )
    conn.execute(
        """
        INSERT INTO rejected_candidate_outcomes (
          candidate_evaluation_id, horizon, target_at, observed_at, chain_id,
          token_address, pair_address, rejection_reasons, detection_price_usd,
          price_usd, raw_return_pct, estimated_cost_pct, net_return_pct, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cursor.lastrowid,
            "24h",
            "2026-05-10T00:00:00+00:00",
            "2026-05-10T00:02:00+00:00",
            "base",
            "0xbad",
            "0xpair",
            to_json(["liquidity_below_10000"]),
            1.0,
            0.4,
            -60.0,
            0.5,
            -60.5,
            "dexscreener_pair",
        ),
    )
    conn.commit()

    archived = archive_completed_rejected_audits(conn, "2026-05-10T00:05:00+00:00")

    assert archived == 1
    audit = conn.execute("SELECT * FROM rejected_filter_audits").fetchone()
    assert audit["symbol"] == "BAD"
    assert audit["filter_verdict"] == "success"
    assert audit["return_24h_pct"] == -60.0
    assert conn.execute("SELECT COUNT(*) FROM filter_results").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM candidate_evaluations").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM outcome_snapshots").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM rejected_candidate_outcomes").fetchone()[0] == 0
