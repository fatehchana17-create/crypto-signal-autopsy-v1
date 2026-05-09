from __future__ import annotations

import sqlite3

from crypto_signal_autopsy.analysis_tables import build_analysis_payload, filter_rule_rows
from crypto_signal_autopsy.config import load_settings
from crypto_signal_autopsy.db import init_db, to_json


def test_analysis_payload_splits_rejected_outcomes_and_explains_them() -> None:
    settings = load_settings()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)

    metrics = {
        "base_symbol": "TEST",
        "base_name": "Test Token",
        "price_usd": 1.0,
        "liquidity_usd": 50_000,
        "volume_h24_usd": 70_000,
        "pair_age_hours": 1.5,
        "price_change_h1_pct": 12,
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
            "0xtoken",
            "0xpair",
            0,
            to_json([]),
            to_json(["liquidity_below_min", "pair_too_young"]),
            settings.filter_version,
            "skipped_no_token",
            to_json(metrics),
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
            "1h",
            "2026-05-09T01:00:00+00:00",
            "2026-05-09T01:02:00+00:00",
            "base",
            "0xtoken",
            "0xpair",
            to_json(["liquidity_below_min", "pair_too_young"]),
            1.0,
            0.7,
            -30.0,
            0.5,
            -30.5,
            "dexscreener",
        ),
    )
    conn.commit()

    payload = build_analysis_payload(conn, settings)

    assert len(payload["tables"]["rejected_1h"]) == 1
    assert payload["tables"]["accepted_1h"] == []
    row = payload["tables"]["rejected_1h"][0]
    assert row["symbol"] == "TEST"
    assert row["net"] == "-30.50%"
    assert "Bad outcome fits the rejection" in row["diagnosis"]
    assert payload["summaries"]["rejected_1h"]["count"] == "1"
    assert payload["summaries"]["rejected_1h"]["priced"] == "1"


def test_filter_rules_show_accept_and_reject_requirements() -> None:
    settings = load_settings()

    rows = filter_rule_rows(settings)

    assert {row["Rule"] for row in rows} >= {"Liquidity", "24h volume", "Pair age", "GoPlus security"}
    liquidity = next(row for row in rows if row["Rule"] == "Liquidity")
    assert "500" in liquidity["Accept needs"]
    assert "below" in liquidity["Rejects when"]
