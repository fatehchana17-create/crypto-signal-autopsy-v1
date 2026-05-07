from __future__ import annotations

from typing import Any

from crypto_signal_autopsy.config import Settings
from crypto_signal_autopsy.db import from_json


REASON_LABELS = {
    "liquidity_below_min": "Liquidity below minimum",
    "volume_24h_below_min": "24h volume below minimum",
    "pair_too_young": "Pair is too young",
    "missing_price_usd": "Missing price",
    "missing_liquidity_usd": "Missing liquidity",
    "missing_volume_h24_usd": "Missing 24h volume",
    "missing_pair_age_hours": "Missing pair age",
    "missing_price_change_h1": "Missing 1h price move",
    "h1_move_overextended": "1h move too overextended",
    "no_signal_pattern": "No accepted signal pattern",
    "buy_tax_above_max": "Buy tax too high",
    "sell_tax_above_max": "Sell tax too high",
}


def build_rejection_record(row: Any, settings: Settings) -> dict[str, Any]:
    data = _as_dict(row)
    metrics = from_json(data.get("raw_metrics"), {})
    reasons = from_json(data.get("rejection_reasons"), [])
    source_tags = metrics.get("source_tags") or []
    chain_id = data.get("chain_id") or metrics.get("chain_id") or settings.dex_chain
    pair_address = data.get("pair_address") or metrics.get("pair_address") or ""
    token_address = data.get("token_address") or metrics.get("token_address") or ""
    liquidity = _as_float(metrics.get("liquidity_usd"))
    volume_24h = _as_float(metrics.get("volume_h24_usd"))
    volume_h1 = _as_float(metrics.get("volume_h1_usd"))
    pair_age = _as_float(metrics.get("pair_age_hours"))
    price_change_h1 = _as_float(metrics.get("price_change_h1_pct"))
    avg_hourly_volume = volume_24h / 24 if volume_24h else None
    required_momentum_volume = (
        avg_hourly_volume * settings.volume_spike_multiple if avg_hourly_volume is not None else None
    )

    clean_momentum_ready = (
        price_change_h1 is not None
        and volume_h1 is not None
        and required_momentum_volume is not None
        and price_change_h1 >= settings.clean_momentum_min_h1_pct
        and volume_h1 >= required_momentum_volume
    )
    boosted_ready = any(str(tag).startswith("boost_") for tag in source_tags)

    return {
        "id": data.get("id"),
        "observed_at": data.get("observed_at"),
        "chain_id": chain_id,
        "token_address": token_address,
        "pair_address": pair_address,
        "symbol": metrics.get("base_symbol") or "UNKNOWN",
        "name": metrics.get("base_name") or "",
        "price_usd": _as_float(metrics.get("price_usd")),
        "liquidity_usd": liquidity,
        "volume_h24_usd": volume_24h,
        "volume_h1_usd": volume_h1,
        "pair_age_hours": pair_age,
        "price_change_h1_pct": price_change_h1,
        "price_change_h24_pct": _as_float(metrics.get("price_change_h24_pct")),
        "security_status": data.get("security_status") or "unknown",
        "source_tags": source_tags,
        "boost_total_amount": _as_float(metrics.get("boost_total_amount")),
        "rejection_reasons": reasons,
        "reason_text": "; ".join(humanize_reason(reason) for reason in reasons),
        "fail_count": len(reasons),
        "dexscreener_url": dexscreener_url(chain_id, pair_address),
        "token_short": short_address(token_address),
        "pair_short": short_address(pair_address),
        "clean_momentum_ready": clean_momentum_ready,
        "boosted_ready": boosted_ready,
        "required_momentum_volume_h1": required_momentum_volume,
        "liquidity_gap_usd": _positive_gap(settings.min_liquidity_usd, liquidity),
        "volume_24h_gap_usd": _positive_gap(settings.min_volume_24h_usd, volume_24h),
        "pair_age_gap_hours": _positive_gap(settings.min_pair_age_hours, pair_age),
    }


