from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from statistics import mean
from typing import Any


WALLET_LABELS = [
    "Smart Wallet",
    "Useful Wallet",
    "High-Risk Wallet",
    "Suspicious Wallet",
    "Dump Wallet",
    "Unproven Wallet",
]


@dataclass(frozen=True)
class WalletSignalMetrics:
    smart_wallet_count: int = 0
    useful_wallet_count: int = 0
    high_risk_wallet_count: int = 0
    suspicious_wallet_count: int = 0
    dump_wallet_count: int = 0
    unproven_wallet_count: int = 0
    total_wallet_buy_usd: float = 0.0
    total_wallet_sell_usd: float = 0.0
    net_wallet_flow_usd: float = 0.0
    smart_or_useful_wallets_entered_within_30m: int = 0
    low_rug_exposure_wallet_count: int = 0
    deployer_linked_wallet_involved: bool = False


def run_wallet_signals(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT DISTINCT wt.token_address, wt.pair_id, wt.chain,
               COALESCE(p.scan_time, fr.scan_time) AS scan_time
        FROM wallet_trades wt
        LEFT JOIN pairs p ON p.pair_id = wt.pair_id
        LEFT JOIN filter_results fr ON fr.pair_id = wt.pair_id
        WHERE wt.pair_id IS NOT NULL
        """
    ).fetchall()
    written = 0
    clusters = 0
    for row in rows:
        if not row["scan_time"]:
            continue
        metrics = calculate_token_wallet_signal(conn, row["token_address"], row["pair_id"])
        score = calculate_wallet_signal_score(metrics)
        assert 0 <= score <= 100
        label = label_wallet_signal(score)
        _upsert_wallet_token_signal(conn, row, metrics, score, label)
        written += 1
        clusters += _upsert_wallet_cluster(conn, row, metrics)
    apply_wallet_signals_to_filter_results(conn)
    conn.commit()
    return {"wallet_token_signals": written, "wallet_clusters": clusters}


def calculate_token_wallet_signal(
    conn: sqlite3.Connection,
    token_address: str,
    pair_id: str,
) -> WalletSignalMetrics:
    trades = conn.execute(
        """
        SELECT *
        FROM wallet_trades
        WHERE token_address = ? AND pair_id = ?
        """,
        (token_address, pair_id),
    ).fetchall()
    if not trades:
        return WalletSignalMetrics()

    wallet_addresses = sorted({row["wallet_address"] for row in trades if row["wallet_address"]})
    if wallet_addresses:
        wallet_rows = conn.execute(
            """
            SELECT *
            FROM wallets
            WHERE wallet_address IN ({})
            """.format(",".join("?" for _ in wallet_addresses)),
            tuple(wallet_addresses),
        ).fetchall()
    else:
        wallet_rows = []
    wallet_map = {row["wallet_address"]: row for row in wallet_rows}
    labels: dict[str, set[str]] = {label: set() for label in WALLET_LABELS}
    low_rug_wallets: set[str] = set()
    smart_or_useful_early: set[str] = set()
    for trade in trades:
        wallet = wallet_map.get(trade["wallet_address"])
        label = wallet["wallet_label"] if wallet else "Unproven Wallet"
        if label not in labels:
            label = "Unproven Wallet"
        labels[label].add(trade["wallet_address"])
        if wallet and wallet["rug_exposure_rate"] is not None and float(wallet["rug_exposure_rate"]) <= 0.10:
            low_rug_wallets.add(trade["wallet_address"])
        if (
            trade["side"] == "buy"
            and label in {"Smart Wallet", "Useful Wallet"}
            and trade["pair_age_minutes_at_trade"] is not None
            and float(trade["pair_age_minutes_at_trade"]) <= 30
        ):
            smart_or_useful_early.add(trade["wallet_address"])

    total_buy = sum(float(row["amount_usd"] or 0) for row in trades if row["side"] == "buy")
    total_sell = sum(float(row["amount_usd"] or 0) for row in trades if row["side"] == "sell")
    return WalletSignalMetrics(
        smart_wallet_count=len(labels["Smart Wallet"]),
        useful_wallet_count=len(labels["Useful Wallet"]),
        high_risk_wallet_count=len(labels["High-Risk Wallet"]),
        suspicious_wallet_count=len(labels["Suspicious Wallet"]),
        dump_wallet_count=len(labels["Dump Wallet"]),
        unproven_wallet_count=len(labels["Unproven Wallet"]),
        total_wallet_buy_usd=total_buy,
        total_wallet_sell_usd=total_sell,
        net_wallet_flow_usd=total_buy - total_sell,
        smart_or_useful_wallets_entered_within_30m=len(smart_or_useful_early),
        low_rug_exposure_wallet_count=len(low_rug_wallets),
        deployer_linked_wallet_involved=False,
    )


def calculate_wallet_signal_score(signal: WalletSignalMetrics) -> float:
    score = 0

    if signal.useful_wallet_count >= 3:
        score += 20
    elif signal.useful_wallet_count >= 2:
        score += 15
    elif signal.useful_wallet_count >= 1:
        score += 10

    if signal.smart_wallet_count >= 2:
        score += 35
    elif signal.smart_wallet_count >= 1:
        score += 20

    if signal.net_wallet_flow_usd >= 10000:
        score += 15
    elif signal.net_wallet_flow_usd >= 5000:
        score += 10
    elif signal.net_wallet_flow_usd >= 1000:
        score += 5

    if signal.smart_or_useful_wallets_entered_within_30m >= 3:
        score += 15
    elif signal.smart_or_useful_wallets_entered_within_30m >= 2:
        score += 10

    if signal.low_rug_exposure_wallet_count >= 2:
        score += 10

    if signal.suspicious_wallet_count >= 3:
        score -= 30
    elif signal.suspicious_wallet_count >= 1:
        score -= 15

    if signal.dump_wallet_count >= 2:
        score -= 25
    elif signal.dump_wallet_count >= 1:
        score -= 10

    if signal.deployer_linked_wallet_involved:
        score -= 30

    return max(0.0, min(float(score), 100.0))


def label_wallet_signal(score: float) -> str:
    if score >= 75:
        return "Strong Wallet Signal"
    if score >= 55:
        return "Moderate Wallet Signal"
    if score >= 35:
        return "Weak Wallet Signal"
    return "No Useful Wallet Signal"


def apply_wallet_signals_to_filter_results(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT f.id, f.hard_reject, f.risk_score, f.opportunity_score, f.final_label,
               w.wallet_signal_score, w.wallet_signal_label
        FROM filter_results f
        JOIN wallet_token_signals w ON w.pair_id = f.pair_id AND w.scan_time = f.scan_time
        """
    ).fetchall()
    updated = 0
    for row in rows:
        risk = float(row["risk_score"] or 100)
        opportunity = float(row["opportunity_score"] or 0)
        wallet_score = float(row["wallet_signal_score"] or 0)
        final_research_score = opportunity * 0.50 + wallet_score * 0.25 + (100 - risk) * 0.25
        final_label = _wallet_adjusted_label(
            current_label=row["final_label"],
            hard_reject=bool(row["hard_reject"]),
            risk_score=risk,
            final_research_score=final_research_score,
            wallet_signal_score=wallet_score,
        )
        conn.execute(
            """
            UPDATE filter_results
            SET wallet_signal_score = ?,
                wallet_signal_label = ?,
                final_research_score = ?,
                final_label = ?
            WHERE id = ?
            """,
            (wallet_score, row["wallet_signal_label"], final_research_score, final_label, row["id"]),
        )
        updated += 1
    conn.commit()
    return updated


