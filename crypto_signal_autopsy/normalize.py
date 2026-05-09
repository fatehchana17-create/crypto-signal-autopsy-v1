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
    txns_m5 = _nested(pair, "txns", "m5") or {}
    txns_h24 = _nested(pair, "txns", "h24") or {}
    liquidity_usd = _float(_nested(pair, "liquidity", "usd"))
    volume_h24_usd = _float(_nested(pair, "volume", "h24"))
    fdv = _float(pair.get("fdv"))
    market_cap = _float(pair.get("marketCap"))
    volume_liquidity_ratio = (
        volume_h24_usd / liquidity_usd if volume_h24_usd is not None and liquidity_usd else None
    )
    fdv_liquidity_ratio = fdv / liquidity_usd if fdv is not None and liquidity_usd else None
    marketcap_liquidity_ratio = market_cap / liquidity_usd if market_cap is not None and liquidity_usd else None
    buys_h1 = _int(txns_h1.get("buys"))
    sells_h1 = _int(txns_h1.get("sells"))
    total_txns_1h = _sum_ints(buys_h1, sells_h1)
    buys_h24 = _int(txns_h24.get("buys"))
    sells_h24 = _int(txns_h24.get("sells"))
    total_txns_24h = _sum_ints(buys_h24, sells_h24)
    buy_ratio = buys_h1 / total_txns_1h if buys_h1 is not None and total_txns_1h else None

    return {
        "observed_at": observed_at,
        "chain_id": chain_id,
        "token_address": token_address,
        "pair_address": pair_address,
        "pair_id": f"{chain_id}:{pair_address}",
        "dex_id": pair.get("dexId"),
        "base_symbol": base_token.get("symbol"),
        "base_name": base_token.get("name"),
        "base_decimals": _int(base_token.get("decimals")),
        "quote_token_address": quote_token.get("address"),
        "quote_symbol": quote_token.get("symbol"),
        "price_usd": _float(pair.get("priceUsd")),
        "liquidity_usd": liquidity_usd,
        "fdv": fdv,
        "market_cap": market_cap,
        "fdv_liquidity_ratio": fdv_liquidity_ratio,
        "marketcap_liquidity_ratio": marketcap_liquidity_ratio,
        "volume_5m_usd": _float(_nested(pair, "volume", "m5")),
        "volume_h1_usd": _float(_nested(pair, "volume", "h1")),
        "volume_h6_usd": _float(_nested(pair, "volume", "h6")),
        "volume_h24_usd": volume_h24_usd,
        "volume_liquidity_ratio": volume_liquidity_ratio,
        "buys_m5": _int(txns_m5.get("buys")),
        "sells_m5": _int(txns_m5.get("sells")),
        "txns_15m_buys": _int(txns_m5.get("buys")),
        "txns_15m_sells": _int(txns_m5.get("sells")),
        "txns_1h_buys": buys_h1,
        "txns_1h_sells": sells_h1,
        "txns_24h_buys": buys_h24,
        "txns_24h_sells": sells_h24,
        "total_txns_1h": total_txns_1h,
        "total_txns_24h": total_txns_24h,
        "buy_ratio": buy_ratio,
        "unique_buyers_15m": _int(txns_m5.get("buys")),
        "unique_buyers_1h": buys_h1,
        "unique_buyers_24h": buys_h24,
        "price_change_5m_pct": _float(_nested(pair, "priceChange", "m5")),
        "price_change_h1_pct": _float(_nested(pair, "priceChange", "h1")),
        "price_change_h6_pct": _float(_nested(pair, "priceChange", "h6")),
        "price_change_h24_pct": _float(_nested(pair, "priceChange", "h24")),
        "buys_h1": buys_h1,
        "sells_h1": sells_h1,
        "pair_created_at": created_iso,
        "pair_age_minutes": age_hours * 60 if age_hours is not None else None,
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


def _sum_ints(*values: int | None) -> int | None:
    if any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)
