from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import csv
import json
import sqlite3
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS api_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  provider TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  status TEXT NOT NULL,
  message TEXT,
  request_url TEXT
);

CREATE TABLE IF NOT EXISTS ux_blank_input_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_time TEXT,
  page_context TEXT,
  input_type TEXT,
  blank_count_in_session INTEGER,
  recovery_message TEXT,
  suggestions TEXT,
  suggestion_clicked TEXT,
  recovered INTEGER,
  time_to_next_valid_input_seconds REAL
);

CREATE TABLE IF NOT EXISTS cex_market_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  coin_id TEXT NOT NULL,
  symbol TEXT,
  name TEXT,
  market_cap_rank INTEGER,
  price_usd REAL,
  volume_24h_usd REAL,
  price_change_1h_pct REAL,
  price_change_24h_pct REAL,
  market_cap_usd REAL,
  raw_json TEXT NOT NULL,
  UNIQUE(observed_at, coin_id)
);

CREATE TABLE IF NOT EXISTS dex_token_snapshots (
  snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  chain_id TEXT NOT NULL,
  token_address TEXT NOT NULL,
  pair_address TEXT NOT NULL,
  dex_id TEXT,
  base_symbol TEXT,
  base_name TEXT,
  quote_symbol TEXT,
  price_usd REAL,
  liquidity_usd REAL,
  volume_h1_usd REAL,
  volume_h6_usd REAL,
  volume_h24_usd REAL,
  price_change_h1_pct REAL,
  price_change_h24_pct REAL,
  buys_h1 INTEGER,
  sells_h1 INTEGER,
  pair_created_at TEXT,
  pair_age_hours REAL,
  source_tags TEXT NOT NULL,
  boost_total_amount REAL,
  raw_json TEXT NOT NULL,
  UNIQUE(observed_at, chain_id, pair_address)
);

CREATE INDEX IF NOT EXISTS idx_dex_pair_time
  ON dex_token_snapshots(pair_address, observed_at);

CREATE TABLE IF NOT EXISTS security_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  chain_id TEXT NOT NULL,
  token_address TEXT NOT NULL,
  status TEXT NOT NULL,
  risk_flags TEXT NOT NULL,
  buy_tax_pct REAL,
  sell_tax_pct REAL,
  raw_json TEXT NOT NULL,
  UNIQUE(observed_at, chain_id, token_address)
);

CREATE TABLE IF NOT EXISTS candidate_evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  chain_id TEXT NOT NULL,
  token_address TEXT NOT NULL,
  pair_address TEXT NOT NULL,
  accepted INTEGER NOT NULL,
  signal_types TEXT NOT NULL,
  rejection_reasons TEXT NOT NULL,
  filter_version TEXT NOT NULL,
  security_status TEXT NOT NULL,
  raw_metrics TEXT NOT NULL,
  UNIQUE(observed_at, chain_id, pair_address)
);

CREATE INDEX IF NOT EXISTS idx_candidate_eval_pair
  ON candidate_evaluations(pair_address, observed_at);

CREATE TABLE IF NOT EXISTS signals (
  signal_id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  chain_id TEXT NOT NULL,
  token_address TEXT NOT NULL,
  pair_address TEXT NOT NULL,
  base_symbol TEXT,
  signal_type TEXT NOT NULL,
  detection_price_usd REAL NOT NULL,
  detection_liquidity_usd REAL,
  detection_volume_h24_usd REAL,
  detection_pair_age_hours REAL,
  delayed_entry_at TEXT,
  delayed_entry_price_usd REAL,
  delayed_entry_observed_at TEXT,
  filter_version TEXT NOT NULL,
  source_tags TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  detection_snapshot_id INTEGER,
  UNIQUE(chain_id, pair_address, signal_type)
);

CREATE INDEX IF NOT EXISTS idx_signals_created
  ON signals(created_at);

CREATE TABLE IF NOT EXISTS signal_outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id INTEGER NOT NULL,
  horizon TEXT NOT NULL,
  target_at TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  price_usd REAL,
  raw_return_pct REAL,
  estimated_cost_pct REAL,
  net_return_pct REAL,
  delayed_raw_return_pct REAL,
  delayed_net_return_pct REAL,
  btc_return_pct REAL,
  eth_return_pct REAL,
  top100_equal_return_pct REAL,
  matched_all_passing_median_return_pct REAL,
  source TEXT NOT NULL,
  UNIQUE(signal_id, horizon),
  FOREIGN KEY(signal_id) REFERENCES signals(signal_id)
);

CREATE TABLE IF NOT EXISTS rejected_candidate_outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_evaluation_id INTEGER NOT NULL,
  horizon TEXT NOT NULL,
  target_at TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  chain_id TEXT NOT NULL,
  token_address TEXT NOT NULL,
  pair_address TEXT NOT NULL,
  rejection_reasons TEXT NOT NULL,
  detection_price_usd REAL,
  price_usd REAL,
  raw_return_pct REAL,
  estimated_cost_pct REAL,
  net_return_pct REAL,
  source TEXT NOT NULL,
  UNIQUE(candidate_evaluation_id, horizon),
  FOREIGN KEY(candidate_evaluation_id) REFERENCES candidate_evaluations(id)
);

CREATE INDEX IF NOT EXISTS idx_rejected_outcomes_horizon
  ON rejected_candidate_outcomes(horizon, observed_at);

CREATE TABLE IF NOT EXISTS tokens (
    token_address TEXT PRIMARY KEY,
    chain TEXT,
    symbol TEXT,
    name TEXT,
    decimals INTEGER,
    created_at_scan TEXT,
    first_seen_at TEXT,
    source TEXT
);

