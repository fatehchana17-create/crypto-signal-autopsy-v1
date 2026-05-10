from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import csv
import json
import sqlite3
from pathlib import Path
from statistics import mean, median
from typing import Any

from crypto_signal_autopsy import db
from crypto_signal_autopsy.baselines import cex_return_pct
from crypto_signal_autopsy.config import HORIZONS, Settings


LABELS = [
    "Reject",
    "Watchlist",
    "High-Risk Momentum Watchlist",
    "Research Candidate",
    "Pending Paper Candidate",
    "Paper Trade Candidate",
]

QUANT_TABLES = [
    "category_performance",
    "accepted_failure_diagnosis",
    "missed_winner_review",
    "baseline_comparisons",
    "ten_x_failure_review",
    "data_quality_report",
]


def refresh_quant_analytics(conn: sqlite3.Connection, settings: Settings) -> dict[str, int]:
    db.init_db(conn)
    updated_at = _now()
    counts = {
        "category_performance": _refresh_category_performance(conn, updated_at),
        "accepted_failure_diagnosis": _refresh_accepted_failure_diagnosis(conn, updated_at),
        "missed_winner_review": _refresh_missed_winner_review(conn, updated_at),
        "baseline_comparisons": _refresh_baseline_comparisons(conn, updated_at),
        "ten_x_failure_review": _refresh_ten_x_failure_review(conn, updated_at),
        "data_quality_report": _refresh_data_quality_report(conn, updated_at, settings),
    }
    conn.commit()
    return counts


def export_quant_data(conn: sqlite3.Connection, export_dir: Path) -> dict[str, int]:
    export_dir.mkdir(parents=True, exist_ok=True)
    dashboard_dir = export_dir / "dashboard" / "data"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for table in QUANT_TABLES:
        rows = _table_rows(conn, table)
        counts[table] = _write_csv(export_dir / f"{table}.csv", rows)
        _write_json(dashboard_dir / f"{table}.json", rows)
    return counts


def write_public_quant_json_data(conn: sqlite3.Connection, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for table in QUANT_TABLES:
        _write_json(out_dir / f"{table}.json", _table_rows(conn, table))


def build_quant_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        "category_performance": _table_rows(conn, "category_performance"),
        "accepted_failure_diagnosis": _table_rows(conn, "accepted_failure_diagnosis"),
        "missed_winner_review": _table_rows(conn, "missed_winner_review"),
        "baseline_comparisons": _table_rows(conn, "baseline_comparisons"),
        "ten_x_failure_review": _table_rows(conn, "ten_x_failure_review"),
        "data_quality_report": _table_rows(conn, "data_quality_report"),
    }


