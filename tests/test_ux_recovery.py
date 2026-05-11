from __future__ import annotations

import sqlite3

from crypto_signal_autopsy import db
from crypto_signal_autopsy.ux_recovery import (
    blank_recovery_response,
    is_blank_input,
    log_blank_input_event,
    mark_latest_blank_input_recovered,
    ux_blank_input_event_rows,
)


def test_blank_recovery_is_contextual_and_limited_to_three_suggestions() -> None:
    response = blank_recovery_response("wallet_intelligence", "wallet_search", 1)

    assert response["status"] == "blank_input"
    assert "Wallet search is empty" in response["message"]
    assert len(response["suggestions"]) == 3
    assert response["suggestions"][0]["action"] == "tracked_wallets"


def test_repeated_blank_does_not_repeat_same_message() -> None:
    first = blank_recovery_response("token_search", "global_dashboard_search", 1)
    third = blank_recovery_response("token_search", "global_dashboard_search", 3)

    assert first["message"] != third["message"]
    assert third["message"] == "Still empty. Choose a starter to continue."


def test_blank_input_metadata_is_logged_without_user_text() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    db.init_db(conn)
    response = blank_recovery_response("manual_review", "manual_review_filters", 1)

    event_id = log_blank_input_event(
        conn,
        page_context=response["page_context"],
        input_type=response["input_type"],
        blank_count_in_session=response["blank_count_in_session"],
        recovery_message=response["message"],
        suggestions=response["suggestions"],
    )

    assert event_id > 0
    rows = ux_blank_input_event_rows(conn)
    assert rows[0]["page_context"] == "manual_review"
    assert "secret token text" not in str(rows[0])
    assert mark_latest_blank_input_recovered(
        conn,
        page_context="manual_review",
        input_type="manual_review_filters",
        suggestion_clicked="valid_filter",
    )
    recovered = ux_blank_input_event_rows(conn)[0]
    assert recovered["recovered"] == 1
    assert recovered["suggestion_clicked"] == "valid_filter"


def test_is_blank_input_accepts_none_and_whitespace() -> None:
    assert is_blank_input(None)
    assert is_blank_input("   ")
    assert not is_blank_input("LO0P")
