from __future__ import annotations

from r2a_web.workspace_state import active_run_autorefresh_off_message, autorefresh_decision


def _session(*, interval: int = 5) -> dict:
    return {
        "workspace": {"repo_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/repo"},
        "active_run_id": "run-1",
        "auto_refresh_interval_seconds": interval,
        "workflow_running": False,
    }


def test_auto_refresh_interval_zero_does_not_refresh(monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a_web.workspace_state.read_run_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("run record should not be read when refresh is off")),
    )

    decision = autorefresh_decision(_session(interval=0), ui_polling_enabled=True)

    assert decision["should_refresh"] is False
    assert decision["reason"] == "auto-refresh disabled; manual refresh only"
    assert decision["interval_seconds"] == 0


def test_auto_refresh_running_run_does_not_schedule_refresh(monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a_web.workspace_state.read_run_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("auto-refresh must not read runtime records")),
    )
    session = _session(interval=5)

    decision = autorefresh_decision(session, ui_polling_enabled=True)

    assert decision["should_refresh"] is False
    assert decision["reason"] == "auto-refresh disabled; manual refresh only"
    assert decision["interval_seconds"] == 0
    assert decision["status"] == ""


def test_auto_refresh_feature_flag_disabled_has_diagnostic(monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a_web.workspace_state.read_run_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("run record should not be read when feature flag blocks polling")),
    )

    decision = autorefresh_decision(_session(interval=5), ui_polling_enabled=False)

    assert decision["should_refresh"] is False
    assert decision["reason"] == "auto-refresh disabled; manual refresh only"


def test_auto_refresh_terminal_grace_does_not_schedule_refresh(monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a_web.workspace_state.read_run_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("auto-refresh must not read runtime records")),
    )
    session = _session(interval=5)

    first = autorefresh_decision(session, ui_polling_enabled=True, terminal_grace_refreshes=2)
    second = autorefresh_decision(session, ui_polling_enabled=True, terminal_grace_refreshes=2)
    third = autorefresh_decision(session, ui_polling_enabled=True, terminal_grace_refreshes=2)

    assert first["should_refresh"] is False
    assert first["reason"] == "auto-refresh disabled; manual refresh only"
    assert first["terminal_grace_remaining"] == 0
    assert second["should_refresh"] is False
    assert second["terminal_grace_remaining"] == 0
    assert third["should_refresh"] is False
    assert third["reason"] == "auto-refresh disabled; manual refresh only"


def test_auto_refresh_decision_does_not_write_workflow_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "r2a_web.workspace_state.read_run_record",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("auto-refresh must not read runtime records")),
    )
    session = _session(interval=10)

    decision = autorefresh_decision(session, ui_polling_enabled=True)

    assert decision["should_refresh"] is False
    assert session["workflow_running"] is False
    assert "workflow_result" not in session
    assert "workflow_error" not in session


def test_active_run_auto_refresh_off_has_warning_message(monkeypatch) -> None:
    monkeypatch.setattr("r2a_web.workspace_state.read_run_record", lambda repo_path, run_id: {"status": "running"})
    session = _session(interval=0)

    message = active_run_autorefresh_off_message(session)

    assert "Status refresh: Manual" in message
    assert "Refresh Status" in message