def _wallet_adjusted_label(
    current_label: str,
    hard_reject: bool,
    risk_score: float,
    final_research_score: float,
    wallet_signal_score: float,
) -> str:
    if hard_reject:
        return "Reject"
    if final_research_score >= 75 and risk_score <= 35:
        return "Paper Trade Candidate"
    if final_research_score >= 65 and risk_score <= 50:
        return "Research Candidate"
    if wallet_signal_score >= 55 and risk_score <= 70:
        return "Watchlist"
    if wallet_signal_score >= 55 and risk_score > 70:
        return "High-Risk Momentum Watchlist"
    return current_label


def _upsert_wallet_token_signal(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    metrics: WalletSignalMetrics,
    score: float,
    label: str,
) -> None:
    conn.execute(
        """
        INSERT INTO wallet_token_signals (
          token_address, pair_id, chain, scan_time, smart_wallet_count,
          useful_wallet_count, high_risk_wallet_count, suspicious_wallet_count,
          dump_wallet_count, unproven_wallet_count, total_wallet_buy_usd,
          total_wallet_sell_usd, net_wallet_flow_usd,
          smart_or_useful_wallets_entered_within_30m,
          low_rug_exposure_wallet_count, deployer_linked_wallet_involved,
          wallet_signal_score, wallet_signal_label
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pair_id, scan_time) DO UPDATE SET
          smart_wallet_count = excluded.smart_wallet_count,
          useful_wallet_count = excluded.useful_wallet_count,
          high_risk_wallet_count = excluded.high_risk_wallet_count,
          suspicious_wallet_count = excluded.suspicious_wallet_count,
          dump_wallet_count = excluded.dump_wallet_count,
          unproven_wallet_count = excluded.unproven_wallet_count,
          total_wallet_buy_usd = excluded.total_wallet_buy_usd,
          total_wallet_sell_usd = excluded.total_wallet_sell_usd,
          net_wallet_flow_usd = excluded.net_wallet_flow_usd,
          smart_or_useful_wallets_entered_within_30m = excluded.smart_or_useful_wallets_entered_within_30m,
          low_rug_exposure_wallet_count = excluded.low_rug_exposure_wallet_count,
          deployer_linked_wallet_involved = excluded.deployer_linked_wallet_involved,
          wallet_signal_score = excluded.wallet_signal_score,
          wallet_signal_label = excluded.wallet_signal_label
        """,
        (
            row["token_address"],
            row["pair_id"],
            row["chain"],
            row["scan_time"],
            metrics.smart_wallet_count,
            metrics.useful_wallet_count,
            metrics.high_risk_wallet_count,
            metrics.suspicious_wallet_count,
            metrics.dump_wallet_count,
            metrics.unproven_wallet_count,
            metrics.total_wallet_buy_usd,
            metrics.total_wallet_sell_usd,
            metrics.net_wallet_flow_usd,
            metrics.smart_or_useful_wallets_entered_within_30m,
            metrics.low_rug_exposure_wallet_count,
            1 if metrics.deployer_linked_wallet_involved else 0,
            score,
            label,
        ),
    )


