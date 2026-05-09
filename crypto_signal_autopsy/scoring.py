from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crypto_signal_autopsy.clients.goplus import SecurityResult
from crypto_signal_autopsy.config import HARD_REJECT, HIGH_RISK_MOMENTUM, MODEL_VERSION


FINAL_LABELS = [
    "Reject",
    "Watchlist",
    "High-Risk Momentum Watchlist",
    "Research Candidate",
    "Paper Trade Candidate",
]


@dataclass(frozen=True)
class V2Score:
    hard_reject: bool
    qualifies_high_risk_momentum: bool
    reject_reasons: list[str]
    risk_score: float
    opportunity_score: float
    final_label: str
    risk_category: str
    opportunity_category: str
    model_version: str = MODEL_VERSION


def score_token(metrics: dict[str, Any], security: SecurityResult) -> V2Score:
    reject_reasons = hard_reject_reasons(metrics, security)
    risk_score = calculate_risk_score(metrics, security)
    opportunity_score = calculate_opportunity_score(metrics, security)
    high_risk_momentum = qualifies_high_risk_momentum(metrics, security)
    final_label = choose_final_label(
        hard_reject=bool(reject_reasons),
        qualifies_high_risk_momentum=high_risk_momentum,
        risk_score=risk_score,
        opportunity_score=opportunity_score,
    )
    if final_label == "Reject" and not reject_reasons:
        reject_reasons = ["score_below_v2_thresholds"]
    return V2Score(
        hard_reject=bool(reject_reasons and reject_reasons != ["score_below_v2_thresholds"]),
        qualifies_high_risk_momentum=high_risk_momentum,
        reject_reasons=reject_reasons,
        risk_score=risk_score,
        opportunity_score=opportunity_score,
        final_label=final_label,
        risk_category=risk_category(risk_score),
        opportunity_category=opportunity_category(opportunity_score),
    )


def hard_reject_reasons(metrics: dict[str, Any], security: SecurityResult) -> list[str]:
    reasons: list[str] = []
    security_raw = security.raw or {}

    if _security_bool(security, "is_honeypot"):
        reasons.append("honeypot_detected")
    if _security_bool(security, "cannot_sell") or _security_bool(security, "cannot_sell_all"):
        reasons.append("cannot_sell")
    if _security_bool(security, "blacklist_function") or _security_bool(security, "is_blacklisted"):
        reasons.append("blacklist_function_detected")
    if _security_bool(security, "trading_disabled"):
        reasons.append("trading_disabled")
    if _security_bool(security, "is_mintable") and not _security_bool(security, "owner_renounced"):
        reasons.append("dangerous_owner_permissions")
    if "owner_change_balance" in security.risk_flags or "hidden_owner" in security.risk_flags:
        reasons.append("dangerous_owner_permissions")

    liquidity = _num(metrics.get("liquidity_usd"))
    volume_24h = _num(metrics.get("volume_h24_usd"))
    pair_age_minutes = _pair_age_minutes(metrics)
    fdv_liquidity_ratio = _num(metrics.get("fdv_liquidity_ratio"))
    buy_ratio = _buy_ratio(metrics)
    unique_buyers_1h = _unique_buyers_1h(metrics)
    top_holder_pct = _security_num(security_raw, "top_holder_pct", "holder_count_1_pct")
    top_10_holder_pct = _security_num(security_raw, "top_10_holder_pct", "top_10_holder_rate")
    buy_tax = _num(security.buy_tax_pct)
    sell_tax = _num(security.sell_tax_pct)

    if liquidity is None:
        reasons.append("liquidity_missing")
    elif liquidity < HARD_REJECT["min_liquidity_usd"]:
        reasons.append("liquidity_below_10000")
    if volume_24h is not None and volume_24h < HARD_REJECT["min_volume_24h"]:
        reasons.append("volume_24h_below_15000")
    if pair_age_minutes is not None and pair_age_minutes < HARD_REJECT["min_pair_age_minutes"]:
        reasons.append("pair_age_below_10_minutes")
    if top_holder_pct is not None and top_holder_pct > HARD_REJECT["max_top_holder_pct"]:
        reasons.append("top_holder_above_25_percent")
    if top_10_holder_pct is not None and top_10_holder_pct > HARD_REJECT["max_top_10_holder_pct"]:
        reasons.append("top_10_holders_above_60_percent")
    if fdv_liquidity_ratio is not None and fdv_liquidity_ratio > HARD_REJECT["max_fdv_liquidity_ratio"]:
        reasons.append("fdv_liquidity_ratio_above_200")
    if buy_ratio is not None and buy_ratio < HARD_REJECT["min_buy_ratio"]:
        reasons.append("buy_pressure_below_30_percent")
    if unique_buyers_1h is not None and unique_buyers_1h < HARD_REJECT["min_unique_buyers_1h"]:
        reasons.append("unique_buyers_below_10")
    if (buy_tax is not None and buy_tax > HARD_REJECT["max_buy_tax"]) or (
        sell_tax is not None and sell_tax > HARD_REJECT["max_sell_tax"]
    ):
        reasons.append("tax_above_10_percent")

    return sorted(set(reasons))


