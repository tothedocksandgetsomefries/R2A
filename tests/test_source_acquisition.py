from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

from r2a.core.paths import artifact_dir, report_path
from r2a.core.state import make_initial_state
from r2a.tools.source_acquisition import (
    _normalize_github_clone_url,
    acquire_source,
    discover_source,
    read_source_acquisition,
)
from r2a.tools.workflow_decision import PAPER_STRUCTURED_KEYS, aggregate_terminal_decision
from r2a.workflow.router import route_after_paper


def test_paper_ready_but_source_missing_requests_source(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    state = make_initial_state(tmp_path, paper_path=paper)

    state = acquire_source(state)
    decision = aggregate_terminal_decision(state)

    assert read_source_acquisition(tmp_path)["source_status"] == "not_found"
    assert decision["typed_decision"] == "request_source"
    assert route_after_paper(state) == "final"


def test_existing_local_source_is_recorded_before_planner(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    state = make_initial_state(tmp_path, paper_path=paper)

    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    assert source["source_status"] == "available"
    assert source["local_path"] == str(tmp_path.resolve())
    assert report_path(tmp_path, "source_acquisition").exists()
    assert (tmp_path / ".r2a" / "results" / "source_verification.csv").exists()


def test_artifact_source_directory_takes_priority_over_clone(tmp_path: Path) -> None:
    """Test that existing artifact source directory is recognized before attempting clone."""
    paper = _write_paper_bundle(tmp_path)

    # Create artifact source directory with meaningful content
    artifact_source = artifact_dir(tmp_path) / "artifacts" / "source"
    artifact_source.mkdir(parents=True, exist_ok=True)
    (artifact_source / "README.md").write_text("# Project", encoding="utf-8")
    (artifact_source / "main.py").write_text("print('hello')", encoding="utf-8")

    # Add a candidate URL to simulate that we would try to clone
    state = make_initial_state(tmp_path, paper_path=paper)
    state["official_source_url"] = "https://github.com/example/repo"

    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    # Should be available without attempting clone
    assert source["source_status"] == "available"
    assert source["local_path"] == str(artifact_source.resolve())
    assert source["source_type"] == "official"
    assert source["blockers"] == []


def test_clone_nonzero_exit_but_usable_directory_returns_available(tmp_path: Path) -> None:
    """CRITICAL TEST: git clone returns non-zero but directory is usable -> available."""
    paper = _write_paper_bundle(tmp_path)

    # Add explicit official source phrase
    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "Our code is available at https://github.com/example/repo\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)
    state["official_source_url"] = "https://github.com/example/repo"

    # Mock git clone to return non-zero but create usable directory
    def mock_run(*args, **kwargs):
        # Create the target directory with meaningful source
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "README.md").write_text("# Cloned Project", encoding="utf-8")
        (target / "setup.py").write_text("from setuptools import setup", encoding="utf-8")

        # Return non-zero exit code
        result = mock.MagicMock()
        result.returncode = 1  # Non-zero but directory is usable
        result.stdout = ""
        result.stderr = "warning: some warning"
        return result

    # Patch both subprocess.run and shutil.which in the source_acquisition module
    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        state = acquire_source(state)

    source = read_source_acquisition(tmp_path)

    # CRITICAL: Should be available despite non-zero exit code
    assert source["source_status"] == "available", f"Expected available, got {source['source_status']}"
    assert source["local_path"] != ""
    assert source["blockers"] == []
    # Should have warning about non-zero exit code
    assert any("non-zero" in w.lower() for w in source.get("warnings", []))


def test_clone_failure_with_unusable_directory_returns_clone_failed(tmp_path: Path) -> None:
    """Test that clone failure with unusable directory returns clone_failed, not missing."""
    paper = _write_paper_bundle(tmp_path)

    # Add explicit official source phrase
    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "Our implementation is available at https://github.com/example/repo\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)
    repo_url = "https://github.com/example/repo"
    state["official_source_url"] = repo_url

    # Mock git clone to fail completely
    def mock_run(*args, **kwargs):
        result = mock.MagicMock()
        result.returncode = 128
        result.stdout = ""
        result.stderr = "fatal: repository not found"
        return result

    # Patch both subprocess.run and shutil.which
    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        state = acquire_source(state)

    source = read_source_acquisition(tmp_path)

    # Should be clone_failed, NOT missing
    assert source["source_status"] == "clone_failed"
    # CRITICAL: repo_url must be preserved
    assert source["repo_url"] == repo_url
    assert source["local_path"] is None
    # Should have blockers
    assert len(source["blockers"]) > 0
    assert source["blockers"][0]["reason_code"] == "OFFICIAL_SOURCE_CLONE_FAILED"


def test_github_tree_url_normalizes_repo_and_branch() -> None:
    normalized = _normalize_github_clone_url("https://github.com/rutgers-db/DynamicSegmentGraph/tree/release_version")

    assert normalized["repo_url"] == "https://github.com/rutgers-db/DynamicSegmentGraph"
    assert normalized["branch"] == "release_version"
    assert normalized["subpath"] is None
    assert normalized["reason"] == "github_tree_url"
    assert normalized["normalized"] is True


def test_github_normalization_keeps_root_and_git_urls_cloneable() -> None:
    root = _normalize_github_clone_url("https://github.com/owner/repo")
    dot_git = _normalize_github_clone_url("https://github.com/owner/repo.git")

    assert root["repo_url"] == "https://github.com/owner/repo"
    assert root["branch"] is None
    assert root["reason"] == "github_repo_root"
    assert dot_git["repo_url"] == "https://github.com/owner/repo.git"
    assert dot_git["branch"] is None
    assert dot_git["reason"] == "github_repo_root"


def test_github_blob_url_extracts_repo_branch_and_records_subpath() -> None:
    normalized = _normalize_github_clone_url("https://github.com/owner/repo/blob/main/path/to/file.py")

    assert normalized["repo_url"] == "https://github.com/owner/repo"
    assert normalized["branch"] == "main"
    assert normalized["subpath"] == "path/to/file.py"
    assert normalized["reason"] == "github_blob_url"
    assert "ignored for clone" in normalized["diagnostic"]


def test_github_tree_url_trims_trailing_punctuation() -> None:
    normalized = _normalize_github_clone_url("https://github.com/owner/repo/tree/main).")

    assert normalized["repo_url"] == "https://github.com/owner/repo"
    assert normalized["branch"] == "main"
    assert normalized["reason"] == "github_tree_url"


def test_github_tree_url_strips_query_and_fragment() -> None:
    with_query = _normalize_github_clone_url("https://github.com/owner/repo/tree/main?tab=readme")
    with_fragment = _normalize_github_clone_url("https://github.com/owner/repo/tree/main#readme")

    assert with_query["repo_url"] == "https://github.com/owner/repo"
    assert with_query["branch"] == "main"
    assert with_fragment["repo_url"] == "https://github.com/owner/repo"
    assert with_fragment["branch"] == "main"


def test_non_github_url_is_not_rewritten() -> None:
    url = "https://gitlab.com/owner/repo/tree/main"
    normalized = _normalize_github_clone_url(url)

    assert normalized["repo_url"] == url
    assert normalized["branch"] is None
    assert normalized["normalized"] is False
    assert normalized["reason"] == "non_github_url"


def test_github_tree_url_clone_uses_branch_and_normalized_repo(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    url = "https://github.com/rutgers-db/DynamicSegmentGraph/tree/release_version"
    report_path(tmp_path, "paper_context").write_text(
        "# Paper Context\n\n"
        f"Our artifact is available at {url}\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, paper_path=paper)
    state["official_source_url"] = url
    captured: dict[str, list[str]] = {}

    def mock_run(command, *args, **kwargs):
        captured["command"] = list(command)
        target = Path(command[-1])
        target.mkdir(parents=True, exist_ok=True)
        (target / "main.py").write_text("print('official')\n", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "Cloned successfully"
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        acquire_source(state)

    command = captured["command"]
    source = read_source_acquisition(tmp_path)

    assert command[:6] == ["git", "clone", "--depth", "1", "--branch", "release_version"]
    assert command[6] == "https://github.com/rutgers-db/DynamicSegmentGraph"
    assert "/tree/release_version" not in command
    assert source["source_status"] == "available"
    assert source["repo_url"] == "https://github.com/rutgers-db/DynamicSegmentGraph"
    assert source["source_url_normalization"]["original_url"] == url
    assert source["source_url_normalization"]["branch"] == "release_version"


def test_no_url_and_no_local_source_returns_not_found(tmp_path: Path) -> None:
    """Test that missing source without URL returns source_status=not_found."""
    paper = _write_paper_bundle(tmp_path)
    state = make_initial_state(tmp_path, paper_path=paper)
    # No URLs and no local source

    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    assert source["source_status"] == "not_found"
    assert source["repo_url"] is None
    assert source["local_path"] is None
    assert len(source["blockers"]) > 0
    # CRITICAL: Should use OFFICIAL_SOURCE_NOT_FOUND, not CLONE_FAILED
    assert source["blockers"][0]["reason_code"] == "OFFICIAL_SOURCE_NOT_FOUND"


def test_no_empty_warnings_in_output(tmp_path: Path) -> None:
    """Test that no empty warnings are written to JSON."""
    paper = _write_paper_bundle(tmp_path)
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    state = make_initial_state(tmp_path, paper_path=paper)

    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    # All warnings should be non-empty strings
    for warning in source.get("warnings", []):
        assert warning and warning.strip(), f"Found empty warning: {warning!r}"


def test_available_source_includes_git_metadata(tmp_path: Path) -> None:
    """Test that available source includes commit and branch when possible."""
    paper = _write_paper_bundle(tmp_path)
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")

    # Create a git repo in tmp_path
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=False)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, capture_output=True, check=False)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=False)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=False)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, capture_output=True, check=False)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/example/repo"], cwd=tmp_path, capture_output=True, check=False)

    state = make_initial_state(tmp_path, paper_path=paper)
    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    assert source["source_status"] == "available"
    # Commit and branch should be populated
    assert source.get("commit") is not None
    assert source.get("branch") is not None


