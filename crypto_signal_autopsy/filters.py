from __future__ import annotations

from dataclasses import dataclass
from sqlite3 import Row
from typing import Any

from crypto_signal_autopsy.clients.goplus import SecurityResult
from crypto_signal_autopsy.config import Settings


@dataclass(frozen=True)
class Evaluation:
    accepted: bool
    signal_types: list[str]
    rejection_reasons: list[str]
    filter_version: str


def evaluate_candidate(
    metrics: dict[str, Any],
    security: SecurityResult,
    previous_snapshot: Row | None,
    settings: Settings,
) -> Evaluation:
    rejections = _hard_rejections(metrics, security, settings)
    if rejections:
        return Evaluation(False, [], rejections, settings.filter_version)

    signal_types: list[str] = []
    if _is_clean_momentum(metrics, settings):
        signal_types.append("clean_momentum")
    if _is_liquidity_expansion(metrics, previous_snapshot, settings):
        signal_types.append("liquidity_expansion")
    if any(tag.startswith("boost_") for tag in metrics.get("source_tags", [])):
        signal_types.append("boosted_but_not_garbage")

    if not signal_types:
        return Evaluation(False, [], ["no_signal_pattern"], settings.filter_version)

    return Evaluation(True, signal_types, [], settings.filter_version)


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
