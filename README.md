# Crypto Signal Autopsy V2

Crypto Signal Autopsy V2 is a public crypto research dashboard.

It does not give buy signals.
It does not auto-trade.
It does not provide financial advice.
It does not claim to predict pumps.

The system scans public crypto market data, applies hard safety filters, scores tokens by risk and opportunity, tracks future outcomes, and studies whether the filters are useful.

The goal is to learn:

- which filters protect against bad tokens,
- which filters block early winners,
- which tokens become illiquid,
- which tokens rug,
- which score buckets perform better over time,
- whether any research edge exists after enough data.

## What V2 Does

- Scans CoinGecko top-100 CEX market context.
- Discovers Base-chain DEX candidates from DEX Screener profile and boost endpoints.
- Uses GoPlus security data when an access token is available.
- Gives every scanned pair one final research label: Reject, Watchlist, High-Risk Momentum Watchlist, Research Candidate, or Paper Trade Candidate.
- Calculates risk score and opportunity score from 0 to 100.
- Tracks outcomes at 15m, 1h, 4h, 24h, 3d, and 7d.
- Exports V2 CSV files for labels, outcomes, outliers, filter lessons, and score buckets.
- Builds a static GitHub Pages dashboard that does not call APIs from the browser.

## Important Labels

`Paper Trade Candidate` means simulated tracking only. It does not mean buy.

`High-Risk Momentum Watchlist` means a risky token is being studied because it is moving aggressively. It is not a recommendation.

Average return can be distorted by outliers. Median return is usually more reliable.

## Quick Start

Use the Python 3.12 runtime available on this machine:

```powershell
& 'C:\Users\H.H\AppData\Roaming\uv\python\cpython-3.12.13-windows-x86_64-none\python.exe' -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
python -m crypto_signal_autopsy.cli init-db
python -m crypto_signal_autopsy.cli run-once
python -m crypto_signal_autopsy.cli dashboard --port 8501
```

The dashboard command starts Streamlit at `http://localhost:8501`.

The scanner also works without the dashboard:

```powershell
python -m crypto_signal_autopsy.cli scan
python -m crypto_signal_autopsy.cli track
python -m crypto_signal_autopsy.cli export
python -m crypto_signal_autopsy.cli static-site
```

## Architecture

```text
APIs
  -> Python scanner
  -> SQLite database
  -> CSV/JSON-style static payload
  -> GitHub Pages dashboard
```

The public website is static. It works even when the local machine is off.

## Hourly Automation

The local automation daemon runs one cycle, exports CSVs, then sleeps:

```powershell
python -m crypto_signal_autopsy.cli daemon --interval-minutes 60
```

This keeps working while that Python process is alive. For true always-on use after reboot, run the same command through Windows Task Scheduler, a VPS, Render, Railway, or another process manager.

GitHub Actions also runs the scanner on a schedule and publishes the static dashboard.

## V2 CSV Outputs

- `exports/tokens.csv`
- `exports/pairs.csv`
- `exports/security_checks.csv`
- `exports/filter_results.csv`
- `exports/social_catalysts.csv`
- `exports/paper_trades.csv`
- `exports/outcome_snapshots.csv`
- `exports/review_notes.csv`
- `exports/dashboard_summary.csv`
- `exports/outliers.csv`
- `exports/filter_saved_us.csv`
- `exports/filter_blocked_winners.csv`
- `exports/score_bucket_performance.csv`

Old V1 CSVs are still exported for compatibility.

## Success Rules

V2 is useful only if it keeps collecting honest evidence. Do not judge early rows as edge.

Continue only if the system can show:

- enough tracked tokens per label,
- median and average returns by horizon,
- outlier-aware performance,
- filters that saved the system from bad tokens,
- filters that blocked winners,
- score buckets that improve with more data,
- low API/data error rates.

Kill or revise if the data is sparse, noisy, outlier-driven, worse than baseline, or too error-prone to trust.
