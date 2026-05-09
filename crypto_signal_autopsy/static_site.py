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

    payload = _build_payload(conn, settings)
    write_public_wallet_json_data(conn, target_dir / "data")
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
    @media (max-width: 880px) {{
      .grid, .two, .four {{ grid-template-columns: 1fr; }}
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
      <h2>V2 Bucket Tables</h2>
      <p class="small">Paper Trade Candidate means simulated tracking only. High-Risk Momentum Watchlist means risky token being studied, not recommended.</p>
      <div id="bucketTables"></div>
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
        ["Token", "Horizon", "Net", "Liquidity", "24h Vol", "Age", "Why", "What it means", "Link"],
        auditRows(rows)
      );
    }}

    function renderExtremeTable(id, rows) {{
      document.getElementById(id).innerHTML = table(
        ["Token", "Horizon", "Net", "Liquidity", "Why", "What it means", "Link"],
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
          ["Token", "Chain", "Age", "Liquidity", "24h Vol", "1h Move", "Risk", "Opp", "Reasons", "DEX"],
          (v2.bucket_tables[label] || []).slice(0, 12).map(row => `<tr>
            <td><strong>${{row.symbol}}</strong><br><span class="small">${{row.scan_time}}</span></td>
            <td>${{row.chain}}</td>
            <td>${{row.pair_age}}</td>
            <td>${{row.liquidity}}</td>
            <td>${{row.volume24h}}</td>
            <td>${{row.move1h}}</td>
            <td>${{row.risk_score}}</td>
            <td>${{row.opportunity_score}}</td>
            <td class="reason">${{row.main_reasons}}</td>
            <td>${{row.url ? `<a href="${{row.url}}" target="_blank" rel="noreferrer">Open</a>` : ""}}</td>
          </tr>`)
        )}}
      `).join("");
    }}

    function renderOutcomeRows(id, rows) {{
      document.getElementById(id).innerHTML = table(
        ["Token", "Label", "Horizon", "Return", "Reasons", "Illiquid", "Rug", "Volume Gone"],
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
        ["Wallet", "Chain", "Label", "Quality", "Risk", "Tokens", "Win", "Median", "Avg", "Rug", "Quick Dump", "Hold", "Last"],
        wallets.leaderboard.slice(0, 20).map(row => `<tr>
          <td title="${{row.wallet_full}}"><strong>${{row.wallet}}</strong></td>
          <td>${{row.chain}}</td><td>${{row.label}}</td><td>${{row.quality}}</td><td>${{row.risk}}</td>
          <td>${{row.tokens}}</td><td>${{row.win_rate}}</td><td>${{row.median_return}}</td><td>${{row.average_return}}</td>
          <td>${{row.rug_exposure}}</td><td>${{row.quick_dump}}</td><td>${{row.avg_hold}}</td><td>${{row.last_active}}</td>
        </tr>`)
      );
      document.getElementById("walletActivity").innerHTML = table(
        ["Time", "Wallet", "Label", "Side", "Token", "Amount", "Pair Age", "Liquidity", "Token Risk", "Token Opp", "Wallet Signal"],
        wallets.activity_feed.slice(0, 20).map(row => `<tr>
          <td>${{row.time}}</td><td>${{row.wallet}}</td><td>${{row.wallet_label}}</td><td>${{row.side}}</td>
          <td><strong>${{row.token}}</strong></td><td>${{row.amount}}</td><td>${{row.pair_age}}</td><td>${{row.liquidity}}</td>
          <td>${{row.token_risk}}</td><td>${{row.token_opportunity}}</td><td>${{row.wallet_signal}}</td>
        </tr>`)
      );
      document.getElementById("walletSignalTokens").innerHTML = table(
        ["Token", "Chain", "Smart", "Useful", "Suspicious", "Net Flow", "Score", "Wallet Label", "Token Label", "Risk", "Opp"],
        wallets.token_signals.slice(0, 20).map(row => `<tr>
          <td><strong>${{row.token}}</strong></td><td>${{row.chain}}</td><td>${{row.smart}}</td><td>${{row.useful}}</td>
          <td>${{row.suspicious}}</td><td>${{row.net_flow}}</td><td>${{row.wallet_score}}</td><td>${{row.wallet_label}}</td>
          <td>${{row.token_label}}</td><td>${{row.risk}}</td><td>${{row.opportunity}}</td>
        </tr>`)
      );
      document.getElementById("suspiciousWallets").innerHTML = table(
        ["Wallet", "Label", "Risk", "Tokens", "Rug", "Quick Dump", "First Minute", "One-Hit", "Suspicion", "Notes"],
        wallets.suspicious_wallets.slice(0, 20).map(row => `<tr>
          <td title="${{row.wallet_full}}"><strong>${{row.wallet}}</strong></td><td>${{row.label}}</td><td>${{row.risk}}</td>
          <td>${{row.tokens}}</td><td>${{row.rug_exposure}}</td><td>${{row.quick_dump}}</td><td>${{row.first_minute}}</td>
          <td>${{row.one_hit}}</td><td>${{row.suspicion}}</td><td>${{row.notes}}</td>
        </tr>`)
      );
      document.getElementById("walletClusters").innerHTML = table(
        ["Token", "Cluster Type", "Wallets", "Buy USD", "Avg Quality", "Avg Risk", "Scan Time", "Notes"],
        wallets.clusters.slice(0, 20).map(row => `<tr>
          <td><strong>${{row.token}}</strong></td><td>${{row.cluster_type}}</td><td>${{row.wallet_count}}</td>
          <td>${{row.total_buy}}</td><td>${{row.avg_quality}}</td><td>${{row.avg_risk}}</td><td>${{row.scan_time}}</td><td>${{row.notes}}</td>
        </tr>`)
      );
    }}

    renderBucketTables();
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