def qualifies_high_risk_momentum(metrics: dict[str, Any], security: SecurityResult) -> bool:
    security_raw = security.raw or {}
    checks = [
        _gte(_num(metrics.get("liquidity_usd")), HIGH_RISK_MOMENTUM["min_liquidity_usd"]),
        _gte(_num(metrics.get("volume_h24_usd")), HIGH_RISK_MOMENTUM["min_volume_24h"]),
        _gte(_pair_age_minutes(metrics), HIGH_RISK_MOMENTUM["min_pair_age_minutes"]),
        _lte(_num(metrics.get("price_change_h1_pct")), HIGH_RISK_MOMENTUM["max_price_change_1h"]),
        _gte(_txns_15m(metrics), HIGH_RISK_MOMENTUM["min_txns_15m"]),
        _gte(_unique_buyers_15m(metrics), HIGH_RISK_MOMENTUM["min_unique_buyers_15m"]),
        _lte(_security_num(security_raw, "top_holder_pct", "holder_count_1_pct"), HIGH_RISK_MOMENTUM["max_top_holder_pct"]),
        _lte(_security_num(security_raw, "top_10_holder_pct", "top_10_holder_rate"), HIGH_RISK_MOMENTUM["max_top_10_holder_pct"]),
        _lte(_num(metrics.get("fdv_liquidity_ratio")), HIGH_RISK_MOMENTUM["max_fdv_liquidity_ratio"]),
        _gte(_buy_ratio(metrics), HIGH_RISK_MOMENTUM["min_buy_ratio"]),
        _gte(_security_score(security), HIGH_RISK_MOMENTUM["min_security_score"]),
        _lte(_num(security.buy_tax_pct), HIGH_RISK_MOMENTUM["max_buy_tax"]),
        _lte(_num(security.sell_tax_pct), HIGH_RISK_MOMENTUM["max_sell_tax"]),
    ]
    return all(checks)


def calculate_risk_score(metrics: dict[str, Any], security: SecurityResult) -> float:
    score = 0.0
    security_score = _security_score(security)
    buy_tax = _num(security.buy_tax_pct) or 0
    sell_tax = _num(security.sell_tax_pct) or 0
    liquidity = _num(metrics.get("liquidity_usd")) or 0
    top_holder_pct = _security_num(security.raw or {}, "top_holder_pct", "holder_count_1_pct")
    top_10_holder_pct = _security_num(security.raw or {}, "top_10_holder_pct", "top_10_holder_rate")
    pair_age_minutes = _pair_age_minutes(metrics) or 0
    pair_age_hours = pair_age_minutes / 60
    volume_24h = _num(metrics.get("volume_h24_usd")) or 0
    volume_liquidity_ratio = _num(metrics.get("volume_liquidity_ratio")) or 0
    price_change_1h = _num(metrics.get("price_change_h1_pct")) or 0

    if security_score is None:
        score += 20
    elif security_score < 60:
        score += 30
    elif security_score < 80:
        score += 15
    elif security_score < 90:
        score += 5
    if _security_bool(security, "is_honeypot"):
        score += 30
    if buy_tax > 5 or sell_tax > 5:
        score += 10
    if buy_tax > 10 or sell_tax > 10:
        score += 20

    if liquidity < 10_000:
        score += 20
    elif liquidity < 25_000:
        score += 15
    elif liquidity < 50_000:
        score += 10
    elif liquidity < 100_000:
        score += 5

    if top_holder_pct is not None:
        if top_holder_pct > 25:
            score += 15
        elif top_holder_pct > 15:
            score += 10
        elif top_holder_pct > 10:
            score += 5
    if top_10_holder_pct is not None:
        if top_10_holder_pct > 60:
            score += 15
        elif top_10_holder_pct > 45:
            score += 10
        elif top_10_holder_pct > 35:
            score += 5

    if pair_age_minutes < 10:
        score += 10
    elif pair_age_minutes < 60:
        score += 8
    elif pair_age_hours < 6:
        score += 5
    elif pair_age_hours < 24:
        score += 2

    if volume_24h < 15_000:
        score += 10
    elif volume_24h < 50_000:
        score += 6
    elif volume_24h < 100_000:
        score += 3
    if volume_liquidity_ratio > 20:
        score += 10
    elif volume_liquidity_ratio > 10:
        score += 5

    if price_change_1h > 300:
        score += 10
    elif price_change_1h > 150:
        score += 8
    elif price_change_1h > 80:
        score += 5
    elif price_change_1h > 40:
        score += 2

    if _is_boosted(metrics) and liquidity < 25_000:
        score += 5

    return min(score, 100)


