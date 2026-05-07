from __future__ import annotations

from datetime import datetime
from typing import Any

from crypto_signal_autopsy.timeutils import from_millis, iso_utc


def normalize_pair(
    pair: dict[str, Any],
    observed_at: str,
    source: dict[str, Any],
    now: datetime,
) -> dict[str, Any] | None:
    base_token = pair.get("baseToken") or {}
    quote_token = pair.get("quoteToken") or {}
    pair_address = pair.get("pairAddress")
    token_address = base_token.get("address") or source.get("token_address")
    chain_id = pair.get("chainId")
    if not pair_address or not token_address or not chain_id:
        return None

    created = from_millis(pair.get("pairCreatedAt"))
    age_hours = None
    created_iso = None
    if created:
        created_iso = iso_utc(created)
        age_hours = max((now - created).total_seconds() / 3600, 0)

    txns_h1 = _nested(pair, "txns", "h1") or {}

    return {
        "observed_at": observed_at,
        "chain_id": chain_id,
        "token_address": token_address,
        "pair_address": pair_address,
        "dex_id": pair.get("dexId"),
        "base_symbol": base_token.get("symbol"),
        "base_name": base_token.get("name"),
        "quote_symbol": quote_token.get("symbol"),
        "price_usd": _float(pair.get("priceUsd")),
        "liquidity_usd": _float(_nested(pair, "liquidity", "usd")),
        "volume_h1_usd": _float(_nested(pair, "volume", "h1")),
        "volume_h6_usd": _float(_nested(pair, "volume", "h6")),
        "volume_h24_usd": _float(_nested(pair, "volume", "h24")),
        "price_change_h1_pct": _float(_nested(pair, "priceChange", "h1")),
        "price_change_h24_pct": _float(_nested(pair, "priceChange", "h24")),
        "buys_h1": _int(txns_h1.get("buys")),
        "sells_h1": _int(txns_h1.get("sells")),
        "pair_created_at": created_iso,
        "pair_age_hours": age_hours,
        "source_tags": source.get("source_tags", []),
        "boost_total_amount": source.get("boost_total_amount"),
        "raw_json": pair,
    }


def choose_best_pairs(
    pairs: list[dict[str, Any]],
    candidate_sources: dict[str, dict[str, Any]],
    observed_at: str,
    now: datetime,
) -> list[dict[str, Any]]:
    best_by_token: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        base = pair.get("baseToken") or {}
        token_address = base.get("address")
        if not token_address or token_address not in candidate_sources:
            continue
        metrics = normalize_pair(pair, observed_at, candidate_sources[token_address], now)
        if not metrics:
            continue
        current_best = best_by_token.get(token_address)
        if current_best is None or (metrics.get("liquidity_usd") or 0) > (
            current_best.get("liquidity_usd") or 0
        ):
            best_by_token[token_address] = metrics
    return list(best_by_token.values())


def _nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _float(value: Any) -> float | None:
    if value in {None, "", "null"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    if value in {None, "", "null"}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
