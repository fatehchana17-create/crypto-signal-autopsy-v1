from __future__ import annotations

from datetime import timedelta
import sqlite3

from crypto_signal_autopsy import db
from crypto_signal_autopsy.api_moralis import MoralisWalletProvider, WalletDataProvider, WalletTrade
from crypto_signal_autopsy.clients.http import ApiError, JsonHttpClient
from crypto_signal_autopsy.config import Settings
from crypto_signal_autopsy.timeutils import iso_utc, utc_now
from crypto_signal_autopsy.wallet_scoring import run_wallet_scoring
from crypto_signal_autopsy.wallet_signals import run_wallet_signals


def run_wallet_module(conn: sqlite3.Connection, settings: Settings) -> dict[str, int]:
    db.init_db(conn)
    stats = {
        "wallet_module_enabled": 1 if settings.wallet_module_enabled else 0,
        "wallet_pairs_checked": 0,
        "wallet_trades_seen": 0,
        "wallet_trades_inserted": 0,
        "wallets_scored": 0,
        "wallet_token_signals": 0,
        "wallet_clusters": 0,
        "api_errors": 0,
    }
    if not _provider_is_configured(settings):
        _log_status(conn, "disabled", _inactive_message(settings))
        return stats

    http = JsonHttpClient(settings.request_timeout_seconds, settings.user_agent)
    provider = _build_provider(settings, http)
    if provider is None:
        _log_status(conn, "disabled", _inactive_message(settings))
        return stats

    discovery_stats = discover_wallets_for_scanned_pairs(conn, settings, provider)
    stats.update(discovery_stats)
    score_stats = run_wallet_scoring(conn)
    signal_stats = run_wallet_signals(conn)
    stats["wallets_scored"] = score_stats["wallets_scored"]
    stats["wallet_token_signals"] = signal_stats["wallet_token_signals"]
    stats["wallet_clusters"] = signal_stats["wallet_clusters"]
    return stats


def discover_wallets_for_scanned_pairs(
    conn: sqlite3.Connection,
    settings: Settings,
    provider: WalletDataProvider,
) -> dict[str, int]:
    stats = {
        "wallet_pairs_checked": 0,
        "wallet_trades_seen": 0,
        "wallet_trades_inserted": 0,
        "api_errors": 0,
    }
    from_date = iso_utc(utc_now() - timedelta(days=max(settings.wallet_history_days, 1)))
    rows = conn.execute(
        """
        SELECT p.*
        FROM pairs p
        WHERE p.chain = ? AND p.pair_address IS NOT NULL
        ORDER BY p.scan_time DESC
        LIMIT ?
        """,
        (settings.dex_chain, max(settings.wallet_pair_limit, 1)),
    ).fetchall()
    for pair in rows:
        stats["wallet_pairs_checked"] += 1
        try:
            trades = provider.fetch_pair_trades(
                chain=pair["chain"],
                pair_address=pair["pair_address"],
                from_date=from_date,
                limit=settings.wallet_trade_limit,
            )
        except (ApiError, ValueError, NotImplementedError) as exc:
            stats["api_errors"] += 1
            _log_api_error(conn, provider.provider_name, "fetch_pair_trades", pair["pair_address"], str(exc))
            continue
        for trade in trades:
            stats["wallet_trades_seen"] += 1
            if insert_wallet_trade(conn, pair, trade):
                stats["wallet_trades_inserted"] += 1
    conn.commit()
    return stats


def insert_wallet_trade(conn: sqlite3.Connection, pair: sqlite3.Row, trade: WalletTrade) -> bool:
    if not trade.wallet_address or trade.side not in {"buy", "sell"} or not trade.trade_time:
        return False
    wallet_address = trade.wallet_address.lower()
    now = utc_now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO wallets (
          wallet_address, chain, first_seen_at, last_seen_at, wallet_label,
          wallet_quality_score, wallet_risk_score, tokens_traded, realized_exits,
          notes, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(wallet_address) DO UPDATE SET
          last_seen_at = MAX(wallets.last_seen_at, excluded.last_seen_at),
          wallet_label = COALESCE(wallets.wallet_label, excluded.wallet_label),
          updated_at = excluded.updated_at
        """,
        (
            wallet_address,
            pair["chain"],
            trade.trade_time,
            trade.trade_time,
            "Unproven Wallet",
            0,
            0,
            0,
            0,
            "New wallet discovered from scanned token trade history.",
            now,
        ),
    )
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO wallet_trades (
          wallet_address, token_address, pair_id, chain, trade_time, side,
          amount_usd, token_amount, price_usd, liquidity_usd_at_trade,
          pair_age_minutes_at_trade, tx_hash, source, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            wallet_address,
            pair["token_address"],
            pair["pair_id"],
            pair["chain"],
            trade.trade_time,
            trade.side,
            trade.amount_usd,
            trade.token_amount,
            trade.price_usd,
            pair["liquidity_usd"],
            pair["pair_age_minutes"],
            trade.tx_hash,
            trade.source,
            db.to_json(trade.raw_json),
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def _build_provider(settings: Settings, http: JsonHttpClient | None = None) -> WalletDataProvider | None:
    if not settings.wallet_module_enabled:
        return None
    if settings.wallet_provider == "moralis":
        if not settings.moralis_api_key or http is None:
            return None
        return MoralisWalletProvider(http, settings.moralis_api_key)
    return None


def _provider_is_configured(settings: Settings) -> bool:
    if not settings.wallet_module_enabled:
        return False
    if settings.wallet_provider == "moralis":
        return bool(settings.moralis_api_key)
    if settings.wallet_provider == "birdeye":
        return bool(settings.birdeye_api_key)
    return False


def _inactive_message(settings: Settings) -> str:
    if not settings.wallet_module_enabled:
        return "Wallet module inactive: disabled by CSA_WALLET_MODULE_ENABLED."
    if settings.wallet_provider == "moralis" and not settings.moralis_api_key:
        return "Wallet module inactive: missing MORALIS_API_KEY."
    if settings.wallet_provider == "birdeye" and not settings.birdeye_api_key:
        return "Wallet module inactive: missing BIRDEYE_API_KEY."
    return f"Wallet module inactive: provider {settings.wallet_provider!r} is not implemented."


def _log_status(conn: sqlite3.Connection, status: str, message: str) -> None:
    db.log_api_event(conn, utc_now().isoformat(timespec="seconds"), "wallet_module", "status", status, message)


def _log_api_error(
    conn: sqlite3.Connection,
    api_name: str,
    endpoint: str,
    related_address: str | None,
    error_message: str,
) -> None:
    now = utc_now().isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO api_errors (api_name, endpoint, related_address, error_message, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (api_name, endpoint, related_address, error_message, now),
    )
    db.log_api_event(conn, now, api_name, endpoint, "error", error_message, related_address)