def _upsert_wallet_cluster(conn: sqlite3.Connection, row: sqlite3.Row, metrics: WalletSignalMetrics) -> int:
    strong_wallets = metrics.smart_wallet_count + metrics.useful_wallet_count
    if strong_wallets < 2:
        return 0
    profile_rows = conn.execute(
        """
        SELECT w.wallet_quality_score, w.wallet_risk_score
        FROM wallets w
        JOIN wallet_trades wt ON wt.wallet_address = w.wallet_address
        WHERE wt.pair_id = ?
          AND wt.side = 'buy'
          AND w.wallet_label IN ('Smart Wallet', 'Useful Wallet')
        """,
        (row["pair_id"],),
    ).fetchall()
    qualities = [float(item["wallet_quality_score"] or 0) for item in profile_rows]
    risks = [float(item["wallet_risk_score"] or 0) for item in profile_rows]
    conn.execute(
        """
        INSERT INTO wallet_clusters (
          token_address, pair_id, chain, scan_time, cluster_type, wallet_count,
          total_buy_usd, average_wallet_quality_score, average_wallet_risk_score, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(pair_id, scan_time, cluster_type) DO UPDATE SET
          wallet_count = excluded.wallet_count,
          total_buy_usd = excluded.total_buy_usd,
          average_wallet_quality_score = excluded.average_wallet_quality_score,
          average_wallet_risk_score = excluded.average_wallet_risk_score,
          notes = excluded.notes
        """,
        (
            row["token_address"],
            row["pair_id"],
            row["chain"],
            row["scan_time"],
            "smart_or_useful_buy_cluster",
            strong_wallets,
            metrics.total_wallet_buy_usd,
            mean(qualities) if qualities else 0,
            mean(risks) if risks else 0,
            "Multiple Smart/Useful wallets bought the same scanned token. Research signal only.",
        ),
    )
    return 1
