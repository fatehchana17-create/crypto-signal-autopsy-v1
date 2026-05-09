from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from statistics import mean, median
from typing import Any


MIN_TOKENS_TRADED_FOR_SMART = 20
MIN_DAYS_HISTORY_FOR_SMART = 30
MIN_REALIZED_EXITS_FOR_SMART = 10
LOW_LIQUIDITY_USD = 10_000


@dataclass(frozen=True)
class WalletMetrics:
    tokens_traded: int = 0
    realized_exits: int = 0
    win_rate: float = 0.0
    median_realized_return_pct: float = 0.0
    avg_realized_return_pct: float = 0.0
    total_realized_pnl_usd: float = 0.0
    rug_exposure_rate: float = 1.0
    low_liquidity_trade_rate: float = 1.0
    quick_dump_rate: float = 0.0
    first_minute_entry_rate: float = 0.0
    avg_hold_time_minutes: float = 0.0
    biggest_win_contribution_pct: float = 0.0
    suspicious_behavior_score: float = 0.0
    deployer_link_detected: bool = False
    lp_wallet_link_detected: bool = False
    repeated_buy_sell_loop_count: int = 0
    successful_early_entries: int = 0
    profitable_tokens: int = 0
    realized_exit_rate: float = 0.0
    history_days: float = 0.0


def run_wallet_scoring(conn: sqlite3.Connection) -> dict[str, int]:
    rebuild_wallet_performance(conn)
    scored = score_all_wallets(conn)
    return {"wallets_scored": scored}


def rebuild_wallet_performance(conn: sqlite3.Connection) -> int:
    groups = conn.execute(
        """
        SELECT wallet_address, token_address, chain
        FROM wallet_trades
        WHERE wallet_address IS NOT NULL AND token_address IS NOT NULL
        GROUP BY wallet_address, token_address, chain
        """
    ).fetchall()
    updated = 0
    now = _utc_now()
    for group in groups:
        trades = conn.execute(
            """
            SELECT *
            FROM wallet_trades
            WHERE wallet_address = ? AND token_address = ? AND chain = ?
            ORDER BY trade_time ASC
            """,
            (group["wallet_address"], group["token_address"], group["chain"]),
        ).fetchall()
        row = _performance_row(trades, now)
        if row is None:
            continue
        conn.execute(
            """
            INSERT INTO wallet_performance (
              wallet_address, token_address, chain, first_buy_time, avg_buy_price,
              total_buy_usd, first_sell_time, avg_sell_price, total_sell_usd,
              realized_pnl_usd, realized_return_pct, max_unrealized_return_pct,
              max_drawdown_pct, hold_time_minutes, rugged_after_entry,
              became_illiquid_after_entry, volume_disappeared_after_entry, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet_address, token_address, chain) DO UPDATE SET
              first_buy_time = excluded.first_buy_time,
              avg_buy_price = excluded.avg_buy_price,
              total_buy_usd = excluded.total_buy_usd,
              first_sell_time = excluded.first_sell_time,
              avg_sell_price = excluded.avg_sell_price,
              total_sell_usd = excluded.total_sell_usd,
              realized_pnl_usd = excluded.realized_pnl_usd,
              realized_return_pct = excluded.realized_return_pct,
              max_unrealized_return_pct = excluded.max_unrealized_return_pct,
              max_drawdown_pct = excluded.max_drawdown_pct,
              hold_time_minutes = excluded.hold_time_minutes,
              rugged_after_entry = excluded.rugged_after_entry,
              became_illiquid_after_entry = excluded.became_illiquid_after_entry,
              volume_disappeared_after_entry = excluded.volume_disappeared_after_entry,
              updated_at = excluded.updated_at
            """,
            row,
        )
        updated += 1
    conn.commit()
    return updated


