from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import json
import shutil
import sqlite3
from statistics import mean, median
from typing import Any

from crypto_signal_autopsy.analysis_tables import build_analysis_payload
from crypto_signal_autopsy.config import Settings
from crypto_signal_autopsy.db import from_json, init_db
from crypto_signal_autopsy.quant_analytics import (
    build_quant_payload,
    refresh_quant_analytics,
    write_public_quant_json_data,
)
from crypto_signal_autopsy.review import build_rejection_record, humanize_reason, money, pct
from crypto_signal_autopsy.v2_dashboard import build_v2_payload
from crypto_signal_autopsy.wallet_exports import build_wallet_dashboard_payload, write_public_wallet_json_data


def build_static_site(conn: sqlite3.Connection, settings: Settings, out_dir: Path | None = None) -> Path:
    init_db(conn)
    target_dir = out_dir or settings.project_root / "docs"
    target_dir.mkdir(parents=True, exist_ok=True)
    asset_dir = target_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    header_asset = settings.project_root / "assets" / "dashboard-header.png"
    if header_asset.exists():
        shutil.copyfile(header_asset, asset_dir / "dashboard-header.png")
    (target_dir / ".nojekyll").write_text("", encoding="utf-8")

    refresh_quant_analytics(conn, settings)
    payload = _build_payload(conn, settings)
    write_public_wallet_json_data(conn, target_dir / "data")
    write_public_quant_json_data(conn, target_dir / "data")
    html = _render_html(payload)
    out_path = target_dir / "index.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


def _build_payload(conn: sqlite3.Connection, settings: Settings) -> dict[str, Any]:
    evaluations = conn.execute("SELECT * FROM candidate_evaluations ORDER BY observed_at DESC").fetchall()
    rejected_outcomes = conn.execute(
        "SELECT * FROM rejected_candidate_outcomes ORDER BY observed_at DESC"
    ).fetchall()
    signals = conn.execute("SELECT COUNT(*) AS count FROM signals").fetchone()["count"]
    signal_outcomes = conn.execute("SELECT COUNT(*) AS count FROM signal_outcomes").fetchone()["count"]
    api_events = conn.execute("SELECT COUNT(*) AS count FROM api_events").fetchone()["count"]

    records = [
        build_rejection_record(row, settings)
        for row in evaluations
        if not bool(row["accepted"])
    ]
    reason_counts = Counter(
        humanize_reason(reason)
        for record in records
        for reason in record.get("rejection_reasons", [])
    )
    outcome_summary = _outcome_summary(rejected_outcomes)

    latest_seen = records[0]["observed_at"] if records else "No scan yet"
    latest_outcome = rejected_outcomes[0]["observed_at"] if rejected_outcomes else "No outcomes yet"
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "latest_seen": latest_seen,
        "latest_outcome": latest_outcome,
        "counts": {
            "accepted_signals": signals,
            "accepted_outcomes": signal_outcomes,
            "rejected_tokens": len(records),
            "rejected_outcomes": len(rejected_outcomes),
            "api_events": api_events,
        },
        "reason_counts": reason_counts.most_common(8),
        "outcome_summary": outcome_summary,
        "latest_rejections": [_public_record(record) for record in records[:80]],
        "analysis": build_analysis_payload(conn, settings),
        "v2": build_v2_payload(conn),
        "wallets": build_wallet_dashboard_payload(conn, settings),
        "quant": build_quant_payload(conn),
    }


def _outcome_summary(rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row["net_return_pct"]
        if value is not None:
            grouped[row["horizon"]].append(float(value))
    summary = []
    for horizon in sorted(grouped):
        values = grouped[horizon]
        summary.append(
            {
                "horizon": horizon,
                "count": str(len(values)),
                "median": pct(median(values)),
                "average": pct(mean(values)),
                "best": pct(max(values)),
                "worst": pct(min(values)),
            }
        )
    return summary


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "seen": record.get("observed_at"),
        "symbol": record.get("symbol"),
        "name": record.get("name"),
        "liquidity": money(record.get("liquidity_usd")),
        "volume24h": money(record.get("volume_h24_usd")),
        "age": f"{record['pair_age_hours']:.2f}h" if record.get("pair_age_hours") is not None else "Missing",
        "move1h": pct(record.get("price_change_h1_pct")),
        "fails": record.get("fail_count"),
        "reasons": record.get("reason_text") or "",
        "url": record.get("dexscreener_url") or "",
    }