def test_recover_from_previous_missing_status(tmp_path: Path) -> None:
    """Test that existing artifact source directory is recognized even if previous run wrote missing."""
    paper = _write_paper_bundle(tmp_path)

    # Create artifact source directory
    artifact_source = artifact_dir(tmp_path) / "artifacts" / "source"
    artifact_source.mkdir(parents=True, exist_ok=True)
    (artifact_source / "README.md").write_text("# Project", encoding="utf-8")
    (artifact_source / "code.py").write_text("x = 1", encoding="utf-8")

    # Write a stale SOURCE_ACQUISITION.json with missing status
    source_path = report_path(tmp_path, "source_acquisition")
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text('{"source_status": "missing"}', encoding="utf-8")

    state = make_initial_state(tmp_path, paper_path=paper)
    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    # Should recover to available
    assert source["source_status"] == "available"
    assert source["local_path"] == str(artifact_source.resolve())
    assert source["blockers"] == []


# =============================================================================
# NEW TESTS: URL Classification and Selection
# =============================================================================

def test_explicit_official_code_url_auto_clones(tmp_path: Path) -> None:
    """Test: Paper says 'Our code is available at' -> should auto clone."""
    paper = _write_paper_bundle(tmp_path)

    # Write paper context with explicit official code statement
    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "Our code is available at https://github.com/acme/paper-code\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)

    # Mock git clone to succeed
    def mock_run(*args, **kwargs):
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "README.md").write_text("# Paper Code", encoding="utf-8")
        (target / "main.py").write_text("print('official')", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "Cloned successfully"
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        state = acquire_source(state)

    source = read_source_acquisition(tmp_path)

    # Should be available
    assert source["source_status"] == "available"
    # Should have candidates with classification
    assert "candidates" in source
    # Find the official candidate
    official = next((c for c in source["candidates"] if c["url"] == "https://github.com/acme/paper-code"), None)
    assert official is not None
    assert official["candidate_type"] == "official_implementation_repo"
    assert official["origin"] == "paper_artifact"
    assert official["classification"] == "official"
    assert official["confidence"] == "high"


def test_faiss_dependency_url_not_auto_cloned(tmp_path: Path) -> None:
    """Test: Paper says 'We compare with FAISS' -> should NOT auto clone FAISS."""
    paper = _write_paper_bundle(tmp_path)

    # Write paper context with FAISS as baseline
    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "We compare with FAISS: https://github.com/facebookresearch/faiss\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)

    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    # Should be implementation-missing, NOT clone_failed
    assert source["source_status"] == "base_repo_available_implementation_missing"
    assert source["repo_url"] is None
    assert source["local_path"] is None

    # Should have candidates with correct classification
    assert "candidates" in source
    faiss_candidate = next((c for c in source["candidates"] if "faiss" in c["url"].lower()), None)
    assert faiss_candidate is not None
    assert faiss_candidate["candidate_type"] in {"official_base_repo", "dependency_repo"}
    assert faiss_candidate["classification"] == "related_or_dependency"

    # Blocker should be OFFICIAL_SOURCE_NOT_FOUND
    assert source["blockers"][0]["reason_code"] == "BASE_REPO_AVAILABLE_IMPLEMENTATION_MISSING"


def test_benchmark_repo_not_auto_cloned(tmp_path: Path) -> None:
    """Test: Benchmark URL should not be auto-cloned as official source."""
    paper = _write_paper_bundle(tmp_path)

    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "We evaluate on benchmark https://github.com/spcl/fanns-benchmark\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)
    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    # Should be not_found; benchmark repos are not source implementation repos.
    assert source["source_status"] == "not_found"

    # Candidate should be classified as benchmark or related_or_dependency
    benchmark_candidate = next((c for c in source["candidates"] if "benchmark" in c["url"].lower() or "fanns" in c["url"].lower()), None)
    if benchmark_candidate:
        assert benchmark_candidate["candidate_type"] in ("benchmark_repo", "dependency_repo")


def test_huggingface_dataset_not_treated_as_source(tmp_path: Path) -> None:
    """Test: HuggingFace dataset/model URLs should not be cloned as source."""
    paper = _write_paper_bundle(tmp_path)

    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "Dataset is available at https://huggingface.co/datasets/xxx\n"
        "Model checkpoint: https://huggingface.co/yyy/model\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)
    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    # Should be not_found
    assert source["source_status"] == "not_found"

    # Check classification
    for candidate in source.get("candidates", []):
        if "huggingface.co/datasets" in candidate["url"]:
            assert candidate["candidate_type"] == "dataset_or_model"
        elif "huggingface.co" in candidate["url"]:
            assert candidate["candidate_type"] in ("dataset_or_model", "unknown_candidate")


def test_multiple_candidates_selects_high_confidence_official(tmp_path: Path) -> None:
    """Test: When multiple URLs present, only clone high-confidence official."""
    paper = _write_paper_bundle(tmp_path)

    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "Our code is available at https://github.com/acme/paper-code\n\n"
        "## Baselines\n\n"
        "We use FAISS as a baseline: https://github.com/facebookresearch/faiss\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)

    # Mock git clone
    def mock_run(*args, **kwargs):
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "README.md").write_text("# Paper Code", encoding="utf-8")
        (target / "main.py").write_text("print('official')", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "Cloned"
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        state = acquire_source(state)

    source = read_source_acquisition(tmp_path)

    # Should be available
    assert source["source_status"] == "available"

    # Should have selected the official source
    assert source.get("selected_source") is not None
    assert source["selected_source"]["url"] == "https://github.com/acme/paper-code"

    # FAISS should be in candidates but classified as dependency
    # Note: FAISS is a known dependency repository, so it should be classified as related_or_dependency
    faiss_candidate = next((c for c in source["candidates"] if "faiss" in c["url"].lower()), None)
    assert faiss_candidate is not None
    # FAISS should be classified as base/dependency because it's a known base repo
    assert faiss_candidate["candidate_type"] in ("official_base_repo", "dependency_repo"), \
        f"Expected base/dependency, got {faiss_candidate['candidate_type']}"


def test_no_high_confidence_official_does_not_clone(tmp_path: Path) -> None:
    """Test: Multiple GitHub URLs but no official phrase -> do not clone."""
    paper = _write_paper_bundle(tmp_path)

    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "We use FAISS https://github.com/facebookresearch/faiss\n"
        "We compare with NHQ https://github.com/AshenOn3/NHQ\n"
        "Built on PyTorch https://github.com/pytorch/pytorch\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)
    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    # Should report base/dependency availability without selecting an implementation.
    assert source["source_status"] == "base_repo_available_implementation_missing"
    assert source["selected_source"] is None

    # All candidates should be dependencies
    for candidate in source.get("candidates", []):
        if "github.com" in candidate["url"]:
            assert candidate["candidate_type"] in ("official_base_repo", "dependency_repo", "unknown_candidate")


def test_candidates_preserved_in_source_acquisition_json(tmp_path: Path) -> None:
    """Test: All candidates are preserved in SOURCE_ACQUISITION.json."""
    paper = _write_paper_bundle(tmp_path)

    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "Our code: https://github.com/acme/paper-code\n"
        "Baseline: https://github.com/facebookresearch/faiss\n"
        "Dataset: https://huggingface.co/datasets/xxx\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)

    # Mock git clone
    def mock_run(*args, **kwargs):
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "main.py").write_text("print('ok')", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        state = acquire_source(state)

    source = read_source_acquisition(tmp_path)

    # All three URLs should be in candidates
    candidate_urls = [c["url"] for c in source.get("candidates", [])]
    assert any("acme/paper-code" in url for url in candidate_urls)
    assert any("faiss" in url.lower() for url in candidate_urls)
    assert any("huggingface.co/datasets" in url for url in candidate_urls)


def test_selection_reason_documented(tmp_path: Path) -> None:
    """Test: selection_reason is documented when source is found or not found."""
    paper = _write_paper_bundle(tmp_path)

    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "Our implementation is available at https://github.com/acme/paper-code\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)

    def mock_run(*args, **kwargs):
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "main.py").write_text("print('ok')", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        state = acquire_source(state)

    source = read_source_acquisition(tmp_path)

    # Should have selection_reason
    assert "selected_source" in source
    assert source["selected_source"] is not None
    assert "selection_reason" in source["selected_source"]
    assert source["selected_source"]["selection_reason"]


def test_not_found_includes_selection_reason(tmp_path: Path) -> None:
    """Test: When source not found, selection_reason explains why."""
    paper = _write_paper_bundle(tmp_path)

    paper_context = report_path(tmp_path, "paper_context")
    paper_context.write_text(
        "# Paper Context\n\n"
        "We compare with FAISS https://github.com/facebookresearch/faiss\n",
        encoding="utf-8"
    )

    state = make_initial_state(tmp_path, paper_path=paper)
    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    # Should have selection_reason explaining why no source was selected
    assert source["source_status"] == "base_repo_available_implementation_missing"
    assert source["selected_source"] is None
    assert "selection_reason" in source
    assert "official" in source["selection_reason"].lower() or "confidence" in source["selection_reason"].lower()


def test_acorn_faiss_base_repo_is_not_official_implementation(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    report_path(tmp_path, "paper_reproduction_card").write_text(
        "# PAPER_REPRODUCTION_CARD\n\n"
        "Artifact URL: Not available (implementation described in FAISS codebase but no repository link).\n\n"
        "ACORN implementation: implemented in modified FAISS, no fork, branch, commit, artifact package, or paper-specific repository URL provided.\n\n"
        "FAISS base library: https://github.com/facebookresearch/faiss\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, paper_path=paper)

    state = acquire_source(state)
    source = read_source_acquisition(tmp_path)

    assert source["source_status"] == "base_repo_available_implementation_missing"
    faiss_candidate = next(c for c in source["candidates"] if "facebookresearch/faiss" in c["url"].lower())
    assert faiss_candidate["candidate_type"] == "official_base_repo"
    assert faiss_candidate["classification"] == "related_or_dependency"
    assert faiss_candidate["selected"] is False
    assert source["selected_source"] is None
    assert source["base_repo_candidates"]


def test_artifact_not_available_does_not_promote_nearby_baseline_links(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    report_path(tmp_path, "paper_context").write_text(
        "# Paper Context\n\n"
        "Artifact URL: Not available.\n\n"
        "Baselines and dependencies mentioned nearby:\n"
        "- FAISS https://github.com/facebookresearch/faiss\n"
        "- DiskANN https://github.com/microsoft/DiskANN\n"
        "- NHQ https://github.com/AshenOn3/NHQ\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, paper_path=paper)

    acquire_source(state)
    source = read_source_acquisition(tmp_path)

    assert source["source_status"] in {"base_repo_available_implementation_missing", "not_found"}
    for candidate in source["candidates"]:
        assert candidate["candidate_type"] != "official_implementation_repo"
        assert candidate["selected"] is False


def test_arxiv_not_available_does_not_block_artifact_url_label(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    repo_url = "https://github.com/spcl/fanns-benchmark"
    report_path(tmp_path, "paper_context").write_text(
        "# Paper Context\n\n"
        "- **arXiv/DOI**: Not available\n"
        f"- **Artifact URL**: {repo_url}\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, paper_path=paper)

    def mock_run(*args, **kwargs):
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "main.py").write_text("print('official')\n", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "Cloned"
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        acquire_source(state)

    source = read_source_acquisition(tmp_path)
    candidate = next(c for c in source["candidates"] if c["url"] == repo_url)

    assert source["source_status"] == "available"
    assert candidate["candidate_type"] == "official_implementation_repo"
    assert candidate["classification"] == "official"
    assert candidate["confidence"] == "high"
    assert candidate["selected"] is True


def test_code_label_next_line_promotes_only_github_repo(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    repo_url = "https://github.com/spcl/fanns-benchmark"
    dataset_url = "https://huggingface.co/datasets/SPCL/arxiv-for-fanns-small"
    report_path(tmp_path, "paper_context").write_text(
        "# Paper Context\n\n"
        "Code:\n"
        f"{repo_url}\n\n"
        "Datasets:\n"
        f"{dataset_url}\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, paper_path=paper)

    def mock_run(*args, **kwargs):
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "main.py").write_text("print('official')\n", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "Cloned"
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        acquire_source(state)

    source = read_source_acquisition(tmp_path)
    github_candidate = next(c for c in source["candidates"] if c["url"] == repo_url)
    dataset_candidate = next(c for c in source["candidates"] if c["url"] == dataset_url)

    assert source["source_status"] == "available"
    assert github_candidate["candidate_type"] == "official_implementation_repo"
    assert github_candidate["selected"] is True
    assert dataset_candidate["candidate_type"] == "dataset_or_model"
    assert dataset_candidate["classification"] == "dataset_or_model"
    assert dataset_candidate["selected"] is False


def test_artifact_url_not_available_remains_negative(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    report_path(tmp_path, "paper_context").write_text(
        "# Paper Context\n\n"
        "Artifact URL: Not available\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, paper_path=paper)

    acquire_source(state)
    source = read_source_acquisition(tmp_path)

    assert source["source_status"] == "not_found"
    assert source["selected_source"] is None
    assert all(c["candidate_type"] != "official_implementation_repo" for c in source["candidates"])


def test_unlabeled_dependency_github_url_is_not_official_source(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    repo_url = "https://github.com/facebookresearch/faiss"
    report_path(tmp_path, "paper_context").write_text(
        "# Paper Context\n\n"
        f"We use FAISS: {repo_url}\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, paper_path=paper)

    acquire_source(state)
    source = read_source_acquisition(tmp_path)
    candidate = next(c for c in source["candidates"] if c["url"] == repo_url)

    assert source["source_status"] == "base_repo_available_implementation_missing"
    assert candidate["candidate_type"] in {"official_base_repo", "dependency_repo"}
    assert candidate["classification"] == "related_or_dependency"
    assert candidate["selected"] is False


def test_code_label_detection_is_not_repo_specific(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    repo_url = "https://github.com/owner/paper-code"
    report_path(tmp_path, "paper_context").write_text(
        "# Paper Context\n\n"
        f"Code: {repo_url}\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, paper_path=paper)

    def mock_run(*args, **kwargs):
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "main.py").write_text("print('official')\n", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "Cloned"
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        acquire_source(state)

    source = read_source_acquisition(tmp_path)
    candidate = next(c for c in source["candidates"] if c["url"] == repo_url)

    assert source["source_status"] == "available"
    assert candidate["candidate_type"] == "official_implementation_repo"
    assert candidate["classification"] == "official"
    assert candidate["selected"] is True


def test_user_provided_repo_hint_is_selected_with_provenance(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    report_path(tmp_path, "paper_context").write_text(
        "# Paper Context\n\n"
        "A low-confidence related link appears here https://github.com/facebookresearch/faiss\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, paper_path=paper)
    state["user_hints"] = {
        "source_urls": ["https://github.com/user/paper-impl"],
        "dataset_urls": ["https://huggingface.co/datasets/example/data"],
    }

    def mock_run(*args, **kwargs):
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "main.py").write_text("print('hint source')\n", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = "Cloned"
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        acquire_source(state)

    source = read_source_acquisition(tmp_path)

    assert source["source_status"] == "available"
    assert source["source_type"] == "user_provided_hint"
    selected = source["selected_source"]
    assert selected["url"] == "https://github.com/user/paper-impl"
    assert selected["origin"] == "user_provided_hint"
    assert selected["candidate_type"] == "official_implementation_repo"
    assert "not verified paper evidence" in next(c for c in source["candidates"] if c["url"] == selected["url"])["evidence"][0]


def test_candidate_schema_records_selection_rationale(tmp_path: Path) -> None:
    paper = _write_paper_bundle(tmp_path)
    report_path(tmp_path, "paper_context").write_text(
        "# Paper Context\n\n"
        "Our code is available at https://github.com/acme/paper-code\n"
        "Documentation is at https://github.com/acme/paper-docs\n",
        encoding="utf-8",
    )
    state = make_initial_state(tmp_path, paper_path=paper)

    def mock_run(*args, **kwargs):
        target = artifact_dir(tmp_path) / "artifacts" / "source"
        target.mkdir(parents=True, exist_ok=True)
        (target / "main.py").write_text("print('ok')", encoding="utf-8")
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    with mock.patch("r2a.tools.source_acquisition.subprocess.run", side_effect=mock_run), \
         mock.patch("r2a.tools.source_acquisition.shutil.which", return_value="/usr/bin/git"):
        acquire_source(state)

    candidates = read_source_acquisition(tmp_path)["candidates"]
    for candidate in candidates:
        for key in ("url", "candidate_type", "origin", "confidence", "evidence_span", "selection_reason", "selected", "why_selected", "why_not_selected"):
            assert key in candidate
    assert any(c["selected"] and c["why_selected"] for c in candidates)


def _write_paper_bundle(repo: Path) -> Path:
    paper = repo / "paper.txt"
    paper.write_text("paper text", encoding="utf-8")
    for key in PAPER_STRUCTURED_KEYS:
        path = report_path(repo, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"source_or_artifact_urls":[]}' if key == "paper_output" else f"# {key}\n\nok\n", encoding="utf-8")
    return paper
