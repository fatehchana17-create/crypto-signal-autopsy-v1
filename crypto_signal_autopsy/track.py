from __future__ import annotations

from collections import defaultdict
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
from crypto_signal_autopsy.config import HORIZONS as HORIZON_MINUTES, Settings
from crypto_signal_autopsy.timeutils import iso_utc, parse_iso, utc_now


HORIZONS = {name: timedelta(minutes=minutes) for name, minutes in HORIZON_MINUTES.items()}


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
        "outcome_snapshots": 0,
        "rejected_audits_archived": 0,
        "api_errors": 0,
    }

    if not _has_recent_cex_markets(conn, now):
        try:
            markets = coingecko.fetch_top_markets(settings.cex_top_n)
            stats["cex_markets"] = db.insert_cex_markets(conn, observed_at, markets)
        except (ApiError, ValueError) as exc:
            stats["api_errors"] += 1
            db.log_api_event(conn, observed_at, "coingecko", "/coins/markets", "error", str(exc))

    delayed_signals = _signals_needing_delayed_entry(conn, observed_at)
    due = _due_signal_horizons(conn, now)
    rejected_due = _due_rejected_horizons(conn, now)
    v2_due = _due_v2_outcome_horizons(conn, now)
    pair_cache = _prefetch_pairs(
        conn,
        dex,
        observed_at,
        stats,
        delayed_signals,
        due,
        rejected_due,
        v2_due,
    )

    for signal in delayed_signals:
        pair = _cached_pair(pair_cache, signal["chain_id"], signal["pair_address"])
        if pair is None:
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

    for signal, horizon, target_at in due:
        pair = _cached_pair(pair_cache, signal["chain_id"], signal["pair_address"])
        if pair is None:
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

        pair = _cached_pair(pair_cache, candidate["chain_id"], candidate["pair_address"])
        if pair is None:
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

    for filter_result, horizon, target_at in v2_due:
        try:
            raw_metrics = json.loads(filter_result["raw_metrics"])
        except json.JSONDecodeError:
            db.log_api_event(
                conn,
                observed_at,
                "local",
                "outcome_snapshots",
                "invalid_raw_metrics",
                f"pair_id={filter_result['pair_id']}",
            )
            continue
        pair = _cached_pair(pair_cache, filter_result["chain_id"], filter_result["pair_address"])
        if pair is None:
            continue
        price = _price_from_pair(pair)
        liquidity = _liquidity_from_pair(pair)
        volume = _volume_h24_from_pair(pair)
        price_at_scan = raw_metrics.get("price_usd")
        liquidity_at_scan = raw_metrics.get("liquidity_usd")
        volume_at_scan = raw_metrics.get("volume_h24_usd")
        return_pct = _return_pct(price_at_scan, price)
        history = _history_prices(conn, filter_result["pair_address"], filter_result["scan_time"], observed_at)
        mfe, mae = _excursions(price_at_scan, [value for value in history + [price] if value is not None])
        liquidity_drop_pct = _drop_pct(liquidity_at_scan, liquidity)
        volume_disappeared = (
            volume is not None
            and volume_at_scan is not None
            and volume_at_scan > 0
            and volume < volume_at_scan * 0.20
        )
        became_illiquid = bool(
            (liquidity is not None and liquidity < 5_000)
            or (liquidity_drop_pct is not None and liquidity_drop_pct >= 80)
        )
        rugged = bool(
            (return_pct is not None and return_pct <= -80)
            or (liquidity_drop_pct is not None and liquidity_drop_pct >= 90)
        )
        inserted = db.insert_outcome_snapshot(
            conn,
            {
                "token_address": filter_result["token_address"],
                "pair_id": filter_result["pair_id"],
                "label_at_scan": filter_result["final_label"],
                "scan_time": filter_result["scan_time"],
                "horizon": horizon,
                "price_at_scan": price_at_scan,
                "price_at_horizon": price,
                "return_pct": return_pct,
                "max_favorable_excursion_pct": mfe,
                "max_adverse_excursion_pct": mae,
                "drawdown_before_pump_pct": mae,
                "liquidity_at_scan": liquidity_at_scan,
                "liquidity_at_horizon": liquidity,
                "volume_at_scan": volume_at_scan,
                "volume_at_horizon": volume,
                "became_illiquid": became_illiquid,
                "rugged": rugged,
                "volume_disappeared": volume_disappeared,
                "snapshot_time": observed_at,
            },
        )
        if inserted:
            stats["outcome_snapshots"] += 1

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
    stats["rejected_audits_archived"] = db.archive_completed_rejected_audits(conn, observed_at)
    return stats


def _has_recent_cex_markets(conn: sqlite3.Connection, now) -> bool:
    latest = conn.execute("SELECT MAX(observed_at) AS value FROM cex_market_snapshots").fetchone()["value"]
    if not latest:
        return False
    return now - parse_iso(latest) <= timedelta(minutes=30)


