import os
import time
from pathlib import Path

from r2a.core.paths import report_path
from r2a.tools.stage_transaction import (
    commit_planner_transaction,
    commit_reviewer_transaction,
    planner_allowed_outputs,
    planner_staging_dir,
    reviewer_allowed_outputs,
    reviewer_staging_dir,
    validate_planner_transaction,
    validate_reviewer_transaction,
    write_planner_transaction_metadata,
)


def _valid_task() -> str:
    return """# TASK_SPEC

## Reproducibility Gate Summary
ok

## Max Evidence Level Allowed
L2_input_contract_ready

## L3 Entry Criteria
not yet

## L4 Alignment Criteria
not yet
"""


def _valid_contract(mode: str = "verification_only") -> str:
    return f"""# EXPERIMENT_CONTRACT

## Contract Mode
{mode}

## Max Evidence Level Allowed
L2_input_contract_ready

## Reproducibility Gate
ok

## Claim Restrictions
No full reproduction claim.
"""


def _write_valid_candidate(staging: Path) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "PLANNER_OUTPUT.json").write_text('{"schema_version":"2.0"}', encoding="utf-8")
    (staging / "TASK_SPEC.md").write_text(_valid_task(), encoding="utf-8")
    (staging / "EXPERIMENT_CONTRACT.md").write_text(_valid_contract(), encoding="utf-8")


def _write_valid_reviewer_candidate(staging: Path, verdict: str = "PASS_WITH_LIMITATIONS") -> None:
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "REVIEW_REPORT.md").write_text(f"# REVIEW_REPORT\n\n## Verdict\n\n{verdict}\n", encoding="utf-8")
    (staging / "REVIEW_FEEDBACK.json").write_text(
        '{"schema_version":1,"verdict":"' + verdict + '","should_iterate":false}',
        encoding="utf-8",
    )


def test_planner_candidate_commits_only_after_validation(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_candidate(staging)

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )
    assert metadata["validation_status"] == "PASS"
    assert not report_path(tmp_path, "task").exists()

    committed = commit_planner_transaction(tmp_path, staging, metadata)
    write_planner_transaction_metadata(tmp_path, committed)

    assert report_path(tmp_path, "task").read_text(encoding="utf-8").startswith("# TASK_SPEC")
    assert report_path(tmp_path, "experiment_contract").read_text(encoding="utf-8").startswith("# EXPERIMENT_CONTRACT")
    assert report_path(tmp_path, "planner_output").read_text(encoding="utf-8").startswith("{")
    assert committed["committed"] is True
    assert committed["committed_files"] == [
        ".r2a/PLANNER_OUTPUT.json",
        ".r2a/TASK_SPEC.md",
        ".r2a/EXPERIMENT_CONTRACT.md",
    ]


