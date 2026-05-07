# Deployment

The local dashboard stops when the laptop sleeps because the laptop is currently the server.

For a free cloud MVP, use this setup:

1. Push this project to its own GitHub repository.
2. Deploy `streamlit_app.py` on Streamlit Community Cloud.
3. Let GitHub Actions run `.github/workflows/hourly-scan.yml` every hour.

This means:

- The website can be opened from a public `streamlit.app` URL.
- The hourly scanner runs in GitHub Actions, not on the laptop.
- The scanner commits `data/autopsy.sqlite` and CSV exports back to GitHub.
- Streamlit reads the latest committed database when the app is awake.

Important limits:

- Streamlit Community Cloud apps sleep after inactivity, so this is not guaranteed hot 24/7.
- Local SQLite storage on Streamlit Cloud should not be trusted as permanent storage.
- GitHub Actions schedules are good enough for a free MVP, but not guaranteed exact to the minute.
- For real production 24/7, move the database to hosted Postgres and run the scanner on a small paid server or worker.

## Streamlit Cloud Settings

Use these values:

- Repository: your GitHub repository for this project
- Branch: `main`
- Main file path: `streamlit_app.py`
- Python version: `3.12`

Optional secrets:

```text
COINGECKO_DEMO_API_KEY=""
COINGECKO_PRO_API_KEY=""
GOPLUS_ACCESS_TOKEN=""
```

The app works without those keys, but GoPlus security stays skipped until `GOPLUS_ACCESS_TOKEN` is set.

## GitHub Actions

The workflow can also be run manually:

1. Open the GitHub repository.
2. Go to `Actions`.
3. Select `Hourly crypto scan`.
4. Click `Run workflow`.

After the first successful run, GitHub should contain:

- `data/autopsy.sqlite`
- `exports/rejections.csv`
- `exports/rejected_outcomes.csv`
- `exports/signals.csv`
- `exports/outcomes.csv`
- `exports/api_events.csv`
