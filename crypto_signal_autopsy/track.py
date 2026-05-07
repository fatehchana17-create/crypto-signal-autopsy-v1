from __future__ import annotations

from datetime import timedelta
import json
import sqlite3

from crypto_signal_autopsy import db
from crypto_signal_autopsy.baselines import (
    cex_return_pct,
    matched_all_passing_median_return_pct,
    top100_equal_return_pct,
)
from crypto_signal_autopsy.clients.coingecko import CoinGeckoClient
from crypto_signal_autopsy.clients.dexscreener import DexScreenerClient
from crypto_signal_autopsy.clients.http import ApiError, JsonHttpClient
from crypto_signal_autopsy.config import Settings
from crypto_signal_autopsy.timeutils import iso_utc, parse_iso, utc_now


HORIZONS = {"1h": timedelta(hours=1), "24h": timedelta(hours=24)}


def run_tracking(conn: sqlite3.Connection, settings: Settings) -> dict[str, int]:
    db.init_db(conn)
    now = utc_now()
    observed_at = iso_utc(now)
    http = JsonHttpClient(settings.request_timeout_seconds, settings.user_agent)
    coingecko = CoinGeckoClient(settings, http)
    dex = DexScreenerClient(http)

    stats = {
        "cex_markets": 0,
        "delayed_entries": 0,
        "outcomes": 0,
        "rejected_outcomes": 0,
        "api_errors": 0,
    }

    try:
        markets = coingecko.fetch_top_markets(settings.cex_top_n)
        stats["cex_markets"] = db.insert_cex_markets(conn, observed_at, markets)
    except (ApiError, ValueError) as exc:
        stats["api_errors"] += 1
        db.log_api_event(conn, observed_at, "coingecko", "/coins/markets", "error", str(exc))

    for signal in _signals_needing_delayed_entry(conn, observed_at):
        try:
            pair = dex.fetch_pair(signal["chain_id"], signal["pair_address"])
        except ApiError as exc:
            stats["api_errors"] += 1
            db.log_api_event(conn, observed_at, "dexscreener", "/latest/dex/pairs", "error", str(exc))
            continue
        price = _price_from_pair(pair)
        if price is None:
            continue
        conn.execute(
            """
            UPDATE signals
            SET delayed_entry_price_usd = ?, delayed_entry_observed_at = ?
            WHERE signal_id = ?
            """,
            (price, observed_at, signal["signal_id"]),
        )
        conn.commit()
        stats["delayed_entries"] += 1

    due = _due_signal_horizons(conn, now)
    for signal, horizon, target_at in due:
        try:
            pair = dex.fetch_pair(signal["chain_id"], signal["pair_address"])
        except ApiError as exc:
            stats["api_errors"] += 1
            db.log_api_event(conn, observed_at, "dexscreener", "/latest/dex/pairs", "error", str(exc))
            continue
        price = _price_from_pair(pair)
        if price is None:
            db.log_api_event(
                conn,
                observed_at,
                "dexscreener",
                "/latest/dex/pairs",
                "missing_price",
                f"pair={signal['pair_address']}",
            )
            continue

        raw_return = _return_pct(signal["detection_price_usd"], price)
        delayed_raw_return = _return_pct(signal["delayed_entry_price_usd"], price)
        net_return = raw_return - settings.estimated_cost_pct if raw_return is not None else None
        delayed_net_return = (
            delayed_raw_return - settings.estimated_cost_pct if delayed_raw_return is not None else None
        )
        target_iso = iso_utc(target_at)
        conn.execute(
            """
            INSERT OR IGNORE INTO signal_outcomes (
              signal_id, horizon, target_at, observed_at, price_usd,
              raw_return_pct, estimated_cost_pct, net_return_pct,
              delayed_raw_return_pct, delayed_net_return_pct, btc_return_pct,
              eth_return_pct, top100_equal_return_pct,
              matched_all_passing_median_return_pct, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal["signal_id"],
                horizon,
                target_iso,
                observed_at,
                price,
                raw_return,
                settings.estimated_cost_pct,
                net_return,
                delayed_raw_return,
                delayed_net_return,
                cex_return_pct(conn, "bitcoin", signal["created_at"], observed_at),
                cex_return_pct(conn, "ethereum", signal["created_at"], observed_at),
                top100_equal_return_pct(conn, signal["created_at"], observed_at),
                matched_all_passing_median_return_pct(conn, horizon, signal["signal_id"]),
                "dexscreener_pair",
            ),
        )
        conn.commit()
        stats["outcomes"] += 1

    rejected_due = _due_rejected_horizons(conn, now)
    for candidate, horizon, target_at in rejected_due:
        try:
            raw_metrics = json.loads(candidate["raw_metrics"])
        except json.JSONDecodeError:
            db.log_api_event(
                conn,
                observed_at,
                "local",
                "rejected_candidate_outcomes",
                "invalid_raw_metrics",
                f"candidate_evaluation_id={candidate['id']}",
            )
            continue

        try:
            pair = dex.fetch_pair(candidate["chain_id"], candidate["pair_address"])
        except ApiError as exc:
            stats["api_errors"] += 1
            db.log_api_event(conn, observed_at, "dexscreener", "/latest/dex/pairs", "error", str(exc))
            continue

        price = _price_from_pair(pair)
        detection_price = raw_metrics.get("price_usd")
        raw_return = _return_pct(detection_price, price)
        net_return = raw_return - settings.estimated_cost_pct if raw_return is not None else None
        conn.execute(
            """
            INSERT OR IGNORE INTO rejected_candidate_outcomes (
              candidate_evaluation_id, horizon, target_at, observed_at,
              chain_id, token_address, pair_address, rejection_reasons,
              detection_price_usd, price_usd, raw_return_pct,
              estimated_cost_pct, net_return_pct, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate["id"],
                horizon,
                iso_utc(target_at),
                observed_at,
                candidate["chain_id"],
                candidate["token_address"],
                candidate["pair_address"],
                candidate["rejection_reasons"],
                detection_price,
                price,
                raw_return,
                settings.estimated_cost_pct,
                net_return,
                "dexscreener_pair",
            ),
        )
        conn.commit()
        stats["rejected_outcomes"] += 1

    conn.execute(
        """
        UPDATE signals
        SET status = 'complete'
        WHERE status = 'open'
          AND EXISTS (
            SELECT 1 FROM signal_outcomes o
            WHERE o.signal_id = signals.signal_id AND o.horizon = '24h'
          )
        """
    )
    conn.commit()
    return stats


