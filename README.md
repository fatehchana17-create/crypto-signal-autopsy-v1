# Crypto Signal Autopsy V1

Tiny research system for one job:

> Do simple public crypto signals survive basic filters and beat fair baselines after costs?

This is not an AI crypto opportunity finder, not an auto-trader, and not a source of buy/sell advice. V1 is a deterministic paper-tracking lab that tries to avoid fooling itself.

## What V1 Does

- Scans CoinGecko top-100 CEX market context.
- Discovers Base-chain DEX candidates from DEX Screener profile/boost endpoints.
- Applies hard filters for liquidity, volume, pair age, price overextension, and GoPlus risks when available.
- Records accepted and rejected candidates in SQLite.
- Paper-tracks accepted signals at 1h and 24h.
- Exports clean CSVs.
- Shows a compact Streamlit dashboard.

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

The dashboard command starts Streamlit at `http://localhost:8501`. The scanner also works without the dashboard:

```powershell
python -m crypto_signal_autopsy.cli scan
python -m crypto_signal_autopsy.cli track
python -m crypto_signal_autopsy.cli export
```

## Hourly Automation

The local automation daemon runs one cycle, exports CSVs, then sleeps:

```powershell
python -m crypto_signal_autopsy.cli daemon --interval-minutes 60
```

This keeps working while that Python process is alive. For true always-on use after reboot, run the same command through Windows Task Scheduler, a VPS, Render, Railway, or another process manager.

The automation log is written to `logs/automation.log`.

## Rejected Token Audit

Rejected candidates are not ignored. V1 stores why each token was rejected and later tracks rejected-token performance at:

```text
1h
24h
```

This matters because rejected tokens are the filter test. If rejected tokens perform better than accepted signals, the filters are wrong.

The dashboard also has a `Manual Review` tab. It shows each rejected token with a DEX Screener link, actual liquidity, volume, pair age, 1h move, rejection reasons, and an actual-vs-required table for the selected token.

## API Notes

- CoinGecko `/coins/markets` is used for top-100 market context.
- DEX Screener profile/boost/token endpoints are used for Base candidates.
- GoPlus token security is optional by default because the current docs require bearer auth for `token_security/{chain_id}`.

If `GOPLUS_ACCESS_TOKEN` is missing, V1 records `security_status=skipped_no_token`. Set `CSA_REQUIRE_GOPLUS=true` if you want unchecked tokens rejected instead.

## Success Rules To Watch

Do not judge early rows as edge. Keep collecting until there are enough clean tracked signals:

- At least 200 clean tracked signals.
- At least 50 signals per signal type before judging that type.
- Median net return beats matched baseline.
- Average net return still beats baseline after removing top 5% outliers.
- 5-minute delayed entry does not collapse the result.
- Rejection filters reduce losses compared with unfiltered candidates.
- Data missing/error rate stays below 5-10%.

Kill or revise if the data is sparse, noisy, outlier-driven, worse than baseline, or too error-prone to trust.

## Outputs

- `data/autopsy.sqlite`: local SQLite database.
- `exports/signals.csv`: accepted signal detections.
- `exports/outcomes.csv`: 1h/24h paper-tracking outcomes.
- `exports/rejections.csv`: rejected candidate rows and reasons.
- `exports/api_events.csv`: API errors, skips, and notable events.