def _refresh_category_performance(conn: sqlite3.Connection, updated_at: str) -> int:
    _clear(conn, "category_performance")
    label_counts = _label_counts(conn)
    rows = conn.execute(
        """
        SELECT label_at_scan AS category, horizon, return_pct
        FROM outcome_snapshots
        WHERE return_pct IS NOT NULL
        """
    ).fetchall()
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in rows:
        grouped[(row["category"] or "Unknown", row["horizon"] or "")].append(float(row["return_pct"]))

    inserted = 0
    for category in LABELS:
        category_count = label_counts.get(category, 0)
        for horizon in HORIZONS:
            values = grouped.get((category, horizon), [])
            if values:
                med = median(values)
                avg = mean(values)
                best = max(values)
                worst = min(values)
                positive_rate = sum(1 for value in values if value > 0) / len(values)
            else:
                med = avg = best = worst = positive_rate = None
            verdict = category_verdict(category_count, med, positive_rate, worst, category)
            conn.execute(
                """
                INSERT INTO category_performance (
                  category, horizon, count, median_return, average_return,
                  best_return, worst_return, positive_rate, verdict, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (category, horizon, len(values), med, avg, best, worst, positive_rate, verdict, updated_at),
            )
            inserted += 1
    return inserted


def category_verdict(
    count: int,
    median_24h: float | None,
    positive_rate: float | None,
    worst_return: float | None,
    category: str,
) -> str:
    if category == "High-Risk Momentum Watchlist" and count == 0:
        return "Inactive"
    if count < 10 and category != "Reject":
        return "Too Small Sample"
    if median_24h is None or positive_rate is None:
        return "Too Small Sample" if count else "Inactive"
    if category == "Reject":
        if median_24h < 0 and positive_rate < 0.40:
            return "Good"
        return "Mixed"
    if category in {"Paper Trade Candidate", "Research Candidate", "Pending Paper Candidate"}:
        if median_24h > 0 and positive_rate >= 0.50 and (worst_return is None or worst_return > -40):
            return "Good"
        if median_24h < 0 or (worst_return is not None and worst_return <= -40):
            return "Bad"
        return "Mixed"
    if category == "High-Risk Momentum Watchlist":
        return "Research Only"
    return "Mixed"


def _refresh_accepted_failure_diagnosis(conn: sqlite3.Connection, created_at: str) -> int:
    _clear(conn, "accepted_failure_diagnosis")
    rows = _outcome_rows(conn, labels=("Research Candidate", "Pending Paper Candidate", "Paper Trade Candidate"))
    inserted = 0
    for row in rows:
        return_pct = _num(row["return_pct"])
        if return_pct is None or return_pct > -25:
            continue
        metrics = _loads(row["raw_metrics"], {})
        failure_reasons = _failure_reasons(row, metrics)
        symbol = metrics.get("base_symbol") or "UNKNOWN"
        diagnosis = (
            f"{symbol} failed after being labeled {row['label_at_scan']}. "
            f"Main likely causes: {', '.join(failure_reasons).lower() or 'unknown failure'}."
        )
        recommendation = _failure_recommendation(failure_reasons)
        conn.execute(
            """
            INSERT INTO accepted_failure_diagnosis (
              token_address, symbol, pair_id, original_label, horizon, return_pct,
              max_adverse_excursion_pct, max_favorable_excursion_pct,
              liquidity_at_scan, liquidity_at_horizon, volume_at_scan,
              volume_at_horizon, price_change_1h_at_scan, failure_reasons,
              diagnosis_text, fix_recommendation, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["token_address"],
                symbol,
                row["pair_id"],
                row["label_at_scan"],
                row["horizon"],
                return_pct,
                row["max_adverse_excursion_pct"],
                row["max_favorable_excursion_pct"],
                row["liquidity_at_scan"],
                row["liquidity_at_horizon"],
                row["volume_at_scan"],
                row["volume_at_horizon"],
                metrics.get("price_change_h1_pct"),
                db.to_json(failure_reasons),
                diagnosis,
                recommendation,
                created_at,
            ),
        )
        inserted += 1
    return inserted


