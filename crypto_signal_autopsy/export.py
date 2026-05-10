from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from statistics import mean, median

from crypto_signal_autopsy import db
from crypto_signal_autopsy.config import load_settings
from crypto_signal_autopsy.quant_analytics import export_quant_data, refresh_quant_analytics
from crypto_signal_autopsy.wallet_exports import export_wallet_data


EXPORTS = {
    "signals": "signals",
    "outcomes": "signal_outcomes",
    "rejected_outcomes": "rejected_candidate_outcomes",
    "rejections": "candidate_evaluations",
    "api_events": "api_events",
    "tokens": "tokens",
    "pairs": "pairs",
    "security_checks": "security_checks",
    "filter_results": "filter_results",
    "social_catalysts": "social_catalysts",
    "paper_trades": "paper_trades",
    "outcome_snapshots": "outcome_snapshots",
    "rejected_filter_audits": "rejected_filter_audits",
    "review_notes": "review_notes",
}


def export_all(conn: sqlite3.Connection, export_dir: Path) -> dict[str, int]:
    db.init_db(conn)
    settings = load_settings()
    quant_counts = refresh_quant_analytics(conn, settings)
    export_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, table in EXPORTS.items():
        counts[name] = db.export_table(conn, table, export_dir / f"{name}.csv")
    counts["dashboard_summary"] = _export_dashboard_summary(conn, export_dir / "dashboard_summary.csv")
    counts["outliers"] = _export_outliers(conn, export_dir / "outliers.csv")
    counts["filter_saved_us"] = _export_filter_saved_us(conn, export_dir / "filter_saved_us.csv")
    counts["filter_blocked_winners"] = _export_filter_blocked_winners(conn, export_dir / "filter_blocked_winners.csv")
    counts["filter_accuracy"] = _export_filter_accuracy(conn, export_dir / "filter_accuracy.csv")
    counts["score_bucket_performance"] = _export_score_bucket_performance(
        conn, export_dir / "score_bucket_performance.csv"
    )
    counts.update(quant_counts)
    counts.update(export_quant_data(conn, export_dir))
    counts.update(export_wallet_data(conn, export_dir))
    return counts


def _export_dashboard_summary(conn: sqlite3.Connection, out_path: Path) -> int:
    labels = _label_counts(conn)
    latest_scan = conn.execute("SELECT MAX(scan_time) AS value FROM filter_results").fetchone()["value"]
    api_errors = db.active_api_error_count(conn)
    row = {
        "total_tokens_scanned": str(sum(labels.values())),
        "rejected_tokens": str(labels.get("Reject", 0)),
        "watchlist_tokens": str(labels.get("Watchlist", 0)),
        "high_risk_momentum_tokens": str(labels.get("High-Risk Momentum Watchlist", 0)),
        "research_candidates": str(labels.get("Research Candidate", 0)),
        "pending_paper_candidates": str(labels.get("Pending Paper Candidate", 0)),
        "paper_trade_candidates": str(labels.get("Paper Trade Candidate", 0)),
        "api_errors": str(api_errors),
        "completed_rejected_audits": str(
            conn.execute("SELECT COUNT(*) AS value FROM rejected_filter_audits").fetchone()["value"] or 0
        ),
        "last_scan_time": latest_scan or "",
        "model_version": "V2",
    }
    _write_rows(out_path, [row])
    return 1


def _export_outliers(conn: sqlite3.Connection, out_path: Path) -> int:
    rows = conn.execute(
        """
        SELECT o.*, f.reject_reasons, p.liquidity_usd AS latest_liquidity, p.volume_24h AS latest_volume
        FROM outcome_snapshots o
        LEFT JOIN filter_results f
          ON f.pair_id = o.pair_id AND f.scan_time = o.scan_time
        LEFT JOIN pairs p ON p.pair_id = o.pair_id
        WHERE o.return_pct IS NOT NULL
          AND (ABS(o.return_pct) >= 50 OR o.rugged = 1 OR o.became_illiquid = 1 OR o.volume_disappeared = 1)
        ORDER BY ABS(o.return_pct) DESC
        LIMIT 200
        """
    ).fetchall()
    output = [_outlier_row(row) for row in rows]
    _write_rows(out_path, output)
    return len(output)