def build_requirement_rows(record: dict[str, Any], settings: Settings) -> list[dict[str, str]]:
    rows = [
        _min_row(
            "Liquidity",
            record.get("liquidity_usd"),
            settings.min_liquidity_usd,
            money,
        ),
        _min_row(
            "24h volume",
            record.get("volume_h24_usd"),
            settings.min_volume_24h_usd,
            money,
        ),
        _min_row(
            "Pair age",
            record.get("pair_age_hours"),
            settings.min_pair_age_hours,
            lambda value: f"{value:.2f}h",
        ),
        _max_row(
            "1h price move",
            record.get("price_change_h1_pct"),
            settings.max_price_change_h1_pct,
            pct,
        ),
        _min_row(
            "Clean momentum: 1h move",
            record.get("price_change_h1_pct"),
            settings.clean_momentum_min_h1_pct,
            pct,
        ),
        _min_row(
            "Clean momentum: 1h volume",
            record.get("volume_h1_usd"),
            record.get("required_momentum_volume_h1"),
            money,
        ),
        {
            "Check": "Boosted token source",
            "Actual": "Yes" if record.get("boosted_ready") else "No",
            "Required": "Yes for boosted_but_not_garbage",
            "Gap": "OK" if record.get("boosted_ready") else "Not a boosted candidate",
            "Status": "Pass" if record.get("boosted_ready") else "No",
        },
        {
            "Check": "GoPlus security",
            "Actual": str(record.get("security_status") or "unknown"),
            "Required": "ok when token is configured",
            "Gap": "Token not connected" if record.get("security_status") == "skipped_no_token" else "",
            "Status": "Skipped" if record.get("security_status") == "skipped_no_token" else "Recorded",
        },
    ]
    return rows


def build_review_table(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "Seen": record["observed_at"],
            "Symbol": record["symbol"],
            "Token": record["token_short"],
            "Pair": record["pair_short"],
            "Price": record["price_usd"],
            "Liquidity": record["liquidity_usd"],
            "24h Volume": record["volume_h24_usd"],
            "Age h": record["pair_age_hours"],
            "1h Move %": record["price_change_h1_pct"],
            "Fails": record["fail_count"],
            "Reasons": record["reason_text"],
            "DEX Screener": record["dexscreener_url"],
        }
        for record in records
    ]


def humanize_reason(reason: str) -> str:
    if reason.startswith("goplus_"):
        return f"GoPlus risk: {reason.removeprefix('goplus_').replace('_', ' ')}"
    if reason.startswith("goplus_required_"):
        return f"GoPlus required but {reason.removeprefix('goplus_required_').replace('_', ' ')}"
    return REASON_LABELS.get(reason, reason.replace("_", " ").capitalize())


def short_address(address: str | None) -> str:
    if not address:
        return ""
    if len(address) <= 12:
        return address
    return f"{address[:6]}...{address[-4:]}"


def dexscreener_url(chain_id: str, pair_address: str) -> str:
    if not chain_id or not pair_address:
        return ""
    return f"https://dexscreener.com/{chain_id}/{pair_address}"


def money(value: float | None) -> str:
    if value is None:
        return "Missing"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.4f}" if abs(value) < 1 else f"${value:.2f}"


def pct(value: float | None) -> str:
    if value is None:
        return "Missing"
    return f"{value:.2f}%"


def _min_row(label: str, actual: float | None, required: float | None, formatter) -> dict[str, str]:
    if actual is None:
        return {
            "Check": label,
            "Actual": "Missing",
            "Required": f">= {formatter(required)}" if required is not None else "Required",
            "Gap": "Data missing",
            "Status": "Fail",
        }
    if required is None:
        return {
            "Check": label,
            "Actual": formatter(actual),
            "Required": "Not available",
            "Gap": "Need baseline data",
            "Status": "Unknown",
        }
    passed = actual >= required
    gap = required - actual
    return {
        "Check": label,
        "Actual": formatter(actual),
        "Required": f">= {formatter(required)}",
        "Gap": "OK" if passed else f"Needs {formatter(gap)} more",
        "Status": "Pass" if passed else "Fail",
    }


def _max_row(label: str, actual: float | None, required: float, formatter) -> dict[str, str]:
    if actual is None:
        return {
            "Check": label,
            "Actual": "Missing",
            "Required": f"<= {formatter(required)}",
            "Gap": "Data missing",
            "Status": "Fail",
        }
    passed = actual <= required
    gap = actual - required
    return {
        "Check": label,
        "Actual": formatter(actual),
        "Required": f"<= {formatter(required)}",
        "Gap": "OK" if passed else f"{formatter(gap)} too high",
        "Status": "Pass" if passed else "Fail",
    }


def _positive_gap(required: float, actual: float | None) -> float | None:
    if actual is None:
        return None
    return max(required - actual, 0)


def _as_float(value: Any) -> float | None:
    if value in {None, "", "null"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    if hasattr(row, "to_dict"):
        return row.to_dict()
    return dict(row)