def _refresh_missed_winner_review(conn: sqlite3.Connection, created_at: str) -> int:
    _clear(conn, "missed_winner_review")
    rows = _outcome_rows(conn, labels=("Reject", "Watchlist"))
    inserted = 0
    for row in rows:
        return_pct = _num(row["return_pct"])
        if return_pct is None or return_pct < 50:
            continue
        metrics = _loads(row["raw_metrics"], {})
        reasons = _loads(row["reject_reasons"], [])
        security_score = row["security_score"]
        score, label = assess_tradability(metrics, security_score)
        high_risk_miss = _should_be_high_risk_momentum(metrics, reasons)
        lesson = _missed_winner_lesson(metrics, reasons, label, high_risk_miss)
        conn.execute(
            """
            INSERT INTO missed_winner_review (
              token_address, symbol, pair_id, original_label, horizon,
              max_future_return_pct, return_at_horizon_pct, reject_reasons,
              liquidity_at_scan, volume_at_scan, pair_age_hours_at_scan,
              price_change_1h_at_scan, security_score, fdv,
              fdv_liquidity_ratio, tradability_score, tradability_label,
              should_be_high_risk_momentum, main_lesson, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["token_address"],
                metrics.get("base_symbol") or "UNKNOWN",
                row["pair_id"],
                row["label_at_scan"],
                row["horizon"],
                return_pct,
                return_pct,
                row["reject_reasons"] or "[]",
                metrics.get("liquidity_usd"),
                metrics.get("volume_h24_usd"),
                metrics.get("pair_age_hours"),
                metrics.get("price_change_h1_pct"),
                security_score,
                metrics.get("fdv"),
                metrics.get("fdv_liquidity_ratio"),
                score,
                label,
                1 if high_risk_miss else 0,
                lesson,
                created_at,
            ),
        )
        inserted += 1
    return inserted


def assess_tradability(metrics: dict[str, Any], security_score: Any) -> tuple[float, str]:
    score = 0.0
    liquidity = _num(metrics.get("liquidity_usd")) or 0
    volume = _num(metrics.get("volume_h24_usd")) or 0
    age = _num(metrics.get("pair_age_hours")) or 0
    move = _num(metrics.get("price_change_h1_pct")) or 0
    buy_ratio = _buy_ratio(metrics)
    security = _num(security_score)

    if liquidity >= 50_000:
        score += 25
    elif liquidity >= 25_000:
        score += 15
    if volume >= 50_000:
        score += 20
    if age >= 0.5:
        score += 15
    if move <= 250:
        score += 15
    if security is not None and security >= 65:
        score += 15
    if buy_ratio is not None and buy_ratio >= 0.40:
        score += 10
    if score >= 70:
        return score, "Possibly Tradable"
    if score >= 45:
        return score, "High-Risk Tradable"
    return score, "Likely Not Tradable"


def _refresh_baseline_comparisons(conn: sqlite3.Connection, created_at: str) -> int:
    _clear(conn, "baseline_comparisons")
    rows = _outcome_rows(conn)
    prepared = []
    for row in rows:
        return_pct = _num(row["return_pct"])
        if return_pct is None:
            continue
        metrics = _loads(row["raw_metrics"], {})
        prepared.append(
            {
                "row": row,
                "metrics": metrics,
                "return_pct": return_pct,
                "horizon": row["horizon"],
                "liquidity_bucket": _liquidity_bucket(row["liquidity_at_scan"]),
                "age_bucket": _age_bucket(metrics.get("pair_age_hours")),
            }
        )

    all_by_horizon: dict[str, list[float]] = defaultdict(list)
    liq_by_key: dict[tuple[str, str], list[float]] = defaultdict(list)
    age_by_key: dict[tuple[str, str], list[float]] = defaultdict(list)
    for item in prepared:
        all_by_horizon[item["horizon"]].append(item["return_pct"])
        liq_by_key[(item["horizon"], item["liquidity_bucket"])].append(item["return_pct"])
        age_by_key[(item["horizon"], item["age_bucket"])].append(item["return_pct"])

    inserted = 0
    for item in prepared:
        row = item["row"]
        metrics = item["metrics"]
        horizon = item["horizon"]
        all_median = _median_or_none(all_by_horizon[horizon])
        same_liq = _median_or_none(liq_by_key[(horizon, item["liquidity_bucket"])])
        same_age = _median_or_none(age_by_key[(horizon, item["age_bucket"])])
        btc = cex_return_pct(conn, "bitcoin", row["scan_time"], row["snapshot_time"])
        eth = cex_return_pct(conn, "ethereum", row["scan_time"], row["snapshot_time"])
        excess_btc = _diff(item["return_pct"], btc)
        excess_eth = _diff(item["return_pct"], eth)
        excess_all = _diff(item["return_pct"], all_median)
        excess_liq = _diff(item["return_pct"], same_liq)
        excess_age = _diff(item["return_pct"], same_age)
        verdict = _baseline_verdict(excess_all, excess_liq)
        conn.execute(
            """
            INSERT INTO baseline_comparisons (
              token_address, symbol, pair_id, label, horizon, token_return_pct,
              btc_return_pct, eth_return_pct, all_scanned_median_pct,
              same_liquidity_bucket_median_pct, same_age_bucket_median_pct,
              excess_vs_btc, excess_vs_eth, excess_vs_all_scanned,
              excess_vs_same_liquidity, excess_vs_same_age, baseline_verdict,
              created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["token_address"],
                metrics.get("base_symbol") or "UNKNOWN",
                row["pair_id"],
                row["label_at_scan"],
                horizon,
                item["return_pct"],
                btc,
                eth,
                all_median,
                same_liq,
                same_age,
                excess_btc,
                excess_eth,
                excess_all,
                excess_liq,
                excess_age,
                verdict,
                created_at,
            ),
        )
        inserted += 1
    return inserted