def score_all_wallets(conn: sqlite3.Connection) -> int:
    wallets = conn.execute(
        """
        SELECT wallet_address, chain, MIN(trade_time) AS first_seen_at, MAX(trade_time) AS last_seen_at
        FROM wallet_trades
        WHERE wallet_address IS NOT NULL
        GROUP BY wallet_address, chain
        """
    ).fetchall()
    now = _utc_now()
    for wallet in wallets:
        metrics = calculate_wallet_metrics(conn, wallet["wallet_address"], wallet["chain"])
        quality_score = calculate_wallet_quality_score(metrics)
        risk_score = calculate_wallet_risk_score(metrics)
        assert 0 <= quality_score <= 100
        assert 0 <= risk_score <= 100
        label = label_wallet(metrics, quality_score, risk_score)
        conn.execute(
            """
            INSERT INTO wallets (
              wallet_address, chain, first_seen_at, last_seen_at, wallet_label,
              wallet_quality_score, wallet_risk_score, tokens_traded, realized_exits,
              win_rate, median_realized_return_pct, avg_realized_return_pct,
              total_realized_pnl_usd, rug_exposure_rate, low_liquidity_trade_rate,
              quick_dump_rate, first_minute_entry_rate, avg_hold_time_minutes,
              biggest_win_contribution_pct, suspicious_behavior_score, notes, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet_address) DO UPDATE SET
              chain = excluded.chain,
              first_seen_at = COALESCE(wallets.first_seen_at, excluded.first_seen_at),
              last_seen_at = excluded.last_seen_at,
              wallet_label = excluded.wallet_label,
              wallet_quality_score = excluded.wallet_quality_score,
              wallet_risk_score = excluded.wallet_risk_score,
              tokens_traded = excluded.tokens_traded,
              realized_exits = excluded.realized_exits,
              win_rate = excluded.win_rate,
              median_realized_return_pct = excluded.median_realized_return_pct,
              avg_realized_return_pct = excluded.avg_realized_return_pct,
              total_realized_pnl_usd = excluded.total_realized_pnl_usd,
              rug_exposure_rate = excluded.rug_exposure_rate,
              low_liquidity_trade_rate = excluded.low_liquidity_trade_rate,
              quick_dump_rate = excluded.quick_dump_rate,
              first_minute_entry_rate = excluded.first_minute_entry_rate,
              avg_hold_time_minutes = excluded.avg_hold_time_minutes,
              biggest_win_contribution_pct = excluded.biggest_win_contribution_pct,
              suspicious_behavior_score = excluded.suspicious_behavior_score,
              notes = excluded.notes,
              updated_at = excluded.updated_at
            """,
            (
                wallet["wallet_address"],
                wallet["chain"],
                wallet["first_seen_at"],
                wallet["last_seen_at"],
                label,
                quality_score,
                risk_score,
                metrics.tokens_traded,
                metrics.realized_exits,
                metrics.win_rate,
                metrics.median_realized_return_pct,
                metrics.avg_realized_return_pct,
                metrics.total_realized_pnl_usd,
                metrics.rug_exposure_rate,
                metrics.low_liquidity_trade_rate,
                metrics.quick_dump_rate,
                metrics.first_minute_entry_rate,
                metrics.avg_hold_time_minutes,
                metrics.biggest_win_contribution_pct,
                metrics.suspicious_behavior_score,
                "Conservative V3 wallet score. Missing realized history keeps wallets unproven.",
                now,
            ),
        )
    conn.commit()
    return len(wallets)


