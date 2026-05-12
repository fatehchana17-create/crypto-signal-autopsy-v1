from __future__ import annotations

from collections import Counter, defaultdict
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
    "Momentum Trap",
    "Weak Overextended Pump",
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
    "winner_survival_lab",
    "tradable_candidates",
    "winner_loser_dna",
    "delayed_entry_simulator",
    "wallet_reputation_memory",
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
        "winner_survival_lab": _refresh_winner_survival_lab(conn, updated_at),
        "tradable_candidates": _refresh_tradable_candidates(conn, updated_at),
        "winner_loser_dna": _refresh_winner_loser_dna(conn, updated_at),
        "delayed_entry_simulator": _refresh_delayed_entry_simulator(conn, updated_at),
        "wallet_reputation_memory": _refresh_wallet_reputation_memory(conn, updated_at),
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
        "winner_survival_lab": _table_rows(conn, "winner_survival_lab"),
        "tradable_candidates": _table_rows(conn, "tradable_candidates"),
        "winner_loser_dna": _table_rows(conn, "winner_loser_dna"),
        "delayed_entry_simulator": _table_rows(conn, "delayed_entry_simulator"),
        "wallet_reputation_memory": _table_rows(conn, "wallet_reputation_memory"),
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
    if count == 0:
        return "Inactive"
    if count < 10 and category != "Reject":
        return "Too Small Sample"
    if median_24h is None or positive_rate is None:
        return "Too Small Sample" if count else "Inactive"
    if category in {"Reject", "Momentum Trap", "Weak Overextended Pump"}:
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
    rows = _outcome_rows(conn, labels=("Reject", "Watchlist", "Momentum Trap", "Weak Overextended Pump"))
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


