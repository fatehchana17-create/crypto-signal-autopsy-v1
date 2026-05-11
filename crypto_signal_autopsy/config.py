from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[1]

MODEL_VERSION = "V2"

HORIZONS = {
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "8h": 480,
    "24h": 1440,
}

HARD_REJECT = {
    "min_liquidity_usd": 10_000,
    "min_volume_24h": 15_000,
    "min_pair_age_minutes": 10,
    "max_top_holder_pct": 25,
    "max_top_10_holder_pct": 60,
    "max_fdv_liquidity_ratio": 200,
    "min_buy_ratio": 0.30,
    "min_unique_buyers_1h": 10,
    "max_buy_tax": 10,
    "max_sell_tax": 10,
}

HIGH_RISK_MOMENTUM = {
    "min_liquidity_usd": 25_000,
    "min_volume_24h": 40_000,
    "min_pair_age_minutes": 45,
    "max_pair_age_hours": 6,
    "min_price_change_1h": 80,
    "max_price_change_1h": 700,
    "min_txns_15m": 10,
    "min_unique_buyers_15m": 10,
    "min_unique_buyers_1h": 50,
    "max_top_holder_pct": 25,
    "max_top_10_holder_pct": 60,
    "max_fdv_liquidity_ratio": 200,
    "min_buy_ratio": 0.45,
    "min_security_score": 60,
    "max_buy_tax": 10,
    "max_sell_tax": 10,
}

MOMENTUM_TRAP_GUARD = {
    "max_hot_launch_age_hours": 3,
    "min_hot_price_change_1h": 80,
    "min_healthy_age_hours": 0.75,
    "min_hot_unique_buyers_1h": 50,
    "min_hot_buy_ratio": 0.45,
    "danger_buy_ratio": 0.35,
    "min_extreme_price_change_1h": 700,
    "max_too_early_age_hours": 0.75,
    "high_volume_liquidity_ratio": 12,
}

PAPER_TRADE_GATE = {
    "max_risk_score": 30,
    "min_opportunity_score": 75,
    "min_ten_x_score": 80,
    "max_price_change_1h": 80,
    "max_price_change_5m": 30,
    "min_liquidity_usd": 150_000,
    "min_volume_24h": 200_000,
    "min_pair_age_hours": 3,
    "max_volume_liquidity_ratio": 15,
    "min_unique_buyers_1h": 30,
    "min_buy_ratio": 0.45,
    "max_buy_ratio": 0.70,
    "max_top_holder_pct": 15,
    "max_top_10_holder_pct": 45,
    "min_security_score": 80,
    "max_buy_tax": 5,
    "max_sell_tax": 5,
}

DELAYED_ENTRY_SURVIVAL = {
    "required_wait_minutes": 30,
    "max_drawdown_during_wait_pct": -12,
    "min_return_during_wait_pct": -5,
    "min_liquidity_retention_pct": 85,
    "max_volume_fade_pct": 50,
    "max_bearish_volume_expansion_pct": 50,
    "max_spread_or_slippage_pct": 5,
}

