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
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def to_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def from_json(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    return json.loads(value)


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


def export_table(conn: sqlite3.Connection, table: str, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        out_path.write_text("", encoding="utf-8")
        return 0

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(dict(row) for row in rows)
    return len(rows)
