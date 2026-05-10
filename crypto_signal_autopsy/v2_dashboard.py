from __future__ import annotations

from collections import defaultdict
import json
import sqlite3
from statistics import mean, median
from typing import Any

from crypto_signal_autopsy import db
from crypto_signal_autopsy.config import HORIZONS, MODEL_VERSION
from crypto_signal_autopsy.review import money, pct


LABELS = [
    "Reject",
    "Watchlist",
    "High-Risk Momentum Watchlist",
    "Research Candidate",
    "Paper Trade Candidate",
]


def build_v2_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    label_counts = _label_counts(conn)
    filter_accuracy = _filter_accuracy(conn)
    latest_scan = _scalar(conn, "SELECT MAX(scan_time) FROM filter_results") or "No candidate found yet"
    latest_attempt = (
        _scalar(
            conn,
            """
            SELECT MAX(observed_at)
            FROM api_events
            WHERE provider IN ('coingecko', 'dexscreener')
            """,
        )
        or latest_scan
    )
    api_errors = db.active_api_error_count(conn)
    return {
        "model_version": MODEL_VERSION,
        "disclaimer": "Research only. No financial advice. No buy signals. No sell signals. No auto trading.",
        "latest_scan": latest_scan,
        "latest_scan_attempt": latest_attempt,
        "api_errors": api_errors,
        "label_counts": label_counts,
        "health_cards": _health_cards(label_counts, latest_scan, latest_attempt, api_errors, filter_accuracy),
        "bucket_tables": {label: _bucket_rows(conn, label) for label in LABELS},
        "filter_accuracy": filter_accuracy,
        "rejected_audits": _rejected_audit_rows(conn),
        "performance": _performance_rows(conn),
        "outliers": {
            "missed_winners": _outcome_extremes(conn, "missed_winners"),
            "avoided_losers": _outcome_extremes(conn, "avoided_losers"),
            "high_risk_pumps": _outcome_extremes(conn, "high_risk_pumps"),
            "rugs_dumps": _outcome_extremes(conn, "rugs_dumps"),
        },
        "filters_saved_us": _filter_lesson_rows(conn, saved=True),
        "filters_blocked_winners": _filter_lesson_rows(conn, saved=False),
        "score_buckets": _score_bucket_rows(conn),
        "paper_trades": _paper_trade_rows(conn),
        "review_notes": _review_note_rows(conn),
    }


def _health_cards(
    label_counts: dict[str, int],
    latest_scan: str,
    latest_attempt: str,
    api_errors: int,
    filter_accuracy: dict[str, str],
) -> list[dict[str, str]]:
    return [
        {"label": "Total Tokens Scanned", "value": str(sum(label_counts.values()))},
        {"label": "Rejected Tokens", "value": str(label_counts.get("Reject", 0))},
        {"label": "Watchlist Tokens", "value": str(label_counts.get("Watchlist", 0))},
        {
            "label": "High-Risk Momentum",
            "value": str(label_counts.get("High-Risk Momentum Watchlist", 0)),
        },
        {"label": "Research Candidates", "value": str(label_counts.get("Research Candidate", 0))},
        {"label": "Paper Trade Candidates", "value": str(label_counts.get("Paper Trade Candidate", 0))},
        {"label": "Active API Errors", "value": str(api_errors)},
        {"label": "Rejected Audit Accuracy", "value": filter_accuracy["accuracy_rate"]},
        {"label": "Completed Rejected Audits", "value": filter_accuracy["completed"]},
        {"label": "Last Scan Attempt", "value": latest_attempt},
        {"label": "Last Candidate Found", "value": latest_scan},
        {"label": "Model Version", "value": MODEL_VERSION},
    ]


