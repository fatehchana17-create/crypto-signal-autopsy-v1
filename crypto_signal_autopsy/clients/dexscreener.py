from __future__ import annotations

from collections import defaultdict
from typing import Any

from crypto_signal_autopsy.clients.http import JsonHttpClient


class DexScreenerClient:
    provider = "dexscreener"
    base_url = "https://api.dexscreener.com"

    def __init__(self, http: JsonHttpClient):
        self.http = http

    def latest_profiles(self) -> list[dict[str, Any]]:
        return self._as_list(
            self.http.get_json(
                self.provider,
                "/token-profiles/latest/v1",
                f"{self.base_url}/token-profiles/latest/v1",
            )
        )

    def latest_boosts(self) -> list[dict[str, Any]]:
        return self._as_list(
            self.http.get_json(
                self.provider,
                "/token-boosts/latest/v1",
                f"{self.base_url}/token-boosts/latest/v1",
            )
        )

    def top_boosts(self) -> list[dict[str, Any]]:
        return self._as_list(
            self.http.get_json(
                self.provider,
                "/token-boosts/top/v1",
                f"{self.base_url}/token-boosts/top/v1",
            )
        )

    def discover_candidate_tokens(self, chain_id: str) -> dict[str, dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"source_tags": set(), "boost_total_amount": 0.0, "raw_sources": []}
        )

        for item in self.latest_profiles():
            self._add_candidate(candidates, item, chain_id, "profile_latest")
        for item in self.latest_boosts():
            self._add_candidate(candidates, item, chain_id, "boost_latest")
        for item in self.top_boosts():
            self._add_candidate(candidates, item, chain_id, "boost_top")

        normalized: dict[str, dict[str, Any]] = {}
        for address, payload in candidates.items():
            normalized[address] = {
                "token_address": address,
                "source_tags": sorted(payload["source_tags"]),
                "boost_total_amount": payload["boost_total_amount"] or None,
                "raw_sources": payload["raw_sources"],
            }
        return normalized

    def fetch_pairs_for_tokens(
        self,
        chain_id: str,
        token_addresses: list[str],
    ) -> list[dict[str, Any]]:
        pairs: list[dict[str, Any]] = []
        for chunk in _chunks(token_addresses, 30):
            joined = ",".join(chunk)
            data = self.http.get_json(
                self.provider,
                "/tokens/v1/{chainId}/{tokenAddresses}",
                f"{self.base_url}/tokens/v1/{chain_id}/{joined}",
            )
            pairs.extend(self._as_list(data))
        return pairs

    def fetch_pair(self, chain_id: str, pair_address: str) -> dict[str, Any] | None:
        data = self.http.get_json(
            self.provider,
            "/latest/dex/pairs/{chainId}/{pairId}",
            f"{self.base_url}/latest/dex/pairs/{chain_id}/{pair_address}",
        )
        pairs = data.get("pairs") if isinstance(data, dict) else None
        if not pairs:
            return None
        return pairs[0]

    @staticmethod
    def _as_list(data: Any) -> list[dict[str, Any]]:
        if data is None:
            return []
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
        return []

    @staticmethod
    def _add_candidate(
        candidates: dict[str, dict[str, Any]],
        item: dict[str, Any],
        chain_id: str,
        tag: str,
    ) -> None:
        if item.get("chainId") != chain_id:
            return
        address = item.get("tokenAddress")
        if not address:
            return
        payload = candidates[address]
        payload["source_tags"].add(tag)
        payload["raw_sources"].append(item)
        amount = item.get("totalAmount") or item.get("amount") or 0
        try:
            payload["boost_total_amount"] += float(amount)
        except (TypeError, ValueError):
            pass


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]