SAFE_RESEARCH = {
    "min_liquidity_usd": 50_000,
    "min_volume_24h": 100_000,
    "min_pair_age_hours": 6,
    "max_price_change_1h": 80,
    "min_txns_1h": 50,
    "min_txns_24h": 300,
    "min_unique_buyers_1h": 25,
    "min_buy_ratio": 0.45,
    "max_top_holder_pct": 15,
    "max_top_10_holder_pct": 45,
    "max_fdv_liquidity_ratio": 50,
    "max_marketcap_liquidity_ratio": 40,
    "min_security_score": 80,
    "max_buy_tax": 5,
    "max_sell_tax": 5,
    "max_estimated_slippage_pct": 5,
}


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_csv(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _path_from_env(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


@dataclass(frozen=True)
class Settings:
    project_root: Path
    db_path: Path
    export_dir: Path
    dex_chain: str
    dex_search_queries: list[str]
    goplus_chain_id: str
    cex_top_n: int
    coingecko_demo_api_key: str | None
    coingecko_pro_api_key: str | None
    goplus_access_token: str | None
    moralis_api_key: str | None
    birdeye_api_key: str | None
    require_goplus: bool
    wallet_module_enabled: bool
    wallet_provider: str
    wallet_history_days: int
    wallet_pair_limit: int
    wallet_trade_limit: int
    min_liquidity_usd: float
    min_volume_24h_usd: float
    min_pair_age_hours: float
    max_price_change_h1_pct: float
    max_buy_tax_pct: float
    max_sell_tax_pct: float
    volume_spike_multiple: float
    clean_momentum_min_h1_pct: float
    liquidity_expansion_min_pct: float
    delayed_entry_minutes: int
    estimated_cost_pct: float
    request_timeout_seconds: float
    filter_version: str = "v1.0"
    user_agent: str = "crypto-signal-autopsy-v1/0.1"


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        project_root=PROJECT_ROOT,
        db_path=_path_from_env("CSA_DB_PATH", "data/autopsy.sqlite"),
        export_dir=_path_from_env("CSA_EXPORT_DIR", "exports"),
        dex_chain=os.getenv("CSA_DEX_CHAIN", "base").strip() or "base",
        dex_search_queries=_env_csv(
            "CSA_DEX_SEARCH_QUERIES",
            "base,virtual,ai,agent,meme,degen,pepe,dog,cat,coin,moon,launch",
        ),
        goplus_chain_id=os.getenv("CSA_GOPLUS_CHAIN_ID", "8453").strip() or "8453",
        cex_top_n=_env_int("CSA_CEX_TOP_N", 300),
        coingecko_demo_api_key=os.getenv("COINGECKO_DEMO_API_KEY") or None,
        coingecko_pro_api_key=os.getenv("COINGECKO_PRO_API_KEY") or None,
        goplus_access_token=os.getenv("GOPLUS_ACCESS_TOKEN") or os.getenv("GOPLUS_API_KEY") or None,
        moralis_api_key=os.getenv("MORALIS_API_KEY") or None,
        birdeye_api_key=os.getenv("BIRDEYE_API_KEY") or None,
        require_goplus=_env_bool("CSA_REQUIRE_GOPLUS", False),
        wallet_module_enabled=_env_bool("CSA_WALLET_MODULE_ENABLED", True),
        wallet_provider=os.getenv("CSA_WALLET_PROVIDER", "moralis").strip().lower() or "moralis",
        wallet_history_days=_env_int("CSA_WALLET_HISTORY_DAYS", 7),
        wallet_pair_limit=_env_int("CSA_WALLET_PAIR_LIMIT", 25),
        wallet_trade_limit=_env_int("CSA_WALLET_TRADE_LIMIT", 100),
        min_liquidity_usd=_env_float("CSA_MIN_LIQUIDITY_USD", 500_000),
        min_volume_24h_usd=_env_float("CSA_MIN_VOLUME_24H_USD", 100_000),
        min_pair_age_hours=_env_float("CSA_MIN_PAIR_AGE_HOURS", 6),
        max_price_change_h1_pct=_env_float("CSA_MAX_PRICE_CHANGE_H1_PCT", 75),
        max_buy_tax_pct=_env_float("CSA_MAX_BUY_TAX_PCT", 10),
        max_sell_tax_pct=_env_float("CSA_MAX_SELL_TAX_PCT", 10),
        volume_spike_multiple=_env_float("CSA_VOLUME_SPIKE_MULTIPLE", 3),
        clean_momentum_min_h1_pct=_env_float("CSA_CLEAN_MOMENTUM_MIN_H1_PCT", 3),
        liquidity_expansion_min_pct=_env_float("CSA_LIQUIDITY_EXPANSION_MIN_PCT", 5),
        delayed_entry_minutes=_env_int("CSA_DELAYED_ENTRY_MINUTES", 5),
        estimated_cost_pct=_env_float("CSA_ESTIMATED_COST_PCT", 0.5),
        request_timeout_seconds=_env_float("CSA_REQUEST_TIMEOUT_SECONDS", 20),
    )
