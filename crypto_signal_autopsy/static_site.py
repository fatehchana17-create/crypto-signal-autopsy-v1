from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import json
import sqlite3
from statistics import mean, median
from typing import Any

from crypto_signal_autopsy.config import Settings
from crypto_signal_autopsy.db import from_json, init_db
from crypto_signal_autopsy.review import build_rejection_record, humanize_reason, money, pct


def build_static_site(conn: sqlite3.Connection, settings: Settings, out_dir: Path | None = None) -> Path:
    init_db(conn)
    target_dir = out_dir or settings.project_root / "docs"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / ".nojekyll").write_text("", encoding="utf-8")

    payload = _build_payload(conn, settings)
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
  <title>Crypto Signal Autopsy V1</title>
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
        url("../assets/dashboard-header.png") center / cover no-repeat,
        #fff;
      box-shadow: 0 18px 45px rgba(18,53,59,.10);
    }}
    h1 {{ margin: 0 0 10px; font-size: 42px; line-height: 1.03; letter-spacing: 0; }}
    .lead {{ margin: 0; max-width: 760px; color: var(--muted); font-size: 18px; line-height: 1.55; }}
    .meta {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 20px; }}
    .pill {{ border: 1px solid var(--line); border-radius: 999px; background: rgba(255,255,255,.78); padding: 8px 12px; color: var(--muted); font-weight: 700; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; margin: 20px 0; }}
    .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 18px; box-shadow: 0 12px 30px rgba(18,53,59,.07); }}
    .metric {{ font-size: 31px; font-weight: 800; margin-top: 6px; }}
    .label {{ color: var(--muted); font-size: 13px; font-weight: 800; text-transform: uppercase; letter-spacing: .06em; }}
    .two {{ display: grid; grid-template-columns: .9fr 1.1fr; gap: 16px; margin-top: 16px; }}
    h2 {{ margin: 0 0 14px; font-size: 23px; letter-spacing: 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 12px 10px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; font-size: 14px; }}
    th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }}
    tr:hover td {{ background: var(--soft); }}
    a {{ color: var(--accent); font-weight: 800; text-decoration: none; }}
    .reason {{ color: var(--warn); font-weight: 700; }}
    .tools {{ display: flex; gap: 10px; margin-bottom: 12px; flex-wrap: wrap; }}
    input, select {{ border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; min-height: 40px; background: #fff; color: var(--ink); }}
    input {{ flex: 1 1 260px; }}
    .small {{ color: var(--muted); font-size: 13px; line-height: 1.5; }}
    @media (max-width: 880px) {{
      .grid, .two {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 33px; }}
      header {{ padding: 24px; }}
      th:nth-child(4), td:nth-child(4), th:nth-child(5), td:nth-child(5) {{ display: none; }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <h1>Crypto Signal Autopsy V1</h1>
      <p class="lead">A public 24/7 mirror of the research dashboard. It shows what the scanner found, why tokens were rejected, and what happened after the audit windows.</p>
      <div class="meta">
        <span class="pill">Base chain</span>
        <span class="pill">GitHub Actions hourly scan</span>
        <span class="pill">Rejected-token audit</span>
      </div>
    </header>

    <section class="grid" id="metrics"></section>

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
  </main>

  <script id="payload" type="application/json">{safe_data}</script>
  <script>
    const payload = JSON.parse(document.getElementById("payload").textContent);
    const counts = payload.counts;
    const metrics = [
      ["Rejected Tokens", counts.rejected_tokens],
      ["Rejected Outcomes", counts.rejected_outcomes],
      ["Accepted Signals", counts.accepted_signals],
      ["API Events", counts.api_events],
    ];
    document.getElementById("metrics").innerHTML = metrics.map(([label, value]) => `
      <div class="card"><div class="label">${{label}}</div><div class="metric">${{value}}</div></div>
    `).join("");

    function table(headers, rows) {{
      if (!rows.length) return '<p class="small">No rows yet.</p>';
      return `<table><thead><tr>${{headers.map(h => `<th>${{h}}</th>`).join("")}}</tr></thead><tbody>${{rows.join("")}}</tbody></table>`;
    }}

    document.getElementById("outcomes").innerHTML = table(
      ["Horizon", "Count", "Median", "Average", "Best", "Worst"],
      payload.outcome_summary.map(row => `<tr><td>${{row.horizon}}</td><td>${{row.count}}</td><td>${{row.median}}</td><td>${{row.average}}</td><td>${{row.best}}</td><td>${{row.worst}}</td></tr>`)
    ) + `<p class="small">Latest scan: ${{payload.latest_seen}}<br>Latest outcome: ${{payload.latest_outcome}}</p>`;

    document.getElementById("reasons").innerHTML = table(
      ["Reason", "Count"],
      payload.reason_counts.map(([reason, count]) => `<tr><td class="reason">${{reason}}</td><td>${{count}}</td></tr>`)
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