CREATE TABLE IF NOT EXISTS pairs (
    pair_id TEXT PRIMARY KEY,
    token_address TEXT,
    chain TEXT,
    dex_name TEXT,
    pair_address TEXT,
    base_token TEXT,
    quote_token TEXT,
    pair_created_at TEXT,
    pair_age_minutes REAL,
    pair_age_hours REAL,
    liquidity_usd REAL,
    fdv REAL,
    market_cap REAL,
    fdv_liquidity_ratio REAL,
    marketcap_liquidity_ratio REAL,
    price_usd REAL,
    volume_5m REAL,
    volume_1h REAL,
    volume_6h REAL,
    volume_24h REAL,
    volume_liquidity_ratio REAL,
    txns_5m_buys INTEGER,
    txns_5m_sells INTEGER,
    txns_15m_buys INTEGER,
    txns_15m_sells INTEGER,
    txns_1h_buys INTEGER,
    txns_1h_sells INTEGER,
    txns_24h_buys INTEGER,
    txns_24h_sells INTEGER,
    total_txns_1h INTEGER,
    total_txns_24h INTEGER,
    buy_ratio REAL,
    unique_buyers_15m INTEGER,
    unique_buyers_1h INTEGER,
    unique_buyers_24h INTEGER,
    price_change_5m REAL,
    price_change_1h REAL,
    price_change_6h REAL,
    price_change_24h REAL,
    estimated_slippage_100_usd REAL,
    raw_json TEXT,
    scan_time TEXT
);

CREATE INDEX IF NOT EXISTS idx_pairs_scan_time
  ON pairs(scan_time);

CREATE TABLE IF NOT EXISTS security_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    chain TEXT,
    provider TEXT,
    security_score REAL,
    is_honeypot INTEGER,
    cannot_sell INTEGER,
    is_blacklisted INTEGER,
    trading_disabled INTEGER,
    is_mintable INTEGER,
    is_proxy INTEGER,
    owner_renounced INTEGER,
    blacklist_function INTEGER,
    dangerous_owner_permissions INTEGER,
    buy_tax REAL,
    sell_tax REAL,
    top_holder_pct REAL,
    top_10_holder_pct REAL,
    lp_locked_pct REAL,
    lp_burned_pct REAL,
    risk_flags TEXT,
    raw_json TEXT,
    scan_time TEXT
);

CREATE INDEX IF NOT EXISTS idx_security_checks_token_time
  ON security_checks(token_address, scan_time);

CREATE TABLE IF NOT EXISTS filter_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    pair_id TEXT,
    scan_time TEXT,
    hard_reject INTEGER,
    qualifies_high_risk_momentum INTEGER,
    reject_reasons TEXT,
    risk_score REAL,
    opportunity_score REAL,
    ten_x_score REAL,
    ten_x_label TEXT,
    ten_x_reasons TEXT,
    final_label TEXT,
    risk_category TEXT,
    opportunity_category TEXT,
    model_version TEXT,
    UNIQUE(pair_id, scan_time, model_version)
);

CREATE INDEX IF NOT EXISTS idx_filter_results_label
  ON filter_results(final_label, scan_time);

CREATE TABLE IF NOT EXISTS social_catalysts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    scan_time TEXT,
    website_present INTEGER,
    twitter_present INTEGER,
    telegram_present INTEGER,
    discord_present INTEGER,
    dexscreener_boosted INTEGER,
    boost_amount REAL,
    coingecko_listed INTEGER,
    coinmarketcap_listed INTEGER,
    cex_listed INTEGER,
    news_mentions INTEGER,
    social_notes TEXT
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    pair_id TEXT,
    created_at TEXT,
    entry_rule TEXT,
    sim_entry_price REAL,
    sim_position_size_usd REAL,
    stop_rule TEXT,
    take_profit_rule TEXT,
    status TEXT,
    exit_time TEXT,
    exit_price REAL,
    net_return_pct REAL,
    notes TEXT,
    UNIQUE(pair_id, entry_rule)
);

CREATE TABLE IF NOT EXISTS outcome_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    pair_id TEXT,
    label_at_scan TEXT,
    scan_time TEXT,
    horizon TEXT,
    price_at_scan REAL,
    price_at_horizon REAL,
    return_pct REAL,
    max_favorable_excursion_pct REAL,
    max_adverse_excursion_pct REAL,
    drawdown_before_pump_pct REAL,
    liquidity_at_scan REAL,
    liquidity_at_horizon REAL,
    volume_at_scan REAL,
    volume_at_horizon REAL,
    became_illiquid INTEGER,
    rugged INTEGER,
    volume_disappeared INTEGER,
    snapshot_time TEXT,
    UNIQUE(pair_id, scan_time, horizon)
);

CREATE INDEX IF NOT EXISTS idx_outcome_snapshots_horizon
  ON outcome_snapshots(horizon, label_at_scan);

CREATE TABLE IF NOT EXISTS rejected_filter_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    pair_id TEXT,
    chain TEXT,
    symbol TEXT,
    scan_time TEXT,
    completed_at TEXT,
    reject_reasons TEXT,
    ten_x_score REAL,
    ten_x_label TEXT,
    ten_x_reasons TEXT,
    price_at_scan REAL,
    price_24h REAL,
    return_24h_pct REAL,
    best_return_pct REAL,
    worst_return_pct REAL,
    rugged INTEGER,
    became_illiquid INTEGER,
    volume_disappeared INTEGER,
    filter_verdict TEXT,
    lesson TEXT,
    UNIQUE(pair_id, scan_time)
);

CREATE INDEX IF NOT EXISTS idx_rejected_filter_audits_verdict
  ON rejected_filter_audits(filter_verdict, completed_at);

CREATE TABLE IF NOT EXISTS review_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    review_time TEXT,
    reviewer TEXT,
    missed_winner INTEGER,
    saved_from_bad_token INTEGER,
    main_lesson TEXT,
    manual_notes TEXT,
    screenshot_url TEXT
);