def calculate_wallet_metrics(conn: sqlite3.Connection, wallet_address: str, chain: str) -> WalletMetrics:
    trades = conn.execute(
        """
        SELECT *
        FROM wallet_trades
        WHERE wallet_address = ? AND chain = ?
        ORDER BY trade_time ASC
        """,
        (wallet_address, chain),
    ).fetchall()
    if not trades:
        return WalletMetrics()

    performances = conn.execute(
        """
        SELECT *
        FROM wallet_performance
        WHERE wallet_address = ? AND chain = ?
        """,
        (wallet_address, chain),
    ).fetchall()
    tokens_traded = len({row["token_address"] for row in trades if row["token_address"]})
    realized = [row for row in performances if row["realized_return_pct"] is not None]
    realized_returns = [float(row["realized_return_pct"]) for row in realized]
    realized_exits = len(realized)
    profitable_tokens = sum(1 for value in realized_returns if value > 0)
    win_rate = profitable_tokens / realized_exits if realized_exits else 0.0
    realized_exit_rate = realized_exits / tokens_traded if tokens_traded else 0.0
    total_pnl = sum(float(row["realized_pnl_usd"] or 0) for row in realized)
    positive_pnl = [float(row["realized_pnl_usd"] or 0) for row in realized if (row["realized_pnl_usd"] or 0) > 0]
    biggest_win_contribution = (max(positive_pnl) / sum(positive_pnl) * 100) if positive_pnl and sum(positive_pnl) else 0.0
    hold_times = [float(row["hold_time_minutes"]) for row in realized if row["hold_time_minutes"] is not None]
    quick_dumps = sum(1 for row in realized if (row["hold_time_minutes"] or 0) < 30)
    successful_early_entries = sum(
        1
        for row in realized
        if (row["realized_return_pct"] or 0) >= 20 and _first_buy_pair_age(conn, wallet_address, row["token_address"]) <= 30
    )
    rugged_tokens = _exposure_rate(conn, wallet_address, chain, "rugged")
    low_liquidity_rate = _low_liquidity_trade_rate(trades)
    first_minute_rate = _first_minute_entry_rate(trades)
    history_days = _history_days([row["trade_time"] for row in trades])
    quick_dump_rate = quick_dumps / realized_exits if realized_exits else 0.0
    repeated_loops = _repeated_buy_sell_loop_count(trades)
    suspicious_behavior_score = min(
        100.0,
        first_minute_rate * 30
        + quick_dump_rate * 30
        + rugged_tokens * 30
        + low_liquidity_rate * 10
        + min(repeated_loops * 3, 15),
    )
    return WalletMetrics(
        tokens_traded=tokens_traded,
        realized_exits=realized_exits,
        win_rate=win_rate,
        median_realized_return_pct=median(realized_returns) if realized_returns else 0.0,
        avg_realized_return_pct=mean(realized_returns) if realized_returns else 0.0,
        total_realized_pnl_usd=total_pnl,
        rug_exposure_rate=rugged_tokens,
        low_liquidity_trade_rate=low_liquidity_rate,
        quick_dump_rate=quick_dump_rate,
        first_minute_entry_rate=first_minute_rate,
        avg_hold_time_minutes=mean(hold_times) if hold_times else 0.0,
        biggest_win_contribution_pct=biggest_win_contribution,
        suspicious_behavior_score=suspicious_behavior_score,
        repeated_buy_sell_loop_count=repeated_loops,
        successful_early_entries=successful_early_entries,
        profitable_tokens=profitable_tokens,
        realized_exit_rate=realized_exit_rate,
        history_days=history_days,
    )


def calculate_wallet_quality_score(metrics: WalletMetrics) -> float:
    score = 0

    if metrics.tokens_traded >= 50:
        score += 10
    elif metrics.tokens_traded >= 20:
        score += 7
    elif metrics.tokens_traded >= 10:
        score += 4
    elif metrics.tokens_traded >= 5:
        score += 2

    if metrics.win_rate >= 0.70:
        score += 15
    elif metrics.win_rate >= 0.60:
        score += 12
    elif metrics.win_rate >= 0.50:
        score += 8
    elif metrics.win_rate >= 0.40:
        score += 4

    if metrics.median_realized_return_pct >= 100:
        score += 15
    elif metrics.median_realized_return_pct >= 50:
        score += 12
    elif metrics.median_realized_return_pct >= 20:
        score += 8
    elif metrics.median_realized_return_pct >= 5:
        score += 4

    if metrics.profitable_tokens >= 20:
        score += 10
    elif metrics.profitable_tokens >= 10:
        score += 7
    elif metrics.profitable_tokens >= 5:
        score += 4

    if metrics.successful_early_entries >= 15:
        score += 15
    elif metrics.successful_early_entries >= 8:
        score += 10
    elif metrics.successful_early_entries >= 3:
        score += 5

    if metrics.realized_exit_rate >= 0.70:
        score += 10
    elif metrics.realized_exit_rate >= 0.50:
        score += 7
    elif metrics.realized_exit_rate >= 0.30:
        score += 4

    if metrics.rug_exposure_rate <= 0.05:
        score += 10
    elif metrics.rug_exposure_rate <= 0.10:
        score += 7
    elif metrics.rug_exposure_rate <= 0.20:
        score += 4

    if metrics.low_liquidity_trade_rate <= 0.10:
        score += 10
    elif metrics.low_liquidity_trade_rate <= 0.25:
        score += 6
    elif metrics.low_liquidity_trade_rate <= 0.40:
        score += 3

    if metrics.suspicious_behavior_score <= 10:
        score += 5
    elif metrics.suspicious_behavior_score <= 25:
        score += 3

    return min(float(score), 100.0)


