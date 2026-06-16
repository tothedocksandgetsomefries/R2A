from __future__ import annotations

from pathlib import Path
from unittest import mock

from r2a.agents.reviewer_agent import run_reviewer_agent
from r2a.core.paths import artifact_dir, report_path
from r2a.core.state import make_initial_state
from r2a.core.user_hints import build_user_hints
from r2a.tools.codex_runner import build_codex_exec_prompt
from r2a.tools.iteration import write_final_report
from r2a.tools.planner_input_builder import build_planner_input
from r2a.tools.source_acquisition import acquire_source, read_source_acquisition
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS


def test_make_initial_state_structures_optional_guidance_urls(tmp_path: Path) -> None:
    state = make_initial_state(
        tmp_path,
        guidance=(
            "Use repo https://github.com/user/paper-impl and dataset "
            "https://huggingface.co/datasets/acme/tiny. Prefer recall and latency."
        ),
        model_weight_urls=["https://huggingface.co/acme/model-weights"],
    )

    hints = state["user_hints"]

    assert hints["origin"] == "user_provided_hint"
    assert hints["source_urls"] == ["https://github.com/user/paper-impl"]
    assert hints["dataset_urls"] == ["https://huggingface.co/datasets/acme/tiny"]
    assert hints["model_weight_urls"] == ["https://huggingface.co/acme/model-weights"]
    assert "recall" in hints["preferred_metrics"]
    assert "latency" in hints["preferred_metrics"]
    assert "Do not treat user guidance as verified paper evidence" in state["extra_context"]


def test_web_build_initial_state_preserves_structured_user_hints() -> None:
    import r2a_web.app as app

    workspace = {
        "workspace_dir": "C:/R2A_WORKSPACES_SAMPLE/run_001",
        "repo_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/repo",
        "paper_path": "C:/R2A_WORKSPACES_SAMPLE/run_001/paper/paper.pdf",
        "goal": "resolved goal",
        "github_repo_url": "https://github.com/user/paper-impl",
        "dataset_downloads": [{"url": "https://example.test/data.tgz", "status": "skipped"}],
        "user_hints": build_user_hints(
            text="optional text with qps",
            dataset_urls=["https://huggingface.co/datasets/acme/tiny"],
        ),
    }

    state = app._build_initial_state(
        workspace,
        guidance="optional text with qps",
        paper_backend="preprocess",
        planner_backend="template",
        engineer_executor="shell",
        manager_backend="rules",
        reviewer_backend="rules",
        auto_approve=True,
        output_language="English",
        auto_iterate=False,
        max_iterations=1,
    )

    assert state["user_hints"]["source_urls"] == ["https://github.com/user/paper-impl"]
    assert "https://huggingface.co/datasets/acme/tiny" in state["user_hints"]["dataset_urls"]
    assert "https://example.test/data.tgz" in state["user_hints"]["dataset_urls"]
    assert "qps" in state["user_hints"]["preferred_metrics"]
    assert state["metadata"]["user_hints"] == state["user_hints"]


def test_optional_guidance_repo_enters_source_candidates_and_dataset_does_not(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    state = make_initial_state(
        tmp_path,
        paper_path=paper,
        user_hints=build_user_hints(
            text="Use provided source and data.",
            source_urls=["https://github.com/user/paper-impl"],
            dataset_urls=["https://huggingface.co/datasets/acme/tiny"],
        ),
    )

    def mock_run(*args, **kwargs):
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "main.py").write_text("print('hint')\n", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "Cloned"
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        updated = acquire_source(state)

    source = read_source_acquisition(tmp_path)
    candidate_urls = [candidate["url"] for candidate in source["candidates"]]

    assert updated["user_hints_path"].endswith("USER_HINTS.json")
    assert "https://github.com/user/paper-impl" in candidate_urls
    assert "https://huggingface.co/datasets/acme/tiny" not in candidate_urls
    selected = source["selected_source"]
    assert selected["origin"] == "user_provided_hint"
    assert source["source_type"] == "user_provided_hint"


def test_planner_input_and_engineer_prompt_include_user_hints(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    state = make_initial_state(
        tmp_path,
        paper_path=paper,
        guidance="Prefer recall. Dataset https://huggingface.co/datasets/acme/tiny",
        github_repo_url="https://github.com/user/paper-impl",
    )
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    state = acquire_source(state)

    bundle = build_planner_input(state)
    report_path(tmp_path, "task").write_text("# TASK_SPEC\n\n## Objective\n\nRun bounded task.\n\n## Allowed Files\n\n.r2a/results/**\n\n## Forbidden Files\n\nNone\n\n## Acceptance Criteria\n\nDone\n\n## Stop Conditions\n\nStop.\n", encoding="utf-8")
    prompt = build_codex_exec_prompt(tmp_path, report_path(tmp_path, "task"))

    assert bundle["user_hints"]["source_urls"] == ["https://github.com/user/paper-impl"]
    assert bundle["optional_guidance"] == bundle["user_hints"]
    assert (tmp_path / ".r2a" / "USER_HINTS.json").exists()
    assert "User Guidance" in prompt
    assert "https://github.com/user/paper-impl" in prompt
    assert "Do not treat user guidance as verified paper evidence" in prompt


def test_reviewer_and_final_reports_show_user_hints_as_unverified_context(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    state = make_initial_state(
        tmp_path,
        paper_path=paper,
        guidance="Dataset hint https://huggingface.co/datasets/acme/tiny",
    )
    _write_minimal_stage_reports(tmp_path)

    reviewed = run_reviewer_agent({**state, "manager_status": "PASS", "engineer_status": "PASS"})
    final_path = write_final_report(reviewed)
    review_text = report_path(tmp_path, "review").read_text(encoding="utf-8")
    final_text = final_path.read_text(encoding="utf-8")

    assert "## User Guidance" in review_text
    assert "https://huggingface.co/datasets/acme/tiny" in review_text
    assert "用户提示 / 源码来源说明" in final_text
    assert "Do not treat user guidance as verified paper evidence" in final_text


def _write_paper_bundle(repo: Path) -> Path:
    paper = repo / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    for key in PAPER_STRUCTURED_KEYS:
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"source_or_artifact_urls":[]}' if key == "paper_output" else f"# {key}\n\nok\n", encoding="utf-8")
    return paper


def _write_minimal_stage_reports(repo: Path) -> None:
    report_path(repo, "task").write_text("# TASK_SPEC\n\n## Objective\n\nDone\n", encoding="utf-8")
    report_path(repo, "experiment_contract").write_text("# EXPERIMENT_CONTRACT\n\nverification_only\n", encoding="utf-8")
    report_path(repo, "execution").write_text("# EXECUTION_REPORT\n\n## Status\n\npassed\n", encoding="utf-8")
    report_path(repo, "check").write_text("# CHECK_REPORT\n\n## Status\n\nPASS\n", encoding="utf-8")
    results = artifact_dir(repo) / "results"
    results.mkdir(parents=True, exist_ok=True)
    (results / "source_verification.csv").write_text(
        "status,artifact_url,source_path,branch,commit,notes\n"
        "PASS,local,.,main,abc,ok\n",
        encoding="utf-8",
    )
