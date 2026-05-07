from __future__ import annotations

from datetime import datetime, timedelta
import logging
from pathlib import Path
import sqlite3
import time

from crypto_signal_autopsy.config import Settings
from crypto_signal_autopsy.export import export_all
from crypto_signal_autopsy.scan import run_scan
from crypto_signal_autopsy.track import run_tracking


def configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def run_daemon(
    conn: sqlite3.Connection,
    settings: Settings,
    interval_minutes: int,
    run_immediately: bool = True,
) -> None:
    interval_seconds = max(interval_minutes, 1) * 60
    logging.info("Starting automation daemon interval_minutes=%s", interval_minutes)

    next_run_at = datetime.now() if run_immediately else datetime.now() + timedelta(seconds=interval_seconds)
    last_wait_log_at: datetime | None = None
    if not run_immediately:
        logging.info("First run scheduled for %s", next_run_at.isoformat(timespec="seconds"))

    while True:
        now = datetime.now()
        if now < next_run_at:
            remaining = max((next_run_at - now).total_seconds(), 1)
            if last_wait_log_at is None or (now - last_wait_log_at).total_seconds() >= 300:
                logging.info(
                    "Waiting for next cycle next_run_at=%s seconds_remaining=%s",
                    next_run_at.isoformat(timespec="seconds"),
                    int(remaining),
                )
                last_wait_log_at = now
            time.sleep(min(remaining, 30))
            continue

        started = datetime.now().isoformat(timespec="seconds")
        try:
            scan_stats = run_scan(conn, settings)
            track_stats = run_tracking(conn, settings)
            export_stats = export_all(conn, settings.export_dir)
            logging.info(
                "cycle started=%s scan=%s track=%s export=%s",
                started,
                scan_stats,
                track_stats,
                export_stats,
            )
        except Exception:
            logging.exception("automation cycle failed")
        next_run_at = datetime.now() + timedelta(seconds=interval_seconds)
        last_wait_log_at = None
        logging.info("Next cycle scheduled for %s", next_run_at.isoformat(timespec="seconds"))
