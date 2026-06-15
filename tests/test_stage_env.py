from r2a.tools.stage_env import build_stage_env


def test_openclaw_backend_does_not_inject_ui_api_key(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "existing-env-dummy-key")

    env = build_stage_env(
        stage="engineer",
        backend="openclaw",
        stage_api_keys={"engineer": "ui-dummy-key"},
        stage_api_key_env_vars={"engineer": "DEEPSEEK_API_KEY"},
    )

    assert env is None


def test_codex_backend_still_allows_stage_key_injection() -> None:
    env = build_stage_env(
        stage="planner",
        backend="codex",
        stage_api_keys={"planner": "planner-dummy-key"},
        stage_api_key_env_vars={"planner": "OPENAI_API_KEY"},
    )

    assert env is not None
    assert env["OPENAI_API_KEY"] == "planner-dummy-key"
