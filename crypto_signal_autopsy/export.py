from __future__ import annotations

import sqlite3
from pathlib import Path

from crypto_signal_autopsy import db


EXPORTS = {
    "signals": "signals",
    "outcomes": "signal_outcomes",
    "rejected_outcomes": "rejected_candidate_outcomes",
    "rejections": "candidate_evaluations",
    "api_events": "api_events",
}


def export_all(conn: sqlite3.Connection, export_dir: Path) -> dict[str, int]:
    db.init_db(conn)
    export_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, table in EXPORTS.items():
        counts[name] = db.export_table(conn, table, export_dir / f"{name}.csv")
    return counts
