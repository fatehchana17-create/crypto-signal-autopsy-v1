# Crypto Signal Autopsy V3

Crypto Signal Autopsy V3 is a public crypto research dashboard.

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

- Scans CoinGecko top-300 CEX market context.
- Discovers Base-chain DEX candidates from DEX Screener profile, boost, and pair-search endpoints.
- Uses GoPlus security data when an access token is available.
- Gives every scanned pair one final research label: Reject, Watchlist, High-Risk Momentum Watchlist, Research Candidate, or Paper Trade Candidate.
- Calculates risk score, opportunity score, and a research-only 10x setup score from 0 to 100.
- Tracks outcomes at 15m, 30m, 1h, 2h, 4h, 8h, and 24h.
- Archives completed rejected-token audits after 24h, then removes the active rejected rows from the database.
- Exports V2 CSV files for labels, outcomes, outliers, filter lessons, and score buckets.
- Builds a static GitHub Pages dashboard that does not call APIs from the browser.

## Smart Wallet Tracking Module

The Smart Wallet Tracking Module studies wallets that interact with scanned tokens.

It tracks wallet behavior, estimates wallet quality/risk, and checks whether wallet activity improves token research.

This module does not provide copy-trading signals.

A wallet buying a token does not mean the token should be bought.

The module exists to answer research questions:

- Do certain wallets repeatedly enter early before strong moves?
- Do those wallets also avoid rugs?
- Do they realize profits or only hold unrealized gains?
- Do suspicious wallets appear before dumps?
- Does wallet clustering improve research candidate quality?

The first V3 provider is Moralis pair swaps for EVM chains. If `MORALIS_API_KEY` is missing, the wallet module is skipped and the normal scanner/dashboard still run.

## Important Labels

`Paper Trade Candidate` means simulated tracking only. It does not mean buy.

`High-Risk Momentum Watchlist` means a risky token is being studied because it is moving aggressively. It is not a recommendation.

`10x Research Setup` means the token has some early conditions worth studying, such as small size, enough liquidity, strong volume versus liquidity, buy pressure, and security sanity. It does not mean the token will 10x.

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
python -m crypto_signal_autopsy.cli wallets
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
- `exports/rejected_filter_audits.csv`
- `exports/filter_accuracy.csv`
- `exports/review_notes.csv`
- `exports/dashboard_summary.csv`
- `exports/outliers.csv`
- `exports/filter_saved_us.csv`
- `exports/filter_blocked_winners.csv`
- `exports/score_bucket_performance.csv`

Old V1 CSVs are still exported for compatibility.

## V3 Wallet CSV Outputs

- `exports/wallets.csv`
- `exports/wallet_trades.csv`
- `exports/wallet_performance.csv`
- `exports/wallet_token_signals.csv`
- `exports/wallet_clusters.csv`
- `exports/wallet_leaderboard.csv`
- `exports/suspicious_wallets.csv`
- `exports/wallet_activity_feed.csv`

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