def _export_filter_saved_us(conn: sqlite3.Connection, out_path: Path) -> int:
    rows = conn.execute(
        """
        SELECT o.*, f.reject_reasons
        FROM outcome_snapshots o
        JOIN filter_results f ON f.pair_id = o.pair_id AND f.scan_time = o.scan_time
        WHERE f.final_label = 'Reject'
          AND (o.return_pct <= -50 OR o.rugged = 1 OR o.became_illiquid = 1 OR o.volume_disappeared = 1)
        ORDER BY o.return_pct ASC
        LIMIT 200
        """
    ).fetchall()
    output = [_filter_lesson_row(row, "saved_from_bad_token") for row in rows]
    output.extend(_archived_filter_lesson_rows(conn, "saved_from_bad_token"))
    _write_rows(out_path, output)
    return len(output)


def _export_filter_blocked_winners(conn: sqlite3.Connection, out_path: Path) -> int:
    rows = conn.execute(
        """
        SELECT o.*, f.reject_reasons
        FROM outcome_snapshots o
        JOIN filter_results f ON f.pair_id = o.pair_id AND f.scan_time = o.scan_time
        WHERE f.final_label IN ('Reject', 'Watchlist', 'High-Risk Momentum Watchlist')
          AND o.return_pct >= 50
        ORDER BY o.return_pct DESC
        LIMIT 200
        """
    ).fetchall()
    output = [_filter_lesson_row(row, "blocked_or_watchlisted_winner") for row in rows]
    output.extend(_archived_filter_lesson_rows(conn, "blocked_or_watchlisted_winner"))
    _write_rows(out_path, output)
    return len(output)


def _export_filter_accuracy(conn: sqlite3.Connection, out_path: Path) -> int:
    rows = conn.execute(
        "SELECT filter_verdict, COUNT(*) AS count FROM rejected_filter_audits GROUP BY filter_verdict"
    ).fetchall()
    counts = {row["filter_verdict"] or "unknown": int(row["count"]) for row in rows}
    success = counts.get("success", 0)
    failure = counts.get("failure", 0)
    unknown = counts.get("unknown", 0)
    judged = success + failure
    row = {
        "completed_rejected_audits": str(judged + unknown),
        "judged_rejected_audits": str(judged),
        "filter_successes": str(success),
        "filter_failures": str(failure),
        "unknown": str(unknown),
        "accuracy_rate_pct": _fmt((success / judged) * 100) if judged else "",
    }
    _write_rows(out_path, [row])
    return 1


def _export_score_bucket_performance(conn: sqlite3.Connection, out_path: Path) -> int:
    rows = conn.execute(
        """
        SELECT f.risk_score, f.opportunity_score, o.return_pct,
               o.rugged, o.became_illiquid, o.volume_disappeared
        FROM filter_results f
        JOIN outcome_snapshots o ON o.pair_id = f.pair_id AND o.scan_time = f.scan_time
        WHERE o.return_pct IS NOT NULL
        """
    ).fetchall()
    buckets: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        key = (_bucket(row["risk_score"]), _bucket(row["opportunity_score"]))
        buckets.setdefault(key, []).append(row)
    output = []
    for (risk_bucket, opportunity_bucket), bucket_rows in sorted(buckets.items()):
        returns = [float(row["return_pct"]) for row in bucket_rows]
        output.append(
            {
                "risk_score_bucket": risk_bucket,
                "opportunity_score_bucket": opportunity_bucket,
                "token_count": str(len(bucket_rows)),
                "median_return": _fmt(median(returns)),
                "average_return": _fmt(mean(returns)),
                "best_return": _fmt(max(returns)),
                "worst_return": _fmt(min(returns)),
                "rug_rate": _rate(bucket_rows, "rugged"),
                "illiquidity_rate": _rate(bucket_rows, "became_illiquid"),
                "volume_disappearance_rate": _rate(bucket_rows, "volume_disappeared"),
            }
        )
    _write_rows(out_path, output)
    return len(output)