CREATE TABLE IF NOT EXISTS wallets (
    wallet_address TEXT PRIMARY KEY,
    chain TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    wallet_label TEXT,
    wallet_quality_score REAL,
    wallet_risk_score REAL,
    tokens_traded INTEGER,
    realized_exits INTEGER,
    win_rate REAL,
    median_realized_return_pct REAL,
    avg_realized_return_pct REAL,
    total_realized_pnl_usd REAL,
    rug_exposure_rate REAL,
    low_liquidity_trade_rate REAL,
    quick_dump_rate REAL,
    first_minute_entry_rate REAL,
    avg_hold_time_minutes REAL,
    biggest_win_contribution_pct REAL,
    suspicious_behavior_score REAL,
    notes TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS wallet_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT,
    token_address TEXT,
    pair_id TEXT,
    chain TEXT,
    trade_time TEXT,
    side TEXT,
    amount_usd REAL,
    token_amount REAL,
    price_usd REAL,
    liquidity_usd_at_trade REAL,
    pair_age_minutes_at_trade REAL,
    tx_hash TEXT,
    source TEXT,
    raw_json TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_trades_tx_hash
  ON wallet_trades(chain, tx_hash, wallet_address, token_address, side)
  WHERE tx_hash IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_trades_logical
  ON wallet_trades(wallet_address, token_address, pair_id, trade_time, side, amount_usd, price_usd)
  WHERE tx_hash IS NULL;

CREATE INDEX IF NOT EXISTS idx_wallet_trades_pair_time
  ON wallet_trades(pair_id, trade_time);

CREATE INDEX IF NOT EXISTS idx_wallet_trades_wallet
  ON wallet_trades(wallet_address, trade_time);

CREATE TABLE IF NOT EXISTS wallet_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT,
    token_address TEXT,
    chain TEXT,
    first_buy_time TEXT,
    avg_buy_price REAL,
    total_buy_usd REAL,
    first_sell_time TEXT,
    avg_sell_price REAL,
    total_sell_usd REAL,
    realized_pnl_usd REAL,
    realized_return_pct REAL,
    max_unrealized_return_pct REAL,
    max_drawdown_pct REAL,
    hold_time_minutes REAL,
    rugged_after_entry INTEGER,
    became_illiquid_after_entry INTEGER,
    volume_disappeared_after_entry INTEGER,
    updated_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_performance_wallet_token
  ON wallet_performance(wallet_address, token_address, chain);

CREATE TABLE IF NOT EXISTS wallet_token_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    pair_id TEXT,
    chain TEXT,
    scan_time TEXT,
    smart_wallet_count INTEGER,
    useful_wallet_count INTEGER,
    high_risk_wallet_count INTEGER,
    suspicious_wallet_count INTEGER,
    dump_wallet_count INTEGER,
    unproven_wallet_count INTEGER,
    total_wallet_buy_usd REAL,
    total_wallet_sell_usd REAL,
    net_wallet_flow_usd REAL,
    smart_or_useful_wallets_entered_within_30m INTEGER,
    low_rug_exposure_wallet_count INTEGER,
    deployer_linked_wallet_involved INTEGER,
    wallet_signal_score REAL,
    wallet_signal_label TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_token_signal_scan
  ON wallet_token_signals(pair_id, scan_time);

CREATE TABLE IF NOT EXISTS wallet_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    pair_id TEXT,
    chain TEXT,
    scan_time TEXT,
    cluster_type TEXT,
    wallet_count INTEGER,
    total_buy_usd REAL,
    average_wallet_quality_score REAL,
    average_wallet_risk_score REAL,
    notes TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wallet_clusters_scan
  ON wallet_clusters(pair_id, scan_time, cluster_type);

CREATE TABLE IF NOT EXISTS api_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_name TEXT,
    endpoint TEXT,
    related_address TEXT,
    error_message TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS category_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT,
    horizon TEXT,
    count INTEGER,
    median_return REAL,
    average_return REAL,
    best_return REAL,
    worst_return REAL,
    positive_rate REAL,
    verdict TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS accepted_failure_diagnosis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    symbol TEXT,
    pair_id TEXT,
    original_label TEXT,
    horizon TEXT,
    return_pct REAL,
    max_adverse_excursion_pct REAL,
    max_favorable_excursion_pct REAL,
    liquidity_at_scan REAL,
    liquidity_at_horizon REAL,
    volume_at_scan REAL,
    volume_at_horizon REAL,
    price_change_1h_at_scan REAL,
    failure_reasons TEXT,
    diagnosis_text TEXT,
    fix_recommendation TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS missed_winner_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    symbol TEXT,
    pair_id TEXT,
    original_label TEXT,
    horizon TEXT,
    max_future_return_pct REAL,
    return_at_horizon_pct REAL,
    reject_reasons TEXT,
    liquidity_at_scan REAL,
    volume_at_scan REAL,
    pair_age_hours_at_scan REAL,
    price_change_1h_at_scan REAL,
    security_score REAL,
    fdv REAL,
    fdv_liquidity_ratio REAL,
    tradability_score REAL,
    tradability_label TEXT,
    should_be_high_risk_momentum INTEGER,
    main_lesson TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS baseline_comparisons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    symbol TEXT,
    pair_id TEXT,
    label TEXT,
    horizon TEXT,
    token_return_pct REAL,
    btc_return_pct REAL,
    eth_return_pct REAL,
    all_scanned_median_pct REAL,
    same_liquidity_bucket_median_pct REAL,
    same_age_bucket_median_pct REAL,
    excess_vs_btc REAL,
    excess_vs_eth REAL,
    excess_vs_all_scanned REAL,
    excess_vs_same_liquidity REAL,
    excess_vs_same_age REAL,
    baseline_verdict TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS ten_x_failure_review (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    symbol TEXT,
    pair_id TEXT,
    ten_x_score REAL,
    risk_adjusted_10x_score REAL,
    manipulation_risk_score REAL,
    horizon TEXT,
    return_pct REAL,
    failure_reasons TEXT,
    diagnosis_text TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS winner_survival_lab (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    symbol TEXT,
    pair_id TEXT,
    original_label TEXT,
    scan_time TEXT,
    setup_type TEXT,
    pump_strength_score REAL,
    trap_risk_score REAL,
    survival_score REAL,
    survival_label TEXT,
    matured_horizons TEXT,
    latest_horizon TEXT,
    latest_return_pct REAL,
    best_return_pct REAL,
    worst_return_pct REAL,
    liquidity_retention_pct REAL,
    volume_retention_pct REAL,
    buy_ratio_at_scan REAL,
    pair_age_hours_at_scan REAL,
    price_change_1h_at_scan REAL,
    ten_x_score REAL,
    learning_note TEXT,
    next_rule_to_test TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS tradable_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT,
    symbol TEXT,
    pair_id TEXT,
    source_label TEXT,
    scan_time TEXT,
    tradable_score REAL,
    tradable_tier TEXT,
    research_window TEXT,
    latest_horizon TEXT,
    latest_return_pct REAL,
    best_return_pct REAL,
    worst_return_pct REAL,
    liquidity_usd REAL,
    volume_24h_usd REAL,
    buy_ratio REAL,
    pair_age_hours REAL,
    price_change_1h_pct REAL,
    risk_score REAL,
    opportunity_score REAL,
    ten_x_score REAL,
    pump_strength_score REAL,
    trap_risk_score REAL,
    survival_score REAL,
    liquidity_retention_pct REAL,
    volume_retention_pct REAL,
    reasons TEXT,
    risk_notes TEXT,
    action_note TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS winner_loser_dna (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dna_type TEXT,
    sample_count INTEGER,
    median_liquidity_usd REAL,
    median_volume_24h_usd REAL,
    median_pair_age_hours REAL,
    median_price_change_1h_pct REAL,
    median_buy_ratio REAL,
    median_pump_strength_score REAL,
    median_trap_risk_score REAL,
    median_survival_score REAL,
    median_best_return_pct REAL,
    median_worst_return_pct REAL,
    common_source_labels TEXT,
    pattern_summary TEXT,
    rule_implication TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS delayed_entry_simulator (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_label TEXT,
    entry_delay TEXT,
    exit_horizon TEXT,
    sample_count INTEGER,
    median_return_pct REAL,
    average_return_pct REAL,
    positive_rate REAL,
    best_return_pct REAL,
    worst_return_pct REAL,
    verdict TEXT,
    lesson TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS wallet_reputation_memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT,
    chain TEXT,
    wallet_label TEXT,
    memory_tier TEXT,
    wallet_quality_score REAL,
    wallet_risk_score REAL,
    tokens_traded INTEGER,
    realized_exits INTEGER,
    win_rate REAL,
    median_realized_return_pct REAL,
    avg_realized_return_pct REAL,
    rug_exposure_rate REAL,
    quick_dump_rate REAL,
    first_minute_entry_rate REAL,
    avg_hold_time_minutes REAL,
    reputation_note TEXT,
    caution_note TEXT,
    last_seen_at TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_report (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric TEXT,
    value TEXT,
    status TEXT,
    detail TEXT,
    updated_at TEXT
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    migrate_schema(conn)
    conn.commit()
    backfill_v2_from_v1(conn)
    backfill_missing_ten_x_scores(conn)
    prune_resolved_api_events(conn)


def migrate_schema(conn: sqlite3.Connection) -> None:
    _ensure_columns(
        conn,
        "filter_results",
        {
            "wallet_signal_score": "REAL",
            "wallet_signal_label": "TEXT",
            "final_research_score": "REAL",
            "ten_x_score": "REAL",
            "ten_x_label": "TEXT",
            "ten_x_reasons": "TEXT",
        },
    )


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, ddl_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")


def to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def from_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


def backfill_v2_from_v1(conn: sqlite3.Connection) -> int:
    from crypto_signal_autopsy.clients.goplus import SecurityResult
    from crypto_signal_autopsy.config import load_settings
    from crypto_signal_autopsy.filters import evaluate_candidate

    settings = load_settings()
    rows = conn.execute(
        """
        SELECT c.*, s.status AS security_status_row, s.risk_flags AS security_risk_flags,
               s.buy_tax_pct, s.sell_tax_pct, s.raw_json AS security_raw_json
        FROM candidate_evaluations c
        LEFT JOIN security_snapshots s
          ON s.token_address = c.token_address
         AND s.observed_at = c.observed_at
        WHERE NOT EXISTS (
          SELECT 1
          FROM filter_results f
          WHERE f.token_address = c.token_address
            AND f.scan_time = c.observed_at
            AND f.pair_id = c.chain_id || ':' || c.pair_address
            AND f.model_version = 'V2'
        )
        ORDER BY c.observed_at ASC
        """
    ).fetchall()
    inserted = 0
    for row in rows:
        try:
            metrics = from_json(row["raw_metrics"], {})
            risk_flags = from_json(row["security_risk_flags"], [])
            security_raw = from_json(row["security_raw_json"], {})
        except json.JSONDecodeError:
            continue
        if not isinstance(metrics, dict):
            continue
        metrics.setdefault("observed_at", row["observed_at"])
        metrics.setdefault("chain_id", row["chain_id"])
        metrics.setdefault("token_address", row["token_address"])
        metrics.setdefault("pair_address", row["pair_address"])
        metrics.setdefault("pair_id", f"{row['chain_id']}:{row['pair_address']}")
        security = SecurityResult(
            row["security_status_row"] or row["security_status"] or "backfilled_no_security",
            risk_flags if isinstance(risk_flags, list) else [],
            row["buy_tax_pct"],
            row["sell_tax_pct"],
            security_raw if isinstance(security_raw, dict) else {},
        )
        evaluation = evaluate_candidate(metrics, security, None, settings)
        insert_v2_scan_records(conn, row["observed_at"], metrics, security, evaluation)
        inserted += 1
    return inserted


def backfill_missing_ten_x_scores(conn: sqlite3.Connection) -> int:
    from crypto_signal_autopsy.clients.goplus import SecurityResult
    from crypto_signal_autopsy.scoring import score_token

    rows = conn.execute(
        """
        SELECT f.id AS filter_result_id, c.raw_metrics,
               s.status AS security_status_row, s.risk_flags AS security_risk_flags,
               s.buy_tax_pct, s.sell_tax_pct, s.raw_json AS security_raw_json
        FROM filter_results f
        JOIN candidate_evaluations c
          ON c.token_address = f.token_address
         AND c.observed_at = f.scan_time
         AND f.pair_id = c.chain_id || ':' || c.pair_address
        LEFT JOIN security_snapshots s
          ON s.token_address = c.token_address
         AND s.observed_at = c.observed_at
        WHERE f.ten_x_score IS NULL
        """
    ).fetchall()
    updated = 0
    for row in rows:
        try:
            metrics = from_json(row["raw_metrics"], {})
            risk_flags = from_json(row["security_risk_flags"], [])
            security_raw = from_json(row["security_raw_json"], {})
        except json.JSONDecodeError:
            continue
        if not isinstance(metrics, dict):
            continue
        security = SecurityResult(
            row["security_status_row"] or "backfilled_no_security",
            risk_flags if isinstance(risk_flags, list) else [],
            row["buy_tax_pct"],
            row["sell_tax_pct"],
            security_raw if isinstance(security_raw, dict) else {},
        )
        score = score_token(metrics, security)
        conn.execute(
            """
            UPDATE filter_results
            SET ten_x_score = ?, ten_x_label = ?, ten_x_reasons = ?
            WHERE id = ?
            """,
            (score.ten_x_score, score.ten_x_label, to_json(score.ten_x_reasons), row["filter_result_id"]),
        )
        updated += 1
    conn.commit()
    return updated


def log_api_event(
    conn: sqlite3.Connection,
    observed_at: str,
    provider: str,
    endpoint: str,
    status: str,
    message: str | None = None,
    request_url: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO api_events (observed_at, provider, endpoint, status, message, request_url)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (observed_at, provider, endpoint, status, message, request_url),
    )
    conn.commit()


def insert_cex_markets(
    conn: sqlite3.Connection,
    observed_at: str,
    markets: Iterable[dict[str, Any]],
) -> int:
    rows = []
    for coin in markets:
        rows.append(
            (
                observed_at,
                coin.get("id"),
                coin.get("symbol"),
                coin.get("name"),
                coin.get("market_cap_rank"),
                coin.get("current_price"),
                coin.get("total_volume"),
                coin.get("price_change_percentage_1h_in_currency"),
                coin.get("price_change_percentage_24h_in_currency")
                or coin.get("price_change_percentage_24h"),
                coin.get("market_cap"),
                to_json(coin),
            )
        )
    conn.executemany(
        """
        INSERT OR IGNORE INTO cex_market_snapshots (
          observed_at, coin_id, symbol, name, market_cap_rank, price_usd,
          volume_24h_usd, price_change_1h_pct, price_change_24h_pct,
          market_cap_usd, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def insert_dex_snapshot(conn: sqlite3.Connection, metrics: dict[str, Any]) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO dex_token_snapshots (
          observed_at, chain_id, token_address, pair_address, dex_id,
          base_symbol, base_name, quote_symbol, price_usd, liquidity_usd,
          volume_h1_usd, volume_h6_usd, volume_h24_usd, price_change_h1_pct,
          price_change_h24_pct, buys_h1, sells_h1, pair_created_at,
          pair_age_hours, source_tags, boost_total_amount, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metrics["observed_at"],
            metrics["chain_id"],
            metrics["token_address"],
            metrics["pair_address"],
            metrics.get("dex_id"),
            metrics.get("base_symbol"),
            metrics.get("base_name"),
            metrics.get("quote_symbol"),
            metrics.get("price_usd"),
            metrics.get("liquidity_usd"),
            metrics.get("volume_h1_usd"),
            metrics.get("volume_h6_usd"),
            metrics.get("volume_h24_usd"),
            metrics.get("price_change_h1_pct"),
            metrics.get("price_change_h24_pct"),
            metrics.get("buys_h1"),
            metrics.get("sells_h1"),
            metrics.get("pair_created_at"),
            metrics.get("pair_age_hours"),
            to_json(metrics.get("source_tags", [])),
            metrics.get("boost_total_amount"),
            to_json(metrics.get("raw_json", {})),
        ),
    )
    conn.commit()
    row = conn.execute(
        """
        SELECT snapshot_id
        FROM dex_token_snapshots
        WHERE observed_at = ? AND chain_id = ? AND pair_address = ?
        """,
        (metrics["observed_at"], metrics["chain_id"], metrics["pair_address"]),
    ).fetchone()
    return int(row["snapshot_id"])


def insert_security_snapshot(
    conn: sqlite3.Connection,
    observed_at: str,
    chain_id: str,
    token_address: str,
    result: Any,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO security_snapshots (
          observed_at, chain_id, token_address, status, risk_flags,
          buy_tax_pct, sell_tax_pct, raw_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observed_at,
            chain_id,
            token_address,
            result.status,
            to_json(result.risk_flags),
            result.buy_tax_pct,
            result.sell_tax_pct,
            to_json(result.raw),
        ),
    )
    conn.commit()


def insert_v2_scan_records(
    conn: sqlite3.Connection,
    observed_at: str,
    metrics: dict[str, Any],
    security: Any,
    evaluation: Any,
) -> None:
    insert_token_record(conn, observed_at, metrics)
    insert_pair_record(conn, observed_at, metrics)
    insert_security_check(conn, observed_at, metrics, security)
    insert_social_catalyst(conn, observed_at, metrics)
    insert_filter_result(conn, observed_at, metrics, evaluation)
    if evaluation.final_label == "Paper Trade Candidate":
        insert_paper_trade_candidate(conn, observed_at, metrics)


def insert_token_record(conn: sqlite3.Connection, observed_at: str, metrics: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO tokens (
          token_address, chain, symbol, name, decimals, created_at_scan, first_seen_at, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(token_address) DO UPDATE SET
          symbol = COALESCE(excluded.symbol, tokens.symbol),
          name = COALESCE(excluded.name, tokens.name),
          source = excluded.source
        """,
        (
            metrics["token_address"],
            metrics.get("chain_id"),
            metrics.get("base_symbol"),
            metrics.get("base_name"),
            metrics.get("base_decimals"),
            observed_at,
            observed_at,
            ",".join(str(tag) for tag in metrics.get("source_tags", [])),
        ),
    )
    conn.commit()


def insert_pair_record(conn: sqlite3.Connection, observed_at: str, metrics: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO pairs (
          pair_id, token_address, chain, dex_name, pair_address, base_token,
          quote_token, pair_created_at, pair_age_minutes, pair_age_hours,
          liquidity_usd, fdv, market_cap, fdv_liquidity_ratio,
          marketcap_liquidity_ratio, price_usd, volume_5m, volume_1h,
          volume_6h, volume_24h, volume_liquidity_ratio, txns_5m_buys,
          txns_5m_sells, txns_15m_buys, txns_15m_sells, txns_1h_buys,
          txns_1h_sells, txns_24h_buys, txns_24h_sells, total_txns_1h,
          total_txns_24h, buy_ratio, unique_buyers_15m, unique_buyers_1h,
          unique_buyers_24h, price_change_5m, price_change_1h,
          price_change_6h, price_change_24h, estimated_slippage_100_usd,
          raw_json, scan_time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metrics.get("pair_id") or f"{metrics.get('chain_id')}:{metrics.get('pair_address')}",
            metrics["token_address"],
            metrics.get("chain_id"),
            metrics.get("dex_id"),
            metrics.get("pair_address"),
            metrics.get("base_symbol"),
            metrics.get("quote_symbol"),
            metrics.get("pair_created_at"),
            metrics.get("pair_age_minutes"),
            metrics.get("pair_age_hours"),
            metrics.get("liquidity_usd"),
            metrics.get("fdv"),
            metrics.get("market_cap"),
            metrics.get("fdv_liquidity_ratio"),
            metrics.get("marketcap_liquidity_ratio"),
            metrics.get("price_usd"),
            metrics.get("volume_5m_usd"),
            metrics.get("volume_h1_usd"),
            metrics.get("volume_h6_usd"),
            metrics.get("volume_h24_usd"),
            metrics.get("volume_liquidity_ratio"),
            metrics.get("buys_m5"),
            metrics.get("sells_m5"),
            metrics.get("txns_15m_buys"),
            metrics.get("txns_15m_sells"),
            metrics.get("txns_1h_buys"),
            metrics.get("txns_1h_sells"),
            metrics.get("txns_24h_buys"),
            metrics.get("txns_24h_sells"),
            metrics.get("total_txns_1h"),
            metrics.get("total_txns_24h"),
            metrics.get("buy_ratio"),
            metrics.get("unique_buyers_15m"),
            metrics.get("unique_buyers_1h"),
            metrics.get("unique_buyers_24h"),
            metrics.get("price_change_5m_pct"),
            metrics.get("price_change_h1_pct"),
            metrics.get("price_change_h6_pct"),
            metrics.get("price_change_h24_pct"),
            metrics.get("estimated_slippage_100_usd"),
            to_json(metrics.get("raw_json", {})),
            observed_at,
        ),
    )
    conn.commit()


def insert_security_check(
    conn: sqlite3.Connection,
    observed_at: str,
    metrics: dict[str, Any],
    security: Any,
) -> None:
    raw = security.raw or {}
    conn.execute(
        """
        INSERT INTO security_checks (
          token_address, chain, provider, security_score, is_honeypot,
          cannot_sell, is_blacklisted, trading_disabled, is_mintable,
          is_proxy, owner_renounced, blacklist_function,
          dangerous_owner_permissions, buy_tax, sell_tax, top_holder_pct,
          top_10_holder_pct, lp_locked_pct, lp_burned_pct, risk_flags,
          raw_json, scan_time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metrics["token_address"],
            metrics.get("chain_id"),
            "goplus",
            _security_num(raw, "security_score", "score"),
            _truthy_int(raw.get("is_honeypot")),
            _truthy_int(raw.get("cannot_sell") or raw.get("cannot_sell_all")),
            _truthy_int(raw.get("is_blacklisted")),
            _truthy_int(raw.get("trading_disabled")),
            _truthy_int(raw.get("is_mintable")),
            _truthy_int(raw.get("is_proxy")),
            _truthy_int(raw.get("owner_renounced")),
            _truthy_int(raw.get("blacklist_function")),
            1 if {"owner_change_balance", "hidden_owner"}.intersection(set(security.risk_flags)) else 0,
            security.buy_tax_pct,
            security.sell_tax_pct,
            _security_num(raw, "top_holder_pct", "holder_count_1_pct"),
            _security_num(raw, "top_10_holder_pct", "top_10_holder_rate"),
            _security_num(raw, "lp_locked_pct"),
            _security_num(raw, "lp_burned_pct"),
            to_json(security.risk_flags),
            to_json(raw),
            observed_at,
        ),
    )
    conn.commit()


def insert_filter_result(conn: sqlite3.Connection, observed_at: str, metrics: dict[str, Any], evaluation: Any) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO filter_results (
          token_address, pair_id, scan_time, hard_reject,
          qualifies_high_risk_momentum, reject_reasons, risk_score,
          opportunity_score, ten_x_score, ten_x_label, ten_x_reasons,
          final_label, risk_category, opportunity_category, model_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metrics["token_address"],
            metrics.get("pair_id") or f"{metrics.get('chain_id')}:{metrics.get('pair_address')}",
            observed_at,
            1 if evaluation.hard_reject else 0,
            1 if evaluation.qualifies_high_risk_momentum else 0,
            to_json(evaluation.rejection_reasons),
            evaluation.risk_score,
            evaluation.opportunity_score,
            evaluation.ten_x_score,
            evaluation.ten_x_label,
            to_json(evaluation.ten_x_reasons or []),
            evaluation.final_label,
            evaluation.risk_category,
            evaluation.opportunity_category,
            evaluation.filter_version,
        ),
    )
    conn.commit()


def insert_social_catalyst(conn: sqlite3.Connection, observed_at: str, metrics: dict[str, Any]) -> None:
    raw = metrics.get("raw_json") or {}
    info = raw.get("info") if isinstance(raw, dict) else {}
    socials = info.get("socials") if isinstance(info, dict) else []
    websites = info.get("websites") if isinstance(info, dict) else []
    conn.execute(
        """
        INSERT INTO social_catalysts (
          token_address, scan_time, website_present, twitter_present,
          telegram_present, discord_present, dexscreener_boosted, boost_amount,
          coingecko_listed, coinmarketcap_listed, cex_listed, news_mentions,
          social_notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metrics["token_address"],
            observed_at,
            1 if websites else 0,
            1 if _social_present(socials, "twitter", "x") else 0,
            1 if _social_present(socials, "telegram") else 0,
            1 if _social_present(socials, "discord") else 0,
            1 if _is_boosted(metrics) else 0,
            metrics.get("boost_total_amount"),
            1 if metrics.get("coingecko_listed") else 0,
            1 if metrics.get("coinmarketcap_listed") else 0,
            1 if metrics.get("cex_listed") else 0,
            0,
            "",
        ),
    )
    conn.commit()


def insert_paper_trade_candidate(conn: sqlite3.Connection, observed_at: str, metrics: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO paper_trades (
          token_address, pair_id, created_at, entry_rule, sim_entry_price,
          sim_position_size_usd, stop_rule, take_profit_rule, status, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metrics["token_address"],
            metrics.get("pair_id") or f"{metrics.get('chain_id')}:{metrics.get('pair_address')}",
            observed_at,
            "V2 paper trade candidate - simulated tracking only",
            metrics.get("price_usd"),
            100.0,
            "Research stop only; no live order",
            "Research take-profit only; no live order",
            "open",
            "Paper Trade Candidate means simulated tracking only. Not a buy signal.",
        ),
    )
    conn.commit()


def insert_outcome_snapshot(conn: sqlite3.Connection, row: dict[str, Any]) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO outcome_snapshots (
          token_address, pair_id, label_at_scan, scan_time, horizon,
          price_at_scan, price_at_horizon, return_pct,
          max_favorable_excursion_pct, max_adverse_excursion_pct,
          drawdown_before_pump_pct, liquidity_at_scan, liquidity_at_horizon,
          volume_at_scan, volume_at_horizon, became_illiquid, rugged,
          volume_disappeared, snapshot_time
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row.get("token_address"),
            row.get("pair_id"),
            row.get("label_at_scan"),
            row.get("scan_time"),
            row.get("horizon"),
            row.get("price_at_scan"),
            row.get("price_at_horizon"),
            row.get("return_pct"),
            row.get("max_favorable_excursion_pct"),
            row.get("max_adverse_excursion_pct"),
            row.get("drawdown_before_pump_pct"),
            row.get("liquidity_at_scan"),
            row.get("liquidity_at_horizon"),
            row.get("volume_at_scan"),
            row.get("volume_at_horizon"),
            1 if row.get("became_illiquid") else 0,
            1 if row.get("rugged") else 0,
            1 if row.get("volume_disappeared") else 0,
            row.get("snapshot_time"),
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def archive_completed_rejected_audits(conn: sqlite3.Connection, completed_at: str) -> int:
    rows = conn.execute(
        """
        SELECT f.id AS filter_result_id, f.token_address, f.pair_id, f.scan_time,
               f.reject_reasons, f.ten_x_score, f.ten_x_label, f.ten_x_reasons,
               c.id AS candidate_evaluation_id, c.chain_id, c.pair_address, c.raw_metrics,
               o.price_at_scan, o.price_at_horizon, o.return_pct, o.rugged,
               o.became_illiquid, o.volume_disappeared
        FROM filter_results f
        JOIN candidate_evaluations c
          ON c.token_address = f.token_address
         AND c.observed_at = f.scan_time
         AND f.pair_id = c.chain_id || ':' || c.pair_address
        JOIN outcome_snapshots o
          ON o.pair_id = f.pair_id
         AND o.scan_time = f.scan_time
         AND o.horizon = '24h'
        WHERE f.final_label = 'Reject'
        ORDER BY f.scan_time ASC
        """
    ).fetchall()
    archived = 0
    for row in rows:
        snapshots = conn.execute(
            """
            SELECT return_pct, rugged, became_illiquid, volume_disappeared
            FROM outcome_snapshots
            WHERE pair_id = ? AND scan_time = ?
            """,
            (row["pair_id"], row["scan_time"]),
        ).fetchall()
        returns = [_as_number(snapshot["return_pct"]) for snapshot in snapshots]
        clean_returns = [value for value in returns if value is not None]
        best_return = max(clean_returns) if clean_returns else None
        worst_return = min(clean_returns) if clean_returns else None
        metrics = _safe_json(row["raw_metrics"], {})
        symbol = metrics.get("base_symbol") if isinstance(metrics, dict) else None
        rugged = any(bool(snapshot["rugged"]) for snapshot in snapshots) or bool(row["rugged"])
        became_illiquid = any(bool(snapshot["became_illiquid"]) for snapshot in snapshots) or bool(row["became_illiquid"])
        volume_disappeared = any(bool(snapshot["volume_disappeared"]) for snapshot in snapshots) or bool(
            row["volume_disappeared"]
        )
        verdict = _rejected_filter_verdict(row["return_pct"], rugged, became_illiquid, volume_disappeared)
        lesson = _rejected_filter_lesson(verdict, row["return_pct"], row["reject_reasons"])
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO rejected_filter_audits (
              token_address, pair_id, chain, symbol, scan_time, completed_at,
              reject_reasons, ten_x_score, ten_x_label, ten_x_reasons,
              price_at_scan, price_24h, return_24h_pct, best_return_pct,
              worst_return_pct, rugged, became_illiquid, volume_disappeared,
              filter_verdict, lesson
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["token_address"],
                row["pair_id"],
                row["chain_id"],
                symbol,
                row["scan_time"],
                completed_at,
                row["reject_reasons"],
                row["ten_x_score"],
                row["ten_x_label"],
                row["ten_x_reasons"],
                row["price_at_scan"],
                row["price_at_horizon"],
                row["return_pct"],
                best_return,
                worst_return,
                1 if rugged else 0,
                1 if became_illiquid else 0,
                1 if volume_disappeared else 0,
                verdict,
                lesson,
            ),
        )
        archived += cursor.rowcount

        conn.execute(
            "DELETE FROM rejected_candidate_outcomes WHERE candidate_evaluation_id = ?",
            (row["candidate_evaluation_id"],),
        )
        conn.execute(
            "DELETE FROM outcome_snapshots WHERE pair_id = ? AND scan_time = ? AND label_at_scan = 'Reject'",
            (row["pair_id"], row["scan_time"]),
        )
        conn.execute("DELETE FROM filter_results WHERE id = ?", (row["filter_result_id"],))
        conn.execute("DELETE FROM candidate_evaluations WHERE id = ?", (row["candidate_evaluation_id"],))
        conn.execute(
            "DELETE FROM security_checks WHERE token_address = ? AND scan_time = ?",
            (row["token_address"], row["scan_time"]),
        )
        conn.execute(
            "DELETE FROM social_catalysts WHERE token_address = ? AND scan_time = ?",
            (row["token_address"], row["scan_time"]),
        )
    conn.commit()
    return archived


def insert_candidate_evaluation(
    conn: sqlite3.Connection,
    observed_at: str,
    metrics: dict[str, Any],
    evaluation: Any,
    security_status: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO candidate_evaluations (
          observed_at, chain_id, token_address, pair_address, accepted,
          signal_types, rejection_reasons, filter_version, security_status,
          raw_metrics
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observed_at,
            metrics["chain_id"],
            metrics["token_address"],
            metrics["pair_address"],
            1 if evaluation.accepted else 0,
            to_json(evaluation.signal_types),
            to_json(evaluation.rejection_reasons),
            evaluation.filter_version,
            security_status,
            to_json(metrics),
        ),
    )
    conn.commit()


def insert_signal(
    conn: sqlite3.Connection,
    metrics: dict[str, Any],
    signal_type: str,
    filter_version: str,
    delayed_entry_at: str,
    snapshot_id: int,
) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO signals (
          created_at, chain_id, token_address, pair_address, base_symbol,
          signal_type, detection_price_usd, detection_liquidity_usd,
          detection_volume_h24_usd, detection_pair_age_hours, delayed_entry_at,
          filter_version, source_tags, detection_snapshot_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            metrics["observed_at"],
            metrics["chain_id"],
            metrics["token_address"],
            metrics["pair_address"],
            metrics.get("base_symbol"),
            signal_type,
            metrics["price_usd"],
            metrics.get("liquidity_usd"),
            metrics.get("volume_h24_usd"),
            metrics.get("pair_age_hours"),
            delayed_entry_at,
            filter_version,
            to_json(metrics.get("source_tags", [])),
            snapshot_id,
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def latest_dex_snapshot_before(
    conn: sqlite3.Connection,
    pair_address: str,
    observed_at: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM dex_token_snapshots
        WHERE pair_address = ? AND observed_at < ?
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        (pair_address, observed_at),
    ).fetchone()


def active_api_error_count(conn: sqlite3.Connection) -> int:
    return len(active_api_errors(conn))


def active_api_errors(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT *
        FROM api_events
        WHERE status = 'error'
        ORDER BY observed_at DESC
        """
    ).fetchall()
    return [row for row in rows if not _api_error_recovered(conn, row)]


def prune_resolved_api_events(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT *
        FROM api_events
        ORDER BY observed_at ASC
        """
    ).fetchall()
    stale_ids: list[int] = []
    for row in rows:
        if row["status"] != "error" or _api_error_recovered(conn, row):
            stale_ids.append(int(row["id"]))
    if not stale_ids:
        return 0
    conn.executemany("DELETE FROM api_events WHERE id = ?", [(row_id,) for row_id in stale_ids])
    conn.commit()
    return len(stale_ids)


def export_table(conn: sqlite3.Connection, table: str, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        out_path.write_text("", encoding="utf-8")
        return 0

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
    return len(rows)


def _truthy_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return 1 if value else 0
    return 1 if str(value).strip().lower() in {"1", "true", "yes", "y"} else 0


def _security_num(raw: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = raw.get(key)
        if value in {None, "", "null"}:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if 0 < number <= 1 and ("pct" in key or "rate" in key):
            return number * 100
        return number
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


def _safe_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _as_number(value: Any) -> float | None:
    if value in {None, "", "null"}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _api_error_recovered(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    recovered_at = _latest_api_success_after(conn, str(row["provider"]), str(row["observed_at"]))
    return recovered_at is not None and recovered_at > row["observed_at"]


def _latest_api_success_after(conn: sqlite3.Connection, provider: str, observed_at: str) -> str | None:
    if provider == "coingecko":
        return conn.execute(
            """
            SELECT MAX(observed_at) AS value
            FROM cex_market_snapshots
            WHERE observed_at > ?
            """,
            (observed_at,),
        ).fetchone()["value"]
    if provider == "dexscreener":
        row = conn.execute(
            """
            SELECT MAX(value) AS value
            FROM (
              SELECT MAX(observed_at) AS value FROM dex_token_snapshots WHERE observed_at > ?
              UNION ALL
              SELECT MAX(scan_time) AS value FROM filter_results WHERE scan_time > ?
              UNION ALL
              SELECT MAX(snapshot_time) AS value FROM outcome_snapshots WHERE snapshot_time > ?
            )
            """,
            (observed_at, observed_at, observed_at),
        ).fetchone()
        return row["value"] if row else None
    return None


def _rejected_filter_verdict(
    return_24h_pct: Any,
    rugged: bool,
    became_illiquid: bool,
    volume_disappeared: bool,
) -> str:
    value = _as_number(return_24h_pct)
    if rugged or became_illiquid or volume_disappeared:
        return "success"
    if value is None:
        return "unknown"
    if value <= 0:
        return "success"
    return "failure"


def _rejected_filter_lesson(verdict: str, return_24h_pct: Any, reject_reasons: str | None) -> str:
    reasons = _safe_json(reject_reasons, [])
    reason_text = ", ".join(str(reason).replace("_", " ") for reason in reasons[:3]) if reasons else "rules failed"
    value = _as_number(return_24h_pct)
    if verdict == "success":
        if value is not None and value <= -25:
            return f"Filter helped: rejected token lost hard after 24h; main reasons were {reason_text}."
        return f"Filter was acceptable: rejected token did not beat zero after 24h; main reasons were {reason_text}."
    if verdict == "failure":
        return f"Filter missed upside: rejected token was positive after 24h; review {reason_text}."
    return f"Could not judge cleanly after 24h; review data quality and {reason_text}."
