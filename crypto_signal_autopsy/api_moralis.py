from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crypto_signal_autopsy.clients.http import JsonHttpClient


@dataclass(frozen=True)
class WalletTrade:
    wallet_address: str
    trade_time: str
    side: str
    amount_usd: float | None
    token_amount: float | None
    price_usd: float | None
    tx_hash: str | None
    source: str
    raw_json: dict[str, Any]


class WalletDataProvider:
    provider_name = "none"

    def fetch_pair_trades(self, chain: str, pair_address: str, from_date: str | None, limit: int) -> list[WalletTrade]:
        raise NotImplementedError

    def fetch_wallet_history(self, chain: str, wallet_address: str) -> list[dict[str, Any]]:
        return []

    def fetch_wallet_pnl(self, chain: str, wallet_address: str) -> dict[str, Any] | None:
        return None


class MoralisWalletProvider(WalletDataProvider):
    provider_name = "moralis"
    base_url = "https://deep-index.moralis.io/api/v2.2"

    def __init__(self, http: JsonHttpClient, api_key: str):
        self.http = http
        self.api_key = api_key

    def fetch_pair_trades(self, chain: str, pair_address: str, from_date: str | None, limit: int) -> list[WalletTrade]:
        params: dict[str, Any] = {
            "chain": chain,
            "limit": max(min(limit, 100), 1),
            "order": "DESC",
            "transactionTypes": "buy,sell",
        }
        if from_date:
            params["fromDate"] = from_date
        data = self.http.get_json(
            self.provider_name,
            "/pairs/{address}/swaps",
            f"{self.base_url}/pairs/{pair_address}/swaps",
            params=params,
            headers={"X-API-Key": self.api_key},
        )
        raw_rows = data.get("result", []) if isinstance(data, dict) else []
        return [_normalize_moralis_trade(row) for row in raw_rows if isinstance(row, dict)]


def _normalize_moralis_trade(row: dict[str, Any]) -> WalletTrade:
    side = str(row.get("transactionType") or row.get("side") or "").strip().lower()
    wallet = str(row.get("walletAddress") or row.get("wallet_address") or "").strip().lower()
    tx_hash = row.get("transactionHash") or row.get("transaction_hash")
    return WalletTrade(
        wallet_address=wallet,
        trade_time=str(row.get("blockTimestamp") or row.get("block_timestamp") or ""),
        side=side,
        amount_usd=_num(row.get("totalValueUsd") or row.get("total_value_usd")),
        token_amount=_num(row.get("baseTokenAmount") or row.get("base_token_amount")),
        price_usd=_num(row.get("baseTokenPriceUsd") or row.get("base_token_price_usd")),
        tx_hash=str(tx_hash).lower() if tx_hash else None,
        source="moralis_pair_swaps",
        raw_json=row,
    )


def _num(value: Any) -> float | None:
    if value in {None, "", "null"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