def _prefetch_pairs(
    conn: sqlite3.Connection,
    dex: DexScreenerClient,
    observed_at: str,
    stats: dict[str, int],
    delayed_signals: list[sqlite3.Row],
    due_signals: list[tuple[sqlite3.Row, str, object]],
    rejected_due: list[tuple[sqlite3.Row, str, object]],
    v2_due: list[tuple[sqlite3.Row, str, object]],
) -> dict[tuple[str, str], dict]:
    refs: dict[str, set[str]] = defaultdict(set)
    for signal in delayed_signals:
        _add_pair_ref(refs, signal["chain_id"], signal["pair_address"])
    for signal, _horizon, _target_at in due_signals:
        _add_pair_ref(refs, signal["chain_id"], signal["pair_address"])
    for candidate, _horizon, _target_at in rejected_due:
        _add_pair_ref(refs, candidate["chain_id"], candidate["pair_address"])
    for filter_result, _horizon, _target_at in v2_due:
        _add_pair_ref(refs, filter_result["chain_id"], filter_result["pair_address"])

    cache: dict[tuple[str, str], dict] = {}
    for chain_id, pair_addresses in refs.items():
        try:
            pairs = dex.fetch_pairs_by_addresses(chain_id, sorted(pair_addresses))
        except ApiError as exc:
            stats["api_errors"] += 1
            db.log_api_event(conn, observed_at, "dexscreener", "/latest/dex/pairs", "error", str(exc))
            continue
        for pair_address, pair in pairs.items():
            cache[(chain_id, pair_address.lower())] = pair
    return cache


def _add_pair_ref(refs: dict[str, set[str]], chain_id: str | None, pair_address: str | None) -> None:
    if chain_id and pair_address:
        refs[str(chain_id)].add(str(pair_address))


def _cached_pair(
    pair_cache: dict[tuple[str, str], dict],
    chain_id: str | None,
    pair_address: str | None,
) -> dict | None:
    if not chain_id or not pair_address:
        return None
    return pair_cache.get((str(chain_id), str(pair_address).lower()))


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


def _due_v2_outcome_horizons(
    conn: sqlite3.Connection,
    now,
) -> list[tuple[sqlite3.Row, str, object]]:
    rows = conn.execute(
        """
        SELECT f.*, c.chain_id, c.pair_address, c.raw_metrics
        FROM filter_results f
        JOIN candidate_evaluations c
          ON c.token_address = f.token_address
         AND c.observed_at = f.scan_time
         AND f.pair_id = c.chain_id || ':' || c.pair_address
        ORDER BY f.scan_time ASC
        """
    ).fetchall()
    due: list[tuple[sqlite3.Row, str, object]] = []
    for row in rows:
        scan_time = parse_iso(row["scan_time"])
        for horizon, delta in HORIZONS.items():
            target_at = scan_time + delta
            if target_at > now:
                continue
            exists = conn.execute(
                """
                SELECT 1
                FROM outcome_snapshots
                WHERE pair_id = ? AND scan_time = ? AND horizon = ?
                """,
                (row["pair_id"], row["scan_time"], horizon),
            ).fetchone()
            if not exists:
                due.append((row, horizon, target_at))
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


def _liquidity_from_pair(pair) -> float | None:
    value = (pair.get("liquidity") or {}).get("usd") if isinstance(pair, dict) else None
    return _float(value)


def _volume_h24_from_pair(pair) -> float | None:
    value = (pair.get("volume") or {}).get("h24") if isinstance(pair, dict) else None
    return _float(value)


def _return_pct(start: float | None, end: float | None) -> float | None:
    if not start or not end or start <= 0:
        return None
    return ((end / start) - 1) * 100


def _history_prices(conn: sqlite3.Connection, pair_address: str, start_at: str, end_at: str) -> list[float]:
    rows = conn.execute(
        """
        SELECT price_usd
        FROM dex_token_snapshots
        WHERE pair_address = ?
          AND observed_at >= ?
          AND observed_at <= ?
          AND price_usd IS NOT NULL
        ORDER BY observed_at ASC
        """,
        (pair_address, start_at, end_at),
    ).fetchall()
    return [float(row["price_usd"]) for row in rows if row["price_usd"] is not None]


def _excursions(start: float | None, prices: list[float]) -> tuple[float | None, float | None]:
    if not start or start <= 0 or not prices:
        return None, None
    high = max(prices)
    low = min(prices)
    return _return_pct(start, high), _return_pct(start, low)


def _drop_pct(start: float | None, end: float | None) -> float | None:
    if start is None or end is None or start <= 0:
        return None
    return max(0.0, ((start - end) / start) * 100)


def _float(value) -> float | None:
    if value in {None, "", "null"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