def calculate_wallet_risk_score(metrics: WalletMetrics) -> float:
    score = 0

    if metrics.first_minute_entry_rate >= 0.70:
        score += 20
    elif metrics.first_minute_entry_rate >= 0.40:
        score += 12
    elif metrics.first_minute_entry_rate >= 0.20:
        score += 6

    if metrics.deployer_link_detected:
        score += 25

    if metrics.lp_wallet_link_detected:
        score += 20

    if metrics.avg_hold_time_minutes < 5:
        score += 15
    elif metrics.avg_hold_time_minutes < 30:
        score += 8

    if metrics.repeated_buy_sell_loop_count >= 10:
        score += 15
    elif metrics.repeated_buy_sell_loop_count >= 5:
        score += 8

    if metrics.quick_dump_rate >= 0.50:
        score += 15
    elif metrics.quick_dump_rate >= 0.25:
        score += 8

    if metrics.tokens_traded >= 10 and metrics.biggest_win_contribution_pct >= 80:
        score += 10

    if metrics.rug_exposure_rate >= 0.40:
        score += 15
    elif metrics.rug_exposure_rate >= 0.20:
        score += 8

    if metrics.low_liquidity_trade_rate >= 0.70:
        score += 10
    elif metrics.low_liquidity_trade_rate >= 0.40:
        score += 5

    if metrics.suspicious_behavior_score >= 75:
        score += 10
    elif metrics.suspicious_behavior_score >= 50:
        score += 5

    return min(float(score), 100.0)


def label_wallet(metrics: WalletMetrics, quality_score: float, risk_score: float) -> str:
    if metrics.tokens_traded < 5:
        return "Unproven Wallet"

    if risk_score >= 75:
        return "Suspicious Wallet"

    if metrics.quick_dump_rate >= 0.50 and risk_score >= 60:
        return "Dump Wallet"

    if (
        quality_score >= 75
        and risk_score < 35
        and metrics.tokens_traded >= MIN_TOKENS_TRADED_FOR_SMART
        and metrics.realized_exits >= MIN_REALIZED_EXITS_FOR_SMART
        and metrics.history_days >= MIN_DAYS_HISTORY_FOR_SMART
    ):
        return "Smart Wallet"

    if quality_score >= 60 and risk_score < 55:
        return "Useful Wallet"

    if quality_score >= 50 and risk_score >= 55:
        return "High-Risk Wallet"

    return "Unproven Wallet"


def _performance_row(trades: list[sqlite3.Row], updated_at: str) -> tuple[Any, ...] | None:
    buys = [row for row in trades if row["side"] == "buy"]
    sells = [row for row in trades if row["side"] == "sell"]
    if not buys:
        return None
    total_buy_usd = sum(float(row["amount_usd"] or 0) for row in buys)
    total_buy_tokens = sum(float(row["token_amount"] or 0) for row in buys)
    total_sell_usd = sum(float(row["amount_usd"] or 0) for row in sells)
    total_sell_tokens = sum(float(row["token_amount"] or 0) for row in sells)
    avg_buy_price = _weighted_price(total_buy_usd, total_buy_tokens, buys)
    avg_sell_price = _weighted_price(total_sell_usd, total_sell_tokens, sells) if sells else None
    first_buy_time = buys[0]["trade_time"]
    first_sell_time = sells[0]["trade_time"] if sells else None
    realized_pnl = None
    realized_return = None
    hold_time = None
    if sells and total_buy_usd:
        realized_pnl = total_sell_usd - total_buy_usd
        realized_return = realized_pnl / total_buy_usd * 100
        hold_time = _minutes_between(first_buy_time, first_sell_time)
    token_address = buys[0]["token_address"]
    wallet_address = buys[0]["wallet_address"]
    chain = buys[0]["chain"]
    return (
        wallet_address,
        token_address,
        chain,
        first_buy_time,
        avg_buy_price,
        total_buy_usd,
        first_sell_time,
        avg_sell_price,
        total_sell_usd if sells else None,
        realized_pnl,
        realized_return,
        None,
        None,
        hold_time,
        0,
        0,
        0,
        updated_at,
    )


