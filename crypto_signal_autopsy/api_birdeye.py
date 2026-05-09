from __future__ import annotations

from crypto_signal_autopsy.api_moralis import WalletDataProvider


class BirdeyeWalletProvider(WalletDataProvider):
    provider_name = "birdeye"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def fetch_pair_trades(self, chain: str, pair_address: str, from_date: str | None, limit: int):
        raise NotImplementedError("Birdeye wallet provider is reserved for a later V3 phase.")