def _refresh_ten_x_failure_review(conn: sqlite3.Connection, created_at: str) -> int:
    _clear(conn, "ten_x_failure_review")
    rows = _outcome_rows(conn)
    inserted = 0
    for row in rows:
        ten_x_score = _num(row["ten_x_score"])
        return_pct = _num(row["return_pct"])
        if ten_x_score is None or return_pct is None or ten_x_score < 80 or return_pct > -25:
            continue
        metrics = _loads(row["raw_metrics"], {})
        reasons = _failure_reasons(row, metrics)
        manipulation_score = _manipulation_risk_score(metrics, row)
        risk_adjusted = max(0.0, ten_x_score - manipulation_score)
        diagnosis = (
            "High 10x score failed. Inspect overextension, timing, volume fade, "
            "liquidity drop, manipulation risk, or lack of buyer continuation."
        )
        conn.execute(
            """
            INSERT INTO ten_x_failure_review (
              token_address, symbol, pair_id, ten_x_score,
              risk_adjusted_10x_score, manipulation_risk_score, horizon,
              return_pct, failure_reasons, diagnosis_text, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["token_address"],
                metrics.get("base_symbol") or "UNKNOWN",
                row["pair_id"],
                ten_x_score,
                risk_adjusted,
                manipulation_score,
                row["horizon"],
                return_pct,
                db.to_json(reasons),
                diagnosis,
                created_at,
            ),
        )
        inserted += 1
    return inserted


def _refresh_data_quality_report(conn: sqlite3.Connection, updated_at: str, settings: Settings) -> int:
    _clear(conn, "data_quality_report")
    rows: list[dict[str, str]] = []
    latest_scan = _scalar(conn, "SELECT MAX(scan_time) FROM filter_results") or ""
    latest_outcome = _scalar(conn, "SELECT MAX(snapshot_time) FROM outcome_snapshots") or ""
    api_errors = db.active_api_error_count(conn)
    rows.append(_quality_row("Last successful scan", latest_scan, "OK" if latest_scan else "Missing", "", updated_at))
    rows.append(
        _quality_row("Last successful outcome update", latest_outcome, "OK" if latest_outcome else "Missing", "", updated_at)
    )
    rows.append(_quality_row("Active API errors", str(api_errors), "OK" if api_errors == 0 else "Needs Attention", "", updated_at))

    filter_rows = conn.execute(
        """
        SELECT c.raw_metrics
        FROM candidate_evaluations c
        ORDER BY c.observed_at DESC
        LIMIT 500
        """
    ).fetchall()
    metrics_list = [_loads(row["raw_metrics"], {}) for row in filter_rows]
    missing_move = sum(1 for metrics in metrics_list if metrics.get("price_change_h1_pct") is None)
    missing_liquidity = sum(1 for metrics in metrics_list if metrics.get("liquidity_usd") is None)
    missing_security = conn.execute(
        """
        SELECT COUNT(*) AS value
        FROM filter_results f
        LEFT JOIN security_checks s
          ON s.token_address = f.token_address AND s.scan_time = f.scan_time
        WHERE s.id IS NULL
        """
    ).fetchone()["value"]
    rows.append(_quality_row("Missing 1h move count", str(missing_move), "OK" if missing_move == 0 else "Review", "", updated_at))
    rows.append(_quality_row("Missing liquidity count", str(missing_liquidity), "OK" if missing_liquidity == 0 else "Review", "", updated_at))
    rows.append(
        _quality_row(
            "Missing security score count",
            str(missing_security),
            "Review" if missing_security else "OK",
            "GoPlus/security can be absent on free or unauthenticated runs.",
            updated_at,
        )
    )
    for horizon in HORIZONS:
        total = conn.execute(
            "SELECT COUNT(*) AS value FROM filter_results WHERE scan_time IS NOT NULL"
        ).fetchone()["value"]
        covered = conn.execute(
            "SELECT COUNT(*) AS value FROM outcome_snapshots WHERE horizon = ?",
            (horizon,),
        ).fetchone()["value"]
        coverage = (covered / total) * 100 if total else 0
        rows.append(
            _quality_row(
                f"{horizon} horizon coverage",
                f"{coverage:.1f}%",
                "OK" if coverage >= 50 or horizon == "24h" else "Review",
                f"{covered} outcome rows from {total} scan rows.",
                updated_at,
            )
        )
    rows.append(
        _quality_row(
            "Timing precision",
            "Approximate",
            "Review",
            "Short-horizon results may be approximate because GitHub Actions runs roughly hourly.",
            updated_at,
        )
    )
    for row in rows:
        conn.execute(
            """
            INSERT INTO data_quality_report (metric, value, status, detail, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (row["metric"], row["value"], row["status"], row["detail"], row["updated_at"]),
        )
    return len(rows)


def _outcome_rows(conn: sqlite3.Connection, labels: tuple[str, ...] | None = None) -> list[sqlite3.Row]:
    params: list[Any] = []
    label_filter = ""
    if labels:
        placeholders = ",".join("?" for _ in labels)
        label_filter = f" AND o.label_at_scan IN ({placeholders})"
        params.extend(labels)
    return conn.execute(
        f"""
        SELECT o.*, f.reject_reasons, f.risk_score, f.opportunity_score,
               f.ten_x_score, f.ten_x_label, f.ten_x_reasons,
               c.raw_metrics, c.chain_id, c.pair_address,
               (
                 SELECT s.security_score
                 FROM security_checks s
                 WHERE s.token_address = o.token_address
                   AND s.scan_time = o.scan_time
                 ORDER BY s.id DESC
                 LIMIT 1
               ) AS security_score,
               (
                 SELECT s.risk_flags
                 FROM security_checks s
                 WHERE s.token_address = o.token_address
                   AND s.scan_time = o.scan_time
                 ORDER BY s.id DESC
                 LIMIT 1
               ) AS security_risk_flags
        FROM outcome_snapshots o
        LEFT JOIN filter_results f
          ON f.pair_id = o.pair_id AND f.scan_time = o.scan_time
        LEFT JOIN candidate_evaluations c
          ON c.token_address = o.token_address
         AND c.observed_at = o.scan_time
         AND o.pair_id = c.chain_id || ':' || c.pair_address
        WHERE o.return_pct IS NOT NULL {label_filter}
        ORDER BY o.snapshot_time DESC
        """,
        params,
    ).fetchall()


def _failure_reasons(row: sqlite3.Row, metrics: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if (_num(metrics.get("price_change_h1_pct")) or 0) > 80:
        reasons.append("Overextension failure")
    liquidity_scan = _num(row["liquidity_at_scan"])
    liquidity_horizon = _num(row["liquidity_at_horizon"])
    if liquidity_scan and liquidity_horizon is not None and liquidity_horizon < liquidity_scan * 0.70:
        reasons.append("Liquidity drop failure")
    volume_scan = _num(row["volume_at_scan"])
    volume_horizon = _num(row["volume_at_horizon"])
    if volume_scan and volume_horizon is not None and volume_horizon < volume_scan * 0.40:
        reasons.append("Volume fade failure")
    if (_num(row["max_adverse_excursion_pct"]) or 0) < -20:
        reasons.append("Timing failure")
    security_score = _num(row["security_score"])
    flags = _loads(row["security_risk_flags"], [])
    if (security_score is not None and security_score < 80) or flags:
        reasons.append("Security failure")
    buy_ratio = _buy_ratio(metrics)
    if buy_ratio is not None and buy_ratio < 0.45:
        reasons.append("No-buyer-continuation failure")
    if not reasons:
        reasons.append("Unknown failure")
    return reasons


def _failure_recommendation(reasons: list[str]) -> str:
    if "Overextension failure" in reasons:
        return "Require delayed-entry survival before promotion and reduce max allowed 1h move."
    if "Liquidity drop failure" in reasons:
        return "Require stronger liquidity retention and reject fast liquidity fade."
    if "Volume fade failure" in reasons:
        return "Require volume continuation after detection before paper promotion."
    if "Security failure" in reasons:
        return "Require clean security data before promotion."
    return "Keep as research only until more stable post-detection evidence appears."


def _should_be_high_risk_momentum(metrics: dict[str, Any], reasons: list[str]) -> bool:
    liquidity = _num(metrics.get("liquidity_usd")) or 0
    age = _num(metrics.get("pair_age_hours")) or 0
    move = _num(metrics.get("price_change_h1_pct")) or 0
    reason_text = " ".join(reasons)
    return (
        liquidity >= 25_000
        and age <= 6
        and 80 <= move <= 700
        and any(token in reason_text for token in ("young", "age", "overextended", "liquidity"))
    )


def _missed_winner_lesson(
    metrics: dict[str, Any],
    reasons: list[str],
    tradability_label: str,
    high_risk_miss: bool,
) -> str:
    if high_risk_miss:
        return "High-Risk Momentum Miss: pumped after rejection, but the setup was young/overextended or below clean thresholds."
    if tradability_label == "Possibly Tradable":
        return "Clean missed winner candidate: review whether thresholds were too strict."
    return "Pump was not clearly tradable; do not loosen clean filters from this row alone."


def _manipulation_risk_score(metrics: dict[str, Any], row: sqlite3.Row) -> float:
    score = 0.0
    if (_num(metrics.get("price_change_h1_pct")) or 0) > 80:
        score += 25
    if (_num(metrics.get("volume_liquidity_ratio")) or 0) > 15:
        score += 20
    if (_num(metrics.get("pair_age_hours")) or 999) < 1:
        score += 20
    if row["rugged"] or row["became_illiquid"]:
        score += 25
    return min(score, 100)


def _baseline_verdict(excess_all: float | None, excess_liq: float | None) -> str:
    if excess_all is None or excess_liq is None:
        return "Insufficient Data"
    if excess_all > 0 and excess_liq > 0:
        return "Beat Baselines"
    if excess_all < 0 and excess_liq < 0:
        return "Failed Baselines"
    return "Mixed"


def _quality_row(metric: str, value: str, status: str, detail: str, updated_at: str) -> dict[str, str]:
    return {"metric": metric, "value": value, "status": status, "detail": detail, "updated_at": updated_at}


def _label_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT final_label, COUNT(*) AS count FROM filter_results GROUP BY final_label").fetchall()
    counts = {label: 0 for label in LABELS}
    counts.update({row["final_label"]: int(row["count"]) for row in rows})
    return counts


def _table_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id DESC").fetchall()]


def _clear(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(f"DELETE FROM {table}")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _median_or_none(values: list[float]) -> float | None:
    return median(values) if values else None


def _liquidity_bucket(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "missing"
    if number < 25_000:
        return "<25k"
    if number < 50_000:
        return "25k-50k"
    if number < 100_000:
        return "50k-100k"
    if number < 250_000:
        return "100k-250k"
    return "250k+"


def _age_bucket(value: Any) -> str:
    number = _num(value)
    if number is None:
        return "missing"
    if number < 1:
        return "<1h"
    if number < 6:
        return "1h-6h"
    if number < 24:
        return "6h-24h"
    if number < 168:
        return "1d-7d"
    return "7d+"


def _buy_ratio(metrics: dict[str, Any]) -> float | None:
    explicit = _num(metrics.get("buy_ratio"))
    if explicit is not None:
        return explicit
    buys = _num(metrics.get("buys_h1") or metrics.get("txns_1h_buys"))
    sells = _num(metrics.get("sells_h1") or metrics.get("txns_1h_sells"))
    if buys is None or sells is None or buys + sells <= 0:
        return None
    return buys / (buys + sells)


def _diff(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return left - right


def _loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _num(value: Any) -> float | None:
    if value in {None, "", "null"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _scalar(conn: sqlite3.Connection, query: str) -> Any:
    row = conn.execute(query).fetchone()
    return row[0] if row else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
