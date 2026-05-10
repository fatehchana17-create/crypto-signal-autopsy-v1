from __future__ import annotations

from datetime import timedelta
import sqlite3

from crypto_signal_autopsy import db
from crypto_signal_autopsy.timeutils import parse_iso
from crypto_signal_autopsy.track import _has_recent_cex_markets


def test_recent_cex_markets_can_be_reused_during_tracking() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    now = parse_iso("2026-05-10T14:00:00+00:00")
    observed_at = "2026-05-10T13:45:00+00:00"
    conn.execute(
        """
        INSERT INTO cex_market_snapshots (
          observed_at, coin_id, symbol, name, raw_json
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (observed_at, "bitcoin", "btc", "Bitcoin", "{}"),
    )
    conn.commit()

    assert _has_recent_cex_markets(conn, now)


def test_stale_cex_markets_are_refreshed_during_tracking() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    now = parse_iso("2026-05-10T14:00:00+00:00")
    stale_at = now - timedelta(hours=1)
    conn.execute(
        """
        INSERT INTO cex_market_snapshots (
          observed_at, coin_id, symbol, name, raw_json
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (stale_at.isoformat(timespec="seconds"), "bitcoin", "btc", "Bitcoin", "{}"),
    )
    conn.commit()

    assert not _has_recent_cex_markets(conn, now)