def _signals_needing_delayed_entry(conn: sqlite3.Connection, observed_at: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM signals
        WHERE delayed_entry_price_usd IS NULL
          AND delayed_entry_at IS NOT NULL
          AND delayed_entry_at <= ?
        ORDER BY delayed_entry_at ASC
        """,
        (observed_at,),
    ).fetchall()


def _due_signal_horizons(
    conn: sqlite3.Connection,
    now,
) -> list[tuple[sqlite3.Row, str, object]]:
    signals = conn.execute("SELECT * FROM signals ORDER BY created_at ASC").fetchall()
    due: list[tuple[sqlite3.Row, str, object]] = []
    for signal in signals:
        created_at = parse_iso(signal["created_at"])
        for horizon, delta in HORIZONS.items():
            target_at = created_at + delta
            if target_at > now:
                continue
            exists = conn.execute(
                "SELECT 1 FROM signal_outcomes WHERE signal_id = ? AND horizon = ?",
                (signal["signal_id"], horizon),
            ).fetchone()
            if not exists:
                due.append((signal, horizon, target_at))
    return due


def _due_rejected_horizons(
    conn: sqlite3.Connection,
    now,
) -> list[tuple[sqlite3.Row, str, object]]:
    candidates = conn.execute(
        """
        SELECT *
        FROM candidate_evaluations
        WHERE accepted = 0
        ORDER BY observed_at ASC
        """
    ).fetchall()
    due: list[tuple[sqlite3.Row, str, object]] = []
    for candidate in candidates:
        detected_at = parse_iso(candidate["observed_at"])
        for horizon, delta in HORIZONS.items():
            target_at = detected_at + delta
            if target_at > now:
                continue
            exists = conn.execute(
                """
                SELECT 1
                FROM rejected_candidate_outcomes
                WHERE candidate_evaluation_id = ? AND horizon = ?
                """,
                (candidate["id"], horizon),
            ).fetchone()
            if not exists:
                due.append((candidate, horizon, target_at))
    return due


def _price_from_pair(pair) -> float | None:
    if not pair:
        return None
    value = pair.get("priceUsd")
    if value in {None, "", "null"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _return_pct(start: float | None, end: float | None) -> float | None:
    if not start or not end or start <= 0:
        return None
    return ((end / start) - 1) * 100
