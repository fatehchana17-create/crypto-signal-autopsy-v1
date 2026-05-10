from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Row
from typing import Any

from crypto_signal_autopsy.clients.goplus import SecurityResult
from crypto_signal_autopsy.config import Settings
from crypto_signal_autopsy.scoring import V2Score, score_token


@dataclass(frozen=True)
class Evaluation:
    accepted: bool
    signal_types: list[str]
    rejection_reasons: list[str]
    filter_version: str
    final_label: str = "Reject"
    risk_score: float | None = None
    opportunity_score: float | None = None
    ten_x_score: float | None = None
    ten_x_label: str = ""
    ten_x_reasons: list[str] | None = None
    risk_category: str = ""
    opportunity_category: str = ""
    qualifies_high_risk_momentum: bool = False
    hard_reject: bool = False
    v2_score: V2Score | None = None


def evaluate_candidate(
    metrics: dict[str, Any],
    security: SecurityResult,
    previous_snapshot: Row | None,
    settings: Settings,
) -> Evaluation:
    v2_score = score_token(metrics, security)
    signal_types = _v2_signal_types(v2_score, metrics, previous_snapshot, settings)
    accepted = v2_score.final_label == "Paper Trade Candidate"
    return Evaluation(
        accepted=accepted,
        signal_types=signal_types if accepted else [],
        rejection_reasons=v2_score.reject_reasons,
        filter_version=v2_score.model_version,
        final_label=v2_score.final_label,
        risk_score=v2_score.risk_score,
        opportunity_score=v2_score.opportunity_score,
        ten_x_score=v2_score.ten_x_score,
        ten_x_label=v2_score.ten_x_label,
        ten_x_reasons=v2_score.ten_x_reasons,
        risk_category=v2_score.risk_category,
        opportunity_category=v2_score.opportunity_category,
        qualifies_high_risk_momentum=v2_score.qualifies_high_risk_momentum,
        hard_reject=v2_score.hard_reject,
        v2_score=v2_score,
    )


def _v2_signal_types(
    v2_score: V2Score,
    metrics: dict[str, Any],
    previous_snapshot: Row | None,
    settings: Settings,
) -> list[str]:
    if v2_score.final_label != "Paper Trade Candidate":
        return []
    signal_types = ["paper_trade_candidate"]
    if _is_clean_momentum(metrics, settings):
        signal_types.append("clean_momentum")
    if _is_liquidity_expansion(metrics, previous_snapshot, settings):
        signal_types.append("liquidity_expansion")
    if any(str(tag).startswith("boost_") for tag in metrics.get("source_tags", [])):
        signal_types.append("boosted_research")
    return signal_types


def _hard_rejections(
    metrics: dict[str, Any],
    security: SecurityResult,
    settings: Settings,
) -> list[str]:
    reasons: list[str] = []
    required = ["price_usd", "liquidity_usd", "volume_h24_usd", "pair_age_hours"]
    for field in required:
        if metrics.get(field) is None:
            reasons.append(f"missing_{field}")

    if reasons:
        return reasons

    if metrics["liquidity_usd"] < settings.min_liquidity_usd:
        reasons.append("liquidity_below_min")
    if metrics["volume_h24_usd"] < settings.min_volume_24h_usd:
        reasons.append("volume_24h_below_min")
    if metrics["pair_age_hours"] < settings.min_pair_age_hours:
        reasons.append("pair_too_young")

    price_change_h1 = metrics.get("price_change_h1_pct")
    if price_change_h1 is None:
        reasons.append("missing_price_change_h1")
    elif price_change_h1 > settings.max_price_change_h1_pct:
        reasons.append("h1_move_overextended")

    if settings.require_goplus and security.status != "ok":
        reasons.append(f"goplus_required_{security.status}")
    if security.has_critical_risk:
        reasons.extend(f"goplus_{flag}" for flag in security.risk_flags)
    if security.buy_tax_pct is not None and security.buy_tax_pct > settings.max_buy_tax_pct:
        reasons.append("buy_tax_above_max")
    if security.sell_tax_pct is not None and security.sell_tax_pct > settings.max_sell_tax_pct:
        reasons.append("sell_tax_above_max")

    return reasons


def _is_clean_momentum(metrics: dict[str, Any], settings: Settings) -> bool:
    price_change_h1 = metrics.get("price_change_h1_pct") or 0
    volume_h1 = metrics.get("volume_h1_usd") or 0
    volume_h24 = metrics.get("volume_h24_usd") or 0
    avg_hourly_volume = volume_h24 / 24 if volume_h24 else 0
    return (
        price_change_h1 >= settings.clean_momentum_min_h1_pct
        and volume_h1 >= avg_hourly_volume * settings.volume_spike_multiple
    )


def _is_liquidity_expansion(
    metrics: dict[str, Any],
    previous_snapshot: Row | None,
    settings: Settings,
) -> bool:
    if previous_snapshot is None:
        return False
    previous_liquidity = previous_snapshot["liquidity_usd"]
    current_liquidity = metrics.get("liquidity_usd")
    if not previous_liquidity or not current_liquidity:
        return False
    min_multiplier = 1 + (settings.liquidity_expansion_min_pct / 100)
    if current_liquidity < previous_liquidity * min_multiplier:
        return False
    price_change_h1 = metrics.get("price_change_h1_pct")
    if price_change_h1 is not None and price_change_h1 > 35:
        return False
    volume_h1 = metrics.get("volume_h1_usd") or 0
    volume_h24 = metrics.get("volume_h24_usd") or 0
    return volume_h1 >= (volume_h24 / 24) * 1.5 if volume_h24 else False