def _refresh_winner_survival_lab(conn: sqlite3.Connection, created_at: str) -> int:
    _clear(conn, "winner_survival_lab")
    grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in _outcome_rows(conn):
        if row["return_pct"] is None:
            continue
        grouped[(row["pair_id"], row["scan_time"], row["token_address"])].append(row)

    inserted = 0
    for rows in grouped.values():
        rows.sort(key=lambda row: _horizon_rank(row["horizon"]))
        first = rows[0]
        latest = rows[-1]
        metrics = _loads(first["raw_metrics"], {})
        returns = [_num(row["return_pct"]) for row in rows]
        returns = [value for value in returns if value is not None]
        if not returns:
            continue

        liquidity_retention = _retention_pct(latest["liquidity_at_scan"], latest["liquidity_at_horizon"])
        volume_retention = _retention_pct(latest["volume_at_scan"], latest["volume_at_horizon"])
        pump_strength = _pump_strength_score(metrics, first)
        trap_risk = _trap_risk_score(metrics, first, rows, liquidity_retention, volume_retention)
        survival_score = _survival_score(rows, liquidity_retention, volume_retention, trap_risk)
        latest_return = _num(latest["return_pct"])
        best_return = max(returns)
        worst_return = min(returns)
        matured_horizons = [row["horizon"] for row in rows if row["return_pct"] is not None]
        survival_label = _survival_label(survival_score, latest["horizon"], latest_return)
        setup_type = _survival_setup_type(
            first["label_at_scan"],
            pump_strength,
            trap_risk,
            survival_score,
            best_return,
        )
        learning_note = _survival_learning_note(
            first["label_at_scan"],
            setup_type,
            pump_strength,
            trap_risk,
            survival_score,
            best_return,
            worst_return,
        )
        next_rule = _survival_next_rule(setup_type, trap_risk, survival_score, liquidity_retention, volume_retention)
        conn.execute(
            """
            INSERT INTO winner_survival_lab (
              token_address, symbol, pair_id, original_label, scan_time,
              setup_type, pump_strength_score, trap_risk_score, survival_score,
              survival_label, matured_horizons, latest_horizon, latest_return_pct,
              best_return_pct, worst_return_pct, liquidity_retention_pct,
              volume_retention_pct, buy_ratio_at_scan, pair_age_hours_at_scan,
              price_change_1h_at_scan, ten_x_score, learning_note,
              next_rule_to_test, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                first["token_address"],
                metrics.get("base_symbol") or "UNKNOWN",
                first["pair_id"],
                first["label_at_scan"],
                first["scan_time"],
                setup_type,
                pump_strength,
                trap_risk,
                survival_score,
                survival_label,
                db.to_json(matured_horizons),
                latest["horizon"],
                latest_return,
                best_return,
                worst_return,
                liquidity_retention,
                volume_retention,
                _buy_ratio(metrics),
                metrics.get("pair_age_hours"),
                metrics.get("price_change_h1_pct"),
                first["ten_x_score"],
                learning_note,
                next_rule,
                created_at,
            ),
        )
        inserted += 1
    return inserted


def _refresh_tradable_candidates(conn: sqlite3.Connection, created_at: str) -> int:
    _clear(conn, "tradable_candidates")
    rows = conn.execute(
        """
        SELECT w.*, f.risk_score, f.opportunity_score, f.ten_x_score,
               f.reject_reasons, c.raw_metrics
        FROM winner_survival_lab w
        LEFT JOIN filter_results f
          ON f.pair_id = w.pair_id
         AND f.scan_time = w.scan_time
         AND f.token_address = w.token_address
        LEFT JOIN candidate_evaluations c
          ON c.token_address = w.token_address
         AND c.observed_at = w.scan_time
         AND w.pair_id = c.chain_id || ':' || c.pair_address
        """
    ).fetchall()

    prepared: list[dict[str, Any]] = []
    for row in rows:
        metrics = _loads(row["raw_metrics"], {})
        assessment = _tradable_assessment(row, metrics)
        if not assessment:
            continue
        prepared.append(assessment)
    prepared.sort(key=lambda item: (item["tradable_score"], item["survival_score"]), reverse=True)

    for item in prepared[:80]:
        conn.execute(
            """
            INSERT INTO tradable_candidates (
              token_address, symbol, pair_id, source_label, scan_time,
              tradable_score, tradable_tier, research_window, latest_horizon,
              latest_return_pct, best_return_pct, worst_return_pct,
              liquidity_usd, volume_24h_usd, buy_ratio, pair_age_hours,
              price_change_1h_pct, risk_score, opportunity_score, ten_x_score,
              pump_strength_score, trap_risk_score, survival_score,
              liquidity_retention_pct, volume_retention_pct, reasons,
              risk_notes, action_note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["token_address"],
                item["symbol"],
                item["pair_id"],
                item["source_label"],
                item["scan_time"],
                item["tradable_score"],
                item["tradable_tier"],
                item["research_window"],
                item["latest_horizon"],
                item["latest_return_pct"],
                item["best_return_pct"],
                item["worst_return_pct"],
                item["liquidity_usd"],
                item["volume_24h_usd"],
                item["buy_ratio"],
                item["pair_age_hours"],
                item["price_change_1h_pct"],
                item["risk_score"],
                item["opportunity_score"],
                item["ten_x_score"],
                item["pump_strength_score"],
                item["trap_risk_score"],
                item["survival_score"],
                item["liquidity_retention_pct"],
                item["volume_retention_pct"],
                db.to_json(item["reasons"]),
                db.to_json(item["risk_notes"]),
                item["action_note"],
                created_at,
            ),
        )
    return len(prepared[:80])


