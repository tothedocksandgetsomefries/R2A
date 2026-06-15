from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).resolve().parents[1] / "r2a_web" / "app.py"


def test_streamlit_first_render_has_no_exception() -> None:
    app = AppTest.from_file(str(APP_PATH))

    app.run(timeout=60)

    assert not app.exception
    assert [title.value for title in app.title] == ["R2A"]
    assert any(header.value == "Run Settings" for header in app.header)
    rendered_sections = {item.value for item in app.subheader}
    assert {
        "1. Upload Paper",
        "3. Workspace",
        "4. Run Workflow",
        "Workflow Review",
    }.issubset(rendered_sections)
    assert any(select.label == "Reviewer backend" for select in app.selectbox)


def test_background_run_supplies_hidden_reviewer_and_iteration_defaults(tmp_path, monkeypatch) -> None:
    import r2a_web.app as app

    repo = tmp_path / "repo"
    repo.mkdir()
    app.st.session_state.workspace = {
        "workspace_dir": str(tmp_path),
        "repo_path": str(repo),
        "paper_path": "",
        "goal": "smoke",
    }
    captured: dict[str, object] = {}

    def fake_build_initial_state(workspace: dict, **kwargs):
        captured.update(kwargs)
        return {
            "repo_path": workspace["repo_path"],
            "workspace_dir": workspace["workspace_dir"],
            "paper_backend": kwargs.get("paper_backend", "preprocess"),
            "auto_iterate": kwargs["auto_iterate"],
            "max_iterations": kwargs["max_iterations"],
            "wsl_distro": "Ubuntu",
        }

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            self.target = target
            self.args = args
            self.daemon = daemon

        def start(self) -> None:
            captured["thread_started"] = True

    monkeypatch.setattr(app, "_build_initial_state", fake_build_initial_state)
    monkeypatch.setattr(app, "new_run_id", lambda: "run_smoke")
    monkeypatch.setattr(app, "create_run_record", lambda *args, **kwargs: None)
    monkeypatch.setattr(app.threading, "Thread", FakeThread)

    app._start_workflow_background(
        guidance="",
        paper_backend="preprocess",
        planner_backend="template",
        engineer_executor="mock",
        manager_backend="rules",
        auto_approve=True,
        output_language="English",
        target_reproduction_level="L4_reduced_paper_aligned",
        download_budget_gb=0,
        allow_official_dataset_download=False,
        allow_full_benchmark=False,
        allow_external_baselines=False,
        codex_executable_path="codex",
        claude_executable_path="ccr",
        codex_stage_timeout=300,
        engineer_execution_environment="windows",
        wsl_distro="Ubuntu",
        wsl_cache_dir="C:/R2A_CACHE_SAMPLE",
        stage_api_keys={},
        stage_api_key_env_vars={},
    )

    assert captured["reviewer_backend"] == app.DEFAULT_REVIEWER_BACKEND
    assert captured["auto_iterate"] == app.DEFAULT_AUTO_ITERATE
    assert captured["max_iterations"] == app.DEFAULT_MAX_ITERATIONS_MINIMAL
    assert captured["thread_started"] is True
