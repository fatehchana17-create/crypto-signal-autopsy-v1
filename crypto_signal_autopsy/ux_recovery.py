from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import sqlite3
from typing import Any


@dataclass(frozen=True)
class RecoverySuggestion:
    label: str
    action: str


CONTEXT_SUGGESTIONS: dict[str, tuple[str, list[RecoverySuggestion]]] = {
    "dashboard_overview": (
        "Your input came through empty. Try one:",
        [
            RecoverySuggestion("View latest candidates", "latest_candidates"),
            RecoverySuggestion("Review missed winners", "missed_winners"),
            RecoverySuggestion("Open momentum traps", "momentum_traps"),
        ],
    ),
    "token_search": (
        "Search is empty. Try a token symbol, pair address, or wallet address.",
        [
            RecoverySuggestion("Latest candidates", "latest_candidates"),
            RecoverySuggestion("Momentum traps", "momentum_traps"),
            RecoverySuggestion("Missed winners", "missed_winners"),
        ],
    ),
    "wallet_intelligence": (
        "Wallet search is empty. Paste a wallet address or choose a wallet category.",
        [
            RecoverySuggestion("Tracked wallets", "tracked_wallets"),
            RecoverySuggestion("Suspicious wallets", "suspicious_wallets"),
            RecoverySuggestion("Smart wallet status", "wallet_status"),
        ],
    ),
    "pump_trap_engine": (
        "Your input came through empty. Try one:",
        [
            RecoverySuggestion("Strong pump setups", "high_risk_momentum"),
            RecoverySuggestion("Momentum traps", "momentum_traps"),
            RecoverySuggestion("Explain pump score", "explain_scores"),
        ],
    ),
    "missed_winner_lab": (
        "Your input came through empty. Try one:",
        [
            RecoverySuggestion("+100% missed winners", "missed_winners"),
            RecoverySuggestion("High-risk momentum misses", "high_risk_momentum"),
            RecoverySuggestion("Rejected tokens that pumped", "blocked_winners"),
        ],
    ),
    "paper_candidate_review": (
        "Your input came through empty. Try one:",
        [
            RecoverySuggestion("Failed paper candidates", "paper_failures"),
            RecoverySuggestion("Pending candidates", "pending_candidates"),
            RecoverySuggestion("Explain survival check", "explain_survival"),
        ],
    ),
    "manual_review": (
        "No search or filters selected. Choose a reason, type a token, or use a quick option.",
        [
            RecoverySuggestion("Latest candidates", "latest_candidates"),
            RecoverySuggestion("Momentum traps", "momentum_traps"),
            RecoverySuggestion("Paper failures", "paper_failures"),
        ],
    ),
}

DEFAULT_CONTEXT = (
    "Your input came through empty. Try one:",
    [
        RecoverySuggestion("Analyze a token", "analyze_token"),
        RecoverySuggestion("Review signal performance", "review_signals"),
        RecoverySuggestion("Explain dashboard scores", "explain_scores"),
    ],
)


def is_blank_input(value: str | None) -> bool:
    return value is None or not value.strip()


def blank_recovery_response(
    page_context: str | None,
    input_type: str,
    blank_count_in_session: int = 1,
) -> dict[str, Any]:
    context_key = (page_context or "").strip() or "default"
    base_message, suggestions = CONTEXT_SUGGESTIONS.get(context_key, DEFAULT_CONTEXT)
    count = max(1, int(blank_count_in_session or 1))
    if count >= 3:
        message = "Still empty. Choose a starter to continue."
    elif count == 2:
        message = "Still empty. Pick one option below."
    else:
        message = base_message
    return {
        "status": "blank_input",
        "message": message,
        "page_context": context_key,
        "input_type": input_type,
        "blank_count_in_session": count,
        "suggestions": [suggestion.__dict__ for suggestion in suggestions[:3]],
    }


def log_blank_input_event(
    conn: sqlite3.Connection,
    *,
    page_context: str,
    input_type: str,
    blank_count_in_session: int,
    recovery_message: str,
    suggestions: list[dict[str, str]],
) -> int:
    event_time = _utc_now()
    cursor = conn.execute(
        """
        INSERT INTO ux_blank_input_events (
          event_time, page_context, input_type, blank_count_in_session,
          recovery_message, suggestions, suggestion_clicked, recovered,
          time_to_next_valid_input_seconds
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_time,
            page_context,
            input_type,
            blank_count_in_session,
            recovery_message,
            json.dumps(suggestions, sort_keys=True, separators=(",", ":")),
            "",
            0,
            None,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def mark_latest_blank_input_recovered(
    conn: sqlite3.Connection,
    *,
    page_context: str,
    input_type: str,
    suggestion_clicked: str = "",
) -> bool:
    row = conn.execute(
        """
        SELECT id, event_time
        FROM ux_blank_input_events
        WHERE page_context = ?
          AND input_type = ?
          AND recovered = 0
        ORDER BY id DESC
        LIMIT 1
        """,
        (page_context, input_type),
    ).fetchone()
    if not row:
        return False
    elapsed = _elapsed_seconds(row["event_time"])
    conn.execute(
        """
        UPDATE ux_blank_input_events
        SET suggestion_clicked = ?,
            recovered = 1,
            time_to_next_valid_input_seconds = ?
        WHERE id = ?
        """,
        (suggestion_clicked, elapsed, row["id"]),
    )
    conn.commit()
    return True


def ux_blank_input_event_rows(conn: sqlite3.Connection, limit: int | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT id, event_time, page_context, input_type, blank_count_in_session,
               recovery_message, suggestions, suggestion_clicked, recovered,
               time_to_next_valid_input_seconds
        FROM ux_blank_input_events
        ORDER BY id DESC
    """
    params: tuple[Any, ...] = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (limit,)
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _elapsed_seconds(event_time: str | None) -> float | None:
    if not event_time:
        return None
    try:
        started = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - started.astimezone(timezone.utc)).total_seconds())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