def test_missing_task_spec_rejects_commit(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    staging.mkdir(parents=True, exist_ok=True)
    started = time.time()
    (staging / "EXPERIMENT_CONTRACT.md").write_text(_valid_contract(), encoding="utf-8")

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "PLANNER_MISSING_REQUIRED_OUTPUT"
    assert not report_path(tmp_path, "task").exists()


def test_stale_candidate_rejects_commit(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    _write_valid_candidate(staging)
    old_time = time.time() - 60
    for path in (staging / "TASK_SPEC.md", staging / "EXPERIMENT_CONTRACT.md"):
        os.utime(path, (old_time, old_time))

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=time.time(),
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "PLANNER_STALE_OUTPUT"


def test_backend_parse_failure_rejects_commit(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_candidate(staging)

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {
            "success": False,
            "returncode": 1,
            "backend_failure_category": "TOOL_CALL_PARSE_FAILURE",
            "transient_backend_failure": True,
            "unexpected_modifications": [],
        },
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "PLANNER_BACKEND_FAILURE"
    assert not report_path(tmp_path, "task").exists()


def test_forbidden_candidate_artifact_rejects_commit(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_candidate(staging)
    forbidden = staging / ".r2a" / "artifacts" / "datasets" / "foo.fvecs"
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_bytes(b"\x00\x00\x00\x00")

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "PLANNER_FORBIDDEN_WRITE"
    assert metadata["rejected_files"]


def test_forbidden_candidate_results_rejects_commit(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_candidate(staging)
    forbidden = staging / ".r2a" / "results" / "reduced_metrics.csv"
    forbidden.parent.mkdir(parents=True, exist_ok=True)
    forbidden.write_text("command_id,dataset\nC1,x\n", encoding="utf-8")

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "PLANNER_FORBIDDEN_WRITE"


def test_official_reduced_requires_clean_input_integrity(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    started = time.time()
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "PLANNER_OUTPUT.json").write_text('{"schema_version":"2.0"}', encoding="utf-8")
    (staging / "TASK_SPEC.md").write_text(_valid_task(), encoding="utf-8")
    (staging / "EXPERIMENT_CONTRACT.md").write_text(_valid_contract("official_reduced"), encoding="utf-8")

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "PLANNER_CONTRACT_VALIDATION_FAILED"
    assert metadata["contract_mode_before_validation"] == "official_reduced"
    assert metadata["contract_mode_after_validation"] == ""


def test_verification_only_contract_with_official_reduced_negation_does_not_request_integrity(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 1, 1)
    started = time.time()
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "PLANNER_OUTPUT.json").write_text('{"schema_version":"2.0"}', encoding="utf-8")
    (staging / "TASK_SPEC.md").write_text(_valid_task(), encoding="utf-8")
    (staging / "EXPERIMENT_CONTRACT.md").write_text(
        _valid_contract("verification_only")
        + "\n## Decision Rationale\n\n`official_reduced` not chosen because this is a mock dry-run.\n",
        encoding="utf-8",
    )

    metadata = validate_planner_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "PASS"
    assert metadata["contract_mode_before_validation"] == "verification_only"


def test_planner_allowed_outputs_are_staging_only(tmp_path: Path) -> None:
    staging = planner_staging_dir(tmp_path, 3, 1)
    allowed = planner_allowed_outputs(tmp_path, staging)

    assert ".r2a/staging/planner/iter_003/attempt_001/PLANNER_OUTPUT.json" in allowed
    assert ".r2a/staging/planner/iter_003/attempt_001/TASK_SPEC.md" in allowed
    assert ".r2a/staging/planner/iter_003/attempt_001/EXPERIMENT_CONTRACT.md" in allowed
    assert ".r2a/TASK_SPEC.md" not in allowed


def test_reviewer_candidate_commits_only_after_validation(tmp_path: Path) -> None:
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_reviewer_candidate(staging)

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )
    assert metadata["validation_status"] == "PASS"
    assert not report_path(tmp_path, "review").exists()

    committed = commit_reviewer_transaction(tmp_path, staging, metadata)

    assert report_path(tmp_path, "review").read_text(encoding="utf-8").startswith("# REVIEW_REPORT")
    assert report_path(tmp_path, "review_feedback").read_text(encoding="utf-8").startswith("{")
    assert committed["committed"] is True
    assert committed["committed_files"] == [".r2a/REVIEW_REPORT.md", ".r2a/REVIEW_FEEDBACK.json"]


def test_reviewer_malformed_feedback_rejects_commit(tmp_path: Path) -> None:
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    started = time.time()
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "REVIEW_REPORT.md").write_text("# REVIEW_REPORT\n\n## Verdict\n\nPASS\n", encoding="utf-8")
    (staging / "REVIEW_FEEDBACK.json").write_text("{not json", encoding="utf-8")

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "REVIEWER_MALFORMED_FEEDBACK"
    assert not report_path(tmp_path, "review").exists()


def test_reviewer_stale_feedback_rejects_commit(tmp_path: Path) -> None:
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    _write_valid_reviewer_candidate(staging)
    old_time = time.time() - 60
    for path in (staging / "REVIEW_REPORT.md", staging / "REVIEW_FEEDBACK.json"):
        os.utime(path, (old_time, old_time))

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=time.time(),
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "REVIEWER_STALE_OUTPUT"


def test_reviewer_manager_fail_rejects_pass_like_verdict(tmp_path: Path) -> None:
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_reviewer_candidate(staging, "PASS")

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
        manager_status="FAIL",
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "REVIEWER_MANAGER_FAIL_PASS"


def test_reviewer_manager_classification_conflict_is_structured_not_rejected(tmp_path: Path) -> None:
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    started = time.time()
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "REVIEW_REPORT.md").write_text("# REVIEW_REPORT\n\n## Verdict\n\nINPUT_CONTRACT_READY\n", encoding="utf-8")
    (staging / "REVIEW_FEEDBACK.json").write_text(
        '{"schema_version":1,'
        '"verdict":"INPUT_CONTRACT_READY",'
        '"execution_status":"PASS",'
        '"classification_conflicts":["Manager FAIL is based on non-blocking input-contract status labels."]}',
        encoding="utf-8",
    )

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
        manager_status="FAIL",
    )

    assert metadata["validation_status"] == "PASS"
    assert metadata["execution_status"] == "MANAGER_CLASSIFICATION_CONFLICT"
    assert metadata["manager_classification_conflict"] is True
    assert metadata["candidate_verdict"] == "INPUT_CONTRACT_READY"


