from __future__ import annotations

from statistics import mean, median
import json
import sqlite3
from typing import Any

from crypto_signal_autopsy.config import Settings
from crypto_signal_autopsy.review import humanize_reason, money, pct


TABLE_KEYS = [
    "rejected_1h",
    "accepted_1h",
    "rejected_24h",
    "accepted_24h",
]


def build_analysis_payload(conn: sqlite3.Connection, settings: Settings) -> dict[str, Any]:
    rejected_rows = _rejected_rows(conn, settings)
    accepted_rows = _accepted_rows(conn, settings)
    tables = {
        "rejected_1h": [row for row in rejected_rows if row["horizon"] == "1h"],
        "accepted_1h": [row for row in accepted_rows if row["horizon"] == "1h"],
        "rejected_24h": [row for row in rejected_rows if row["horizon"] == "24h"],
        "accepted_24h": [row for row in accepted_rows if row["horizon"] == "24h"],
    }
    for key in tables:
        tables[key] = sorted(tables[key], key=lambda item: item["observed_at"] or "", reverse=True)

    return {
        "tables": tables,
        "summaries": {key: _summary(rows) for key, rows in tables.items()},
        "extremes": {
            "rejected_best": _take_sorted(rejected_rows, reverse=True),
            "rejected_worst": _take_sorted(rejected_rows, reverse=False),
            "accepted_best": _take_sorted(accepted_rows, reverse=True),
            "accepted_worst": _take_sorted(accepted_rows, reverse=False),
        },
        "filter_rules": filter_rule_rows(settings),
    }


def filter_rule_rows(settings: Settings) -> list[dict[str, str]]:
    return [
        {
            "Rule": "Liquidity",
            "Accept needs": f">= {money(settings.min_liquidity_usd)}",
            "Rejects when": f"liquidity is below {money(settings.min_liquidity_usd)}",
            "Why it matters": "Low liquidity can make price jumps look huge and exits very hard.",
        },
        {
            "Rule": "24h volume",
            "Accept needs": f">= {money(settings.min_volume_24h_usd)}",
            "Rejects when": f"24h volume is below {money(settings.min_volume_24h_usd)}",
            "Why it matters": "Low volume means weak demand and noisy price movement.",
        },
        {
            "Rule": "Pair age",
            "Accept needs": f">= {settings.min_pair_age_hours:.2f}h",
            "Rejects when": f"pair age is below {settings.min_pair_age_hours:.2f}h",
            "Why it matters": "Very new pairs have almost no history and often behave like pure launch chaos.",
        },
        {
            "Rule": "1h price move",
            "Accept needs": f"<= {settings.max_price_change_h1_pct:.2f}%",
            "Rejects when": f"1h move is above {settings.max_price_change_h1_pct:.2f}%",
            "Why it matters": "Overextended moves can mean the easy move already happened.",
        },
        {
            "Rule": "Clean momentum pattern",
            "Accept needs": (
                f"1h move >= {settings.clean_momentum_min_h1_pct:.2f}% and 1h volume >= "
                f"{settings.volume_spike_multiple:.2f}x average hourly volume"
            ),
            "Rejects when": "the token passes hard filters but has no signal pattern",
            "Why it matters": "This avoids accepting tokens that are clean but not actually showing momentum.",
        },
        {
            "Rule": "Liquidity expansion pattern",
            "Accept needs": f"liquidity rising by >= {settings.liquidity_expansion_min_pct:.2f}% from previous snapshot",
            "Rejects when": "liquidity is not expanding enough after hard filters",
            "Why it matters": "Rising liquidity can be a healthier sign than price movement alone.",
        },
        {
            "Rule": "Boosted token pattern",
            "Accept needs": "boosted token source plus all hard filters passed",
            "Rejects when": "boosted token still fails liquidity, volume, age, price, or security checks",
            "Why it matters": "Boosted tokens are marketing, not proof of quality.",
        },
        {
            "Rule": "GoPlus security",
            "Accept needs": "no critical risk when GoPlus token is connected",
            "Rejects when": "critical security risks or high buy/sell tax are found",
            "Why it matters": "Security checks help avoid honeypot/tax/owner-risk style failures.",
        },
    ]


