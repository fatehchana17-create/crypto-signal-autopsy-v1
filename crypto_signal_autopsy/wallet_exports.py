from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

from crypto_signal_autopsy.config import Settings


WALLET_TABLE_EXPORTS = {
    "wallets": "wallets",
    "wallet_trades": "wallet_trades",
    "wallet_performance": "wallet_performance",
    "wallet_token_signals": "wallet_token_signals",
    "wallet_clusters": "wallet_clusters",
}

WALLET_LEADERBOARD_HEADERS = [
    "wallet_address",
    "chain",
    "wallet_label",
    "wallet_quality_score",
    "wallet_risk_score",
    "tokens_traded",
    "win_rate",
    "median_realized_return_pct",
    "avg_realized_return_pct",
    "rug_exposure_rate",
    "quick_dump_rate",
    "avg_hold_time_minutes",
    "last_seen_at",
]

SUSPICIOUS_WALLET_HEADERS = [
    "wallet_address",
    "chain",
    "wallet_label",
    "wallet_quality_score",
    "wallet_risk_score",
    "tokens_traded",
    "rug_exposure_rate",
    "quick_dump_rate",
    "first_minute_entry_rate",
    "biggest_win_contribution_pct",
    "suspicious_behavior_score",
    "notes",
    "last_seen_at",
]

WALLET_ACTIVITY_HEADERS = [
    "trade_time",
    "wallet_address",
    "wallet_label",
    "side",
    "token",
    "amount_usd",
    "pair_age_minutes_at_trade",
    "liquidity_usd_at_trade",
    "token_risk_score",
    "token_opportunity_score",
    "wallet_signal_score",
]


def export_wallet_data(conn: sqlite3.Connection, export_dir: Path) -> dict[str, int]:
    export_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, table in WALLET_TABLE_EXPORTS.items():
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        _write_csv(export_dir / f"{name}.csv", rows, [item["name"] for item in conn.execute(f"PRAGMA table_info({table})")])
        counts[name] = len(rows)
    counts["wallet_leaderboard"] = _export_wallet_leaderboard(conn, export_dir / "wallet_leaderboard.csv")
    counts["suspicious_wallets"] = _export_suspicious_wallets(conn, export_dir / "suspicious_wallets.csv")
    counts["wallet_activity_feed"] = _export_wallet_activity_feed(conn, export_dir / "wallet_activity_feed.csv")
    _write_wallet_json_data(conn, export_dir / "dashboard" / "data")
    return counts


def build_wallet_dashboard_payload(conn: sqlite3.Connection, settings: Settings) -> dict[str, Any]:
    provider_configured = _provider_configured(settings)
    api_errors = conn.execute(
        "SELECT COUNT(*) AS count FROM api_errors WHERE api_name IN ('moralis', 'birdeye')"
    ).fetchone()["count"]
    wallets_tracked = conn.execute("SELECT COUNT(*) AS count FROM wallets").fetchone()["count"]
    trades_tracked = conn.execute("SELECT COUNT(*) AS count FROM wallet_trades").fetchone()["count"]
    label_counts = {
        row["wallet_label"] or "Unproven Wallet": int(row["count"])
        for row in conn.execute("SELECT wallet_label, COUNT(*) AS count FROM wallets GROUP BY wallet_label")
    }
    last_trade = conn.execute("SELECT MAX(trade_time) AS value FROM wallet_trades").fetchone()["value"]
    status = "Enabled" if settings.wallet_module_enabled and provider_configured else "Disabled"
    warning = ""
    if not settings.wallet_module_enabled:
        warning = "Wallet module inactive: disabled by configuration."
    elif not provider_configured:
        warning = f"Wallet module inactive: missing {settings.wallet_provider.upper()} API key."
    elif not trades_tracked:
        warning = "Wallet module has not collected enough data yet."

    return {
        "status": {
            "enabled": status,
            "active_provider": settings.wallet_provider,
            "last_wallet_scan": last_trade or "No wallet scan yet",
            "wallet_api_errors": str(api_errors),
            "wallets_tracked": str(wallets_tracked),
            "trades_tracked": str(trades_tracked),
            "smart_wallets": str(label_counts.get("Smart Wallet", 0)),
            "useful_wallets": str(label_counts.get("Useful Wallet", 0)),
            "suspicious_wallets": str(label_counts.get("Suspicious Wallet", 0)),
            "warning": warning,
        },
        "leaderboard": _leaderboard_rows(conn),
        "activity_feed": _activity_feed_rows(conn),
        "token_signals": _token_signal_rows(conn),
        "suspicious_wallets": _suspicious_wallet_rows(conn),
        "clusters": _cluster_rows(conn),
    }