def _bucket_rows(conn: sqlite3.Connection, label: str, limit: int = 50) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT f.*, c.chain_id, c.pair_address, c.raw_metrics
        FROM filter_results f
        JOIN candidate_evaluations c
          ON c.token_address = f.token_address
         AND c.observed_at = f.scan_time
         AND f.pair_id = c.chain_id || ':' || c.pair_address
        WHERE f.final_label = ?
        ORDER BY f.scan_time DESC
        LIMIT ?
        """,
        (label, limit),
    ).fetchall()
    output = []
    for row in rows:
        metrics = _loads(row["raw_metrics"], {})
        output.append(
            {
                "symbol": metrics.get("base_symbol") or "UNKNOWN",
                "chain": row["chain_id"] or "",
                "pair_age": _hours(metrics.get("pair_age_hours")),
                "liquidity": money(metrics.get("liquidity_usd")),
                "volume24h": money(metrics.get("volume_h24_usd")),
                "move1h": pct(metrics.get("price_change_h1_pct")),
                "risk_score": _score(row["risk_score"]),
                "opportunity_score": _score(row["opportunity_score"]),
                "ten_x_score": _score(row["ten_x_score"]),
                "ten_x_label": row["ten_x_label"] or "No 10x Setup",
                "ten_x_reasons": _reason_text(row["ten_x_reasons"]),
                "final_label": row["final_label"] or "",
                "main_reasons": _reason_text(row["reject_reasons"]),
                "url": f"https://dexscreener.com/{row['chain_id']}/{row['pair_address']}",
                "scan_time": row["scan_time"] or "",
            }
        )
    return output


def _performance_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    rows = conn.execute(
        "SELECT horizon, return_pct FROM outcome_snapshots WHERE return_pct IS NOT NULL"
    ).fetchall()
    for row in rows:
        grouped[row["horizon"]].append(float(row["return_pct"]))
    output = []
    for horizon in HORIZONS:
        values = grouped.get(horizon, [])
        if not values:
            output.append(
                {
                    "horizon": horizon,
                    "count": "0",
                    "median": "No data",
                    "average": "No data",
                    "best": "No data",
                    "worst": "No data",
                    "outlier_note": "Waiting for outcomes",
                }
            )
            continue
        med = median(values)
        avg = mean(values)
        output.append(
            {
                "horizon": horizon,
                "count": str(len(values)),
                "median": pct(med),
                "average": pct(avg),
                "best": pct(max(values)),
                "worst": pct(min(values)),
                "outlier_note": "Average distorted by outliers" if abs(avg - med) >= 20 else "Median and average close",
            }
        )
    return output


def _outcome_extremes(conn: sqlite3.Connection, kind: str) -> list[dict[str, str]]:
    where = {
        "missed_winners": "o.label_at_scan IN ('Reject','Watchlist') AND o.return_pct >= 50",
        "avoided_losers": "o.label_at_scan = 'Reject' AND o.return_pct <= -50",
        "high_risk_pumps": "o.label_at_scan = 'High-Risk Momentum Watchlist' AND o.return_pct >= 50",
        "rugs_dumps": "(o.rugged = 1 OR o.became_illiquid = 1 OR o.return_pct <= -80)",
    }[kind]
    order = "o.return_pct DESC" if kind in {"missed_winners", "high_risk_pumps"} else "o.return_pct ASC"
    rows = conn.execute(
        f"""
        SELECT o.*, f.reject_reasons
        FROM outcome_snapshots o
        LEFT JOIN filter_results f ON f.pair_id = o.pair_id AND f.scan_time = o.scan_time
        WHERE {where}
        ORDER BY {order}
        LIMIT 25
        """
    ).fetchall()
    return [_outcome_row(row) for row in rows]


def _filter_lesson_rows(conn: sqlite3.Connection, saved: bool) -> list[dict[str, str]]:
    if saved:
        where = "f.final_label = 'Reject' AND (o.return_pct <= -50 OR o.rugged = 1 OR o.became_illiquid = 1 OR o.volume_disappeared = 1)"
        order = "o.return_pct ASC"
    else:
        where = "f.final_label IN ('Reject','Watchlist','High-Risk Momentum Watchlist') AND o.return_pct >= 50"
        order = "o.return_pct DESC"
    rows = conn.execute(
        f"""
        SELECT o.*, f.reject_reasons
        FROM outcome_snapshots o
        JOIN filter_results f ON f.pair_id = o.pair_id AND f.scan_time = o.scan_time
        WHERE {where}
        ORDER BY {order}
        LIMIT 50
        """
    ).fetchall()
    output = [_outcome_row(row) for row in rows]
    archived = _archived_filter_lesson_rows(conn, saved)
    return (output + archived)[:50]


def _filter_accuracy(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT filter_verdict, COUNT(*) AS count FROM rejected_filter_audits GROUP BY filter_verdict"
    ).fetchall()
    counts = {row["filter_verdict"] or "unknown": int(row["count"]) for row in rows}
    success = counts.get("success", 0)
    failure = counts.get("failure", 0)
    unknown = counts.get("unknown", 0)
    judged = success + failure
    completed = judged + unknown
    accuracy = f"{success / judged * 100:.1f}%" if judged else "No data"
    plain = (
        f"{success} success, {failure} failure, {unknown} unknown after 24h."
        if completed
        else "No rejected token has finished the 24h audit yet."
    )
    return {
        "completed": str(completed),
        "judged": str(judged),
        "successes": str(success),
        "failures": str(failure),
        "unknown": str(unknown),
        "accuracy_rate": accuracy,
        "plain": plain,
    }


def _rejected_audit_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT *
        FROM rejected_filter_audits
        ORDER BY completed_at DESC
        LIMIT 50
        """
    ).fetchall()
    return [
        {
            "symbol": row["symbol"] or "UNKNOWN",
            "chain": row["chain"] or "",
            "scan_time": row["scan_time"] or "",
            "completed_at": row["completed_at"] or "",
            "return24h": pct(row["return_24h_pct"]),
            "best": pct(row["best_return_pct"]),
            "worst": pct(row["worst_return_pct"]),
            "verdict": (row["filter_verdict"] or "unknown").title(),
            "lesson": row["lesson"] or "",
            "ten_x_score": _score(row["ten_x_score"]),
            "ten_x_label": row["ten_x_label"] or "",
            "reasons": _reason_text(row["reject_reasons"]),
            "url": _dex_url(row["pair_id"]),
        }
        for row in rows
    ]


