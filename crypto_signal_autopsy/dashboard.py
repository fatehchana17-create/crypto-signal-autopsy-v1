from __future__ import annotations

import base64
from collections import Counter
from html import escape
import sqlite3
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import streamlit as st

from crypto_signal_autopsy.analysis_tables import build_analysis_payload
from crypto_signal_autopsy.config import Settings, load_settings
from crypto_signal_autopsy.db import active_api_error_count, connect, init_db
from crypto_signal_autopsy.quant_analytics import build_quant_payload, refresh_quant_analytics
from crypto_signal_autopsy.review import (
    build_rejection_record,
    build_requirement_rows,
    money,
    pct,
)
from crypto_signal_autopsy.ux_recovery import (
    blank_recovery_response,
    is_blank_input,
    log_blank_input_event,
    mark_latest_blank_input_recovered,
)
from crypto_signal_autopsy.v2_dashboard import LABELS, build_v2_payload
from crypto_signal_autopsy.wallet_exports import build_wallet_dashboard_payload


st.set_page_config(page_title="Crypto Signal Autopsy V3", layout="wide")


def main() -> None:
    settings = load_settings()
    conn = connect(settings.db_path)
    init_db(conn)
    header_uri = _asset_data_uri(settings.project_root / "assets" / "dashboard-header.png")
    _style(header_uri)

    signals = _read(conn, "SELECT * FROM signals ORDER BY created_at DESC")
    outcomes = _read(conn, "SELECT * FROM signal_outcomes ORDER BY observed_at DESC")
    rejected_outcomes = _read(conn, "SELECT * FROM rejected_candidate_outcomes ORDER BY observed_at DESC")
    evaluations = _read(conn, "SELECT * FROM candidate_evaluations ORDER BY observed_at DESC")
    api_events = _read(conn, "SELECT * FROM api_events ORDER BY observed_at DESC LIMIT 200")
    active_api_errors = active_api_error_count(conn)
    rejected_records = _rejected_records(evaluations, settings)

    st.markdown(
        """
        <section class="hero">
          <div class="hero-copy">
            <h1>Crypto Signal Autopsy V3</h1>
            <p>Research dashboard for crypto token risk and opportunity analysis.</p>
            <p class="hero-disclaimer">Research only. No financial advice. No buy signals. No sell signals. No auto trading. Wallet activity is not a buy signal.</p>
            <div class="hero-meta">
              <span>Base chain</span>
              <span>Hourly scanner</span>
              <span>V2 labels and scoring</span>
              <span>V3 wallet research</span>
            </div>
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    accepted_count = int(evaluations["accepted"].sum()) if not evaluations.empty else 0
    rejected_count = len(rejected_records)
    st.markdown('<div class="metric-row">', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Accepted Signals", len(signals))
    col2.metric("Rejected Tokens", rejected_count)
    col3.metric("Rejected Outcomes", len(rejected_outcomes))
    col4.metric("Active API Errors", active_api_errors)
    st.markdown("</div>", unsafe_allow_html=True)

    tab_start, tab_v2, tab_tradable, tab_dna_delay, tab_survival, tab_wallets, tab_overview, tab_analysis, tab_manual, tab_signals, tab_outcomes, tab_rejected_audit, tab_raw, tab_config = st.tabs(
        [
            "Start Here",
            "Token Buckets",
            "Tradable Coins",
            "DNA / Delay",
            "Survival Lab",
            "Smart Wallets",
            "Overview",
            "Analysis",
            "Manual Review",
            "Signals",
            "Outcomes",
            "Rejected Audit",
            "Raw Tables",
            "Config",
        ]
    )

    with tab_start:
        _start_here(conn, settings)
    with tab_v2:
        _v2_lab(conn)
    with tab_tradable:
        _tradable_lab(conn, settings)
    with tab_dna_delay:
        _dna_delay_lab(conn, settings)
    with tab_survival:
        _survival_lab(conn, settings)
    with tab_wallets:
        _wallet_lab(conn, settings)
    with tab_overview:
        _overview(
            signals,
            outcomes,
            rejected_outcomes,
            evaluations,
            api_events,
            rejected_records,
            settings,
            active_api_errors,
        )
    with tab_analysis:
        _analysis_workspace(conn, settings)
    with tab_manual:
        _manual_review(conn, rejected_records, settings)
    with tab_signals:
        st.dataframe(signals, use_container_width=True, hide_index=True)
    with tab_outcomes:
        st.dataframe(outcomes, use_container_width=True, hide_index=True)
    with tab_rejected_audit:
        _rejected_audit(rejected_outcomes)
    with tab_raw:
        st.subheader("Candidate Evaluations")
        st.dataframe(evaluations, use_container_width=True, hide_index=True)
        st.subheader("API Notice History")
        st.caption("This table keeps current API problems only. Old recovered rate limits and harmless notices are pruned automatically.")
        st.dataframe(api_events, use_container_width=True, hide_index=True)
    with tab_config:
        _config_panel(settings, accepted_count)


def _overview(
    signals: pd.DataFrame,
    outcomes: pd.DataFrame,
    rejected_outcomes: pd.DataFrame,
    evaluations: pd.DataFrame,
    api_events: pd.DataFrame,
    rejected_records: list[dict],
    settings: Settings,
    active_api_errors: int,
) -> None:
    _overview_status(signals, outcomes, rejected_outcomes, evaluations, api_events, active_api_errors)

    left, right = st.columns([1.1, 1])
    with left:
        if outcomes.empty:
            st.info("No matured accepted-signal outcomes yet.")
        else:
            grouped = (
                outcomes.groupby("horizon", as_index=False)
                .agg(
                    median_net_return_pct=("net_return_pct", "median"),
                    avg_net_return_pct=("net_return_pct", "mean"),
                    count=("signal_id", "count"),
                )
                .sort_values("horizon")
            )
            st.subheader("Accepted Signal Outcomes")
            _render_simple_table(
                [
                    {
                        "Horizon": row["horizon"],
                        "Median net": pct(row["median_net_return_pct"]),
                        "Average net": pct(row["avg_net_return_pct"]),
                        "Count": str(int(row["count"])),
                    }
                    for _, row in grouped.iterrows()
                ],
                empty_message="No accepted outcome rows yet.",
            )
            st.bar_chart(grouped.set_index("horizon")["median_net_return_pct"])

        if not rejected_outcomes.empty:
            rejected_grouped = (
                rejected_outcomes.groupby("horizon", as_index=False)
                .agg(
                    median_net_return_pct=("net_return_pct", "median"),
                    avg_net_return_pct=("net_return_pct", "mean"),
                    count=("candidate_evaluation_id", "count"),
                )
                .sort_values("horizon")
            )
            st.subheader("Rejected Token Outcomes")
            _render_simple_table(
                [
                    {
                        "Horizon": row["horizon"],
                        "Median net": pct(row["median_net_return_pct"]),
                        "Average net": pct(row["avg_net_return_pct"]),
                        "Count": str(int(row["count"])),
                    }
                    for _, row in rejected_grouped.iterrows()
                ],
                empty_message="No rejected outcome rows yet.",
            )

    with right:
        if evaluations.empty:
            st.info("No scan rows yet.")
        else:
            reason_counts = _reason_counts(evaluations, settings)
            st.subheader("Why Tokens Are Being Rejected")
            _render_simple_table(
                [
                    {"Reason": row["Reason"], "Count": str(int(row["Count"]))}
                    for _, row in reason_counts.iterrows()
                ],
                empty_message="No rejection reasons yet.",
            )

    if not api_events.empty:
        st.subheader("Current API Problems")
        st.dataframe(api_events.head(25), use_container_width=True, hide_index=True)

    if rejected_records:
        st.subheader("Latest Rejected Tokens")
        _render_latest_rejections(rejected_records[:6])


def _start_here(conn: sqlite3.Connection, settings: Settings) -> None:
    v2 = build_v2_payload(conn)
    wallets = build_wallet_dashboard_payload(conn, settings)
    labels = v2["label_counts"]
    wallet_status = wallets["status"]

    st.markdown('<div class="section-title">Start Here</div>', unsafe_allow_html=True)
    st.caption(
        "This is a research notebook. It scans tokens, rejects risky ones, tracks what happened later, "
        "and studies wallets around those tokens. Nothing here is a buy signal."
    )

    cards = [
        ("Tokens checked", str(sum(labels.values()))),
        ("Rejected tokens", str(labels.get("Reject", 0))),
        ("Watchlist tokens", str(labels.get("Watchlist", 0))),
        ("Research candidates", str(labels.get("Research Candidate", 0))),
        ("Wallets tracked", wallet_status["wallets_tracked"]),
        ("Wallet trades saved", wallet_status["trades_tracked"]),
    ]
    for index in range(0, len(cards), 3):
        cols = st.columns(3)
        for col, (label, value) in zip(cols, cards[index : index + 3]):
            col.metric(label, value)

    left, right = st.columns(2)
    with left:
        st.subheader("What the labels mean")
        _render_simple_table(
            [
                {
                    "Label": "Reject",
                    "Plain meaning": "Failed safety or quality rules.",
                    "What to do": "Usually ignore it, then check later if the filter was right.",
                },
                {
                    "Label": "Watchlist",
                    "Plain meaning": "Interesting, but not clean enough for paper trading.",
                    "What to do": "Study only. Wait for more evidence.",
                },
                {
                    "Label": "Research Candidate",
                    "Plain meaning": "Cleaner than most, still not a buy signal.",
                    "What to do": "Read details and compare later performance.",
                },
                {
                    "Label": "Unproven Wallet",
                    "Plain meaning": "Wallet has too little history.",
                    "What to do": "Do not call it smart yet.",
                },
            ],
            "No label guide available.",
        )
    with right:
        st.subheader("Best reading order")
        _render_simple_table(
            [
                {"Step": "1", "Read this": "Token Buckets", "Why": "See why each token was rejected or watched."},
                {"Step": "2", "Read this": "Smart Wallets", "Why": "See wallet behavior, but remember it is research only."},
                {"Step": "3", "Read this": "Overview", "Why": "Check whether accepted/rejected groups are performing well or badly."},
                {"Step": "4", "Read this": "Analysis", "Why": "Use 1h and 24h tables to audit decisions."},
                {"Step": "5", "Read this": "Raw Tables", "Why": "Only for debugging or deeper inspection."},
            ],
            "No reading order available.",
        )

    st.info(
        "Simple rule: a good website here does not mean a good trade. The goal is to avoid fooling ourselves with messy token and wallet data."
    )


def _v2_lab(conn: sqlite3.Connection) -> None:
    payload = build_v2_payload(conn)
    st.markdown('<div class="section-title">Token Decision Buckets</div>', unsafe_allow_html=True)
    st.caption("Start here to see why each token was rejected, watched, or promoted to research. No label is a buy signal.")

    cards = payload["health_cards"]
    for index in range(0, len(cards), 3):
        cols = st.columns(3)
        for col, card in zip(cols, cards[index : index + 3]):
            col.metric(card["label"], card["value"])

    accuracy = payload["filter_accuracy"]
    st.subheader("Rejected Filter Accuracy")
    st.caption(
        "Rejected tokens are watched at 15m, 30m, 1h, 2h, 4h, 8h, and 24h. "
        "After 24h, the active rejected row is archived into this summary."
    )
    cols = st.columns(4)
    cols[0].metric("Accuracy", accuracy["accuracy_rate"])
    cols[1].metric("Success", accuracy["successes"])
    cols[2].metric("Failure", accuracy["failures"])
    cols[3].metric("Completed", accuracy["completed"])
    st.caption(accuracy["plain"])
    _render_simple_table(payload["rejected_audits"], "No rejected token has finished the 24h audit yet.")

    st.subheader("10x Research Logic")
    st.caption("This score only marks research conditions that can appear before large moves. It is not a prediction.")
    _render_simple_table(
        [
            {"Factor": "Small size", "Plain meaning": "Lower FDV or market cap can leave more room, but also adds risk."},
            {"Factor": "Enough liquidity", "Plain meaning": "Enough liquidity to observe real trading, not just a tiny pool."},
            {"Factor": "Volume pressure", "Plain meaning": "Volume is strong compared with liquidity."},
            {"Factor": "Buy pressure", "Plain meaning": "Recent buys are stronger than sells with enough activity."},
            {"Factor": "Young but not instant", "Plain meaning": "Early pair, but not only chaotic launch minutes old."},
            {"Factor": "Security sanity", "Plain meaning": "Critical safety flags override the score."},
        ],
        "No 10x logic rows.",
    )

    st.subheader("Bucket Tables")
    for label in LABELS:
        with st.expander(label, expanded=label in {"Reject", "Watchlist"}):
            _render_v2_bucket_table(payload["bucket_tables"].get(label, []))

    left, right = st.columns(2)
    with left:
        st.subheader("Median vs Average Performance")
        st.caption("Average return can be distorted by outliers. Median return is usually more reliable.")
        _render_simple_table(payload["performance"], "No V2 outcome snapshots yet.")
    with right:
        st.subheader("Score Bucket Performance")
        _render_simple_table(payload["score_buckets"], "No score bucket performance yet.")

    st.divider()
    st.subheader("Outlier Analysis")
    outlier_cols = st.columns(2)
    with outlier_cols[0]:
        st.markdown("**Biggest missed winners**")
        _render_simple_table(payload["outliers"]["missed_winners"], "No missed winners found yet.")
        st.markdown("**Biggest avoided losers**")
        _render_simple_table(payload["outliers"]["avoided_losers"], "No avoided losers found yet.")
    with outlier_cols[1]:
        st.markdown("**High-risk pumps**")
        _render_simple_table(payload["outliers"]["high_risk_pumps"], "No high-risk pumps found yet.")
        st.markdown("**Rugs and dumps**")
        _render_simple_table(payload["outliers"]["rugs_dumps"], "No rugs or dumps found yet.")

    st.divider()
    saved_col, blocked_col = st.columns(2)
    with saved_col:
        st.subheader("Filters That Saved Us")
        _render_simple_table(payload["filters_saved_us"], "No saved-from-bad-token evidence yet.")
    with blocked_col:
        st.subheader("Filters That Blocked Winners")
        _render_simple_table(payload["filters_blocked_winners"], "No blocked-winner evidence yet.")

    st.divider()
    paper_col, notes_col = st.columns(2)
    with paper_col:
        st.subheader("Paper Trade Simulation")
        st.caption("Paper Trade Candidate means simulated tracking only, not a buy signal.")
        _render_simple_table(payload["paper_trades"], "No paper trade candidates yet.")
    with notes_col:
        st.subheader("Manual Review Notes")
        _render_simple_table(payload["review_notes"], "No manual review notes yet.")


def _tradable_lab(conn: sqlite3.Connection, settings: Settings) -> None:
    refresh_quant_analytics(conn, settings)
    rows = build_quant_payload(conn)["tradable_candidates"]
    st.markdown('<div class="section-title">Tradable Coins</div>', unsafe_allow_html=True)
    st.caption(
        "Research-only list for cautious small-move candidates. It does not say buy now and does not promise 5%, 10%, or 30%."
    )
    strong = sum(1 for row in rows if row.get("tradable_tier") == "Tradable Research Candidate")
    avg_score = sum(float(row.get("tradable_score") or 0) for row in rows) / len(rows) if rows else None
    cols = st.columns(4)
    cols[0].metric("Tradable Rows", len(rows))
    cols[1].metric("Strong Candidates", strong)
    cols[2].metric("Cautious Watch", len(rows) - strong)
    cols[3].metric("Avg Score", f"{avg_score:.1f}" if avg_score is not None else "No data")
    if not rows:
        st.info("No tradable candidates yet. This section stays empty unless stricter liquidity, risk, and survival checks pass.")
    else:
        body = []
        for row in rows[:80]:
            actions = _trade_action_links(row)
            body.append(
                f"""
                <tr>
                  <td><strong>{escape(str(row.get("symbol") or "UNKNOWN"))}</strong><br><span class="muted-cell">{escape(str(row.get("scan_time") or ""))}</span></td>
                  <td>{escape(str(row.get("tradable_tier") or ""))}</td>
                  <td class="num">{float(row.get("tradable_score") or 0):.0f}</td>
                  <td>{escape(str(row.get("latest_horizon") or ""))} {escape(pct(row.get("latest_return_pct")))}</td>
                  <td class="num">{escape(pct(row.get("best_return_pct")))}</td>
                  <td class="num">{escape(pct(row.get("worst_return_pct")))}</td>
                  <td class="num">{escape(money(row.get("liquidity_usd")))}</td>
                  <td class="num">{escape(money(row.get("volume_24h_usd")))}</td>
                  <td class="num">{float(row.get("risk_score") or 0):.0f}</td>
                  <td class="num">{float(row.get("survival_score") or 0):.0f}</td>
                  <td>{escape(str(row.get("reasons") or ""))}</td>
                  <td>{escape(str(row.get("risk_notes") or ""))}</td>
                  <td>{actions}</td>
                </tr>
                """
            )
        _render_table(
            headers=[
                "Token",
                "Tier",
                "Score",
                "Latest",
                "Best",
                "Worst",
                "Liquidity",
                "Volume",
                "Risk",
                "Survival",
                "Why",
                "Caution",
                "Open",
            ],
            body="".join(body),
            min_width=1450,
        )
    st.info("Manual risk management is still required. This section is a research shortlist, not a trading command.")


def _dna_delay_lab(conn: sqlite3.Connection, settings: Settings) -> None:
    refresh_quant_analytics(conn, settings)
    payload = build_quant_payload(conn)
    st.markdown('<div class="section-title">Winner DNA vs Loser DNA + Delayed Entry</div>', unsafe_allow_html=True)
    st.caption("These tables study what actually happened after detection so the filters can improve for free.")
    left, right = st.columns(2)
    with left:
        st.subheader("Winner DNA vs Loser DNA")
        _render_simple_table(
            [
                {
                    "DNA": row.get("dna_type") or "",
                    "Samples": str(row.get("sample_count") or 0),
                    "Median liquidity": money(row.get("median_liquidity_usd")),
                    "Median volume": money(row.get("median_volume_24h_usd")),
                    "Median age": f"{float(row.get('median_pair_age_hours') or 0):.1f}h",
                    "Median 1h move": pct(row.get("median_price_change_1h_pct")),
                    "Median survival": f"{float(row.get('median_survival_score') or 0):.0f}",
                    "Rule": row.get("rule_implication") or "",
                }
                for row in payload["winner_loser_dna"]
            ],
            "No DNA profiles yet.",
        )
    with right:
        st.subheader("Delayed Entry Simulator")
        _render_simple_table(
            [
                {
                    "Label": row.get("source_label") or "",
                    "Entry": row.get("entry_delay") or "",
                    "Exit": row.get("exit_horizon") or "",
                    "Rows": str(row.get("sample_count") or 0),
                    "Median": pct(row.get("median_return_pct")),
                    "Average": pct(row.get("average_return_pct")),
                    "Positive": f"{float(row.get('positive_rate') or 0) * 100:.1f}%",
                    "Verdict": row.get("verdict") or "",
                }
                for row in payload["delayed_entry_simulator"][:80]
            ],
            "No delayed entry simulation rows yet.",
        )


def _survival_lab(conn: sqlite3.Connection, settings: Settings) -> None:
    refresh_quant_analytics(conn, settings)
    rows = build_quant_payload(conn)["winner_survival_lab"]
    st.markdown('<div class="section-title">Early Winner Survival Lab</div>', unsafe_allow_html=True)
    st.caption(
        "This studies whether early momentum survived after detection. Pump strength, trap risk, "
        "and survival score are research metrics only, not buy or sell signals."
    )

    setup_counts = Counter(row.get("setup_type") or "Needs More Evidence" for row in rows)
    winner_survivors = sum(
        1
        for row in rows
        if float(row.get("best_return_pct") or 0) >= 50 and float(row.get("survival_score") or 0) >= 50
    )
    accepted_failures = setup_counts.get("Accepted Failure", 0)
    average_survival = (
        sum(float(row.get("survival_score") or 0) for row in rows) / len(rows)
        if rows
        else None
    )
    cols = st.columns(4)
    cols[0].metric("Rows Studied", len(rows))
    cols[1].metric("Winner Survivors", winner_survivors)
    cols[2].metric("Accepted Failures", accepted_failures)
    cols[3].metric("Avg Survival", f"{average_survival:.1f}" if average_survival is not None else "No data")

    st.subheader("Survival Pattern Engine")
    _render_simple_table(
        [
            {
                "Token": row.get("symbol") or "UNKNOWN",
                "Setup": row.get("setup_type") or "",
                "Original label": row.get("original_label") or "",
                "Latest": f"{row.get('latest_horizon') or ''} {pct(row.get('latest_return_pct'))}",
                "Best": pct(row.get("best_return_pct")),
                "Worst": pct(row.get("worst_return_pct")),
                "Pump": f"{float(row.get('pump_strength_score') or 0):.0f}",
                "Trap": f"{float(row.get('trap_risk_score') or 0):.0f}",
                "Survival": f"{float(row.get('survival_score') or 0):.0f}",
                "Survival label": row.get("survival_label") or "",
                "Lesson": row.get("learning_note") or "",
            }
            for row in rows[:80]
        ],
        "No survival rows yet. It fills when tracked candidates have post-detection outcomes.",
    )

    st.subheader("Rules To Test Next")
    rule_counts = Counter(row.get("next_rule_to_test") or "Keep research-only until more horizons mature." for row in rows)
    _render_simple_table(
        [{"Rule experiment": rule, "Rows": str(count)} for rule, count in rule_counts.most_common(12)],
        "No rule experiments yet.",
    )
    st.info(
        "Best current use: compare Missed Winner Study rows against Accepted Failure rows. "
        "The goal is to improve filters without weakening safety."
    )


def _wallet_lab(conn: sqlite3.Connection, settings: Settings) -> None:
    payload = build_wallet_dashboard_payload(conn, settings)
    status = payload["status"]
    st.markdown('<div class="section-title">Smart Wallet Tracker</div>', unsafe_allow_html=True)
    st.caption(
        "Research only. Wallet activity is not a buy signal. Smart Wallet does not mean perfect wallet. "
        "Suspicious Wallet does not prove crime or fraud. Paper tracking only."
    )
    if status.get("warning"):
        st.warning(status["warning"])

    cards = [
        ("Enabled", status["enabled"]),
        ("Provider", status["active_provider"]),
        ("Last Wallet Scan", status["last_wallet_scan"]),
        ("Wallet API Errors", status["wallet_api_errors"]),
        ("Wallets Tracked", status["wallets_tracked"]),
        ("Trades Tracked", status["trades_tracked"]),
        ("Smart Wallets", status["smart_wallets"]),
        ("Useful Wallets", status["useful_wallets"]),
        ("Suspicious Wallets", status["suspicious_wallets"]),
    ]
    for index in range(0, len(cards), 3):
        cols = st.columns(3)
        for col, (label, value) in zip(cols, cards[index : index + 3]):
            col.metric(label, value)

    left, right = st.columns(2)
    with left:
        st.subheader("Wallet Leaderboard")
        st.caption("A simple view of wallets found around scanned tokens. Most new wallets should stay Unproven.")
        _render_simple_table(
            _select_fields(
                payload["leaderboard"],
                ["wallet", "label", "quality", "risk", "tokens", "win_rate", "rug_exposure", "last_active"],
            ),
            "Wallet module has not collected enough data yet.",
        )
    with right:
        st.subheader("Wallet Activity Feed")
        _render_simple_table(
            _select_fields(
                payload["activity_feed"],
                ["time", "wallet", "wallet_label", "side", "token", "amount", "liquidity", "token_risk", "wallet_signal"],
            ),
            "No wallet trade activity saved yet.",
        )

    st.divider()
    signal_col, suspicious_col = st.columns(2)
    with signal_col:
        st.subheader("Wallet Signal Tokens")
        _render_simple_table(
            _select_fields(
                payload["token_signals"],
                ["token", "useful", "suspicious", "net_flow", "wallet_score", "wallet_label", "token_label"],
            ),
            "No token-level wallet signals yet.",
        )
    with suspicious_col:
        st.subheader("Suspicious Wallet Warnings")
        _render_simple_table(
            _select_fields(
                payload["suspicious_wallets"],
                ["wallet", "label", "risk", "tokens", "rug_exposure", "quick_dump", "suspicion", "notes"],
            ),
            "No suspicious wallet rows yet.",
        )

    st.divider()
    st.subheader("Smart Wallet Cluster Detection")
    _render_simple_table(payload["clusters"], "No wallet clusters detected yet.")
    st.subheader("Wallet Memory / Reputation")
    quant_payload = build_quant_payload(conn)
    _render_simple_table(
        [
            {
                "Wallet": f"{str(row.get('wallet_address') or '')[:10]}...",
                "Tier": row.get("memory_tier") or "",
                "Label": row.get("wallet_label") or "",
                "Quality": f"{float(row.get('wallet_quality_score') or 0):.0f}",
                "Risk": f"{float(row.get('wallet_risk_score') or 0):.0f}",
                "Tokens": str(row.get("tokens_traded") or 0),
                "Exits": str(row.get("realized_exits") or 0),
                "Win rate": f"{float(row.get('win_rate') or 0) * 100:.1f}%",
                "Note": row.get("reputation_note") or "",
                "Caution": row.get("caution_note") or "",
            }
            for row in quant_payload["wallet_reputation_memory"][:80]
        ],
        "No wallet memory yet. The wallet module needs tracked wallet history first.",
    )
    st.info(
        "A wallet buying a token does not make the token safe. Hard security filters always override wallet activity. "
        "Smart wallet labels require enough historical evidence and may still be wrong."
    )


def _render_v2_bucket_table(rows: list[dict[str, str]]) -> None:
    if not rows:
        st.info("No tokens in this bucket yet.")
        return
    body = []
    for row in rows[:30]:
        link = ""
        if row.get("url"):
            link = f'<a class="table-action" href="{escape(row["url"])}" target="_blank">Open</a>'
        body.append(
            f"""
            <tr>
              <td><strong>{escape(row.get("symbol", "UNKNOWN"))}</strong><br><span class="muted-cell">{escape(row.get("scan_time", ""))}</span></td>
              <td>{escape(row.get("chain", ""))}</td>
              <td class="num">{escape(row.get("pair_age", ""))}</td>
              <td class="num">{escape(row.get("liquidity", ""))}</td>
              <td class="num">{escape(row.get("volume24h", ""))}</td>
              <td class="num">{escape(row.get("move1h", ""))}</td>
              <td class="num">{escape(row.get("risk_score", ""))}</td>
              <td class="num">{escape(row.get("opportunity_score", ""))}</td>
              <td class="num">{escape(row.get("ten_x_score", ""))}</td>
              <td>{escape(row.get("ten_x_label", ""))}</td>
              <td>{escape(row.get("main_reasons", ""))}</td>
              <td>{link}</td>
            </tr>
            """
        )
    _render_table(
        headers=[
            "Token",
            "Chain",
            "Age",
            "Liquidity",
            "24h Vol",
            "1h Move",
            "Risk",
            "Opp",
            "10x",
            "10x Label",
            "Main Reasons",
            "DEX",
        ],
        body="".join(body),
        min_width=1320,
    )


def _trade_action_links(row: dict[str, object]) -> str:
    chart_url = _chart_url(row)
    swap_url = _swap_url(row)
    links = []
    if chart_url:
        links.append(
            f'<a class="table-action" href="{escape(chart_url)}" target="_blank" rel="noreferrer">Chart</a>'
        )
    if swap_url:
        links.append(
            f'<a class="table-action trade" href="{escape(swap_url)}" target="_blank" rel="noreferrer">Swap Page</a>'
        )
    return '<div class="table-actions">' + "".join(links) + "</div>"


def _chart_url(row: dict[str, object]) -> str:
    chain, pair = _pair_parts(str(row.get("pair_id") or ""))
    if not chain or not pair:
        return ""
    return f"https://dexscreener.com/{quote(chain)}/{quote(pair)}"


def _swap_url(row: dict[str, object]) -> str:
    token = str(row.get("token_address") or "").strip()
    if not token:
        return _chart_url(row)
    chain, _pair = _pair_parts(str(row.get("pair_id") or ""))
    if chain == "solana":
        return f"https://jup.ag/swap/SOL-{quote(token)}"
    chain_map = {
        "base": "base",
        "ethereum": "mainnet",
        "eth": "mainnet",
        "arbitrum": "arbitrum",
        "optimism": "optimism",
        "polygon": "polygon",
    }
    swap_chain = chain_map.get(chain, "base")
    return f"https://app.uniswap.org/swap?chain={quote(swap_chain)}&outputCurrency={quote(token)}"


def _pair_parts(pair_id: str) -> tuple[str, str]:
    if ":" not in pair_id:
        return "", ""
    chain, pair = pair_id.split(":", 1)
    return chain.strip().lower(), pair.strip()


def _manual_review(conn: sqlite3.Connection, records: list[dict], settings: Settings) -> None:
    if not records:
        st.info("No rejected tokens yet.")
        return

    st.markdown('<div class="section-title">Manual Review Workspace</div>', unsafe_allow_html=True)
    top = st.columns([1.25, 1, 0.8, 0.8, 0.72])
    search = top[0].text_input("Search token, symbol, pair, or reason", "", key="manual_review_search")
    reason_options = sorted({reason for record in records for reason in record["rejection_reasons"]})
    selected_reasons = top[1].multiselect("Filter rejection reason", reason_options)
    sort_mode = top[2].selectbox("Sort", ["Newest", "Most failed", "Lowest liquidity", "Closest to pass"])
    near_miss_only = top[3].toggle("Near misses only", value=False)
    apply_filters = top[4].button("Apply", use_container_width=True)

    has_filter_input = not is_blank_input(search) or bool(selected_reasons) or near_miss_only
    if apply_filters and not has_filter_input:
        count = int(st.session_state.get("manual_review_blank_count", 0)) + 1
        st.session_state["manual_review_blank_count"] = count
        response = blank_recovery_response("manual_review", "manual_review_filters", count)
        log_blank_input_event(
            conn,
            page_context=response["page_context"],
            input_type=response["input_type"],
            blank_count_in_session=response["blank_count_in_session"],
            recovery_message=response["message"],
            suggestions=response["suggestions"],
        )
        _render_blank_input_recovery(conn, response)
    elif has_filter_input and int(st.session_state.get("manual_review_blank_count", 0)):
        mark_latest_blank_input_recovered(
            conn,
            page_context="manual_review",
            input_type="manual_review_filters",
            suggestion_clicked="valid_filter",
        )
        st.session_state["manual_review_blank_count"] = 0

    filtered = _filter_records(records, search, selected_reasons, near_miss_only)
    filtered = _sort_records(filtered, sort_mode)
    if not filtered:
        st.warning("No rejected tokens match the current filters.")
        return

    selected_option = st.selectbox(
        "Token details",
        range(len(filtered)),
        format_func=lambda index: _record_label(filtered[index]),
    )
    selected_record = filtered[int(selected_option)]

    _render_review_table(filtered)

    _rejection_detail(selected_record, settings)


def _render_blank_input_recovery(conn: sqlite3.Connection, response: dict) -> None:
    st.markdown(
        f"""
        <div class="blank-input-warning" aria-live="polite">
          {escape(response["message"])}
        </div>
        """,
        unsafe_allow_html=True,
    )
    suggestions = response.get("suggestions", [])[:3]
    if not suggestions:
        return
    cols = st.columns(len(suggestions))
    for index, suggestion in enumerate(suggestions):
        label = str(suggestion.get("label") or "Open")
        action = str(suggestion.get("action") or "")
        if cols[index].button(label, key=f"manual_blank_recovery_{action}_{response['blank_count_in_session']}"):
            mark_latest_blank_input_recovered(
                conn,
                page_context=str(response["page_context"]),
                input_type=str(response["input_type"]),
                suggestion_clicked=action,
            )
            st.session_state["manual_review_blank_count"] = 0
            st.info(f"Starter selected: {label}.")


def _rejection_detail(record: dict, settings: Settings) -> None:
    st.divider()
    title = f"{record['symbol']} on {record['chain_id']}"
    st.subheader(title)
    st.caption(f"Token {record['token_address']} | Pair {record['pair_address']}")

    action_cols = st.columns([0.18, 0.18, 0.64])
    if record.get("dexscreener_url"):
        action_cols[0].link_button("Open DEX Screener", record["dexscreener_url"], use_container_width=True)
    action_cols[1].metric("Failed checks", record["fail_count"])
    action_cols[2].markdown(_reason_chips(record), unsafe_allow_html=True)

    metric_cols = st.columns(4)
    metric_cols[0].metric(
        "Liquidity",
        money(record.get("liquidity_usd")),
        _gap_text(record.get("liquidity_gap_usd"), money),
        delta_color="off",
    )
    metric_cols[1].metric(
        "24h Volume",
        money(record.get("volume_h24_usd")),
        _gap_text(record.get("volume_24h_gap_usd"), money),
        delta_color="off",
    )
    metric_cols[2].metric(
        "Pair Age",
        _hours(record.get("pair_age_hours")),
        _gap_text(record.get("pair_age_gap_hours"), _hours),
        delta_color="off",
    )
    metric_cols[3].metric("1h Move", pct(record.get("price_change_h1_pct")))

    requirement_rows = build_requirement_rows(record, settings)
    st.subheader("Actual vs Required")
    _render_requirement_table(requirement_rows)

    with st.expander("Raw saved data"):
        st.json(record)


def _rejected_audit(rejected_outcomes: pd.DataFrame) -> None:
    if rejected_outcomes.empty:
        st.info("Rejected tokens are saved. 1h and 24h audit rows appear after enough time passes.")
        return
    grouped = (
        rejected_outcomes.groupby("horizon", as_index=False)
        .agg(
            median_net_return_pct=("net_return_pct", "median"),
            avg_net_return_pct=("net_return_pct", "mean"),
            count=("candidate_evaluation_id", "count"),
        )
        .sort_values("horizon")
    )
    _render_simple_table(
        [
            {
                "Horizon": row["horizon"],
                "Median net": pct(row["median_net_return_pct"]),
                "Average net": pct(row["avg_net_return_pct"]),
                "Count": str(int(row["count"])),
            }
            for _, row in grouped.iterrows()
        ],
        empty_message="No rejected outcome rows yet.",
    )
    st.dataframe(rejected_outcomes, use_container_width=True, hide_index=True)


def _analysis_workspace(conn: sqlite3.Connection, settings: Settings) -> None:
    analysis = build_analysis_payload(conn, settings)

    st.markdown('<div class="section-title">Performance Analysis</div>', unsafe_allow_html=True)
    st.caption(
        "These tables separate accepted and rejected tokens, then show what happened after 1h and 24h."
    )

    top_left, top_right = st.columns(2)
    with top_left:
        st.subheader("1h After Rejection")
        _render_analysis_table(analysis["tables"]["rejected_1h"], analysis["summaries"]["rejected_1h"])
    with top_right:
        st.subheader("1h After Acceptance")
        _render_analysis_table(
            analysis["tables"]["accepted_1h"],
            analysis["summaries"]["accepted_1h"],
            empty_message="No accepted signals have matured to 1h yet.",
        )

    bottom_left, bottom_right = st.columns(2)
    with bottom_left:
        st.subheader("24h After Rejection")
        _render_analysis_table(analysis["tables"]["rejected_24h"], analysis["summaries"]["rejected_24h"])
    with bottom_right:
        st.subheader("24h After Acceptance")
        _render_analysis_table(
            analysis["tables"]["accepted_24h"],
            analysis["summaries"]["accepted_24h"],
            empty_message="No accepted signals have matured to 24h yet.",
        )

    st.divider()
    st.markdown('<div class="section-title">Best And Worst Tables</div>', unsafe_allow_html=True)
    best_left, best_right = st.columns(2)
    with best_left:
        st.subheader("Best After Rejection")
        _render_extreme_table(analysis["extremes"]["rejected_best"])
        st.subheader("Worst After Rejection")
        _render_extreme_table(analysis["extremes"]["rejected_worst"])
    with best_right:
        st.subheader("Best After Acceptance")
        _render_extreme_table(
            analysis["extremes"]["accepted_best"],
            empty_message="No accepted winners yet because no token has passed the filters.",
        )
        st.subheader("Worst After Acceptance")
        _render_extreme_table(
            analysis["extremes"]["accepted_worst"],
            empty_message="No accepted losers yet because no token has passed the filters.",
        )

    st.divider()
    st.markdown('<div class="section-title">Acceptance And Rejection Filter Rules</div>', unsafe_allow_html=True)
    st.caption("This is the manual checklist: what a token needs to be accepted, and what makes it rejected.")
    _render_filter_rules(analysis["filter_rules"])


def _render_analysis_table(
    rows: list[dict],
    summary: dict[str, str],
    empty_message: str = "No matured rows yet.",
) -> None:
    summary_html = (
        "<div class='analysis-summary'>"
        f"<span>Rows <strong>{escape(summary.get('count', '0'))}</strong></span>"
        f"<span>Priced <strong>{escape(summary.get('priced', '0'))}</strong></span>"
        f"<span>Median <strong>{escape(summary.get('median', 'No data'))}</strong></span>"
        f"<span>Average <strong>{escape(summary.get('average', 'No data'))}</strong></span>"
        f"<span>Positive <strong>{escape(summary.get('positive_rate', 'No data'))}</strong></span>"
        "</div>"
    )
    st.html(summary_html)
    if not rows:
        st.info(empty_message)
        return

    body = []
    for row in rows[:12]:
        body.append(_analysis_row_html(row, include_horizon=False))
    _render_table(
        headers=["Token", "Net", "Liquidity", "24h Vol", "Age", "Why", "What it means", "DEX"],
        body="".join(body),
        min_width=1100,
    )


def _render_extreme_table(rows: list[dict], empty_message: str = "No rows yet.") -> None:
    if not rows:
        st.info(empty_message)
        return
    body = []
    for row in rows[:10]:
        body.append(_analysis_row_html(row, include_horizon=True, compact=True))
    _render_table(
        headers=["Token", "Horizon", "Net", "Liquidity", "Why", "What it means", "DEX"],
        body="".join(body),
        min_width=980,
    )


def _render_filter_rules(rows: list[dict[str, str]]) -> None:
    body = []
    for row in rows:
        body.append(
            f"""
            <tr>
              <td><strong>{escape(row.get("Rule", ""))}</strong></td>
              <td>{escape(row.get("Accept needs", ""))}</td>
              <td>{escape(row.get("Rejects when", ""))}</td>
              <td>{escape(row.get("Why it matters", ""))}</td>
            </tr>
            """
        )
    _render_table(
        headers=["Rule", "Accept needs", "Rejects when", "Why it matters"],
        body="".join(body),
        min_width=1000,
    )


def _analysis_row_html(row: dict, include_horizon: bool, compact: bool = False) -> str:
    net_value = row.get("net", "")
    net_class = _return_class(row.get("net_return_pct"))
    link = ""
    if row.get("url"):
        link = f'<a class="table-action" href="{escape(str(row["url"]))}" target="_blank">Open</a>'
    token = (
        f"<strong>{escape(str(row.get('symbol') or 'UNKNOWN'))}</strong>"
        f"<br><span class='muted-cell'>{escape(str(row.get('name') or row.get('kind') or ''))}</span>"
    )
    cells = [f"<td>{token}</td>"]
    if include_horizon:
        cells.append(f"<td><span class='muted-cell'>{escape(str(row.get('horizon') or ''))}</span></td>")
    cells.extend(
        [
            f"<td class='num {net_class}'>{escape(str(net_value))}</td>",
            f"<td class='num'>{escape(str(row.get('liquidity') or ''))}</td>",
        ]
    )
    if not compact:
        cells.extend(
            [
                f"<td class='num'>{escape(str(row.get('volume24h') or ''))}</td>",
                f"<td class='num'>{escape(str(row.get('age') or ''))}</td>",
            ]
        )
    cells.extend(
        [
            f"<td>{escape(str(row.get('reason') or ''))}</td>",
            f"<td class='diagnosis-cell'>{escape(str(row.get('diagnosis') or ''))}</td>",
            f"<td>{link}</td>",
        ]
    )
    return f"<tr>{''.join(cells)}</tr>"


def _return_class(value: float | None) -> str:
    if value is None:
        return ""
    if value > 0:
        return "return-good"
    if value < 0:
        return "return-bad"
    return ""


def _config_panel(settings: Settings, accepted_count: int) -> None:
    st.write(
        {
            "db_path": str(settings.db_path),
            "dex_chain": settings.dex_chain,
            "goplus_chain_id": settings.goplus_chain_id,
            "require_goplus": settings.require_goplus,
            "min_liquidity_usd": settings.min_liquidity_usd,
            "min_volume_24h_usd": settings.min_volume_24h_usd,
            "min_pair_age_hours": settings.min_pair_age_hours,
            "max_price_change_h1_pct": settings.max_price_change_h1_pct,
            "clean_momentum_min_h1_pct": settings.clean_momentum_min_h1_pct,
            "volume_spike_multiple": settings.volume_spike_multiple,
            "estimated_cost_pct": settings.estimated_cost_pct,
            "accepted_count": accepted_count,
        }
    )


def _rejected_records(evaluations: pd.DataFrame, settings: Settings) -> list[dict]:
    if evaluations.empty:
        return []
    rejected = evaluations[evaluations["accepted"] == 0]
    return [build_rejection_record(row, settings) for _, row in rejected.iterrows()]


def _filter_records(
    records: list[dict],
    search: str,
    selected_reasons: list[str],
    near_miss_only: bool,
) -> list[dict]:
    needle = search.strip().lower()
    filtered = []
    for record in records:
        if selected_reasons and not set(selected_reasons).intersection(record["rejection_reasons"]):
            continue
        if near_miss_only and record["fail_count"] > 2:
            continue
        searchable = " ".join(
            str(record.get(key, ""))
            for key in ["symbol", "name", "token_address", "pair_address", "reason_text", "chain_id"]
        ).lower()
        if needle and needle not in searchable:
            continue
        filtered.append(record)
    return filtered


def _sort_records(records: list[dict], sort_mode: str) -> list[dict]:
    if sort_mode == "Most failed":
        return sorted(records, key=lambda item: item["fail_count"], reverse=True)
    if sort_mode == "Lowest liquidity":
        return sorted(records, key=lambda item: item.get("liquidity_usd") or 0)
    if sort_mode == "Closest to pass":
        return sorted(records, key=lambda item: (item["fail_count"], item.get("liquidity_gap_usd") or 0))
    return records


def _record_label(record: dict) -> str:
    symbol = record.get("symbol") or "UNKNOWN"
    return f"{symbol} | {record.get('token_short')} | {record.get('reason_text')}"


def _reason_chips(record: dict) -> str:
    chips = "".join(f"<span class='reason-chip'>{reason}</span>" for reason in record["rejection_reasons"])
    if not chips:
        chips = "<span class='reason-chip'>no_reason_recorded</span>"
    return f"<div class='reason-wrap'><strong>Rejected because</strong><div>{chips}</div></div>"


def _overview_status(
    signals: pd.DataFrame,
    outcomes: pd.DataFrame,
    rejected_outcomes: pd.DataFrame,
    evaluations: pd.DataFrame,
    api_events: pd.DataFrame,
    active_api_errors: int,
) -> None:
    accepted = len(signals)
    rejected = int((evaluations["accepted"] == 0).sum()) if not evaluations.empty else 0
    latest_seen = str(evaluations["observed_at"].max()).replace("T", " ").replace("+00:00", " UTC") if not evaluations.empty else "No scan yet"
    stage_title = "Collecting and filtering"
    stage_body = "The scanner is finding real DEX candidates, but none have passed the hard filters yet."
    if accepted:
        stage_title = "Tracking accepted signals"
        stage_body = "Accepted signals exist. The next job is comparing 1h and 24h performance."

    audit_body = "Rejected-token audit is waiting for 1h and 24h tracking rows."
    if len(rejected_outcomes):
        audit_body = "Rejected-token audit has matured rows. Compare rejected vs accepted performance."

    api_body = (
        "No active API errors."
        if active_api_errors == 0
        else f"{active_api_errors} active API error rows need review."
    )
    cards = [
        ("Current stage", stage_title, stage_body),
        ("Last scan seen", latest_seen, f"{rejected} rejected candidates, {accepted} accepted signals."),
        ("Rejected audit", f"{len(rejected_outcomes)} outcomes", audit_body),
        ("API health", "Clean" if active_api_errors == 0 else "Review", api_body),
    ]
    card_html = "".join(
        f"""
        <div class="overview-card">
          <span>{escape(label)}</span>
          <strong>{escape(value)}</strong>
          <p>{escape(body)}</p>
        </div>
        """
        for label, value, body in cards
    )
    st.html(f"<div class='overview-grid'>{card_html}</div>")


def _render_latest_rejections(records: list[dict]) -> None:
    body = []
    for record in records:
        first_reason = record["rejection_reasons"][0] if record.get("rejection_reasons") else "no_reason"
        body.append(
            f"""
            <tr>
              <td><span class="muted-cell">{escape(_short_time(record.get("observed_at")))}</span></td>
              <td><strong>{escape(str(record.get("symbol") or "UNKNOWN"))}</strong></td>
              <td class="num">{escape(money(record.get("liquidity_usd")))}</td>
              <td class="num">{escape(money(record.get("volume_h24_usd")))}</td>
              <td class="num">{escape(_hours(record.get("pair_age_hours")))}</td>
              <td><span class="table-chip">{escape(first_reason)}</span></td>
              <td><a class="table-action" href="{escape(str(record.get("dexscreener_url") or ""))}" target="_blank">Open</a></td>
            </tr>
            """
        )
    _render_table(
        headers=["Seen", "Symbol", "Liquidity", "24h Volume", "Age", "Main Reason", "DEX"],
        body="".join(body),
        min_width=760,
    )


def _render_review_table(records: list[dict]) -> None:
    rows = []
    for record in records:
        reasons = "".join(
            f"<span class='table-chip'>{escape(reason)}</span>" for reason in record.get("rejection_reasons", [])
        )
        rows.append(
            f"""
            <tr>
              <td><span class="muted-cell">{escape(_short_time(record.get("observed_at")))}</span></td>
              <td><strong>{escape(str(record.get("symbol") or "UNKNOWN"))}</strong></td>
              <td><code>{escape(str(record.get("token_short") or ""))}</code></td>
              <td><code>{escape(str(record.get("pair_short") or ""))}</code></td>
              <td class="num">{escape(money(record.get("price_usd")))}</td>
              <td class="num">{escape(money(record.get("liquidity_usd")))}</td>
              <td class="num">{escape(money(record.get("volume_h24_usd")))}</td>
              <td class="num">{escape(_hours(record.get("pair_age_hours")))}</td>
              <td class="center"><span class="fail-badge">{int(record.get("fail_count") or 0)}</span></td>
              <td><div class="table-chip-wrap">{reasons}</div></td>
              <td><a class="table-action" href="{escape(str(record.get("dexscreener_url") or ""))}" target="_blank">Open</a></td>
            </tr>
            """
        )
    _render_table(
        headers=["Seen", "Symbol", "Token", "Pair", "Price", "Liquidity", "24h Volume", "Age", "Fails", "Reasons", "DEX"],
        body="".join(rows),
    )


def _render_requirement_table(rows: list[dict[str, str]]) -> None:
    body_rows = []
    for row in rows:
        status = row.get("Status", "")
        status_class = _status_class(status)
        body_rows.append(
            f"""
            <tr>
              <td><strong>{escape(row.get("Check", ""))}</strong></td>
              <td>{escape(row.get("Actual", ""))}</td>
              <td>{escape(row.get("Required", ""))}</td>
              <td>{escape(row.get("Gap", ""))}</td>
              <td><span class="status-pill {status_class}">{escape(status)}</span></td>
            </tr>
            """
        )
    _render_table(
        headers=["Check", "Actual", "Required", "What was missing", "Status"],
        body="".join(body_rows),
    )


def _render_simple_table(rows: list[dict[str, str]], empty_message: str) -> None:
    if not rows:
        st.info(empty_message)
        return
    headers = list(rows[0].keys())
    body = []
    for row in rows:
        cells = "".join(f"<td>{escape(str(row.get(header, '')))}</td>" for header in headers)
        body.append(f"<tr>{cells}</tr>")
    _render_table(headers=headers, body="".join(body))


def _select_fields(rows: list[dict[str, str]], fields: list[str]) -> list[dict[str, str]]:
    return [{_human_field(field): row.get(field, "") for field in fields} for row in rows]


def _human_field(field: str) -> str:
    return field.replace("_", " ").title()


def _render_table(headers: list[str], body: str, min_width: int = 920) -> None:
    header_html = "".join(f"<th>{escape(header)}</th>" for header in headers)
    st.html(
        f"""
        <div class="elegant-table-wrap" style="--table-min-width: {int(min_width)}px;">
          <table class="elegant-table">
            <thead><tr>{header_html}</tr></thead>
            <tbody>{body}</tbody>
          </table>
        </div>
        """
    )


def _status_class(status: str) -> str:
    normalized = status.strip().lower()
    if normalized == "pass":
        return "status-pass"
    if normalized == "fail":
        return "status-fail"
    if normalized == "skipped":
        return "status-skip"
    return "status-neutral"


def _short_time(value: str | None) -> str:
    if not value:
        return ""
    return value.replace("T", " ").replace("+00:00", " UTC")


def _reason_counts(evaluations: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    rows = []
    rejected = evaluations[evaluations["accepted"] == 0]
    for _, row in rejected.iterrows():
        record = build_rejection_record(row, settings)
        for reason in record["rejection_reasons"]:
            rows.append({"Reason": reason, "Count": 1})
    if not rows:
        return pd.DataFrame(columns=["Reason", "Count"])
    return pd.DataFrame(rows).groupby("Reason", as_index=False)["Count"].sum().sort_values("Count", ascending=False)


def _gap_text(value: float | None, formatter) -> str | None:
    if value is None:
        return None
    if value <= 0:
        return "OK"
    return f"needs {formatter(value)}"


def _hours(value: float | None) -> str:
    if value is None:
        return "Missing"
    return f"{value:.2f}h"


def _style(header_uri: str) -> None:
    css = """
        <style>
        :root {
          --page: #f4f7fb;
          --surface: #ffffff;
          --surface-soft: #f8fafc;
          --line: #d8dee8;
          --text: #111827;
          --muted: #5b6575;
          --teal: #0f766e;
          --amber: #b7791f;
          --blue: #2563eb;
        }
        html, body, [data-testid="stAppViewContainer"] {
          background: var(--page);
          color: var(--text);
        }
        header[data-testid="stHeader"] {
          background: transparent;
          height: 0;
        }
        [data-testid="stToolbar"] {
          display: none;
        }
        .block-container {
          padding-top: 1.1rem;
          max-width: 1500px;
        }
        .hero {
          border: 1px solid #d8dee8;
          border-radius: 8px;
          min-height: 230px;
          padding: 28px 30px;
          margin-bottom: 18px;
          background:
            linear-gradient(90deg, rgba(255, 255, 255, 0.97) 0%, rgba(255, 255, 255, 0.84) 42%, rgba(255, 255, 255, 0.2) 100%),
            url("__HEADER_URI__") center right / cover no-repeat,
            #ffffff;
          box-shadow: 0 16px 42px rgba(15, 23, 42, 0.08);
          display: flex;
          align-items: center;
        }
        .hero-copy {
          max-width: 620px;
        }
        .hero h1 {
          margin: 0;
          color: #111827;
          font-size: 40px;
          line-height: 1.1;
          font-weight: 780;
          letter-spacing: 0;
        }
        .hero p {
          margin: 10px 0 0;
          color: #4b5563;
          font-size: 16px;
          line-height: 1.5;
        }
        .hero-disclaimer {
          color: #8a4b22 !important;
          font-weight: 800;
        }
        .hero-meta {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin-top: 20px;
        }
        .hero-meta span {
          display: inline-flex;
          align-items: center;
          min-height: 30px;
          padding: 6px 10px;
          border-radius: 8px;
          background: rgba(248, 250, 252, 0.9);
          border: 1px solid #d8dee8;
          color: #2f3a4a;
          font-size: 13px;
          font-weight: 650;
        }
        .metric-row {
          margin-bottom: 8px;
        }
        [data-testid="stMetric"] {
          background: #ffffff;
          border: 1px solid #d8dee8;
          border-radius: 8px;
          padding: 14px 16px;
          box-shadow: 0 10px 26px rgba(15, 23, 42, 0.05);
        }
        [data-testid="stMetricValue"] {
          color: #111827;
          font-weight: 760;
        }
        [data-testid="stMetricLabel"] {
          color: #5b6575;
          font-weight: 680;
        }
        [data-testid="stMetricDelta"] {
          color: #475569 !important;
          font-weight: 700;
        }
        [data-testid="stMetricDelta"] svg {
          fill: #64748b !important;
        }
        .section-title {
          margin: 4px 0 14px;
          color: #111827;
          font-size: 22px;
          line-height: 1.25;
          font-weight: 760;
        }
        .analysis-summary {
          display: flex;
          flex-wrap: wrap;
          gap: 8px;
          margin: 2px 0 10px;
        }
        .analysis-summary span {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          min-height: 30px;
          padding: 5px 9px;
          border: 1px solid #d8dee8;
          border-radius: 8px;
          background: #ffffff;
          color: #536174;
          font-size: 12px;
          font-weight: 700;
        }
        .analysis-summary strong {
          color: #111827;
          font-variant-numeric: tabular-nums;
        }
        .reason-wrap {
          min-height: 70px;
          padding: 12px 14px;
          border: 1px solid #d8dee8;
          border-radius: 8px;
          background: #ffffff;
        }
        .reason-wrap strong {
          color: #111827;
          display: block;
          margin-bottom: 8px;
        }
        .blank-input-warning {
          border: 1px solid rgba(245, 158, 11, 0.4);
          background: rgba(245, 158, 11, 0.08);
          color: #92400e;
          border-radius: 8px;
          padding: 10px 12px;
          font-size: 14px;
          font-weight: 760;
          margin: 8px 0 10px;
        }
        .reason-chip {
          display: inline-flex;
          margin: 0 6px 6px 0;
          padding: 6px 9px;
          border-radius: 8px;
          background: #fff7ed;
          border: 1px solid #fed7aa;
          color: #9a3412;
          font-size: 12px;
          font-weight: 700;
        }
        .overview-grid {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 14px;
          margin: 8px 0 22px;
        }
        .overview-card {
          min-height: 138px;
          padding: 16px 17px;
          border: 1px solid #d8dee8;
          border-radius: 8px;
          background: #ffffff;
          box-shadow: 0 12px 30px rgba(15, 23, 42, 0.05);
        }
        .overview-card span {
          display: block;
          margin-bottom: 9px;
          color: #64748b;
          font-size: 12px;
          font-weight: 800;
          text-transform: uppercase;
        }
        .overview-card strong {
          display: block;
          color: #111827;
          font-size: 20px;
          line-height: 1.2;
          font-weight: 800;
        }
        .overview-card p {
          margin: 9px 0 0;
          color: #536174;
          font-size: 13px;
          line-height: 1.45;
        }
        .elegant-table-wrap {
          width: 100%;
          overflow-x: auto;
          margin: 10px 0 18px;
          border: 1px solid #d8dee8;
          border-radius: 8px;
          background: #ffffff;
          box-shadow: 0 12px 30px rgba(15, 23, 42, 0.05);
        }
        .elegant-table {
          width: 100%;
          min-width: var(--table-min-width, 920px);
          border-collapse: separate;
          border-spacing: 0;
          color: #111827;
          font-size: 13px;
          line-height: 1.45;
        }
        .elegant-table thead th {
          position: sticky;
          top: 0;
          z-index: 1;
          padding: 12px 14px;
          background: #f8fafc;
          border-bottom: 1px solid #d8dee8;
          color: #536174;
          font-size: 12px;
          font-weight: 780;
          text-align: left;
          white-space: nowrap;
        }
        .elegant-table tbody td {
          padding: 13px 14px;
          border-bottom: 1px solid #edf1f6;
          vertical-align: middle;
          background: #ffffff;
        }
        .elegant-table tbody tr:last-child td {
          border-bottom: 0;
        }
        .elegant-table tbody tr:hover td {
          background: #f5fbfb;
        }
        .elegant-table code {
          padding: 3px 6px;
          border-radius: 6px;
          background: #f1f5f9;
          color: #334155;
          font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
          font-size: 12px;
        }
        .num {
          text-align: right !important;
          font-variant-numeric: tabular-nums;
          white-space: nowrap;
        }
        .center {
          text-align: center !important;
        }
        .muted-cell {
          color: #64748b;
          white-space: nowrap;
        }
        .table-chip-wrap {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          min-width: 210px;
        }
        .table-chip {
          display: inline-flex;
          align-items: center;
          padding: 4px 8px;
          border-radius: 8px;
          background: #fff7ed;
          border: 1px solid #fed7aa;
          color: #9a3412;
          font-size: 11px;
          font-weight: 720;
          white-space: nowrap;
        }
        .fail-badge {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 28px;
          height: 28px;
          border-radius: 999px;
          background: #fff7ed;
          border: 1px solid #fed7aa;
          color: #9a3412;
          font-weight: 800;
        }
        .table-action {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-height: 32px;
          padding: 6px 11px;
          border: 1px solid #0f766e;
          border-radius: 8px;
          color: #0f766e !important;
          background: #f0fdfa;
          font-weight: 760;
          text-decoration: none !important;
          white-space: nowrap;
        }
        .table-action:hover {
          background: #ccfbf1;
        }
        .table-actions {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          min-width: 140px;
        }
        .table-action.trade {
          border-color: #15803d;
          color: #166534 !important;
          background: #ecfdf3;
        }
        .table-action.trade:hover {
          background: #dcfce7;
        }
        .return-good {
          color: #0f766e !important;
          font-weight: 800;
        }
        .return-bad {
          color: #b42318 !important;
          font-weight: 800;
        }
        .diagnosis-cell {
          min-width: 260px;
          color: #374151;
          line-height: 1.45;
        }
        .status-pill {
          display: inline-flex;
          align-items: center;
          min-height: 26px;
          padding: 4px 9px;
          border-radius: 999px;
          font-size: 11px;
          font-weight: 800;
          white-space: nowrap;
        }
        .status-pass {
          color: #0f766e;
          background: #ecfdf5;
          border: 1px solid #a7f3d0;
        }
        .status-fail {
          color: #b42318;
          background: #fff1f2;
          border: 1px solid #fecdd3;
        }
        .status-skip {
          color: #92400e;
          background: #fffbeb;
          border: 1px solid #fde68a;
        }
        .status-neutral {
          color: #475569;
          background: #f1f5f9;
          border: 1px solid #cbd5e1;
        }
        div[data-testid="stDataFrame"] {
          border: 1px solid #d8dee8;
          border-radius: 8px;
          overflow: hidden;
          box-shadow: 0 10px 24px rgba(15, 23, 42, 0.04);
        }
        div[data-baseweb="tab-list"] {
          gap: 4px;
          border-bottom: 1px solid #d8dee8;
        }
        button[data-baseweb="tab"] {
          border-radius: 8px 8px 0 0;
          padding-left: 14px;
          padding-right: 14px;
        }
        button[data-baseweb="tab"] p {
          color: #536174 !important;
          font-weight: 700;
        }
        button[data-baseweb="tab"][aria-selected="true"] p {
          color: #0f766e !important;
        }
        div[data-testid="stSelectbox"], div[data-testid="stTextInput"], div[data-testid="stMultiSelect"] {
          background: transparent;
        }
        div[data-testid="stTextInput"] div {
          background-color: #ffffff !important;
          color: #111827 !important;
        }
        div[data-testid="stTextInput"] label,
        div[data-testid="stTextInput"] label *,
        div[data-testid="stSelectbox"] label,
        div[data-testid="stSelectbox"] label *,
        div[data-testid="stMultiSelect"] label,
        div[data-testid="stMultiSelect"] label * {
          background: transparent !important;
        }
        div[data-testid="stSelectbox"] label p,
        div[data-testid="stTextInput"] label p,
        div[data-testid="stMultiSelect"] label p,
        div[data-testid="stCheckbox"] label p {
          color: #374151 !important;
          font-weight: 700;
        }
        div[data-baseweb="input"],
        div[data-baseweb="base-input"],
        div[data-baseweb="select"] > div,
        div[data-baseweb="popover"] div[role="listbox"] {
          background: #ffffff !important;
          border-color: #cfd7e3 !important;
          color: #111827 !important;
        }
        input {
          background: #ffffff !important;
          color: #111827 !important;
          caret-color: #0f766e !important;
        }
        div[data-baseweb="select"] span,
        div[data-baseweb="select"] div {
          color: #111827 !important;
        }
        div[data-baseweb="tab-highlight"] {
          background-color: #0f766e !important;
        }
        @media (max-width: 760px) {
          .overview-grid {
            grid-template-columns: 1fr;
          }
          .hero {
            min-height: 260px;
            padding: 22px;
            background:
              linear-gradient(180deg, rgba(255, 255, 255, 0.94) 0%, rgba(255, 255, 255, 0.66) 100%),
              url("__HEADER_URI__") center / cover no-repeat,
              #ffffff;
          }
          .hero h1 {
            font-size: 31px;
          }
        }
        </style>
        """.replace("__HEADER_URI__", header_uri)
    st.markdown(css, unsafe_allow_html=True)


def _asset_data_uri(path: Path) -> str:
    if not path.exists():
        return ""
    mime = "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _read(conn: sqlite3.Connection, query: str) -> pd.DataFrame:
    return pd.read_sql_query(query, conn)


if __name__ == "__main__":
    main()