def calculate_opportunity_score(metrics: dict[str, Any], security: SecurityResult) -> float:
    score = 0.0
    liquidity = _num(metrics.get("liquidity_usd")) or 0
    volume_24h = _num(metrics.get("volume_h24_usd")) or 0
    buy_ratio = _buy_ratio(metrics)
    unique_buyers_1h = _unique_buyers_1h(metrics) or 0
    pair_age_minutes = _pair_age_minutes(metrics) or 0
    pair_age_hours = pair_age_minutes / 60
    fdv_liquidity_ratio = _num(metrics.get("fdv_liquidity_ratio"))
    price_change_1h = _num(metrics.get("price_change_h1_pct")) or 0
    raw = metrics.get("raw_json") or {}
    info = raw.get("info") if isinstance(raw, dict) else {}
    socials = info.get("socials") if isinstance(info, dict) else []
    websites = info.get("websites") if isinstance(info, dict) else []

    if liquidity >= 250_000:
        score += 15
    elif liquidity >= 100_000:
        score += 12
    elif liquidity >= 50_000:
        score += 9
    elif liquidity >= 25_000:
        score += 5

    if volume_24h >= 1_000_000:
        score += 15
    elif volume_24h >= 500_000:
        score += 12
    elif volume_24h >= 100_000:
        score += 9
    elif volume_24h >= 50_000:
        score += 5

    if buy_ratio is not None:
        if buy_ratio >= 0.65:
            score += 15
        elif buy_ratio >= 0.55:
            score += 12
        elif buy_ratio >= 0.45:
            score += 8
        elif buy_ratio >= 0.40:
            score += 4

    if unique_buyers_1h >= 100:
        score += 10
    elif unique_buyers_1h >= 50:
        score += 8
    elif unique_buyers_1h >= 25:
        score += 5
    elif unique_buyers_1h >= 10:
        score += 2

    if pair_age_hours >= 24:
        score += 10
    elif pair_age_hours >= 6:
        score += 8
    elif pair_age_hours >= 1:
        score += 5
    elif pair_age_minutes >= 10:
        score += 2

    if fdv_liquidity_ratio is not None:
        if fdv_liquidity_ratio <= 10:
            score += 10
        elif fdv_liquidity_ratio <= 25:
            score += 8
        elif fdv_liquidity_ratio <= 50:
            score += 5
        elif fdv_liquidity_ratio <= 100:
            score += 2

    if 5 <= price_change_1h <= 40:
        score += 10
    elif 40 < price_change_1h <= 80:
        score += 6
    elif 80 < price_change_1h <= 150:
        score += 3

    if websites:
        score += 2
    if _social_present(socials, "twitter", "x"):
        score += 2
    if _social_present(socials, "telegram") or _social_present(socials, "discord"):
        score += 2
    if _is_boosted(metrics):
        score += 2
    if _truthy(metrics.get("coingecko_listed")) or _truthy(metrics.get("coinmarketcap_listed")):
        score += 2
    if _truthy(metrics.get("cex_listed")):
        score += 5

    return min(score, 100)


