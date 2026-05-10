from __future__ import annotations

from datetime import timedelta
import sqlite3

from crypto_signal_autopsy.clients.coingecko import CoinGeckoClient
from crypto_signal_autopsy.clients.dexscreener import DexScreenerClient
from crypto_signal_autopsy.clients.goplus import GoPlusClient
from crypto_signal_autopsy.clients.http import ApiError, JsonHttpClient
from crypto_signal_autopsy.config import Settings
from crypto_signal_autopsy import db
from crypto_signal_autopsy.filters import evaluate_candidate
from crypto_signal_autopsy.normalize import choose_best_pairs
from crypto_signal_autopsy.timeutils import iso_utc, utc_now


def run_scan(conn: sqlite3.Connection, settings: Settings) -> dict[str, int]:
    db.init_db(conn)
    now = utc_now()
    observed_at = iso_utc(now)
    http = JsonHttpClient(settings.request_timeout_seconds, settings.user_agent)
    coingecko = CoinGeckoClient(settings, http)
    dex = DexScreenerClient(http)
    goplus = GoPlusClient(settings, http)

    stats = {
        "cex_markets": 0,
        "discovered_tokens": 0,
        "pairs": 0,
        "snapshots": 0,
        "accepted_candidates": 0,
        "rejected_candidates": 0,
        "new_signals": 0,
        "api_errors": 0,
    }

    try:
        markets = coingecko.fetch_top_markets(settings.cex_top_n)
        stats["cex_markets"] = db.insert_cex_markets(conn, observed_at, markets)
    except (ApiError, ValueError) as exc:
        stats["api_errors"] += 1
        db.log_api_event(conn, observed_at, "coingecko", "/coins/markets", "error", str(exc))

    try:
        candidate_sources = dex.discover_candidate_tokens(
            settings.dex_chain,
            settings.dex_search_queries,
        )
        stats["discovered_tokens"] = len(candidate_sources)
    except ApiError as exc:
        stats["api_errors"] += 1
        db.log_api_event(conn, observed_at, "dexscreener", "candidate_discovery", "error", str(exc))
        return stats

    if not candidate_sources:
        db.log_api_event(
            conn,
            observed_at,
            "dexscreener",
            "candidate_discovery",
            "empty",
            f"no {settings.dex_chain} token profiles/boosts/search results found",
        )
        return stats

    try:
        pairs = dex.fetch_pairs_for_tokens(settings.dex_chain, list(candidate_sources))
        stats["pairs"] = len(pairs)
    except ApiError as exc:
        stats["api_errors"] += 1
        db.log_api_event(conn, observed_at, "dexscreener", "/tokens/v1", "error", str(exc))
        return stats

    metrics_rows = choose_best_pairs(pairs, candidate_sources, observed_at, now)
    delayed_entry_at = iso_utc(now + timedelta(minutes=settings.delayed_entry_minutes))

    for metrics in metrics_rows:
        snapshot_id = db.insert_dex_snapshot(conn, metrics)
        stats["snapshots"] += 1
        security = goplus.token_security(settings.goplus_chain_id, metrics["token_address"])
        db.insert_security_snapshot(
            conn,
            observed_at,
            settings.goplus_chain_id,
            metrics["token_address"],
            security,
        )
        previous = db.latest_dex_snapshot_before(conn, metrics["pair_address"], observed_at)
        evaluation = evaluate_candidate(metrics, security, previous, settings)
        db.insert_candidate_evaluation(conn, observed_at, metrics, evaluation, security.status)
        db.insert_v2_scan_records(conn, observed_at, metrics, security, evaluation)
        if evaluation.accepted:
            stats["accepted_candidates"] += 1
            for signal_type in evaluation.signal_types:
                inserted = db.insert_signal(
                    conn,
                    metrics,
                    signal_type,
                    settings.filter_version,
                    delayed_entry_at,
                    snapshot_id,
                )
                if inserted:
                    stats["new_signals"] += 1
        else:
            stats["rejected_candidates"] += 1

    return stats
