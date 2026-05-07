from __future__ import annotations

import statistics
import sqlite3
from typing import Iterable


def cex_return_pct(
    conn: sqlite3.Connection,
    coin_id: str,
    start_at: str,
    end_at: str,
) -> float | None:
    start = _price_nearest_before(conn, coin_id, start_at)
    end = _price_nearest_before(conn, coin_id, end_at)
    if not start or not end or start <= 0:
        return None
    return ((end / start) - 1) * 100


def top100_equal_return_pct(
    conn: sqlite3.Connection,
    start_at: str,
    end_at: str,
) -> float | None:
    start_rows = _latest_cex_snapshot_set(conn, start_at)
    end_rows = _latest_cex_snapshot_set(conn, end_at)
    returns = []
    for coin_id, start_price in start_rows.items():
        end_price = end_rows.get(coin_id)
        if start_price and end_price:
            returns.append(((end_price / start_price) - 1) * 100)
    return statistics.fmean(returns) if returns else None


def matched_all_passing_median_return_pct(
    conn: sqlite3.Connection,
    horizon: str,
    exclude_signal_id: int,
) -> float | None:
    rows = conn.execute(
        """
        SELECT net_return_pct
        FROM signal_outcomes
        WHERE horizon = ?
          AND signal_id != ?
          AND net_return_pct IS NOT NULL
        ORDER BY observed_at DESC
        LIMIT 200
        """,
        (horizon, exclude_signal_id),
    ).fetchall()
    values = [row["net_return_pct"] for row in rows]
    return statistics.median(values) if values else None


def _price_nearest_before(conn: sqlite3.Connection, coin_id: str, at: str) -> float | None:
    row = conn.execute(
        """
        SELECT price_usd
        FROM cex_market_snapshots
        WHERE coin_id = ? AND observed_at <= ? AND price_usd IS NOT NULL
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        (coin_id, at),
    ).fetchone()
    return row["price_usd"] if row else None


def _latest_cex_snapshot_set(conn: sqlite3.Connection, at: str) -> dict[str, float]:
    rows = conn.execute(
        """
        SELECT coin_id, price_usd
        FROM cex_market_snapshots
        WHERE observed_at = (
          SELECT MAX(observed_at)
          FROM cex_market_snapshots
          WHERE observed_at <= ?
        )
          AND market_cap_rank <= 100
          AND price_usd IS NOT NULL
        """,
        (at,),
    ).fetchall()
    return {row["coin_id"]: row["price_usd"] for row in rows}