def _refresh_winner_loser_dna(conn: sqlite3.Connection, created_at: str) -> int:
    _clear(conn, "winner_loser_dna")
    rows = conn.execute(
        """
        SELECT w.*, c.raw_metrics
        FROM winner_survival_lab w
        LEFT JOIN candidate_evaluations c
          ON c.token_address = w.token_address
         AND c.observed_at = w.scan_time
         AND w.pair_id = c.chain_id || ':' || c.pair_address
        """
    ).fetchall()
    winner_rows = [
        row
        for row in rows
        if (_num(row["best_return_pct"]) or 0) >= 50 and (_num(row["survival_score"]) or 0) >= 50
    ]
    loser_rows = [
        row
        for row in rows
        if (_num(row["worst_return_pct"]) or 0) <= -25
        or (_num(row["survival_score"]) or 0) < 35
        or row["setup_type"] in {"Accepted Failure", "Dangerous Momentum Trap"}
    ]
    profiles = [
        _dna_profile("Winner DNA", winner_rows, created_at),
        _dna_profile("Loser DNA", loser_rows, created_at),
    ]
    inserted = 0
    for profile in profiles:
        conn.execute(
            """
            INSERT INTO winner_loser_dna (
              dna_type, sample_count, median_liquidity_usd,
              median_volume_24h_usd, median_pair_age_hours,
              median_price_change_1h_pct, median_buy_ratio,
              median_pump_strength_score, median_trap_risk_score,
              median_survival_score, median_best_return_pct,
              median_worst_return_pct, common_source_labels,
              pattern_summary, rule_implication, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile["dna_type"],
                profile["sample_count"],
                profile["median_liquidity_usd"],
                profile["median_volume_24h_usd"],
                profile["median_pair_age_hours"],
                profile["median_price_change_1h_pct"],
                profile["median_buy_ratio"],
                profile["median_pump_strength_score"],
                profile["median_trap_risk_score"],
                profile["median_survival_score"],
                profile["median_best_return_pct"],
                profile["median_worst_return_pct"],
                profile["common_source_labels"],
                profile["pattern_summary"],
                profile["rule_implication"],
                created_at,
            ),
        )
        inserted += 1
    return inserted


def _refresh_delayed_entry_simulator(conn: sqlite3.Connection, created_at: str) -> int:
    _clear(conn, "delayed_entry_simulator")
    rows = _outcome_rows(conn)
    grouped: dict[tuple[str, str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if row["price_at_horizon"] is None:
            continue
        grouped[(row["pair_id"], row["scan_time"], row["token_address"])].append(row)

    simulated: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    entry_delays = {"15m", "30m", "1h"}
    for candidate_rows in grouped.values():
        by_horizon = {row["horizon"]: row for row in candidate_rows}
        label = candidate_rows[0]["label_at_scan"] or "Unknown"
        for entry_delay in entry_delays:
            entry_row = by_horizon.get(entry_delay)
            entry_price = _num(entry_row["price_at_horizon"] if entry_row else None)
            if entry_price is None or entry_price <= 0:
                continue
            for exit_horizon in HORIZONS:
                if _horizon_rank(exit_horizon) <= _horizon_rank(entry_delay):
                    continue
                exit_row = by_horizon.get(exit_horizon)
                exit_price = _num(exit_row["price_at_horizon"] if exit_row else None)
                if exit_price is None:
                    continue
                simulated[(label, entry_delay, exit_horizon)].append((exit_price / entry_price - 1) * 100)

    inserted = 0
    for (label, entry_delay, exit_horizon), values in sorted(simulated.items()):
        if not values:
            continue
        med = median(values)
        avg = mean(values)
        positive_rate = sum(1 for value in values if value > 0) / len(values)
        verdict = _delayed_entry_verdict(len(values), med, positive_rate, min(values))
        lesson = _delayed_entry_lesson(label, entry_delay, exit_horizon, verdict)
        conn.execute(
            """
            INSERT INTO delayed_entry_simulator (
              source_label, entry_delay, exit_horizon, sample_count,
              median_return_pct, average_return_pct, positive_rate,
              best_return_pct, worst_return_pct, verdict, lesson, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                label,
                entry_delay,
                exit_horizon,
                len(values),
                med,
                avg,
                positive_rate,
                max(values),
                min(values),
                verdict,
                lesson,
                created_at,
            ),
        )
        inserted += 1
    return inserted