def _archived_filter_lesson_rows(conn: sqlite3.Connection, saved: bool) -> list[dict[str, str]]:
    if saved:
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
        LIMIT 50
        """
    ).fetchall()
    return [
        {
            "token_address": row["token_address"] or "",
            "pair_id": row["pair_id"] or "",
            "label_at_scan": "Reject",
            "horizon": "24h archived",
            "return": pct(row["return_24h_pct"]),
            "reasons": _reason_text(row["reject_reasons"]),
            "liquidity_scan": "",
            "liquidity_horizon": "",
            "volume_scan": "",
            "volume_horizon": "",
            "became_illiquid": "Yes" if row["became_illiquid"] else "No",
            "rugged": "Yes" if row["rugged"] else "No",
            "volume_disappeared": "Yes" if row["volume_disappeared"] else "No",
        }
        for row in rows
    ]


def _score_bucket_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT f.risk_score, f.opportunity_score, o.return_pct,
               o.rugged, o.became_illiquid, o.volume_disappeared
        FROM filter_results f
        JOIN outcome_snapshots o ON o.pair_id = f.pair_id AND o.scan_time = f.scan_time
        WHERE o.return_pct IS NOT NULL
        """
    ).fetchall()
    buckets: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        buckets[(_bucket(row["risk_score"]), _bucket(row["opportunity_score"]))].append(row)
    output = []
    for (risk_bucket, opp_bucket), bucket_rows in sorted(buckets.items()):
        returns = [float(row["return_pct"]) for row in bucket_rows]
        output.append(
            {
                "risk_bucket": risk_bucket,
                "opportunity_bucket": opp_bucket,
                "count": str(len(bucket_rows)),
                "median": pct(median(returns)),
                "average": pct(mean(returns)),
                "best": pct(max(returns)),
                "worst": pct(min(returns)),
                "rug_rate": _rate(bucket_rows, "rugged"),
                "illiquidity_rate": _rate(bucket_rows, "became_illiquid"),
                "volume_disappearance_rate": _rate(bucket_rows, "volume_disappeared"),
            }
        )
    return output


def _paper_trade_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = conn.execute(
        """
        SELECT *
        FROM paper_trades
        ORDER BY created_at DESC
        LIMIT 50
        """
    ).fetchall()
    return [
        {
            "token_address": row["token_address"] or "",
            "pair_id": row["pair_id"] or "",
            "sim_entry": money(row["sim_entry_price"]),
            "position": money(row["sim_position_size_usd"]),
            "status": row["status"] or "",
            "notes": row["notes"] or "",
        }
        for row in rows
    ]


def _review_note_rows(conn: sqlite3.Connection) -> list[dict[str, str]]:
    rows = conn.execute("SELECT * FROM review_notes ORDER BY review_time DESC LIMIT 50").fetchall()
    return [
        {
            "token_address": row["token_address"] or "",
            "review_time": row["review_time"] or "",
            "missed_winner": str(row["missed_winner"] or 0),
            "saved_from_bad_token": str(row["saved_from_bad_token"] or 0),
            "main_lesson": row["main_lesson"] or "",
            "manual_notes": row["manual_notes"] or "",
        }
        for row in rows
    ]


def _outcome_row(row: sqlite3.Row) -> dict[str, str]:
    return {
        "token_address": row["token_address"] or "",
        "pair_id": row["pair_id"] or "",
        "label_at_scan": row["label_at_scan"] or "",
        "horizon": row["horizon"] or "",
        "return": pct(row["return_pct"]),
        "reasons": _reason_text(row["reject_reasons"]),
        "liquidity_scan": money(row["liquidity_at_scan"]),
        "liquidity_horizon": money(row["liquidity_at_horizon"]),
        "volume_scan": money(row["volume_at_scan"]),
        "volume_horizon": money(row["volume_at_horizon"]),
        "became_illiquid": "Yes" if row["became_illiquid"] else "No",
        "rugged": "Yes" if row["rugged"] else "No",
        "volume_disappeared": "Yes" if row["volume_disappeared"] else "No",
    }


def _label_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT final_label, COUNT(*) AS count FROM filter_results GROUP BY final_label"
    ).fetchall()
    counts = {label: 0 for label in LABELS}
    counts.update({row["final_label"]: int(row["count"]) for row in rows})
    return counts


def _scalar(conn: sqlite3.Connection, query: str) -> Any:
    row = conn.execute(query).fetchone()
    return row[0] if row else None


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _reason_text(value: str | None) -> str:
    reasons = _loads(value, [])
    if not reasons:
        return ""
    return "; ".join(str(reason).replace("_", " ").title() for reason in reasons)


def _hours(value: Any) -> str:
    if value is None:
        return "Missing"
    return f"{float(value):.2f}h"


def _score(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.0f}"


def _bucket(value: Any) -> str:
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
        return "0.0%"
    count = sum(1 for row in rows if row[field])
    return pct((count / len(rows)) * 100)


def _dex_url(pair_id: str | None) -> str:
    if not pair_id or ":" not in pair_id:
        return ""
    chain, pair_address = pair_id.split(":", 1)
    if not chain or not pair_address:
        return ""
    return f"https://dexscreener.com/{chain}/{pair_address}"