def _label_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT final_label, COUNT(*) AS count FROM filter_results GROUP BY final_label"
    ).fetchall()
    return {row["final_label"]: int(row["count"]) for row in rows}


def _outlier_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        "token_address": row["token_address"] or "",
        "pair_id": row["pair_id"] or "",
        "final_label_at_scan": row["label_at_scan"] or "",
        "return_pct": _fmt(row["return_pct"]),
        "horizon": row["horizon"] or "",
        "reject_reasons": row["reject_reasons"] or "",
        "liquidity_at_scan": _fmt(row["liquidity_at_scan"]),
        "liquidity_at_horizon": _fmt(row["liquidity_at_horizon"]),
        "volume_at_scan": _fmt(row["volume_at_scan"]),
        "volume_at_horizon": _fmt(row["volume_at_horizon"]),
        "became_illiquid": str(row["became_illiquid"] or 0),
        "rugged": str(row["rugged"] or 0),
        "volume_disappeared": str(row["volume_disappeared"] or 0),
    }


def _filter_lesson_row(row: sqlite3.Row, lesson_type: str) -> dict[str, str]:
    return {
        "lesson_type": lesson_type,
        "token_address": row["token_address"] or "",
        "pair_id": row["pair_id"] or "",
        "label_at_scan": row["label_at_scan"] or "",
        "horizon": row["horizon"] or "",
        "return_pct": _fmt(row["return_pct"]),
        "reject_reasons": row["reject_reasons"] or "",
        "became_illiquid": str(row["became_illiquid"] or 0),
        "rugged": str(row["rugged"] or 0),
        "volume_disappeared": str(row["volume_disappeared"] or 0),
    }


def _archived_filter_lesson_rows(conn: sqlite3.Connection, lesson_type: str) -> list[dict[str, str]]:
    if lesson_type == "saved_from_bad_token":
        where = "filter_verdict = 'success'"
        order = "return_24h_pct ASC"
    else:
        where = "filter_verdict = 'failure'"
        order = "return_24h_pct DESC"
    rows = conn.execute(
        f"""
        SELECT *
        FROM rejected_filter_audits
        WHERE {where}
        ORDER BY {order}
        LIMIT 200
        """
    ).fetchall()
    return [
        {
            "lesson_type": f"{lesson_type}_archived_24h",
            "token_address": row["token_address"] or "",
            "pair_id": row["pair_id"] or "",
            "label_at_scan": "Reject",
            "horizon": "24h",
            "return_pct": _fmt(row["return_24h_pct"]),
            "reject_reasons": row["reject_reasons"] or "",
            "became_illiquid": str(row["became_illiquid"] or 0),
            "rugged": str(row["rugged"] or 0),
            "volume_disappeared": str(row["volume_disappeared"] or 0),
        }
        for row in rows
    ]


def _write_rows(out_path: Path, rows: list[dict[str, str]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out_path.write_text("", encoding="utf-8")
        return
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _bucket(value) -> str:
    if value is None:
        return "missing"
    number = float(value)
    if number <= 25:
        return "0-25"
    if number <= 50:
        return "26-50"
    if number <= 75:
        return "51-75"
    return "76-100"


def _rate(rows: list[sqlite3.Row], field: str) -> str:
    if not rows:
        return "0.00"
    count = sum(1 for row in rows if row[field])
    return _fmt((count / len(rows)) * 100)


def _fmt(value) -> str:
    if value is None:
        return ""
    return f"{float(value):.4f}"
