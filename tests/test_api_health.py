import sqlite3

from crypto_signal_autopsy import db


def test_active_api_error_is_cleared_after_later_success():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)

    db.log_api_event(
        conn,
        "2026-05-10T06:59:18+00:00",
        "coingecko",
        "/coins/markets",
        "error",
        "rate limited",
    )
    assert db.active_api_error_count(conn) == 1

    conn.execute(
        """
        INSERT INTO cex_market_snapshots (
          observed_at, coin_id, symbol, name, raw_json
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        ("2026-05-10T07:04:20+00:00", "bitcoin", "btc", "Bitcoin", "{}"),
    )
    conn.commit()

    assert db.active_api_error_count(conn) == 0
    assert db.prune_resolved_api_events(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM api_events").fetchone()[0] == 0


def test_active_dexscreener_error_is_cleared_after_later_pair_data():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)

    db.log_api_event(
        conn,
        "2026-05-10T07:10:00+00:00",
        "dexscreener",
        "/latest/dex/pairs",
        "error",
        "temporary failure",
    )
    assert db.active_api_error_count(conn) == 1

    conn.execute(
        """
        INSERT INTO dex_token_snapshots (
          observed_at, chain_id, token_address, pair_address, source_tags, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "2026-05-10T07:11:00+00:00",
            "base",
            "0xtoken",
            "0xpair",
            "[]",
            "{}",
        ),
    )
    conn.commit()

    assert db.active_api_error_count(conn) == 0


def test_prune_removes_non_error_notices():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)

    db.log_api_event(
        conn,
        "2026-05-10T07:00:00+00:00",
        "dexscreener",
        "candidate_discovery",
        "empty",
        "no results found",
    )
    db.log_api_event(
        conn,
        "2026-05-10T07:01:00+00:00",
        "wallet_module",
        "status",
        "disabled",
        "missing key",
    )

    assert db.prune_resolved_api_events(conn) == 2
    assert conn.execute("SELECT COUNT(*) FROM api_events").fetchone()[0] == 0
