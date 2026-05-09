from __future__ import annotations

import argparse
import subprocess
import sys

from crypto_signal_autopsy.config import load_settings
from crypto_signal_autopsy.daemon import configure_logging, run_daemon
from crypto_signal_autopsy.db import connect, init_db
from crypto_signal_autopsy.export import export_all
from crypto_signal_autopsy.scan import run_scan
from crypto_signal_autopsy.static_site import build_static_site
from crypto_signal_autopsy.track import run_tracking
from crypto_signal_autopsy.wallet_discovery import run_wallet_module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="csa")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db", help="Create or migrate the SQLite database.")
    subparsers.add_parser("scan", help="Run one CEX/DEX discovery and signal scan.")
    subparsers.add_parser("track", help="Track due delayed entries and 1h/24h outcomes.")
    subparsers.add_parser("wallets", help="Run V3 smart wallet discovery, scoring, and signals.")
    subparsers.add_parser("run-once", help="Run scan, tracking, and CSV export.")
    subparsers.add_parser("export", help="Export CSV files.")
    subparsers.add_parser("static-site", help="Build the static GitHub Pages dashboard.")
    dashboard_parser = subparsers.add_parser("dashboard", help="Start the Streamlit dashboard.")
    dashboard_parser.add_argument("--port", type=int, default=8501, help="Local Streamlit port.")
    daemon_parser = subparsers.add_parser("daemon", help="Run scan, track, and export on a local interval.")
    daemon_parser.add_argument("--interval-minutes", type=int, default=60, help="Minutes between cycles.")
    daemon_parser.add_argument(
        "--no-run-immediately",
        action="store_true",
        help="Wait one interval before the first cycle.",
    )

    args = parser.parse_args(argv)
    settings = load_settings()
    conn = connect(settings.db_path)

    if args.command == "init-db":
        init_db(conn)
        print(f"Initialized {settings.db_path}")
        return 0

    if args.command == "scan":
        stats = run_scan(conn, settings)
        print(_format_stats("scan", stats))
        return 0

    if args.command == "track":
        stats = run_tracking(conn, settings)
        print(_format_stats("track", stats))
        return 0

    if args.command == "wallets":
        stats = run_wallet_module(conn, settings)
        print(_format_stats("wallets", stats))
        return 0

    if args.command == "run-once":
        scan_stats = run_scan(conn, settings)
        track_stats = run_tracking(conn, settings)
        wallet_stats = run_wallet_module(conn, settings)
        export_counts = export_all(conn, settings.export_dir)
        print(_format_stats("scan", scan_stats))
        print(_format_stats("track", track_stats))
        print(_format_stats("wallets", wallet_stats))
        print(_format_stats("export", export_counts))
        return 0

    if args.command == "export":
        counts = export_all(conn, settings.export_dir)
        print(_format_stats("export", counts))
        return 0

    if args.command == "static-site":
        out_path = build_static_site(conn, settings)
        print(f"static-site: {out_path}")
        return 0

    if args.command == "dashboard":
        dashboard_path = settings.project_root / "crypto_signal_autopsy" / "dashboard.py"
        return subprocess.call(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(dashboard_path),
                "--server.headless=true",
                "--server.port",
                str(args.port),
            ]
        )

    if args.command == "daemon":
        log_path = settings.project_root / "logs" / "automation.log"
        configure_logging(log_path)
        run_daemon(
            conn,
            settings,
            interval_minutes=args.interval_minutes,
            run_immediately=not args.no_run_immediately,
        )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _format_stats(label: str, stats: dict[str, int]) -> str:
    parts = ", ".join(f"{key}={value}" for key, value in stats.items())
    return f"{label}: {parts}"


if __name__ == "__main__":
    raise SystemExit(main())