def _export_wallet_leaderboard(conn: sqlite3.Connection, out_path: Path) -> int:
    rows = conn.execute(_wallet_leaderboard_sql()).fetchall()
    _write_csv(out_path, rows, _keys(rows) or WALLET_LEADERBOARD_HEADERS)
    return len(rows)


def _export_suspicious_wallets(conn: sqlite3.Connection, out_path: Path) -> int:
    rows = conn.execute(_suspicious_wallets_sql()).fetchall()
    _write_csv(out_path, rows, _keys(rows) or SUSPICIOUS_WALLET_HEADERS)
    return len(rows)


def _export_wallet_activity_feed(conn: sqlite3.Connection, out_path: Path) -> int:
    rows = conn.execute(_activity_feed_sql()).fetchall()
    _write_csv(out_path, rows, _keys(rows) or WALLET_ACTIVITY_HEADERS)
    return len(rows)


def _write_wallet_json_data(conn: sqlite3.Connection, data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "wallets.json": _rows_to_dicts(conn.execute(_wallet_leaderboard_sql()).fetchall()),
        "wallet_token_signals.json": _rows_to_dicts(conn.execute(_token_signals_sql()).fetchall()),
        "wallet_leaderboard.json": _rows_to_dicts(conn.execute(_wallet_leaderboard_sql()).fetchall()),
        "suspicious_wallets.json": _rows_to_dicts(conn.execute(_suspicious_wallets_sql()).fetchall()),
        "wallet_activity_feed.json": _rows_to_dicts(conn.execute(_activity_feed_sql()).fetchall()),
    }
    for filename, rows in payloads.items():
        (data_dir / filename).write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")


def write_public_wallet_json_data(conn: sqlite3.Connection, data_dir: Path) -> None:
    _write_wallet_json_data(conn, data_dir)


def _leaderboard_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    return [_format_leaderboard_row(row) for row in conn.execute(_wallet_leaderboard_sql()).fetchall()]


def _activity_feed_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    return [_format_activity_row(row) for row in conn.execute(_activity_feed_sql()).fetchall()]


def _token_signal_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    return [_format_token_signal_row(row) for row in conn.execute(_token_signals_sql()).fetchall()]


def _suspicious_wallet_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    return [_format_suspicious_row(row) for row in conn.execute(_suspicious_wallets_sql()).fetchall()]


def _cluster_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    return [_format_cluster_row(row) for row in conn.execute(_clusters_sql()).fetchall()]


def _wallet_leaderboard_sql() -> str:
    return """
        SELECT wallet_address, chain, wallet_label, wallet_quality_score, wallet_risk_score,
               tokens_traded, win_rate, median_realized_return_pct, avg_realized_return_pct,
               rug_exposure_rate, quick_dump_rate, avg_hold_time_minutes, last_seen_at
        FROM wallets
        ORDER BY
          CASE wallet_label
            WHEN 'Smart Wallet' THEN 1
            WHEN 'Useful Wallet' THEN 2
            WHEN 'High-Risk Wallet' THEN 3
            WHEN 'Suspicious Wallet' THEN 4
            WHEN 'Dump Wallet' THEN 5
            ELSE 6
          END,
          wallet_quality_score DESC,
          wallet_risk_score ASC,
          tokens_traded DESC
        LIMIT 200
    """