def test_reviewer_verification_only_contract_rejects_l3_l4_verdict(tmp_path: Path) -> None:
    r2a = tmp_path / ".r2a"
    r2a.mkdir(parents=True)
    (r2a / "EXPERIMENT_CONTRACT.md").write_text(
        "# EXPERIMENT_CONTRACT\n\n## Contract Mode\n\nverification_only\n",
        encoding="utf-8",
    )
    (r2a / "TASK_SPEC.md").write_text("# TASK_SPEC\n\nMode: verification_only\n", encoding="utf-8")
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_reviewer_candidate(staging, "PASS_REDUCED_ALIGNED")

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "REVIEWER_CONTRACT_L2_CAP_BLOCKED_L3"
    assert "contract mode is verification_only" in metadata["issues"][0]


def test_reviewer_input_integrity_blocker_rejects_l3_l4_verdict(tmp_path: Path) -> None:
    results = tmp_path / ".r2a" / "results"
    data = tmp_path / ".r2a" / "artifacts" / "official" / "datasets"
    results.mkdir(parents=True)
    data.mkdir(parents=True)
    empty_query = data / "query.fvecs"
    empty_query.write_bytes(b"")
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        f"query,EMPTY_PLACEHOLDER_INPUT,{empty_query},official,size_bytes=0\n",
        encoding="utf-8",
    )
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_reviewer_candidate(staging, "PASS_REDUCED_METHOD_ONLY")

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "REVIEWER_INPUT_INTEGRITY_BLOCKED_L3"


def test_reviewer_allows_l3_l4_when_only_paper_reference_dataset_is_missing(tmp_path: Path) -> None:
    results = tmp_path / ".r2a" / "results"
    results.mkdir(parents=True)
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "sift1m_dataset,AVAILABLE,sift_base.fvecs,official,SIFT1M reduced target dataset\n"
        "sift1m_query,AVAILABLE,sift_query.fvecs,official,SIFT1M reduced target query\n"
        "sift1m_groundtruth,AVAILABLE,sift_groundtruth.ivecs,official,SIFT1M reduced target ground truth\n"
        "dataset_Paper,NEEDS_INPUT,,PAPER_EVIDENCE.md,"
        "2,029,997 vectors, 200 dim, 12 predicates. Internal corpus; may not be publicly available.\n",
        encoding="utf-8",
    )
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_reviewer_candidate(staging, "PASS_REDUCED_ALIGNED")

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "PASS"
    assert metadata["candidate_verdict"] == "PASS_REDUCED_ALIGNED"
    assert metadata["input_integrity_status"] == "OK"


def test_reviewer_rejects_l3_l4_when_paper_dataset_is_explicitly_target_required(tmp_path: Path) -> None:
    results = tmp_path / ".r2a" / "results"
    results.mkdir(parents=True)
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes,target_required\n"
        "sift1m_dataset,AVAILABLE,sift_base.fvecs,official,SIFT1M reduced target dataset,true\n"
        "sift1m_query,AVAILABLE,sift_query.fvecs,official,SIFT1M reduced target query,true\n"
        "sift1m_groundtruth,AVAILABLE,sift_groundtruth.ivecs,official,SIFT1M reduced target ground truth,true\n"
        "dataset_Paper,NEEDS_INPUT,,PAPER_EVIDENCE.md,"
        "Internal corpus; may not be publicly available,true\n",
        encoding="utf-8",
    )
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_reviewer_candidate(staging, "PASS_REDUCED_ALIGNED")

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {"success": True, "returncode": 0, "unexpected_modifications": []},
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["execution_status"] == "REVIEWER_INPUT_INTEGRITY_BLOCKED_L3"


def test_reviewer_allowed_outputs_are_staging_only(tmp_path: Path) -> None:
    staging = reviewer_staging_dir(tmp_path, 2, 1)
    allowed = reviewer_allowed_outputs(tmp_path, staging)

    assert ".r2a/staging/reviewer/iter_002/attempt_001/REVIEW_REPORT.md" in allowed
    assert ".r2a/staging/reviewer/iter_002/attempt_001/REVIEW_FEEDBACK.json" in allowed
    assert ".r2a/REVIEW_REPORT.md" not in allowed
    assert ".r2a/REVIEW_FEEDBACK.json" not in allowed


def test_reviewer_forbidden_formal_write_rejects_commit(tmp_path: Path) -> None:
    staging = reviewer_staging_dir(tmp_path, 1, 1)
    started = time.time()
    _write_valid_reviewer_candidate(staging)

    metadata = validate_reviewer_transaction(
        tmp_path,
        staging,
        {
            "success": False,
            "returncode": 0,
            "unexpected_modifications": [".r2a/REVIEW_REPORT.md"],
            "failure_category": "STAGE_BOUNDARY_VIOLATION",
            "execution_status": "REVIEWER_FORBIDDEN_WRITE",
        },
        iteration=1,
        attempt_started_at=started,
    )

    assert metadata["validation_status"] == "FAIL"
    assert metadata["failure_category"] == "STAGE_BOUNDARY_VIOLATION"
    assert metadata["execution_status"] == "REVIEWER_FORBIDDEN_WRITE"