def _render_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    safe_data = data.replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto Signal Autopsy V3</title>
  <style>
    :root {{
      --ink: #12353b;
      --muted: #5d747a;
      --line: #d6e4e3;
      --soft: #f4faf9;
      --card: #ffffff;
      --accent: #2f6f73;
      --warn: #9b5a2e;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(135deg, #f9fbfb 0%, #eaf4f3 100%);
      color: var(--ink);
    }}
    .shell {{ max-width: 1180px; margin: 0 auto; padding: 34px 22px 54px; }}
    header {{
      min-height: 280px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 34px;
      display: flex;
      flex-direction: column;
      justify-content: flex-end;
      background:
        linear-gradient(90deg, rgba(255,255,255,.95), rgba(255,255,255,.72)),
        url("assets/dashboard-header.png") center / cover no-repeat,
        #fff;
      box-shadow: 0 18px 45px rgba(18,53,59,.10);
    }}
    h1 {{ margin: 0 0 10px; font-size: 42px; line-height: 1.03; letter-spacing: 0; }}
    .lead {{ margin: 0; max-width: 760px; color: var(--muted); font-size: 18px; line-height: 1.55; }}
    .disclaimer {{ margin: 14px 0 0; max-width: 820px; color: #8a4b22; font-size: 14px; font-weight: 800; }}
    .meta {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 20px; }}
    .pill {{ border: 1px solid var(--line); border-radius: 999px; background: rgba(255,255,255,.78); padding: 8px 12px; color: var(--muted); font-weight: 700; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 20px 0; }}
    .four {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; margin-top: 16px; }}
    .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 18px; box-shadow: 0 12px 30px rgba(18,53,59,.07); }}
    .metric {{ font-size: 31px; font-weight: 800; margin-top: 6px; }}
    .metric-small {{ font-size: 21px; line-height: 1.25; overflow-wrap: anywhere; }}
    .label {{ color: var(--muted); font-size: 13px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; }}
    .two {{ display: grid; grid-template-columns: .9fr 1.1fr; gap: 16px; margin-top: 16px; }}
    h2 {{ margin: 0 0 14px; font-size: 23px; letter-spacing: 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 12px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 14px; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
    tr:hover td {{ background: var(--soft); }}
    a {{ color: var(--accent); font-weight: 800; text-decoration: none; }}
    .reason {{ color: var(--warn); font-weight: 700; }}
    .good {{ color: #16745c; font-weight: 800; }}
    .bad {{ color: #9b3d2f; font-weight: 800; }}
    .scroll {{ overflow-x: auto; }}
    .section-title {{ margin: 28px 0 12px; font-size: 28px; }}
    .summary-line {{ color: var(--muted); margin: -2px 0 14px; }}
    .tools {{ display: flex; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }}
    input, select {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; min-height: 40px; background: #fff; color: var(--ink); }}
    input {{ flex: 1 1 260px; }}
    .small {{ color: var(--muted); font-size: 13px; line-height: 1.5; }}
    .notice {{ border-left: 4px solid var(--warn); padding: 12px 14px; background: #fff8f2; color: #7e421c; font-weight: 700; border-radius: 6px; }}
    .status-tile {{ border: 1px solid var(--line); border-radius: 8px; padding: 14px; background: var(--soft); }}
    .guide {{ display: grid; gap: 12px; }}
    .guide-row {{ display: grid; grid-template-columns: 150px minmax(0, 1fr); gap: 14px; padding: 13px 0; border-bottom: 1px solid var(--line); }}
    .guide-row:last-child {{ border-bottom: 0; }}
    .guide-key {{ font-weight: 850; color: var(--ink); }}
    .guide-copy {{ color: var(--muted); line-height: 1.5; }}
    .simple-cards {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 12px; }}
    .simple-card {{ border: 1px solid var(--line); border-radius: 8px; padding: 14px; background: var(--soft); }}
    .simple-card strong {{ display: block; font-size: 20px; margin-bottom: 4px; }}
    .badge {{ display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; background: var(--soft); font-size: 12px; font-weight: 850; color: var(--accent); }}
    .advanced-section {{ margin-top: 18px; }}
    .advanced-section summary {{ cursor: pointer; font-weight: 850; font-size: 20px; color: var(--ink); padding: 16px 18px; border: 1px solid var(--line); border-radius: 8px; background: #fff; box-shadow: 0 12px 30px rgba(18,53,59,.06); }}
    .advanced-section[open] summary {{ margin-bottom: 16px; }}
    @media (max-width: 880px) {{
      .grid, .two, .four, .simple-cards, .guide-row {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 33px; }}
      header {{ padding: 24px; }}
      th:nth-child(4), td:nth-child(4), th:nth-child(5), td:nth-child(5) {{ display: none; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <h1>Crypto Signal Autopsy V3</h1>
      <p class="lead">Research dashboard for crypto token risk and opportunity analysis. It separates tokens into evidence buckets and tracks what happened after each scan.</p>
      <p class="disclaimer">Research Only - Not Financial Advice. No buy signals. No sell signals. No auto trading. Wallet activity is not a buy signal.</p>
      <div class="meta">
        <span class="pill">Base chain</span>
        <span class="pill">GitHub Actions hourly scan</span>
        <span class="pill">V2 labels and scoring</span>
        <span class="pill">V3 smart wallet research</span>
      </div>
    </header>

    <section class="grid" id="metrics"></section>

    <section class="card" style="margin-top:16px">
      <h2>Start Here</h2>
      <p class="small">This page is a research notebook. It finds tokens, rejects risky ones, tracks what happened later, and studies wallets around those tokens.</p>
      <div id="simpleGuide"></div>
    </section>

    <section class="two">
      <div class="card"><h2>What The Labels Mean</h2><div id="labelGuide"></div></div>
      <div class="card"><h2>Best Reading Order</h2><div id="readingOrder"></div></div>
    </section>

    <section class="card" style="margin-top:16px">
      <h2>Token Decision Buckets</h2>
      <p class="small">Start here to see why each token was rejected, watched, or promoted to research. None of these labels are buy signals.</p>
      <div id="bucketTables"></div>
    </section>

    <section class="two">
      <div class="card">
        <h2>Rejected Filter Accuracy</h2>
        <p class="small">A rejected token is watched for one day. After 24h, the app decides whether the rejection helped or missed upside, then keeps only this audit summary.</p>
        <div id="filterAccuracy"></div>
      </div>
      <div class="card">
        <h2>10x Research Logic</h2>
        <p class="small">This is a research score for conditions that sometimes appear before large moves. It is not a prediction and not a buy signal.</p>
        <div id="tenXLogic"></div>
      </div>
    </section>

    <section class="card" style="margin-top:16px">
      <h2>Completed Rejected 24h Audits</h2>
      <p class="small">These are rejected tokens that finished the full 15m, 30m, 1h, 2h, 4h, 8h, and 24h check cycle.</p>
      <div id="rejectedAuditArchive"></div>
    </section>

    <h2 class="section-title">Smart Wallet Tracker</h2>
    <p class="summary-line">Wallet tracking studies behavior around scanned tokens. It does not create copy-trading signals.</p>
    <section class="card" style="margin-top:16px">
      <h2>Wallet Module Status</h2>
      <p class="notice">Research Only - Not Financial Advice. Wallet activity is not a buy signal. Smart Wallet does not mean perfect wallet. Suspicious Wallet does not prove crime or fraud. Paper tracking only.</p>
      <div class="grid" id="walletStatus"></div>
      <p class="small" id="walletWarning"></p>
    </section>

    <section class="two">
      <div class="card"><h2>Wallet Leaderboard</h2><div id="walletLeaderboard"></div></div>
      <div class="card"><h2>Wallet Activity Feed</h2><div id="walletActivity"></div></div>
    </section>

    <section class="two">
      <div class="card"><h2>Wallet Signal Tokens</h2><div id="walletSignalTokens"></div></div>
      <div class="card"><h2>Suspicious Wallet Warnings</h2><div id="suspiciousWallets"></div></div>
    </section>

    <section class="card" style="margin-top:16px">
      <h2>Smart Wallet Cluster Detection</h2>
      <div id="walletClusters"></div>
      <p class="small">Wallet tracking is a research signal only. A wallet buying a token does not make the token safe. Hard security filters always override wallet activity. Smart wallet labels require enough historical evidence and may still be wrong.</p>
    </section>

    <details class="advanced-section">
      <summary>Advanced evidence tables</summary>

    <section class="two">
      <div class="card">
        <h2>Median vs Average Performance</h2>
        <p class="small">Average return can be distorted by outliers. Median return is usually more reliable.</p>
        <div id="v2Performance"></div>
      </div>
      <div class="card">
        <h2>Score Bucket Performance</h2>
        <div id="scoreBuckets"></div>
      </div>
    </section>

    <section class="four">
      <div class="card"><h2>Biggest Missed Winners</h2><div id="missedWinners"></div></div>
      <div class="card"><h2>Biggest Avoided Losers</h2><div id="avoidedLosers"></div></div>
      <div class="card"><h2>High-Risk Pumps</h2><div id="highRiskPumps"></div></div>
      <div class="card"><h2>Rugs And Dumps</h2><div id="rugsDumps"></div></div>
    </section>

    <section class="two">
      <div class="card"><h2>Filters That Saved Us</h2><div id="filtersSaved"></div></div>
      <div class="card"><h2>Filters That Blocked Winners</h2><div id="filtersBlocked"></div></div>
    </section>

    <section class="two">
      <div class="card"><h2>Paper Trade Simulation</h2><div id="paperTrades"></div></div>
      <div class="card"><h2>Manual Review Notes</h2><div id="reviewNotes"></div></div>
    </section>

    <section class="two">
      <div class="card">
        <h2>Rejected Outcomes</h2>
        <div id="outcomes"></div>
      </div>
      <div class="card">
        <h2>Why Tokens Fail</h2>
        <div id="reasons"></div>
      </div>
    </section>

    </details>

    <section class="card" style="margin-top:16px">
      <h2>Latest Rejected Tokens</h2>
      <div class="tools">
        <input id="search" placeholder="Search symbol, name, or reason">
        <select id="reasonFilter"><option value="">All reasons</option></select>
      </div>
      <div id="table"></div>
    </section>

    <h2 class="section-title">Four Core Analysis Tables</h2>
    <p class="summary-line">Separate views for what happened after rejection and acceptance at 1h and 24h.</p>
    <section class="four">
      <div class="card"><h2>1h After Rejection</h2><div id="rejected1h"></div></div>
      <div class="card"><h2>1h After Acceptance</h2><div id="accepted1h"></div></div>
      <div class="card"><h2>24h After Rejection</h2><div id="rejected24h"></div></div>
      <div class="card"><h2>24h After Acceptance</h2><div id="accepted24h"></div></div>
    </section>

    <h2 class="section-title">Best And Worst Performance</h2>
    <p class="summary-line">These tables show what worked, what failed, and the reason behind each result.</p>
    <section class="four">
      <div class="card"><h2>Best After Rejection</h2><div id="rejectedBest"></div></div>
      <div class="card"><h2>Worst After Rejection</h2><div id="rejectedWorst"></div></div>
      <div class="card"><h2>Best After Acceptance</h2><div id="acceptedBest"></div></div>
      <div class="card"><h2>Worst After Acceptance</h2><div id="acceptedWorst"></div></div>
    </section>

    <section class="card" style="margin-top:16px">
      <h2>Acceptance And Rejection Filter Rules</h2>
      <p class="small">This is the manual checklist. A token must pass hard filters and show at least one signal pattern before it can be accepted.</p>
      <div id="filterRules"></div>
    </section>
  </main>

  <script id="payload" type="application/json">{safe_data}</script>
  <script>
    const payload = JSON.parse(document.getElementById("payload").textContent);
    const v2 = payload.v2;
    const wallets = payload.wallets;
    const metrics = v2.health_cards.map(card => [card.label, card.value]);
    document.getElementById("metrics").innerHTML = metrics.map(([label, value]) => `
      <div class="card"><div class="label">${{label}}</div><div class="metric ${{String(value).length > 12 ? "metric-small" : ""}}">${{value}}</div></div>
    `).join("");

    function table(headers, rows) {{
      if (!rows.length) return '<p class="small">No rows yet.</p>';
      return `<div class="scroll"><table><thead><tr>${{headers.map(h => `<th>${{h}}</th>`).join("")}}</tr></thead><tbody>${{rows.join("")}}</tbody></table></div>`;
    }}

    function renderSimpleGuide() {{
      const labels = v2.label_counts || {{}};
      const scanned = Object.values(labels).reduce((sum, value) => sum + Number(value || 0), 0);
      const walletStatus = wallets.status || {{}};
      document.getElementById("simpleGuide").innerHTML = `
        <div class="simple-cards">
          <div class="simple-card"><strong>${{scanned}}</strong><span class="small">tokens checked by the scanner</span></div>
          <div class="simple-card"><strong>${{labels["Reject"] || 0}}</strong><span class="small">tokens rejected by safety/research rules</span></div>
          <div class="simple-card"><strong>${{walletStatus.wallets_tracked || 0}}</strong><span class="small">wallets tracked around scanned tokens</span></div>
        </div>
        <div class="guide" style="margin-top:12px">
          <div class="guide-row"><div class="guide-key">What it does</div><div class="guide-copy">The system scans public crypto data, filters risky tokens, saves the reason, then checks later if the decision was good or bad.</div></div>
          <div class="guide-row"><div class="guide-key">Scan timing</div><div class="guide-copy">Last scan attempt shows the hourly job ran. Last candidate found only changes when DEX Screener returns a token worth evaluating.</div></div>
          <div class="guide-row"><div class="guide-key">Rejected audit</div><div class="guide-copy">Rejected tokens are watched at 15m, 30m, 1h, 2h, 4h, 8h, and 24h. After that, only the accuracy summary remains.</div></div>
          <div class="guide-row"><div class="guide-key">10x score</div><div class="guide-copy">The 10x setup score checks size, liquidity, volume, buy pressure, age, momentum, socials, and security. It is research only.</div></div>
          <div class="guide-row"><div class="guide-key">What to trust first</div><div class="guide-copy">Look at the plain labels, rejection reasons, and later performance. Do not treat any row as a buy signal.</div></div>
          <div class="guide-row"><div class="guide-key">Wallet data</div><div class="guide-copy">Wallet tables show who traded scanned tokens. A wallet buying something does not make the token safe.</div></div>
        </div>
      `;
    }}

    function renderLabelGuide() {{
      const rows = [
        ["Reject", "The token failed safety or quality rules.", "Usually ignore it, then check later if the filter saved us or missed a winner."],
        ["Watchlist", "Not clean enough for paper trading, but interesting enough to observe.", "Study only. Wait for more evidence."],
        ["Research Candidate", "Cleaner than most rows, but still not a buy signal.", "Read the details and compare later performance."],
        ["Paper Trade Candidate", "Strongest simulated-tracking bucket.", "Paper tracking only. No real trade instruction."],
        ["Unproven Wallet", "A wallet with too little history.", "Do not call it smart yet."]
      ].map(row => `<tr><td><strong>${{row[0]}}</strong></td><td>${{row[1]}}</td><td>${{row[2]}}</td></tr>`);
      document.getElementById("labelGuide").innerHTML = table(["Label", "Plain meaning", "What to do"], rows);
    }}

    function renderReadingOrder() {{
      const rows = [
        ["1", "Token Decision Buckets", "See which tokens were rejected or watched, and why."],
        ["2", "Smart Wallet Tracker", "See whether wallets are showing useful or risky behavior."],
        ["3", "Latest Rejected Tokens", "Manually inspect the newest rejects with chart links."],
        ["4", "One-hour and 24-hour tables", "Check whether rejected or accepted tokens later did well or badly."],
        ["5", "Advanced evidence", "Open only when you want deeper stats and outlier tables."]
      ].map(row => `<tr><td><strong>${{row[0]}}</strong></td><td>${{row[1]}}</td><td>${{row[2]}}</td></tr>`);
      document.getElementById("readingOrder").innerHTML = table(["Step", "Read this", "Why"], rows);
    }}

    const cellClass = (value) => {{
      const parsed = Number(String(value || "").replace("%", ""));
      if (Number.isNaN(parsed)) return "";
      if (parsed > 0) return "good";
      if (parsed < 0) return "bad";
      return "";
    }};

    function auditRows(rows, limit = 12) {{
      return rows.slice(0, limit).map(row => `<tr>
        <td><strong>${{row.symbol}}</strong><br><span class="small">${{row.name || ""}}</span></td>
        <td>${{row.horizon}}</td>
        <td class="${{cellClass(row.net)}}">${{row.net}}</td>
        <td>${{row.liquidity}}</td>
        <td>${{row.volume24h}}</td>
        <td>${{row.age}}</td>
        <td>${{row.reason}}</td>
        <td>${{row.diagnosis}}</td>
        <td>${{row.url ? `<a href="${{row.url}}" target="_blank" rel="noreferrer">Open</a>` : ""}}</td>
      </tr>`);
    }}

    function summaryLabel(summary) {{
      return `<p class="small">Rows: ${{summary.count}} | Priced: ${{summary.priced}} | Median: ${{summary.median}} | Average: ${{summary.average}} | Positive: ${{summary.positive_rate}}</p>`;
    }}

    function renderAuditTable(id, rows, summary) {{
      document.getElementById(id).innerHTML = summaryLabel(summary) + table(
        ["Token", "Checked after", "Return after cost", "Liquidity", "24h volume", "Age", "Why it was here", "Plain meaning", "Chart"],
        auditRows(rows)
      );
    }}

    function renderExtremeTable(id, rows) {{
      document.getElementById(id).innerHTML = table(
        ["Token", "Checked after", "Return", "Liquidity", "Why it was here", "Plain meaning", "Chart"],
        rows.slice(0, 10).map(row => `<tr>
          <td><strong>${{row.symbol}}</strong><br><span class="small">${{row.name || ""}}</span></td>
          <td>${{row.horizon}}</td>
          <td class="${{cellClass(row.net)}}">${{row.net}}</td>
          <td>${{row.liquidity}}</td>
          <td>${{row.reason}}</td>
          <td>${{row.diagnosis}}</td>
          <td>${{row.url ? `<a href="${{row.url}}" target="_blank" rel="noreferrer">Open</a>` : ""}}</td>
        </tr>`)
      );
    }}

    function renderBucketTables() {{
      const labels = ["Reject", "Watchlist", "High-Risk Momentum Watchlist", "Research Candidate", "Paper Trade Candidate"];
      document.getElementById("bucketTables").innerHTML = labels.map(label => `
        <h2>${{label}}</h2>
        ${{table(
          ["Token", "Age", "Liquidity", "24h volume", "1h move", "Risk", "Opportunity", "10x setup", "Why it is here", "Chart"],
          (v2.bucket_tables[label] || []).slice(0, 12).map(row => `<tr>
            <td><strong>${{row.symbol}}</strong><br><span class="small">${{row.scan_time}}</span></td>
            <td>${{row.pair_age}}</td>
            <td>${{row.liquidity}}</td>
            <td>${{row.volume24h}}</td>
            <td>${{row.move1h}}</td>
            <td>${{row.risk_score}}</td>
            <td>${{row.opportunity_score}}</td>
            <td><span class="badge">${{row.ten_x_score || "0"}}</span><br><span class="small">${{row.ten_x_label || "No 10x Setup"}}</span></td>
            <td class="reason">${{row.main_reasons}}</td>
            <td>${{row.url ? `<a href="${{row.url}}" target="_blank" rel="noreferrer">Open</a>` : ""}}</td>
          </tr>`)
        )}}
      `).join("");
    }}

    function renderFilterAccuracy() {{
      const a = v2.filter_accuracy || {{}};
      document.getElementById("filterAccuracy").innerHTML = `
        <div class="simple-cards">
          <div class="simple-card"><strong>${{a.accuracy_rate || "No data"}}</strong><span class="small">accuracy on judged rejected tokens</span></div>
          <div class="simple-card"><strong>${{a.successes || 0}}</strong><span class="small">filter successes after 24h</span></div>
          <div class="simple-card"><strong>${{a.failures || 0}}</strong><span class="small">filter failures after 24h</span></div>
        </div>
        <p class="small" style="margin-top:12px">${{a.plain || "No rejected token has finished the 24h audit yet."}}</p>
      `;
    }}

    function renderTenXLogic() {{
      const rows = [
        ["Small size", "FDV or market cap is small enough that a large move is possible, but not so tiny that it is pure noise."],
        ["Enough liquidity", "There is enough liquidity to observe real trading, but not so much that a 10x move becomes mathematically harder."],
        ["Volume pressure", "24h volume is strong compared with liquidity, which can mean attention is arriving."],
        ["Buy pressure", "Recent buys are stronger than sells, with enough transactions to matter."],
        ["Young but not instant", "The pair is early, but not only a few chaotic launch minutes old."],
        ["Security sanity", "Critical honeypot, cannot-sell, blacklist, or extreme-risk flags override the score."]
      ].map(row => `<tr><td><strong>${{row[0]}}</strong></td><td>${{row[1]}}</td></tr>`);
      document.getElementById("tenXLogic").innerHTML = table(["Factor", "Plain meaning"], rows);
    }}

    function renderRejectedAuditArchive() {{
      document.getElementById("rejectedAuditArchive").innerHTML = table(
        ["Token", "24h return", "Best", "Worst", "Verdict", "10x setup", "Lesson", "Chart"],
        (v2.rejected_audits || []).slice(0, 20).map(row => `<tr>
          <td><strong>${{row.symbol}}</strong><br><span class="small">${{row.completed_at}}</span></td>
          <td class="${{cellClass(row.return24h)}}">${{row.return24h}}</td>
          <td class="${{cellClass(row.best)}}">${{row.best}}</td>
          <td class="${{cellClass(row.worst)}}">${{row.worst}}</td>
          <td><span class="badge">${{row.verdict}}</span></td>
          <td>${{row.ten_x_score ? `<span class="badge">${{row.ten_x_score}}</span><br>` : ""}}<span class="small">${{row.ten_x_label || ""}}</span></td>
          <td>${{row.lesson}}</td>
          <td>${{row.url ? `<a href="${{row.url}}" target="_blank" rel="noreferrer">Open</a>` : ""}}</td>
        </tr>`)
      );
    }}

    function renderOutcomeRows(id, rows) {{
      document.getElementById(id).innerHTML = table(
        ["Token", "Decision at scan", "Checked after", "Return", "Why", "Liquidity failed?", "Rug?", "Volume gone?"],
        rows.slice(0, 12).map(row => `<tr>
          <td><strong>${{row.token_address.slice(0, 10)}}...</strong></td>
          <td>${{row.label_at_scan}}</td>
          <td>${{row.horizon}}</td>
          <td class="${{cellClass(row.return)}}">${{row.return}}</td>
          <td>${{row.reasons}}</td>
          <td>${{row.became_illiquid}}</td>
          <td>${{row.rugged}}</td>
          <td>${{row.volume_disappeared}}</td>
        </tr>`)
      );
    }}

    function renderWalletStatus() {{
      const status = wallets.status;
      const rows = [
        ["Enabled", status.enabled],
        ["Provider", status.active_provider],
        ["Last wallet scan", status.last_wallet_scan],
        ["API errors", status.wallet_api_errors],
        ["Wallets tracked", status.wallets_tracked],
        ["Trades tracked", status.trades_tracked],
        ["Smart wallets", status.smart_wallets],
        ["Useful wallets", status.useful_wallets],
        ["Suspicious wallets", status.suspicious_wallets],
      ];
      document.getElementById("walletStatus").innerHTML = rows.map(([label, value]) => `
        <div class="status-tile"><div class="label">${{label}}</div><div class="metric ${{String(value).length > 12 ? "metric-small" : ""}}">${{value}}</div></div>
      `).join("");
      document.getElementById("walletWarning").textContent = status.warning || "Wallet module is collecting research data.";
    }}

    function renderWalletTables() {{
      document.getElementById("walletLeaderboard").innerHTML = table(
        ["Wallet", "Plain label", "Quality", "Risk", "Tokens seen", "Win rate", "Rug exposure", "Last active"],
        wallets.leaderboard.slice(0, 20).map(row => `<tr>
          <td title="${{row.wallet_full}}"><strong>${{row.wallet}}</strong></td>
          <td>${{row.label}}</td><td>${{row.quality}}</td><td>${{row.risk}}</td>
          <td>${{row.tokens}}</td><td>${{row.win_rate}}</td><td>${{row.rug_exposure}}</td><td>${{row.last_active}}</td>
        </tr>`)
      );
      document.getElementById("walletActivity").innerHTML = table(
        ["Time", "Wallet", "Wallet label", "Did", "Token", "Amount", "Liquidity", "Token risk", "Wallet signal"],
        wallets.activity_feed.slice(0, 20).map(row => `<tr>
          <td>${{row.time}}</td><td>${{row.wallet}}</td><td>${{row.wallet_label}}</td><td>${{row.side}}</td>
          <td><strong>${{row.token}}</strong></td><td>${{row.amount}}</td><td>${{row.liquidity}}</td>
          <td>${{row.token_risk}}</td><td>${{row.wallet_signal}}</td>
        </tr>`)
      );
      document.getElementById("walletSignalTokens").innerHTML = table(
        ["Token", "Useful wallets", "Suspicious wallets", "Money flow", "Wallet score", "Wallet meaning", "Token status"],
        wallets.token_signals.slice(0, 20).map(row => `<tr>
          <td><strong>${{row.token}}</strong></td><td>${{row.useful}}</td>
          <td>${{row.suspicious}}</td><td>${{row.net_flow}}</td><td>${{row.wallet_score}}</td><td>${{row.wallet_label}}</td>
          <td>${{row.token_label}}</td>
        </tr>`)
      );
      document.getElementById("suspiciousWallets").innerHTML = table(
        ["Wallet", "Warning type", "Risk", "Tokens seen", "Rug exposure", "Quick dumps", "Suspicion score", "Plain note"],
        wallets.suspicious_wallets.slice(0, 20).map(row => `<tr>
          <td title="${{row.wallet_full}}"><strong>${{row.wallet}}</strong></td><td>${{row.label}}</td><td>${{row.risk}}</td>
          <td>${{row.tokens}}</td><td>${{row.rug_exposure}}</td><td>${{row.quick_dump}}</td>
          <td>${{row.suspicion}}</td><td>${{row.notes}}</td>
        </tr>`)
      );
      document.getElementById("walletClusters").innerHTML = table(
        ["Token", "Cluster type", "Wallets", "Total buys", "Average quality", "Average risk", "Found at", "Plain note"],
        wallets.clusters.slice(0, 20).map(row => `<tr>
          <td><strong>${{row.token}}</strong></td><td>${{row.cluster_type}}</td><td>${{row.wallet_count}}</td>
          <td>${{row.total_buy}}</td><td>${{row.avg_quality}}</td><td>${{row.avg_risk}}</td><td>${{row.scan_time}}</td><td>${{row.notes}}</td>
        </tr>`)
      );
    }}

    renderSimpleGuide();
    renderLabelGuide();
    renderReadingOrder();
    renderBucketTables();
    renderFilterAccuracy();
    renderTenXLogic();
    renderRejectedAuditArchive();
    renderWalletStatus();
    renderWalletTables();
    document.getElementById("v2Performance").innerHTML = table(
      ["Horizon", "Count", "Median", "Average", "Best", "Worst", "Outlier Note"],
      v2.performance.map(row => `<tr>
        <td>${{row.horizon}}</td><td>${{row.count}}</td><td>${{row.median}}</td>
        <td>${{row.average}}</td><td>${{row.best}}</td><td>${{row.worst}}</td><td>${{row.outlier_note}}</td>
      </tr>`)
    );
    document.getElementById("scoreBuckets").innerHTML = table(
      ["Risk", "Opp", "Count", "Median", "Average", "Best", "Worst", "Rug", "Illiquid"],
      v2.score_buckets.slice(0, 20).map(row => `<tr>
        <td>${{row.risk_bucket}}</td><td>${{row.opportunity_bucket}}</td><td>${{row.count}}</td>
        <td>${{row.median}}</td><td>${{row.average}}</td><td>${{row.best}}</td><td>${{row.worst}}</td>
        <td>${{row.rug_rate}}</td><td>${{row.illiquidity_rate}}</td>
      </tr>`)
    );
    renderOutcomeRows("missedWinners", v2.outliers.missed_winners);
    renderOutcomeRows("avoidedLosers", v2.outliers.avoided_losers);
    renderOutcomeRows("highRiskPumps", v2.outliers.high_risk_pumps);
    renderOutcomeRows("rugsDumps", v2.outliers.rugs_dumps);
    renderOutcomeRows("filtersSaved", v2.filters_saved_us);
    renderOutcomeRows("filtersBlocked", v2.filters_blocked_winners);
    document.getElementById("paperTrades").innerHTML = table(
      ["Token", "Pair", "Entry", "Position", "Status", "Notes"],
      v2.paper_trades.map(row => `<tr>
        <td>${{row.token_address.slice(0, 10)}}...</td><td>${{row.pair_id}}</td>
        <td>${{row.sim_entry}}</td><td>${{row.position}}</td><td>${{row.status}}</td><td>${{row.notes}}</td>
      </tr>`)
    );
    document.getElementById("reviewNotes").innerHTML = table(
      ["Token", "Time", "Missed Winner", "Saved", "Lesson", "Notes"],
      v2.review_notes.map(row => `<tr>
        <td>${{row.token_address.slice(0, 10)}}...</td><td>${{row.review_time}}</td>
        <td>${{row.missed_winner}}</td><td>${{row.saved_from_bad_token}}</td>
        <td>${{row.main_lesson}}</td><td>${{row.manual_notes}}</td>
      </tr>`)
    );

    document.getElementById("outcomes").innerHTML = table(
      ["Horizon", "Count", "Median", "Average", "Best", "Worst"],
      payload.outcome_summary.map(row => `<tr><td>${{row.horizon}}</td><td>${{row.count}}</td><td>${{row.median}}</td><td>${{row.average}}</td><td>${{row.best}}</td><td>${{row.worst}}</td></tr>`)
    ) + `<p class="small">Latest scan: ${{payload.latest_seen}}<br>Latest outcome: ${{payload.latest_outcome}}</p>`;

    document.getElementById("reasons").innerHTML = table(
      ["Reason", "Count"],
      payload.reason_counts.map(([reason, count]) => `<tr><td class="reason">${{reason}}</td><td>${{count}}</td></tr>`)
    );

    const analysis = payload.analysis;
    renderAuditTable("rejected1h", analysis.tables.rejected_1h, analysis.summaries.rejected_1h);
    renderAuditTable("accepted1h", analysis.tables.accepted_1h, analysis.summaries.accepted_1h);
    renderAuditTable("rejected24h", analysis.tables.rejected_24h, analysis.summaries.rejected_24h);
    renderAuditTable("accepted24h", analysis.tables.accepted_24h, analysis.summaries.accepted_24h);
    renderExtremeTable("rejectedBest", analysis.extremes.rejected_best);
    renderExtremeTable("rejectedWorst", analysis.extremes.rejected_worst);
    renderExtremeTable("acceptedBest", analysis.extremes.accepted_best);
    renderExtremeTable("acceptedWorst", analysis.extremes.accepted_worst);
    document.getElementById("filterRules").innerHTML = table(
      ["Rule", "Accept needs", "Rejects when", "Why it matters"],
      analysis.filter_rules.map(row => `<tr><td><strong>${{row.Rule}}</strong></td><td>${{row["Accept needs"]}}</td><td>${{row["Rejects when"]}}</td><td>${{row["Why it matters"]}}</td></tr>`)
    );

    const reasonFilter = document.getElementById("reasonFilter");
    [...new Set(payload.latest_rejections.flatMap(row => row.reasons.split("; ").filter(Boolean)))].sort().forEach(reason => {{
      const option = document.createElement("option");
      option.value = reason;
      option.textContent = reason;
      reasonFilter.appendChild(option);
    }});

    const search = document.getElementById("search");
    function renderRows() {{
      const q = search.value.trim().toLowerCase();
      const reason = reasonFilter.value;
      const filtered = payload.latest_rejections.filter(row => {{
        const haystack = `${{row.symbol}} ${{row.name}} ${{row.reasons}}`.toLowerCase();
        return (!q || haystack.includes(q)) && (!reason || row.reasons.includes(reason));
      }});
      document.getElementById("table").innerHTML = table(
        ["Token", "Liquidity", "24h Volume", "Age", "1h Move", "Fails", "Reason", "Link"],
        filtered.map(row => `<tr>
          <td><strong>${{row.symbol}}</strong><br><span class="small">${{row.name || ""}}</span></td>
          <td>${{row.liquidity}}</td>
          <td>${{row.volume24h}}</td>
          <td>${{row.age}}</td>
          <td>${{row.move1h}}</td>
          <td>${{row.fails}}</td>
          <td class="reason">${{row.reasons}}</td>
          <td>${{row.url ? `<a href="${{row.url}}" target="_blank" rel="noreferrer">Open</a>` : ""}}</td>
        </tr>`)
      );
    }}
    search.addEventListener("input", renderRows);
    reasonFilter.addEventListener("change", renderRows);
    renderRows();
  </script>
</body>
</html>
"""


def _render_html(payload: dict[str, Any]) -> str:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    safe_data = data.replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Crypto Signal Autopsy Quant Dashboard</title>
  <style>
    :root {{
      --bg: #05070d;
      --bg2: #070b14;
      --panel: rgba(11, 16, 32, .88);
      --panel-soft: rgba(17, 24, 39, .82);
      --border: #1f2937;
      --border-hot: rgba(34, 197, 94, .36);
      --text: #e5e7eb;
      --muted: #9ca3af;
      --green: #22c55e;
      --cyan: #06b6d4;
      --amber: #f59e0b;
      --red: #ef4444;
      --purple: #8b5cf6;
      --shadow: 0 24px 70px rgba(0,0,0,.38);
    }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background:
        linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px),
        radial-gradient(circle at 18% -8%, rgba(6,182,212,.22), transparent 34%),
        radial-gradient(circle at 88% 0%, rgba(34,197,94,.18), transparent 30%),
        var(--bg);
      background-size: 42px 42px, 42px 42px, auto, auto, auto;
    }}
    a {{ color: var(--cyan); text-decoration: none; font-weight: 800; }}
    .shell {{ max-width: 1440px; margin: 0 auto; padding: 24px 22px 70px; }}
    .topnav {{
      position: sticky; top: 0; z-index: 10; display: flex; align-items: center; justify-content: space-between;
      gap: 14px; padding: 12px 0 18px; background: linear-gradient(180deg, rgba(5,7,13,.96), rgba(5,7,13,.72));
      backdrop-filter: blur(12px);
    }}
    .brand {{ display:flex; align-items:center; gap:10px; font-weight:900; letter-spacing:.02em; }}
    .mark {{ width: 28px; height: 28px; border:1px solid var(--green); background: linear-gradient(135deg, rgba(34,197,94,.28), rgba(6,182,212,.15)); display:grid; place-items:center; color:var(--green); }}
    .navlinks {{ display:flex; flex-wrap:wrap; gap:8px; justify-content:flex-end; }}
    .navlinks a {{ color: var(--muted); border:1px solid var(--border); padding:7px 10px; border-radius:6px; font-size:12px; }}
    .hero {{
      min-height: 360px; border: 1px solid var(--border); border-radius: 8px; padding: 34px;
      display:grid; grid-template-columns: minmax(0, 1.15fr) minmax(300px, .85fr); gap: 24px;
      background: linear-gradient(135deg, rgba(11,16,32,.96), rgba(7,11,20,.86));
      box-shadow: var(--shadow); overflow:hidden; position:relative;
    }}
    .hero::after {{ content:""; position:absolute; inset:auto 0 0; height:1px; background:linear-gradient(90deg, transparent, var(--green), var(--cyan), transparent); opacity:.8; }}
    .eyebrow {{ margin:0 0 12px; color:var(--cyan); font-size:12px; font-weight:950; text-transform:uppercase; letter-spacing:.12em; }}
    h1 {{ margin:0 0 12px; font-size: clamp(36px, 5vw, 68px); line-height:.95; letter-spacing:0; }}
    .lead {{ color: var(--muted); max-width: 780px; font-size: 17px; line-height:1.55; margin:0; }}
    .disclaimer {{ margin:18px 0 0; padding:12px 14px; border:1px solid rgba(245,158,11,.42); color:#ffd38a; background:rgba(245,158,11,.08); border-radius:8px; font-weight:800; }}
    .terminal {{ border:1px solid var(--border); background:#070a12; border-radius:8px; padding:18px; min-height:260px; font-family:"Cascadia Mono", Consolas, monospace; box-shadow: inset 0 0 0 1px rgba(255,255,255,.02); }}
    .term-row {{ display:flex; justify-content:space-between; gap:12px; padding:8px 0; border-bottom:1px solid rgba(255,255,255,.06); color:var(--muted); }}
    .term-row strong {{ color: var(--text); text-align:right; }}
    .verdict {{ color: var(--green); }}
    .section {{ margin-top: 24px; }}
    .section-head {{ display:flex; align-items:end; justify-content:space-between; gap:14px; margin:0 0 12px; }}
    h2 {{ margin:0; font-size:24px; letter-spacing:0; }}
    .section-copy {{ color:var(--muted); margin:5px 0 0; line-height:1.5; }}
    .grid {{ display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:14px; }}
    .grid3 {{ display:grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap:14px; }}
    .two {{ display:grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap:14px; }}
    .panel, .metric-card, .category-card {{
      border:1px solid var(--border); background:var(--panel); border-radius:8px; padding:16px; box-shadow: 0 14px 40px rgba(0,0,0,.22);
    }}
    .metric-label {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.08em; font-weight:900; }}
    .metric-value {{ font-size:30px; font-weight:900; margin-top:6px; overflow-wrap:anywhere; }}
    .status {{ display:inline-flex; align-items:center; gap:7px; border:1px solid var(--border); border-radius:999px; padding:5px 9px; font-size:12px; font-weight:900; color:var(--muted); }}
    .dot {{ width:8px; height:8px; border-radius:50%; background:var(--muted); box-shadow:0 0 14px currentColor; }}
    .good, .Good, .Operational {{ color:var(--green); }}
    .bad, .Bad {{ color:var(--red); }}
    .Mixed, .Research {{ color:var(--amber); }}
    .Inactive, .Small, .Too {{ color:var(--muted); }}
    .category-card {{ position:relative; overflow:hidden; }}
    .category-card::before {{ content:""; position:absolute; inset:0 0 auto; height:2px; background:linear-gradient(90deg, var(--green), var(--cyan), var(--purple)); opacity:.65; }}
    .category-top {{ display:flex; justify-content:space-between; gap:12px; align-items:start; }}
    .category-name {{ font-weight:950; font-size:18px; }}
    .category-count {{ color:var(--muted); font-family:"Cascadia Mono", Consolas, monospace; }}
    .kpi-list {{ display:grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap:8px; margin-top:12px; }}
    .kpi {{ background:rgba(255,255,255,.035); border:1px solid rgba(255,255,255,.06); border-radius:6px; padding:9px; }}
    .kpi span {{ display:block; color:var(--muted); font-size:11px; }}
    .kpi strong {{ display:block; margin-top:3px; }}
    .chart {{ min-height:260px; }}
    .bars {{ display:grid; gap:10px; }}
    .bar-row {{ display:grid; grid-template-columns: 76px minmax(0,1fr) 70px; gap:10px; align-items:center; color:var(--muted); font-size:13px; }}
    .bar-track {{ height:12px; background:#0a0f1c; border:1px solid var(--border); border-radius:99px; overflow:hidden; }}
    .bar-fill {{ height:100%; background:linear-gradient(90deg, var(--green), var(--cyan)); min-width:2px; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ padding:10px 9px; border-bottom:1px solid rgba(255,255,255,.07); text-align:left; vertical-align:top; font-size:13px; }}
    th {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.08em; }}
    tr:hover td {{ background:rgba(255,255,255,.025); }}
    .scroll {{ overflow:auto; max-width:100%; }}
    .small {{ color:var(--muted); font-size:12px; line-height:1.45; }}
    .warning {{ border:1px solid rgba(245,158,11,.38); background:rgba(245,158,11,.08); color:#ffd38a; border-radius:8px; padding:12px; font-weight:800; }}
    .tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }}
    button, select, input {{ background:#0b1020; color:var(--text); border:1px solid var(--border); border-radius:6px; padding:9px 10px; font:inherit; }}
    button {{ cursor:pointer; color:var(--muted); font-weight:900; }}
    button.active {{ color:var(--green); border-color:var(--border-hot); background:rgba(34,197,94,.08); }}
    .exports a {{ display:inline-flex; margin:4px 7px 4px 0; padding:7px 9px; border:1px solid var(--border); border-radius:6px; color:var(--muted); }}
    @media (max-width: 1050px) {{ .grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }} .hero, .two, .grid3 {{ grid-template-columns:1fr; }} }}
    @media (max-width: 680px) {{ .shell {{ padding:16px 12px 50px; }} .grid {{ grid-template-columns:1fr; }} .hero {{ padding:20px; }} th:nth-child(n+5), td:nth-child(n+5) {{ display:none; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <nav class="topnav">
      <div class="brand"><div class="mark">Σ</div><span>Crypto Signal Autopsy</span></div>
      <div class="navlinks">
        <a href="#overview">Overview</a><a href="#categories">Categories</a><a href="#charts">Charts</a>
        <a href="#failures">Failures</a><a href="#missed">Missed Winners</a><a href="#baselines">Baselines</a>
        <a href="#wallets">Wallets</a><a href="#quality">Data Quality</a>
      </div>
    </nav>

    <section class="hero" id="overview">
      <div>
        <p class="eyebrow">Executive Quant Overview</p>
        <h1>Crypto Signal Autopsy</h1>
        <p class="lead">Research dashboard for crypto token risk, rejection audits, candidate tracking, and 10x setup analysis.</p>
        <p class="disclaimer">Research only. No buy/sell signals. No financial advice. Paper candidates are simulated research labels only.</p>
      </div>
      <div class="terminal" id="executiveTerminal"></div>
    </section>

    <section class="section">
      <div class="section-head"><div><h2>System Verdict Cards</h2><p class="section-copy">The scanner is working, but candidate edge is not proven yet.</p></div></div>
      <div class="grid" id="healthCards"></div>
    </section>

    <section class="section" id="categories">
      <div class="section-head"><div><h2>Category Performance Cards</h2><p class="section-copy">Each category shows sample size, median return, best/worst outcome, positive rate, and audit verdict.</p></div></div>
      <div class="grid3" id="categoryCards"></div>
    </section>

    <section class="section" id="charts">
      <div class="section-head"><div><h2>Outcome Charts</h2><p class="section-copy">Median and average are both shown because averages can be distorted by outliers.</p></div></div>
      <div class="two">
        <div class="panel"><h2>Outcome Curve By Horizon</h2><div id="outcomeCurve" class="chart"></div></div>
        <div class="panel"><h2>Category Positive Rate</h2><div id="positiveRateChart" class="chart"></div></div>
      </div>
      <div class="two section">
        <div class="panel"><h2>10x Score vs Future Return</h2><div id="tenXScatter" class="chart"></div></div>
        <div class="panel"><h2>Failure Diagnosis Donut</h2><div id="failureDonut" class="chart"></div></div>
      </div>
    </section>

    <section class="section two" id="failures">
      <div class="panel">
        <h2>Accepted Failure Lab</h2>
        <p class="section-copy">Paper candidates are simulated research labels only. Current accepted/paper results are weak until proven by larger samples.</p>
        <div id="failureLab"></div>
      </div>
      <div class="panel">
        <h2>10x Score Failure Review</h2>
        <p class="section-copy">High 10x score failures must be visible, not hidden.</p>
        <div id="tenXFailure"></div>
      </div>
    </section>

    <section class="section two" id="missed">
      <div class="panel">
        <h2>Missed Winner Lab</h2>
        <p class="section-copy">Some rejected tokens pumped hard. This does not automatically mean the filter was wrong. The system must check whether those tokens were actually tradable or only high-risk momentum.</p>
        <div id="missedWinnerLab"></div>
      </div>
      <div class="panel">
        <h2>High-Risk Momentum Lab</h2>
        <p class="section-copy">High-risk momentum. Studied because similar rejected tokens sometimes pumped. Not a buy signal.</p>
        <div id="highRiskMomentumLab"></div>
      </div>
    </section>

    <section class="section" id="baselines">
      <div class="panel">
        <h2>Baseline Comparison</h2>
        <p class="section-copy">No strategy proves edge unless candidates beat BTC, ETH, all-scanned, and similar-token baselines.</p>
        <div id="baselineTable"></div>
      </div>
    </section>

    <section class="section two" id="wallets">
      <div class="panel"><h2>Wallet Intelligence Status</h2><div id="walletStatus"></div></div>
      <div class="panel"><h2>10x Candidate Engine</h2><div id="tenXEngine"></div></div>
    </section>

    <section class="section" id="quality">
      <div class="panel">
        <h2>Data Quality Panel</h2>
        <p class="warning">Short-horizon results may be approximate because GitHub Actions runs roughly hourly.</p>
        <div id="dataQuality"></div>
      </div>
    </section>

    <section class="section">
      <div class="panel">
        <h2>Raw Tables / Exports</h2>
        <p class="section-copy">All data is generated before deployment. The public dashboard does not call crypto APIs from the browser.</p>
        <div class="exports" id="exports"></div>
      </div>
    </section>
  </main>

  <script id="payload" type="application/json">{safe_data}</script>
  <script>
    const payload = JSON.parse(document.getElementById("payload").textContent);
    const v2 = payload.v2 || {{}};
    const quant = payload.quant || {{}};
    const wallets = payload.wallets || {{}};
    const labels = ["Reject","Watchlist","High-Risk Momentum Watchlist","Research Candidate","Paper Trade Candidate"];
    const fmtPct = value => value === null || value === undefined || value === "" ? "No data" : `${{Number(value).toFixed(2)}}%`;
    const money = value => value === null || value === undefined || value === "" ? "No data" : `$${{Number(value).toLocaleString(undefined, {{maximumFractionDigits: 0}})}}`;
    const cls = value => {{
      const n = Number(String(value ?? "").replace("%",""));
      if (Number.isNaN(n)) return "";
      return n > 0 ? "good" : n < 0 ? "bad" : "";
    }};
    const cell = value => `<td class="${{cls(value)}}">${{value ?? ""}}</td>`;
    const table = (headers, rows, empty="No rows yet.") => rows && rows.length
      ? `<div class="scroll"><table><thead><tr>${{headers.map(h=>`<th>${{h}}</th>`).join("")}}</tr></thead><tbody>${{rows.join("")}}</tbody></table></div>`
      : `<p class="small">${{empty}}</p>`;
    const parsePct = value => Number(String(value ?? "0").replace("%","")) || 0;
    const byCategory = category => (quant.category_performance || []).filter(row => row.category === category);
    const perf = (category, horizon) => byCategory(category).find(row => row.horizon === horizon) || {{}};
    const labelCounts = v2.label_counts || {{}};

    const paper1h = perf("Paper Trade Candidate", "1h");
    const activeErrors = Number(v2.api_errors || 0);
    const totalScanned = Object.values(labelCounts).reduce((sum, value) => sum + Number(value || 0), 0);
    const systemHealth = activeErrors === 0 && totalScanned > 0 ? "Operational" : "Needs Attention";
    const paperEdge = Number(paper1h.median_return || -1) < 0 ? "Not Proven / Weak" : "Needs More Sample";
    document.getElementById("executiveTerminal").innerHTML = [
      ["Model version", v2.model_version || "V2"],
      ["Last scan time", v2.latest_scan || payload.latest_seen],
      ["Last outcome time", payload.latest_outcome],
      ["Total tokens scanned", totalScanned],
      ["Rejected audit accuracy", (v2.filter_accuracy || {{}}).accuracy_rate || "No data"],
      ["API errors", activeErrors],
      ["Wallet module", (wallets.status || {{}}).enabled || "Unknown"],
      ["Current System Verdict", "Research pipeline working. Paper candidate edge not proven."]
    ].map(([k,v]) => `<div class="term-row"><span>${{k}}</span><strong class="${{k.includes("Verdict") ? "verdict" : ""}}">${{v}}</strong></div>`).join("");

    const healthCards = [
      ["Research Pipeline Status", systemHealth, "The scanner is working, but candidate edge is not proven yet."],
      ["Paper Candidate Warning", paperEdge, "Paper candidates are simulated research labels only. Current accepted/paper results are weak until proven by larger samples."],
      ["Rejected Audit Strength", (v2.filter_accuracy || {{}}).accuracy_rate || "No data", "Rejected-token audit is currently the strongest part of the system. It helps measure whether filters are protecting against weak tokens."],
      ["Missed Winner Warning", "Audit Required", "Some rejected tokens pumped hard. This does not automatically mean the filter was wrong. The system must check whether those tokens were actually tradable or only high-risk momentum."],
      ["No Auto Trading", "Blocked", "This dashboard is not ready for auto-trading. Auto-trading should not be added until candidate performance beats baselines with enough data."]
    ];
    document.getElementById("healthCards").innerHTML = healthCards.map(([label,value,copy]) => `
      <div class="metric-card"><div class="metric-label">${{label}}</div><div class="metric-value">${{value}}</div><p class="small">${{copy}}</p></div>
    `).join("");

    function categoryVerdictClass(verdict) {{
      if ((verdict || "").includes("Good")) return "Good";
      if ((verdict || "").includes("Bad")) return "Bad";
      if ((verdict || "").includes("Inactive")) return "Inactive";
      if ((verdict || "").includes("Small")) return "Small";
      return "Mixed";
    }}

    document.getElementById("categoryCards").innerHTML = labels.map(label => {{
      const p1 = perf(label, "1h"), p4 = perf(label, "4h"), p24 = perf(label, "24h");
      const base = p24.verdict ? p24 : p4.verdict ? p4 : p1;
      const verdict = base.verdict || "Too Small Sample";
      return `<div class="category-card">
        <div class="category-top"><div><div class="category-name">${{label}}</div><div class="category-count">${{labelCounts[label] || 0}} rows</div></div><span class="status ${{categoryVerdictClass(verdict)}}"><i class="dot"></i>${{verdict}}</span></div>
        <div class="kpi-list">
          <div class="kpi"><span>Median 1h</span><strong class="${{cls(p1.median_return)}}">${{fmtPct(p1.median_return)}}</strong></div>
          <div class="kpi"><span>Median 4h</span><strong class="${{cls(p4.median_return)}}">${{fmtPct(p4.median_return)}}</strong></div>
          <div class="kpi"><span>Median 24h</span><strong class="${{cls(p24.median_return)}}">${{fmtPct(p24.median_return)}}</strong></div>
          <div class="kpi"><span>Avg 1h</span><strong class="${{cls(p1.average_return)}}">${{fmtPct(p1.average_return)}}</strong></div>
          <div class="kpi"><span>Best</span><strong class="${{cls(base.best_return)}}">${{fmtPct(base.best_return)}}</strong></div>
          <div class="kpi"><span>Worst</span><strong class="${{cls(base.worst_return)}}">${{fmtPct(base.worst_return)}}</strong></div>
          <div class="kpi"><span>Positive</span><strong>${{base.positive_rate == null ? "No data" : `${{(Number(base.positive_rate)*100).toFixed(1)}}%`}}</strong></div>
          <div class="kpi"><span>Sample</span><strong>${{base.count || 0}}</strong></div>
        </div>
      </div>`;
    }}).join("");

    function renderBars(id, rows, valueKey, labelKey) {{
      const values = rows.map(row => Math.abs(Number(row[valueKey]) || 0));
      const max = Math.max(1, ...values);
      document.getElementById(id).innerHTML = `<div class="bars">${{rows.map(row => {{
        const value = Number(row[valueKey]) || 0;
        return `<div class="bar-row"><span>${{row[labelKey]}}</span><div class="bar-track"><div class="bar-fill" style="width:${{Math.min(100, Math.abs(value)/max*100)}}%; background:${{value < 0 ? "linear-gradient(90deg,var(--red),var(--amber))" : "linear-gradient(90deg,var(--green),var(--cyan))"}}"></div></div><strong class="${{cls(value)}}">${{fmtPct(value)}}</strong></div>`;
      }}).join("")}}</div>`;
    }}

    renderBars("outcomeCurve", v2.performance || [], row => row, "horizon");
    document.getElementById("outcomeCurve").innerHTML = `<div class="bars">${{(v2.performance || []).map(row => {{
      const med = parsePct(row.median);
      const avg = parsePct(row.average);
      return `<div class="bar-row"><span>${{row.horizon}}</span><div class="bar-track"><div class="bar-fill" style="width:${{Math.min(100, Math.abs(avg) * 4 + 4)}}%; background:${{avg < 0 ? "linear-gradient(90deg,var(--red),var(--amber))" : "linear-gradient(90deg,var(--green),var(--cyan))"}}"></div></div><strong class="${{cls(avg)}}">avg ${{row.average}}</strong></div>
      <div class="bar-row"><span></span><div class="bar-track"><div class="bar-fill" style="width:${{Math.min(100, Math.abs(med) * 4 + 4)}}%; opacity:.55"></div></div><strong class="${{cls(med)}}">med ${{row.median}}</strong></div>`;
    }}).join("")}}</div>`;

    const posRows = labels.map(label => {{
      const row = perf(label, "1h");
      return {{label, positive: Number(row.positive_rate || 0) * 100}};
    }});
    document.getElementById("positiveRateChart").innerHTML = `<div class="bars">${{posRows.map(row => `<div class="bar-row"><span>${{row.label.replace(" Candidate","")}}</span><div class="bar-track"><div class="bar-fill" style="width:${{Math.min(100,row.positive)}}%"></div></div><strong>${{row.positive.toFixed(1)}}%</strong></div>`).join("")}}</div>`;

    const tenXFailures = quant.ten_x_failure_review || [];
    document.getElementById("tenXScatter").innerHTML = tenXFailures.length
      ? `<svg width="100%" height="250" viewBox="0 0 520 250" role="img">${{tenXFailures.slice(0,80).map(row => {{
          const x = 40 + Math.min(100, Number(row.ten_x_score || 0)) * 4.4;
          const y = 210 - Math.max(-100, Math.min(100, Number(row.return_pct || 0))) * 0.9;
          return `<circle cx="${{x}}" cy="${{y}}" r="5" fill="var(--red)"><title>${{row.symbol}} ${{fmtPct(row.return_pct)}}</title></circle>`;
        }}).join("")}}<line x1="40" y1="210" x2="490" y2="210" stroke="#334155"/><line x1="40" y1="25" x2="40" y2="210" stroke="#334155"/><text x="42" y="22" fill="#9ca3af" font-size="12">Return</text><text x="400" y="238" fill="#9ca3af" font-size="12">10x score</text></svg>`
      : `<p class="small">No 10x score failures yet.</p>`;

    const reasonCounts = {{}};
    (quant.accepted_failure_diagnosis || []).forEach(row => {{
      let reasons = [];
      try {{ reasons = JSON.parse(row.failure_reasons || "[]"); }} catch {{}}
      reasons.forEach(reason => reasonCounts[reason] = (reasonCounts[reason] || 0) + 1);
    }});
    const reasonRows = Object.entries(reasonCounts);
    document.getElementById("failureDonut").innerHTML = reasonRows.length
      ? table(["Failure category","Count"], reasonRows.map(([k,v])=>`<tr><td>${{k}}</td><td>${{v}}</td></tr>`))
      : `<p class="small">No accepted failures yet. This section will populate when research or paper candidates lose more than 25%.</p>`;

    document.getElementById("failureLab").innerHTML = table(
      ["Token","Label","Horizon","Return","Failure reasons","Fix"],
      (quant.accepted_failure_diagnosis || []).slice(0,18).map(row => `<tr><td><strong>${{row.symbol}}</strong></td><td>${{row.original_label}}</td><td>${{row.horizon}}</td>${{cell(fmtPct(row.return_pct))}}<td>${{row.failure_reasons}}</td><td>${{row.fix_recommendation}}</td></tr>`),
      "No accepted failures yet. This section will populate when research or paper candidates lose more than 25%."
    );
    document.getElementById("tenXFailure").innerHTML = table(
      ["Token","10x","Risk-adjusted","Horizon","Return","Diagnosis"],
      tenXFailures.slice(0,18).map(row => `<tr><td><strong>${{row.symbol}}</strong></td><td>${{Number(row.ten_x_score || 0).toFixed(0)}}</td><td>${{Number(row.risk_adjusted_10x_score || 0).toFixed(0)}}</td><td>${{row.horizon}}</td>${{cell(fmtPct(row.return_pct))}}<td>${{row.diagnosis_text}}</td></tr>`),
      "No 10x score failures yet."
    );
    document.getElementById("missedWinnerLab").innerHTML = table(
      ["Token","Original label","Horizon","Return","Tradability","High-risk momentum?","Lesson"],
      (quant.missed_winner_review || []).slice(0,20).map(row => `<tr><td><strong>${{row.symbol}}</strong></td><td>${{row.original_label}}</td><td>${{row.horizon}}</td>${{cell(fmtPct(row.return_at_horizon_pct))}}<td>${{row.tradability_label}}</td><td>${{row.should_be_high_risk_momentum ? "Yes" : "No"}}</td><td>${{row.main_lesson}}</td></tr>`),
      "No missed winners found yet."
    );
    document.getElementById("highRiskMomentumLab").innerHTML = table(
      ["Token","Liquidity","Volume","Move","10x","Reasons","Chart"],
      (v2.bucket_tables?.["High-Risk Momentum Watchlist"] || []).slice(0,20).map(row => `<tr><td><strong>${{row.symbol}}</strong></td><td>${{row.liquidity}}</td><td>${{row.volume24h}}</td><td>${{row.move1h}}</td><td>${{row.ten_x_score}}</td><td>${{row.main_reasons || "High-risk momentum research"}}</td><td>${{row.url ? `<a href="${{row.url}}" target="_blank" rel="noreferrer">Open</a>` : ""}}</td></tr>`),
      "No High-Risk Momentum tokens yet. If rejected tokens keep pumping, check whether routing rules are too strict."
    );
    document.getElementById("baselineTable").innerHTML = table(
      ["Token","Label","Horizon","Token","BTC","ETH","All scanned","Same liquidity","Same age","Verdict"],
      (quant.baseline_comparisons || []).slice(0,40).map(row => `<tr><td><strong>${{row.symbol}}</strong></td><td>${{row.label}}</td><td>${{row.horizon}}</td>${{cell(fmtPct(row.token_return_pct))}}${{cell(fmtPct(row.btc_return_pct))}}${{cell(fmtPct(row.eth_return_pct))}}${{cell(fmtPct(row.excess_vs_all_scanned))}}${{cell(fmtPct(row.excess_vs_same_liquidity))}}${{cell(fmtPct(row.excess_vs_same_age))}}<td>${{row.baseline_verdict}}</td></tr>`),
      "No baseline comparison yet. Baseline data must be collected before edge can be evaluated."
    );

    const w = wallets.status || {{}};
    document.getElementById("walletStatus").innerHTML = `
      <div class="grid">
        ${{[["Enabled",w.enabled],["Provider",w.active_provider],["Wallets",w.wallets_tracked],["Trades",w.trades_tracked],["Smart",w.smart_wallets],["Useful",w.useful_wallets],["Suspicious",w.suspicious_wallets],["API errors",w.wallet_api_errors]].map(([k,v]) => `<div class="kpi"><span>${{k}}</span><strong>${{v ?? "No data"}}</strong></div>`).join("")}}
      </div>
      <p class="small">${{w.warning || "Wallets are tracked but not yet classified. Wallet labels require historical evidence, realized exits, and risk scoring."}}</p>
    `;
    document.getElementById("tenXEngine").innerHTML = table(
      ["Label","Rows"],
      Object.entries((v2.bucket_tables || {{}})).map(([label, rows]) => `<tr><td>${{label}}</td><td>${{rows.length}}</td></tr>`)
    ) + `<p class="small">10x Potential Score, Risk-Adjusted 10x Score, Manipulation Risk Score, and Tradability Score are audit metrics only. They are not predictions.</p>`;
    document.getElementById("dataQuality").innerHTML = table(
      ["Metric","Value","Status","Detail"],
      (quant.data_quality_report || []).map(row => `<tr><td>${{row.metric}}</td><td>${{row.value}}</td><td class="${{row.status === "OK" ? "good" : "Mixed"}}">${{row.status}}</td><td>${{row.detail || ""}}</td></tr>`)
    );
    const repoExports = "https://github.com/fatehchana17-create/crypto-signal-autopsy-v1/blob/main/exports/";
    const exportNames = ["category_performance","accepted_failure_diagnosis","missed_winner_review","baseline_comparisons","ten_x_failure_review","data_quality_report","filter_results","outcome_snapshots","paper_trades","wallets"];
    document.getElementById("exports").innerHTML = exportNames.map(name => `<a href="${{repoExports}}${{name}}.csv">${{name}}.csv</a><a href="data/${{name}}.json">${{name}}.json</a>`).join("");
  </script>
</body>
</html>
"""