def _rejected_rows(conn: sqlite3.Connection, settings: Settings) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.*, c.observed_at AS detected_at, c.rejection_reasons, c.raw_metrics
        FROM rejected_candidate_outcomes r
        JOIN candidate_evaluations c ON c.id = r.candidate_evaluation_id
        ORDER BY r.observed_at DESC
        """
    ).fetchall()
    output = []
    for row in rows:
        metrics = _loads(row["raw_metrics"], {})
        reasons = _loads(row["rejection_reasons"], [])
        net = _as_float(row["net_return_pct"])
        output.append(
            {
                "kind": "Rejected",
                "horizon": row["horizon"],
                "symbol": metrics.get("base_symbol") or "UNKNOWN",
                "name": metrics.get("base_name") or "",
                "detected_at": row["detected_at"],
                "observed_at": row["observed_at"],
                "net_return_pct": net,
                "raw_return_pct": _as_float(row["raw_return_pct"]),
                "net": pct(net),
                "raw": pct(_as_float(row["raw_return_pct"])),
                "liquidity": money(_as_float(metrics.get("liquidity_usd"))),
                "volume24h": money(_as_float(metrics.get("volume_h24_usd"))),
                "age": _hours(_as_float(metrics.get("pair_age_hours"))),
                "move1h": pct(_as_float(metrics.get("price_change_h1_pct"))),
                "result": _result_label(net),
                "reason": "; ".join(humanize_reason(reason) for reason in reasons),
                "diagnosis": _diagnose_rejected(net, reasons, metrics, settings),
                "url": _dex_url(metrics, row["chain_id"], row["pair_address"]),
            }
        )
    return output


def _accepted_rows(conn: sqlite3.Connection, settings: Settings) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT o.*, s.created_at AS detected_at, s.base_symbol, s.signal_type,
               s.detection_liquidity_usd, s.detection_volume_h24_usd,
               s.detection_pair_age_hours, s.source_tags, s.chain_id, s.pair_address
        FROM signal_outcomes o
        JOIN signals s ON s.signal_id = o.signal_id
        ORDER BY o.observed_at DESC
        """
    ).fetchall()
    output = []
    for row in rows:
        net = _as_float(row["net_return_pct"])
        signal_type = row["signal_type"] or ""
        output.append(
            {
                "kind": "Accepted",
                "horizon": row["horizon"],
                "symbol": row["base_symbol"] or "UNKNOWN",
                "name": signal_type.replace("_", " ").title(),
                "detected_at": row["detected_at"],
                "observed_at": row["observed_at"],
                "net_return_pct": net,
                "raw_return_pct": _as_float(row["raw_return_pct"]),
                "net": pct(net),
                "raw": pct(_as_float(row["raw_return_pct"])),
                "liquidity": money(_as_float(row["detection_liquidity_usd"])),
                "volume24h": money(_as_float(row["detection_volume_h24_usd"])),
                "age": _hours(_as_float(row["detection_pair_age_hours"])),
                "move1h": "Passed",
                "result": _result_label(net),
                "reason": f"Accepted as {signal_type}",
                "diagnosis": _diagnose_accepted(net, signal_type, settings),
                "url": f"https://dexscreener.com/{row['chain_id']}/{row['pair_address']}",
            }
        )
    return output


def _summary(rows: list[dict[str, Any]]) -> dict[str, str]:
    values = [row["net_return_pct"] for row in rows if row.get("net_return_pct") is not None]
    if not values:
        return {
            "count": str(len(rows)),
            "priced": "0",
            "median": "No data",
            "average": "No data",
            "best": "No data",
            "worst": "No data",
            "positive_rate": "No data",
        }
    positive = sum(1 for value in values if value > 0)
    return {
        "count": str(len(rows)),
        "priced": str(len(values)),
        "median": pct(median(values)),
        "average": pct(mean(values)),
        "best": pct(max(values)),
        "worst": pct(min(values)),
        "positive_rate": f"{positive}/{len(values)} ({positive / len(values) * 100:.1f}%)",
    }


def _take_sorted(rows: list[dict[str, Any]], reverse: bool, limit: int = 10) -> list[dict[str, Any]]:
    valid = [row for row in rows if row.get("net_return_pct") is not None]
    return sorted(valid, key=lambda row: row["net_return_pct"], reverse=reverse)[:limit]


def _diagnose_rejected(
    net_return_pct: float | None,
    reasons: list[str],
    metrics: dict[str, Any],
    settings: Settings,
) -> str:
    if net_return_pct is None:
        return "No outcome price was available, so performance cannot be judged yet."

    reason_text = []
    if "liquidity_below_min" in reasons:
        reason_text.append(f"liquidity was only {money(_as_float(metrics.get('liquidity_usd')))}")
    if "volume_24h_below_min" in reasons:
        reason_text.append(f"24h volume was only {money(_as_float(metrics.get('volume_h24_usd')))}")
    if "pair_too_young" in reasons:
        reason_text.append(f"pair age was {_hours(_as_float(metrics.get('pair_age_hours')))}")
    if "h1_move_overextended" in reasons:
        reason_text.append(f"1h move was already {pct(_as_float(metrics.get('price_change_h1_pct')))}")
    if any(reason.startswith("missing_") for reason in reasons):
        reason_text.append("required data was missing")

    context = "; ".join(reason_text) or "it failed the rejection rules"
    if net_return_pct >= 25:
        return f"Pumped after rejection, but it was still high risk because {context}."
    if net_return_pct > 0:
        return f"Recovered after rejection, but the setup was not clean because {context}."
    if net_return_pct <= -25:
        return f"Bad outcome fits the rejection: {context}."
    return f"Mostly weak/flat after rejection; rejection reason was {context}."


def _diagnose_accepted(net_return_pct: float | None, signal_type: str, settings: Settings) -> str:
    if net_return_pct is None:
        return "Accepted signal is waiting for a mature outcome."
    label = signal_type.replace("_", " ") or "signal"
    if net_return_pct >= 10:
        return f"Accepted {label} worked after filters; compare against baselines before trusting it."
    if net_return_pct <= -10:
        return f"Accepted {label} failed after entry; inspect whether timing, liquidity, or delayed entry broke it."
    return f"Accepted {label} was close to flat; not strong enough to prove edge."


def _result_label(net_return_pct: float | None) -> str:
    if net_return_pct is None:
        return "Missing"
    if net_return_pct >= 25:
        return "Strong winner"
    if net_return_pct > 0:
        return "Positive"
    if net_return_pct <= -25:
        return "Bad loser"
    return "Weak or flat"


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _hours(value: float | None) -> str:
    if value is None:
        return "Missing"
    return f"{value:.2f}h"


def _dex_url(metrics: dict[str, Any], chain_id: str, pair_address: str) -> str:
    raw = metrics.get("raw_json") or {}
    if isinstance(raw, dict) and raw.get("url"):
        return str(raw["url"])
    if chain_id and pair_address:
        return f"https://dexscreener.com/{chain_id}/{pair_address}"
    return ""