def _activity_feed_sql() -> str:
    return """
        SELECT wt.trade_time, wt.wallet_address, COALESCE(w.wallet_label, 'Unproven Wallet') AS wallet_label,
               wt.side, COALESCE(t.symbol, wt.token_address) AS token, wt.amount_usd,
               wt.pair_age_minutes_at_trade, wt.liquidity_usd_at_trade,
               f.risk_score AS token_risk_score, f.opportunity_score AS token_opportunity_score,
               ws.wallet_signal_score
        FROM wallet_trades wt
        LEFT JOIN wallets w ON w.wallet_address = wt.wallet_address
        LEFT JOIN tokens t ON t.token_address = wt.token_address
        LEFT JOIN filter_results f ON f.pair_id = wt.pair_id
        LEFT JOIN wallet_token_signals ws ON ws.pair_id = wt.pair_id
        ORDER BY wt.trade_time DESC
        LIMIT 200
    """


def _token_signals_sql() -> str:
    return """
        SELECT COALESCE(t.symbol, ws.token_address) AS token, ws.chain,
               ws.smart_wallet_count, ws.useful_wallet_count, ws.suspicious_wallet_count,
               ws.net_wallet_flow_usd, ws.wallet_signal_score, ws.wallet_signal_label,
               f.final_label AS token_final_label, f.risk_score, f.opportunity_score
        FROM wallet_token_signals ws
        LEFT JOIN tokens t ON t.token_address = ws.token_address
        LEFT JOIN filter_results f ON f.pair_id = ws.pair_id AND f.scan_time = ws.scan_time
        ORDER BY ws.wallet_signal_score DESC, ws.net_wallet_flow_usd DESC
        LIMIT 200
    """


def _suspicious_wallets_sql() -> str:
    return """
        SELECT wallet_address, chain, wallet_label, wallet_quality_score, wallet_risk_score,
               tokens_traded, rug_exposure_rate, quick_dump_rate, first_minute_entry_rate,
               biggest_win_contribution_pct, suspicious_behavior_score, notes, last_seen_at
        FROM wallets
        WHERE wallet_label IN ('Suspicious Wallet', 'Dump Wallet', 'High-Risk Wallet')
           OR wallet_risk_score >= 55
           OR quick_dump_rate >= 0.25
           OR rug_exposure_rate >= 0.20
           OR suspicious_behavior_score >= 50
        ORDER BY wallet_risk_score DESC, suspicious_behavior_score DESC
        LIMIT 200
    """


def _clusters_sql() -> str:
    return """
        SELECT COALESCE(t.symbol, wc.token_address) AS token, wc.cluster_type, wc.wallet_count,
               wc.total_buy_usd, wc.average_wallet_quality_score, wc.average_wallet_risk_score,
               wc.scan_time, wc.notes
        FROM wallet_clusters wc
        LEFT JOIN tokens t ON t.token_address = wc.token_address
        ORDER BY wc.scan_time DESC, wc.wallet_count DESC
        LIMIT 200
    """


def _format_leaderboard_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        "wallet": _short_wallet(row["wallet_address"]),
        "wallet_full": row["wallet_address"] or "",
        "chain": row["chain"] or "",
        "label": row["wallet_label"] or "Unproven Wallet",
        "quality": _fmt(row["wallet_quality_score"]),
        "risk": _fmt(row["wallet_risk_score"]),
        "tokens": str(row["tokens_traded"] or 0),
        "win_rate": _pct(row["win_rate"]),
        "median_return": _pct(row["median_realized_return_pct"]),
        "average_return": _pct(row["avg_realized_return_pct"]),
        "rug_exposure": _pct_ratio(row["rug_exposure_rate"]),
        "quick_dump": _pct_ratio(row["quick_dump_rate"]),
        "avg_hold": _minutes(row["avg_hold_time_minutes"]),
        "last_active": row["last_seen_at"] or "",
    }