def _weighted_price(total_usd: float, total_tokens: float, rows: list[sqlite3.Row]) -> float | None:
    if total_usd and total_tokens:
        return total_usd / total_tokens
    prices = [float(row["price_usd"]) for row in rows if row["price_usd"] is not None]
    return mean(prices) if prices else None


def _exposure_rate(conn: sqlite3.Connection, wallet_address: str, chain: str, field: str) -> float:
    rows = conn.execute(
        f"""
        SELECT DISTINCT wt.token_address
        FROM wallet_trades wt
        JOIN outcome_snapshots o ON o.token_address = wt.token_address
        WHERE wt.wallet_address = ? AND wt.chain = ? AND o.{field} = 1
        """,
        (wallet_address, chain),
    ).fetchall()
    tokens = conn.execute(
        """
        SELECT COUNT(DISTINCT token_address) AS count
        FROM wallet_trades
        WHERE wallet_address = ? AND chain = ?
        """,
        (wallet_address, chain),
    ).fetchone()["count"]
    if not tokens:
        return 1.0
    return len(rows) / int(tokens)


def _low_liquidity_trade_rate(trades: list[sqlite3.Row]) -> float:
    if not trades:
        return 1.0
    low = sum(1 for row in trades if row["liquidity_usd_at_trade"] is None or float(row["liquidity_usd_at_trade"]) < LOW_LIQUIDITY_USD)
    return low / len(trades)


def _first_minute_entry_rate(trades: list[sqlite3.Row]) -> float:
    buys = [row for row in trades if row["side"] == "buy"]
    if not buys:
        return 0.0
    first_minute = sum(1 for row in buys if row["pair_age_minutes_at_trade"] is not None and float(row["pair_age_minutes_at_trade"]) <= 1)
    return first_minute / len(buys)


def _repeated_buy_sell_loop_count(trades: list[sqlite3.Row]) -> int:
    loops = 0
    previous_side = None
    for row in sorted(trades, key=lambda item: item["trade_time"] or ""):
        side = row["side"]
        if side not in {"buy", "sell"}:
            continue
        if previous_side and side != previous_side:
            loops += 1
        previous_side = side
    return loops


def _first_buy_pair_age(conn: sqlite3.Connection, wallet_address: str, token_address: str) -> float:
    row = conn.execute(
        """
        SELECT pair_age_minutes_at_trade
        FROM wallet_trades
        WHERE wallet_address = ? AND token_address = ? AND side = 'buy'
        ORDER BY trade_time ASC
        LIMIT 1
        """,
        (wallet_address, token_address),
    ).fetchone()
    if row is None or row["pair_age_minutes_at_trade"] is None:
        return 999999.0
    return float(row["pair_age_minutes_at_trade"])


def _history_days(times: list[str]) -> float:
    parsed = [_parse_time(value) for value in times if value]
    parsed = [value for value in parsed if value is not None]
    if len(parsed) < 2:
        return 0.0
    return max((max(parsed) - min(parsed)).total_seconds() / 86400, 0.0)


def _minutes_between(start: str, end: str | None) -> float | None:
    if not end:
        return None
    start_dt = _parse_time(start)
    end_dt = _parse_time(end)
    if not start_dt or not end_dt:
        return None
    return max((end_dt - start_dt).total_seconds() / 60, 0.0)


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
