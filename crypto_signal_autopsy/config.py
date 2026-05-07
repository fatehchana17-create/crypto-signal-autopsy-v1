from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
    goplus_chain_id: str
    cex_top_n: int
    coingecko_demo_api_key: str | None
    coingecko_pro_api_key: str | None
    goplus_access_token: str | None
    require_goplus: bool
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
        goplus_chain_id=os.getenv("CSA_GOPLUS_CHAIN_ID", "8453").strip() or "8453",
        cex_top_n=_env_int("CSA_CEX_TOP_N", 100),
        coingecko_demo_api_key=os.getenv("COINGECKO_DEMO_API_KEY") or None,
        coingecko_pro_api_key=os.getenv("COINGECKO_PRO_API_KEY") or None,
        goplus_access_token=os.getenv("GOPLUS_ACCESS_TOKEN") or None,
        require_goplus=_env_bool("CSA_REQUIRE_GOPLUS", False),
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