def _refresh_wallet_reputation_memory(conn: sqlite3.Connection, created_at: str) -> int:
    _clear(conn, "wallet_reputation_memory")
    rows = conn.execute(
        """
        SELECT *
        FROM wallets
        ORDER BY wallet_quality_score DESC, wallet_risk_score ASC, tokens_traded DESC
        LIMIT 300
        """
    ).fetchall()
    inserted = 0
    for row in rows:
        tier = _wallet_memory_tier(row)
        note = _wallet_reputation_note(row, tier)
        caution = _wallet_caution_note(row)
        conn.execute(
            """
            INSERT INTO wallet_reputation_memory (
              wallet_address, chain, wallet_label, memory_tier,
              wallet_quality_score, wallet_risk_score, tokens_traded,
              realized_exits, win_rate, median_realized_return_pct,
              avg_realized_return_pct, rug_exposure_rate, quick_dump_rate,
              first_minute_entry_rate, avg_hold_time_minutes,
              reputation_note, caution_note, last_seen_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["wallet_address"],
                row["chain"],
                row["wallet_label"],
                tier,
                row["wallet_quality_score"],
                row["wallet_risk_score"],
                row["tokens_traded"],
                row["realized_exits"],
                row["win_rate"],
                row["median_realized_return_pct"],
                row["avg_realized_return_pct"],
                row["rug_exposure_rate"],
                row["quick_dump_rate"],
                row["first_minute_entry_rate"],
                row["avg_hold_time_minutes"],
                note,
                caution,
                row["last_seen_at"],
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


def _pump_strength_score(metrics: dict[str, Any], row: sqlite3.Row | None = None) -> float:
    score = 0.0
    liquidity = _num(metrics.get("liquidity_usd")) or _num(row["liquidity_at_scan"] if row else None) or 0
    volume = _num(metrics.get("volume_h24_usd")) or _num(row["volume_at_scan"] if row else None) or 0
    volume_liquidity_ratio = _num(metrics.get("volume_liquidity_ratio"))
    if volume_liquidity_ratio is None and liquidity > 0:
        volume_liquidity_ratio = volume / liquidity
    age = _num(metrics.get("pair_age_hours")) or 0
    move = _num(metrics.get("price_change_h1_pct")) or 0
    buy_ratio = _buy_ratio(metrics)
    buyers = _num(metrics.get("unique_buyers_1h") or metrics.get("buys_h1") or metrics.get("txns_1h_buys")) or 0
    ten_x = _num(row["ten_x_score"] if row else None)

    if 50_000 <= liquidity <= 500_000:
        score += 20
    elif 25_000 <= liquidity < 50_000 or 500_000 < liquidity <= 1_000_000:
        score += 12
    elif liquidity > 0:
        score += 4

    if volume_liquidity_ratio is not None:
        if 1 <= volume_liquidity_ratio <= 8:
            score += 20
        elif 8 < volume_liquidity_ratio <= 15:
            score += 12
        elif 0.4 <= volume_liquidity_ratio < 1:
            score += 8
        elif volume_liquidity_ratio > 15:
            score += 5

    if buy_ratio is not None:
        if buy_ratio >= 0.55:
            score += 15
        elif buy_ratio >= 0.45:
            score += 10
        elif buy_ratio >= 0.35:
            score += 4

    if buyers >= 100:
        score += 10
    elif buyers >= 50:
        score += 7
    elif buyers >= 20:
        score += 4

    if 20 <= age * 60 <= 360:
        score += 20
    elif 6 < age <= 24:
        score += 10
    elif 10 <= age * 60 < 20:
        score += 5

    if 20 <= move <= 180:
        score += 15
    elif 5 <= move < 20:
        score += 8
    elif 180 < move <= 700:
        score += 8

    if ten_x is not None:
        if ten_x >= 75:
            score += 10
        elif ten_x >= 60:
            score += 5
    return min(score, 100.0)


def _trap_risk_score(
    metrics: dict[str, Any],
    row: sqlite3.Row,
    rows: list[sqlite3.Row],
    liquidity_retention_pct: float | None,
    volume_retention_pct: float | None,
) -> float:
    score = 0.0
    liquidity = _num(metrics.get("liquidity_usd")) or _num(row["liquidity_at_scan"]) or 0
    volume = _num(metrics.get("volume_h24_usd")) or _num(row["volume_at_scan"]) or 0
    volume_liquidity_ratio = _num(metrics.get("volume_liquidity_ratio"))
    if volume_liquidity_ratio is None and liquidity > 0:
        volume_liquidity_ratio = volume / liquidity
    age = _num(metrics.get("pair_age_hours")) or 0
    move = _num(metrics.get("price_change_h1_pct")) or 0
    buy_ratio = _buy_ratio(metrics)
    security_score = _num(row["security_score"])
    flags = _loads(row["security_risk_flags"], [])
    worst_return = min((_num(item["return_pct"]) or 0) for item in rows)

    if move > 700:
        score += 25
    elif move > 250:
        score += 18
    elif move > 150:
        score += 12
    elif move > 80:
        score += 7
    if age < 0.75 and move >= 80:
        score += 15
    if age < 0.33:
        score += 10
    if buy_ratio is None and move >= 80:
        score += 5
    elif buy_ratio is not None and buy_ratio < 0.35:
        score += 18
    elif buy_ratio is not None and buy_ratio < 0.45:
        score += 10
    if volume_liquidity_ratio is not None:
        if volume_liquidity_ratio > 20:
            score += 15
        elif volume_liquidity_ratio > 12:
            score += 10
    if liquidity < 25_000:
        score += 12
    elif liquidity < 50_000:
        score += 6
    if security_score is None:
        score += 5
    elif security_score < 60:
        score += 20
    elif security_score < 80:
        score += 8
    if flags:
        score += 15
    if any(item["rugged"] or item["became_illiquid"] or item["volume_disappeared"] for item in rows):
        score += 20
    if worst_return <= -80:
        score += 25
    elif worst_return <= -50:
        score += 15
    elif worst_return <= -25:
        score += 8
    if liquidity_retention_pct is not None:
        if liquidity_retention_pct < 50:
            score += 20
        elif liquidity_retention_pct < 70:
            score += 12
    if volume_retention_pct is not None and volume_retention_pct < 30:
        score += 10
    return min(score, 100.0)


def _survival_score(
    rows: list[sqlite3.Row],
    liquidity_retention_pct: float | None,
    volume_retention_pct: float | None,
    trap_risk_score: float,
) -> float:
    values = [_num(row["return_pct"]) for row in rows]
    returns = [value for value in values if value is not None]
    if not returns:
        return 0.0
    horizon_scores = []
    for value in returns:
        if value >= 50:
            horizon_scores.append(100)
        elif value >= 15:
            horizon_scores.append(80)
        elif value >= 0:
            horizon_scores.append(65)
        elif value >= -10:
            horizon_scores.append(45)
        elif value >= -25:
            horizon_scores.append(25)
        else:
            horizon_scores.append(0)
    score = mean(horizon_scores) * 0.45
    best = max(returns)
    worst = min(returns)
    if best >= 100:
        score += 20
    elif best >= 50:
        score += 15
    elif best >= 15:
        score += 8
    if worst >= -10:
        score += 15
    elif worst >= -25:
        score += 8
    if liquidity_retention_pct is not None:
        if liquidity_retention_pct >= 80:
            score += 10
        elif liquidity_retention_pct >= 60:
            score += 5
    if volume_retention_pct is not None and volume_retention_pct >= 40:
        score += 5
    score -= trap_risk_score * 0.18
    return max(0.0, min(score, 100.0))


def _survival_label(score: float, latest_horizon: str, latest_return_pct: float | None) -> str:
    if latest_return_pct is None:
        return "Waiting For Outcome"
    if latest_horizon != "24h":
        if score >= 70:
            return "Surviving So Far"
        if score < 30:
            return "Fading Early"
        return "Still Testing"
    if score >= 75:
        return "24h Winner Survivor"
    if score >= 55:
        return "24h Survived"
    if score >= 35:
        return "Weak 24h Survival"
    return "Failed Survival"


def _survival_setup_type(
    original_label: str | None,
    pump_strength_score: float,
    trap_risk_score: float,
    survival_score: float,
    best_return_pct: float,
) -> str:
    label = original_label or ""
    accepted = label in {"Research Candidate", "Pending Paper Candidate", "Paper Trade Candidate"}
    rejected_or_watched = label in {"Reject", "Watchlist", "Momentum Trap", "Weak Overextended Pump"}
    if label == "Paper Trade Candidate" and survival_score >= 55:
        return "Paper Candidate Survivor"
    if accepted and survival_score < 35:
        return "Accepted Failure"
    if rejected_or_watched and best_return_pct >= 50 and trap_risk_score < 65 and pump_strength_score >= 55:
        return "Missed Winner Study"
    if trap_risk_score >= 70:
        return "Dangerous Momentum Trap"
    if pump_strength_score >= 65 and trap_risk_score < 55:
        return "Healthy High-Risk Momentum"
    if pump_strength_score >= 50 and trap_risk_score >= 55:
        return "Weak Overextended Pump"
    if accepted and trap_risk_score < 50:
        return "Clean Research Candidate"
    return "Needs More Evidence"


def _survival_learning_note(
    original_label: str | None,
    setup_type: str,
    pump_strength_score: float,
    trap_risk_score: float,
    survival_score: float,
    best_return_pct: float,
    worst_return_pct: float,
) -> str:
    if setup_type == "Missed Winner Study":
        return "Rejected or watched token later pumped; study whether the strict filters blocked tradable early momentum."
    if setup_type == "Accepted Failure":
        return "Accepted/research token failed after detection; promotion rules need more post-detection survival evidence."
    if setup_type == "Paper Candidate Survivor":
        return "Paper candidate survived after detection; keep tracking against baselines before trusting the label."
    if setup_type == "Dangerous Momentum Trap":
        return "Trap risk dominated the setup; do not loosen filters from this row unless survival improves across horizons."
    if survival_score >= 60 and best_return_pct >= 50:
        return "Strong pump plus survival appeared together; compare this pattern against future winners."
    if worst_return_pct <= -50:
        return "Large adverse move appeared after detection; check liquidity fade, buyer continuation, and timing."
    if pump_strength_score >= 65 and trap_risk_score < 55:
        return "Early momentum looks healthy so far, but sample size and delayed-entry survival still matter."
    return f"{original_label or 'Token'} needs more evidence before changing filters."


def _survival_next_rule(
    setup_type: str,
    trap_risk_score: float,
    survival_score: float,
    liquidity_retention_pct: float | None,
    volume_retention_pct: float | None,
) -> str:
    if liquidity_retention_pct is not None and liquidity_retention_pct < 70:
        return "Require stronger liquidity retention before promotion."
    if volume_retention_pct is not None and volume_retention_pct < 30:
        return "Require volume continuation after detection."
    if setup_type == "Missed Winner Study":
        return "Test a separate high-risk momentum route instead of weakening clean filters."
    if setup_type == "Accepted Failure":
        return "Delay promotion until 30m and 1h survival checks pass."
    if trap_risk_score >= 70:
        return "Keep trap guard strict; review only after repeated survivor examples."
    if survival_score >= 60:
        return "Compare against same-age and same-liquidity baselines before extending."
    return "Keep research-only until more horizons mature."


def _retention_pct(start: Any, end: Any) -> float | None:
    start_number = _num(start)
    end_number = _num(end)
    if start_number is None or end_number is None or start_number <= 0:
        return None
    return (end_number / start_number) * 100


def _horizon_rank(horizon: str) -> int:
    try:
        return list(HORIZONS).index(horizon)
    except ValueError:
        return 999


def _tradable_assessment(row: sqlite3.Row, metrics: dict[str, Any]) -> dict[str, Any] | None:
    source_label = row["original_label"] or ""
    setup_type = row["setup_type"] or ""
    if source_label in {"Reject", "Momentum Trap", "Weak Overextended Pump"}:
        return None
    if setup_type in {"Accepted Failure", "Dangerous Momentum Trap", "Weak Overextended Pump"}:
        return None

    liquidity = _num(metrics.get("liquidity_usd"))
    volume = _num(metrics.get("volume_h24_usd"))
    age = _num(row["pair_age_hours_at_scan"]) or _num(metrics.get("pair_age_hours"))
    move = _num(row["price_change_1h_at_scan"]) or _num(metrics.get("price_change_h1_pct"))
    buy_ratio = _num(row["buy_ratio_at_scan"]) or _buy_ratio(metrics)
    risk_score = _num(row["risk_score"])
    opportunity_score = _num(row["opportunity_score"])
    ten_x_score = _num(row["ten_x_score"])
    pump_strength = _num(row["pump_strength_score"]) or 0
    trap_risk = _num(row["trap_risk_score"]) or 0
    survival = _num(row["survival_score"]) or 0
    latest_return = _num(row["latest_return_pct"])
    best_return = _num(row["best_return_pct"])
    worst_return = _num(row["worst_return_pct"])
    liquidity_retention = _num(row["liquidity_retention_pct"])
    volume_retention = _num(row["volume_retention_pct"])

    if liquidity is None or volume is None or age is None or move is None:
        return None
    if liquidity < 75_000 or volume < 75_000 or age < 1:
        return None
    if trap_risk > 60 or survival < 35:
        return None
    if risk_score is not None and risk_score > 55:
        return None
    if worst_return is not None and worst_return <= -35:
        return None

    score = 0.0
    reasons: list[str] = []
    risk_notes: list[str] = []

    if liquidity >= 250_000:
        score += 25
        reasons.append("deep_liquidity")
    elif liquidity >= 100_000:
        score += 20
        reasons.append("enough_liquidity")
    else:
        score += 10
        risk_notes.append("liquidity_is_only_moderate")

    if volume >= 500_000:
        score += 20
        reasons.append("strong_real_volume")
    elif volume >= 100_000:
        score += 15
        reasons.append("enough_real_volume")
    else:
        score += 8
        risk_notes.append("volume_is_only_moderate")

    if buy_ratio is not None:
        if 0.48 <= buy_ratio <= 0.65:
            score += 15
            reasons.append("balanced_buy_pressure")
        elif 0.45 <= buy_ratio <= 0.72:
            score += 10
            reasons.append("acceptable_buy_pressure")
        else:
            risk_notes.append("buy_pressure_is_not_balanced")
    else:
        risk_notes.append("buy_ratio_missing")

    if survival >= 70:
        score += 20
        reasons.append("strong_post_detection_survival")
    elif survival >= 55:
        score += 15
        reasons.append("survived_after_detection")
    elif survival >= 40:
        score += 8
        risk_notes.append("survival_still_weak")

    if trap_risk <= 25:
        score += 15
        reasons.append("low_trap_risk")
    elif trap_risk <= 45:
        score += 8
        reasons.append("manageable_trap_risk")
    else:
        risk_notes.append("trap_risk_needs_attention")

    if -5 <= move <= 50:
        score += 10
        reasons.append("not_overextended")
    elif 50 < move <= 80:
        score += 5
        risk_notes.append("momentum_is_hot")
    elif move > 80:
        score -= 12
        risk_notes.append("already_strongly_pumped")
    elif move < -15:
        score -= 8
        risk_notes.append("falling_at_detection")

    if 3 <= age <= 168:
        score += 10
        reasons.append("old_enough_to_observe")
    elif 1 <= age < 3:
        score += 5
        risk_notes.append("still_young")

    if risk_score is not None:
        if risk_score <= 30:
            score += 10
            reasons.append("lower_model_risk")
        elif risk_score <= 45:
            score += 6
            reasons.append("acceptable_model_risk")
    if opportunity_score is not None and opportunity_score >= 55:
        score += 5
        reasons.append("good_opportunity_score")
    if pump_strength >= 65 and trap_risk <= 45:
        score += 5
        reasons.append("healthy_momentum_pattern")

    if liquidity_retention is not None and liquidity_retention < 75:
        score -= 15
        risk_notes.append("liquidity_retention_weak")
    if volume_retention is not None and volume_retention < 30:
        score -= 8
        risk_notes.append("volume_retention_weak")
    if latest_return is not None and latest_return < -10:
        score -= 10
        risk_notes.append("latest_return_is_negative")

    score = max(0.0, min(score, 100.0))
    if score >= 78:
        tier = "Tradable Research Candidate"
    elif score >= 65:
        tier = "Cautious Tradable Watch"
    else:
        return None

    return {
        "token_address": row["token_address"],
        "symbol": row["symbol"] or metrics.get("base_symbol") or "UNKNOWN",
        "pair_id": row["pair_id"],
        "source_label": source_label,
        "scan_time": row["scan_time"],
        "tradable_score": score,
        "tradable_tier": tier,
        "research_window": "Small-move research: 5-30% possible range, not promised.",
        "latest_horizon": row["latest_horizon"],
        "latest_return_pct": latest_return,
        "best_return_pct": best_return,
        "worst_return_pct": worst_return,
        "liquidity_usd": liquidity,
        "volume_24h_usd": volume,
        "buy_ratio": buy_ratio,
        "pair_age_hours": age,
        "price_change_1h_pct": move,
        "risk_score": risk_score,
        "opportunity_score": opportunity_score,
        "ten_x_score": ten_x_score,
        "pump_strength_score": pump_strength,
        "trap_risk_score": trap_risk,
        "survival_score": survival,
        "liquidity_retention_pct": liquidity_retention,
        "volume_retention_pct": volume_retention,
        "reasons": reasons,
        "risk_notes": risk_notes or ["risk_still_exists"],
        "action_note": "Research only. If traded manually, use strict risk management; this is not a buy signal.",
    }


def _dna_profile(dna_type: str, rows: list[sqlite3.Row], created_at: str) -> dict[str, Any]:
    del created_at
    labels = Counter(row["original_label"] or "Unknown" for row in rows)
    common_labels = ", ".join(f"{label}: {count}" for label, count in labels.most_common(4))
    sample_count = len(rows)
    profile = {
        "dna_type": dna_type,
        "sample_count": sample_count,
        "median_liquidity_usd": _median_metric_from_rows(rows, "liquidity_usd"),
        "median_volume_24h_usd": _median_metric_from_rows(rows, "volume_h24_usd"),
        "median_pair_age_hours": _median_from_rows(rows, "pair_age_hours_at_scan"),
        "median_price_change_1h_pct": _median_from_rows(rows, "price_change_1h_at_scan"),
        "median_buy_ratio": _median_from_rows(rows, "buy_ratio_at_scan"),
        "median_pump_strength_score": _median_from_rows(rows, "pump_strength_score"),
        "median_trap_risk_score": _median_from_rows(rows, "trap_risk_score"),
        "median_survival_score": _median_from_rows(rows, "survival_score"),
        "median_best_return_pct": _median_from_rows(rows, "best_return_pct"),
        "median_worst_return_pct": _median_from_rows(rows, "worst_return_pct"),
        "common_source_labels": common_labels or "No sample yet",
    }
    if dna_type == "Winner DNA":
        profile["pattern_summary"] = (
            "Winners are rows with +50% or better upside and enough survival after detection."
            if sample_count
            else "Waiting for enough winner samples."
        )
        profile["rule_implication"] = "Look for survival plus low trap risk before loosening strict filters."
    else:
        profile["pattern_summary"] = (
            "Losers are rows with large drawdown, weak survival, accepted failure, or trap behavior."
            if sample_count
            else "Waiting for enough loser samples."
        )
        profile["rule_implication"] = "Delay promotion when liquidity fades, trap risk rises, or survival stays weak."
    return profile


def _median_from_rows(rows: list[sqlite3.Row], key: str) -> float | None:
    values = [_num(row[key]) for row in rows]
    values = [value for value in values if value is not None]
    return median(values) if values else None


def _median_metric_from_rows(rows: list[sqlite3.Row], key: str) -> float | None:
    values = []
    for row in rows:
        metrics = _loads(row["raw_metrics"], {})
        values.append(_num(metrics.get(key)))
    values = [value for value in values if value is not None]
    return median(values) if values else None


def _delayed_entry_verdict(count: int, median_return: float, positive_rate: float, worst_return: float) -> str:
    if count < 10:
        return "Too Small Sample"
    if median_return >= 5 and positive_rate >= 0.55 and worst_return > -35:
        return "Promising Delay"
    if median_return <= -5 or worst_return <= -50:
        return "Delay Fails"
    return "Mixed Delay"


def _delayed_entry_lesson(label: str, entry_delay: str, exit_horizon: str, verdict: str) -> str:
    if verdict == "Promising Delay":
        return f"{label}: waiting {entry_delay} still held up into {exit_horizon}; compare with baselines."
    if verdict == "Delay Fails":
        return f"{label}: waiting {entry_delay} did not protect the setup by {exit_horizon}; keep stricter survival rules."
    return f"{label}: {entry_delay} to {exit_horizon} needs more samples before changing rules."


def _wallet_memory_tier(row: sqlite3.Row) -> str:
    quality = _num(row["wallet_quality_score"]) or 0
    risk = _num(row["wallet_risk_score"]) or 0
    tokens = int(row["tokens_traded"] or 0)
    realized = int(row["realized_exits"] or 0)
    win_rate = _num(row["win_rate"]) or 0
    rug = _num(row["rug_exposure_rate"]) or 0
    if quality >= 70 and risk <= 35 and tokens >= 10 and realized >= 5 and win_rate >= 0.50 and rug <= 0.10:
        return "Useful Wallet Memory"
    if quality >= 50 and risk <= 50 and tokens >= 5:
        return "Watch Wallet Memory"
    if risk >= 65 or rug >= 0.25 or (_num(row["quick_dump_rate"]) or 0) >= 0.35:
        return "Avoid / Suspicious Memory"
    return "Unproven Memory"


def _wallet_reputation_note(row: sqlite3.Row, tier: str) -> str:
    if tier == "Useful Wallet Memory":
        return "Wallet has useful historical behavior, but it is still not a copy-trade signal."
    if tier == "Watch Wallet Memory":
        return "Wallet has some useful history; keep observing before trusting it."
    if tier == "Avoid / Suspicious Memory":
        return "Wallet behavior is risky or dump-heavy; treat token activity around it carefully."
    return "Wallet does not have enough proven history yet."


def _wallet_caution_note(row: sqlite3.Row) -> str:
    notes: list[str] = []
    if (_num(row["rug_exposure_rate"]) or 0) > 0.10:
        notes.append("rug_exposure")
    if (_num(row["quick_dump_rate"]) or 0) > 0.20:
        notes.append("quick_dump_behavior")
    if int(row["realized_exits"] or 0) < 5:
        notes.append("low_realized_exit_history")
    if (_num(row["wallet_risk_score"]) or 0) > 50:
        notes.append("high_wallet_risk")
    return ", ".join(notes) if notes else "normal_caution"


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