def _format_activity_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        "time": row["trade_time"] or "",
        "wallet": _short_wallet(row["wallet_address"]),
        "wallet_label": row["wallet_label"] or "Unproven Wallet",
        "side": row["side"] or "",
        "token": row["token"] or "",
        "amount": _money(row["amount_usd"]),
        "pair_age": _minutes(row["pair_age_minutes_at_trade"]),
        "liquidity": _money(row["liquidity_usd_at_trade"]),
        "token_risk": _fmt(row["token_risk_score"]),
        "token_opportunity": _fmt(row["token_opportunity_score"]),
        "wallet_signal": _fmt(row["wallet_signal_score"]),
    }


def _format_token_signal_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        "token": row["token"] or "",
        "chain": row["chain"] or "",
        "smart": str(row["smart_wallet_count"] or 0),
        "useful": str(row["useful_wallet_count"] or 0),
        "suspicious": str(row["suspicious_wallet_count"] or 0),
        "net_flow": _money(row["net_wallet_flow_usd"]),
        "wallet_score": _fmt(row["wallet_signal_score"]),
        "wallet_label": row["wallet_signal_label"] or "",
        "token_label": row["token_final_label"] or "",
        "risk": _fmt(row["risk_score"]),
        "opportunity": _fmt(row["opportunity_score"]),
    }


def _format_suspicious_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        "wallet": _short_wallet(row["wallet_address"]),
        "wallet_full": row["wallet_address"] or "",
        "chain": row["chain"] or "",
        "label": row["wallet_label"] or "Unproven Wallet",
        "quality": _fmt(row["wallet_quality_score"]),
        "risk": _fmt(row["wallet_risk_score"]),
        "tokens": str(row["tokens_traded"] or 0),
        "rug_exposure": _pct_ratio(row["rug_exposure_rate"]),
        "quick_dump": _pct_ratio(row["quick_dump_rate"]),
        "first_minute": _pct_ratio(row["first_minute_entry_rate"]),
        "one_hit": _pct(row["biggest_win_contribution_pct"]),
        "suspicion": _fmt(row["suspicious_behavior_score"]),
        "notes": row["notes"] or "",
        "last_active": row["last_seen_at"] or "",
    }


def _format_cluster_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        "token": row["token"] or "",
        "cluster_type": row["cluster_type"] or "",
        "wallet_count": str(row["wallet_count"] or 0),
        "total_buy": _money(row["total_buy_usd"]),
        "avg_quality": _fmt(row["average_wallet_quality_score"]),
        "avg_risk": _fmt(row["average_wallet_risk_score"]),
        "scan_time": row["scan_time"] or "",
        "notes": row["notes"] or "",
    }


def _write_csv(out_path: Path, rows: list[sqlite3.Row], headers: list[str]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))


def _provider_configured(settings: Settings) -> bool:
    if settings.wallet_provider == "moralis":
        return bool(settings.moralis_api_key)
    if settings.wallet_provider == "birdeye":
        return bool(settings.birdeye_api_key)
    return False


def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _keys(rows: list[sqlite3.Row]) -> list[str]:
    return list(rows[0].keys()) if rows else []


def _short_wallet(value: str | None) -> str:
    if not value:
        return ""
    return f"{value[:6]}...{value[-4:]}" if len(value) > 12 else value


def _money(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"${float(value):,.0f}"


def _fmt(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value):.2f}"


def _pct(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value):.2f}%"


def _pct_ratio(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value) * 100:.2f}%"


def _minutes(value: Any) -> str:
    if value is None or value == "":
        return ""
    number = float(value)
    if number >= 1440:
        return f"{number / 1440:.1f}d"
    if number >= 60:
        return f"{number / 60:.1f}h"
    return f"{number:.0f}m"