def choose_final_label(
    hard_reject: bool,
    qualifies_high_risk_momentum: bool,
    risk_score: float,
    opportunity_score: float,
) -> str:
    if hard_reject and not qualifies_high_risk_momentum:
        return "Reject"
    if qualifies_high_risk_momentum and opportunity_score >= 50:
        return "High-Risk Momentum Watchlist"
    if risk_score > 75:
        return "Reject"
    if risk_score <= 35 and opportunity_score >= 75:
        return "Paper Trade Candidate"
    if risk_score <= 45 and opportunity_score >= 65:
        return "Research Candidate"
    if risk_score <= 60 and opportunity_score >= 45:
        return "Watchlist"
    return "Reject"


def risk_category(risk_score: float) -> str:
    if risk_score <= 25:
        return "Lower Risk"
    if risk_score <= 50:
        return "Medium Risk"
    if risk_score <= 75:
        return "High Risk"
    return "Extreme Risk"


def opportunity_category(opportunity_score: float) -> str:
    if opportunity_score >= 75:
        return "Strong Opportunity"
    if opportunity_score >= 55:
        return "Moderate Opportunity"
    if opportunity_score >= 35:
        return "Weak Opportunity"
    return "Low Opportunity"


def _pair_age_minutes(metrics: dict[str, Any]) -> float | None:
    value = _num(metrics.get("pair_age_minutes"))
    if value is not None:
        return value
    hours = _num(metrics.get("pair_age_hours"))
    return hours * 60 if hours is not None else None


def _buy_ratio(metrics: dict[str, Any]) -> float | None:
    explicit = _num(metrics.get("buy_ratio"))
    if explicit is not None:
        return explicit
    buys = _num(metrics.get("buys_h1") or metrics.get("txns_1h_buys"))
    sells = _num(metrics.get("sells_h1") or metrics.get("txns_1h_sells"))
    if buys is None or sells is None or buys + sells <= 0:
        return None
    return buys / (buys + sells)


def _txns_15m(metrics: dict[str, Any]) -> float | None:
    explicit = _num(metrics.get("txns_15m"))
    if explicit is not None:
        return explicit
    buys = _num(metrics.get("txns_15m_buys") or metrics.get("buys_m5"))
    sells = _num(metrics.get("txns_15m_sells") or metrics.get("sells_m5"))
    if buys is None or sells is None:
        return None
    return buys + sells


def _unique_buyers_15m(metrics: dict[str, Any]) -> float | None:
    return _num(metrics.get("unique_buyers_15m") or metrics.get("txns_15m_buys") or metrics.get("buys_m5"))


def _unique_buyers_1h(metrics: dict[str, Any]) -> float | None:
    return _num(metrics.get("unique_buyers_1h") or metrics.get("txns_1h_buys") or metrics.get("buys_h1"))


def _security_score(security: SecurityResult) -> float | None:
    raw = security.raw or {}
    direct = _security_num(raw, "security_score", "score")
    if direct is not None:
        return direct
    if security.status != "ok":
        return None
    base = 100.0
    if security.risk_flags:
        base -= min(len(security.risk_flags) * 12, 60)
    if security.buy_tax_pct and security.buy_tax_pct > 5:
        base -= 10
    if security.sell_tax_pct and security.sell_tax_pct > 5:
        base -= 10
    return max(base, 0)


def _security_bool(security: SecurityResult, key: str) -> bool:
    if key in security.risk_flags:
        return True
    raw = security.raw or {}
    return _truthy(raw.get(key))


def _security_num(raw: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _num(raw.get(key))
        if value is None:
            continue
        if 0 < value <= 1 and ("pct" in key or "rate" in key):
            return value * 100
        return value
    return None


def _social_present(socials: Any, *needles: str) -> bool:
    if not isinstance(socials, list):
        return False
    for item in socials:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get(key, "")) for key in ("type", "url")).lower()
        if any(needle in text for needle in needles):
            return True
    return False


def _is_boosted(metrics: dict[str, Any]) -> bool:
    tags = metrics.get("source_tags") or []
    return bool(metrics.get("boost_total_amount")) or any(str(tag).startswith("boost_") for tag in tags)


def _gte(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _lte(value: float | None, threshold: float) -> bool:
    return value is not None and value <= threshold


def _num(value: Any) -> float | None:
    if value in {None, "", "null"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}
