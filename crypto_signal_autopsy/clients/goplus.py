from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crypto_signal_autopsy.clients.http import ApiError, JsonHttpClient
from crypto_signal_autopsy.config import Settings


CRITICAL_FLAGS = {
    "cannot_buy",
    "cannot_sell_all",
    "is_blacklisted",
    "is_honeypot",
    "honeypot_with_same_creator",
    "is_mintable",
    "owner_change_balance",
    "hidden_owner",
    "selfdestruct",
    "external_call",
    "trading_cooldown",
    "personal_slippage_modifiable",
    "slippage_modifiable",
    "is_anti_whale",
}


@dataclass(frozen=True)
class SecurityResult:
    status: str
    risk_flags: list[str]
    buy_tax_pct: float | None
    sell_tax_pct: float | None
    raw: dict[str, Any]

    @property
    def has_critical_risk(self) -> bool:
        return bool(self.risk_flags)


class GoPlusClient:
    provider = "goplus"
    base_url = "https://api.gopluslabs.io/api/v1"

    def __init__(self, settings: Settings, http: JsonHttpClient):
        self.settings = settings
        self.http = http

    def token_security(self, chain_id: str, token_address: str) -> SecurityResult:
        if not self.settings.goplus_access_token:
            return SecurityResult("skipped_no_token", [], None, None, {})

        headers = {"Authorization": f"Bearer {self.settings.goplus_access_token}"}
        try:
            data = self.http.get_json(
                self.provider,
                "/token_security/{chain_id}",
                f"{self.base_url}/token_security/{chain_id}",
                params={"contract_addresses": token_address},
                headers=headers,
            )
        except ApiError as exc:
            return SecurityResult(f"api_error_{exc.status_code or 'network'}", [], None, None, {"error": str(exc)})

        item = _extract_token_result(data, token_address)
        if not item:
            return SecurityResult("no_data", [], None, None, data if isinstance(data, dict) else {"raw": data})

        risk_flags = sorted(flag for flag in CRITICAL_FLAGS if _truthy(item.get(flag)))
        buy_tax_pct = _tax_pct(item.get("buy_tax"))
        sell_tax_pct = _tax_pct(item.get("sell_tax"))
        return SecurityResult("ok", risk_flags, buy_tax_pct, sell_tax_pct, item)


def _extract_token_result(data: Any, token_address: str) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if isinstance(result, dict):
        for key in {token_address, token_address.lower(), token_address.upper()}:
            value = result.get(key)
            if isinstance(value, dict):
                return value
        if len(result) == 1:
            value = next(iter(result.values()))
            if isinstance(value, dict):
                return value
    return None


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _tax_pct(value: Any) -> float | None:
    if value in {None, "", "null"}:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1:
        return number * 100
    return number
