from __future__ import annotations

import json
import struct
from pathlib import Path

from r2a.tools.input_integrity import (
    summarize_official_input_integrity,
    validate_input_contract,
    validate_input_file,
)


def _write_fvecs(path: Path, dim: int = 2, values: tuple[float, ...] = (1.0, 2.0)) -> None:
    """Write a single fvecs record."""
    path.write_bytes(struct.pack("<i", dim) + struct.pack("<" + "f" * dim, *values[:dim]))


def _write_ivecs(path: Path, k: int = 2, values: tuple[int, ...] = (1, 2)) -> None:
    """Write a single ivecs record."""
    path.write_bytes(struct.pack("<i", k) + struct.pack("<" + "i" * k, *values[:k]))


def _write_fvecs_records(path: Path, records: list[tuple[int, list[float]]]) -> None:
    """Write multiple fvecs records with potentially varying dimensions."""
    data = b""
    for dim, values in records:
        data += struct.pack("<i", dim) + struct.pack("<" + "f" * dim, *values[:dim])
    path.write_bytes(data)


def _write_ivecs_records(path: Path, records: list[tuple[int, list[int]]]) -> None:
    """Write multiple ivecs records with potentially varying dimensions (k values)."""
    data = b""
    for k, values in records:
        data += struct.pack("<i", k) + struct.pack("<" + "i" * k, *values[:k])
    path.write_bytes(data)


def test_zero_byte_fvecs_is_empty_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "database_vectors.fvecs"
    path.write_bytes(b"")

    result = validate_input_file(path)

    assert result["integrity_status"] == "EMPTY_PLACEHOLDER_INPUT"
    assert result["max_evidence_level_allowed"] == "L2_input_contract_ready"


def test_zero_byte_ivecs_is_empty_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "ground_truth.ivecs"
    path.write_bytes(b"")

    result = validate_input_file(path)

    assert result["integrity_status"] == "EMPTY_PLACEHOLDER_INPUT"


def test_valid_minimal_fvecs_is_ok(tmp_path: Path) -> None:
    path = tmp_path / "query_vectors.fvecs"
    _write_fvecs(path)

    result = validate_input_file(path)

    assert result["integrity_status"] == "OK"
    assert result["dimension"] == 2
    assert result["record_count_estimate"] == 1


def test_valid_minimal_ivecs_is_ok(tmp_path: Path) -> None:
    path = tmp_path / "ground_truth.ivecs"
    _write_ivecs(path)

    result = validate_input_file(path)

    assert result["integrity_status"] == "OK"
    assert result["dimension"] == 2
    assert result["record_count_estimate"] == 1


def test_invalid_ann_size_is_inconsistent(tmp_path: Path) -> None:
    path = tmp_path / "database_vectors.fvecs"
    path.write_bytes(struct.pack("<i", 4) + b"\x00\x00")

    result = validate_input_file(path)

    assert result["integrity_status"] == "SIZE_INCONSISTENT"


def test_empty_json_inputs_are_placeholders(tmp_path: Path) -> None:
    path = tmp_path / "metadata.json"
    path.write_text(json.dumps({}), encoding="utf-8")

    result = validate_input_file(path)

    assert result["integrity_status"] == "EMPTY_PLACEHOLDER_INPUT"


def test_contract_with_empty_required_input_caps_at_l2(tmp_path: Path) -> None:
    database = tmp_path / "database_vectors.fvecs"
    database.write_bytes(b"")

    result = validate_input_contract([database])

    assert result["recommended_status"] == "NEEDS_OFFICIAL_INPUT"
    assert result["max_evidence_level_allowed"] == "L2_input_contract_ready"
    assert result["all_required_inputs_ok"] is False


def test_repo_summary_detects_empty_official_input_placeholder(tmp_path: Path) -> None:
    data_dir = tmp_path / ".r2a" / "artifacts" / "repo" / "datasets" / "small"
    data_dir.mkdir(parents=True)
    (data_dir / "database_vectors.fvecs").write_bytes(b"")
    results = tmp_path / ".r2a" / "results"
    results.mkdir(parents=True)
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        f"dataset,READY,{data_dir / 'database_vectors.fvecs'},official,size_bytes=0; integrity_status=EMPTY_PLACEHOLDER_INPUT\n",
        encoding="utf-8",
    )

    summary = summarize_official_input_integrity(tmp_path)

    assert summary["has_blocking_issue"] is True
    assert summary["recommended_status"] == "NEEDS_OFFICIAL_INPUT"
    assert summary["max_evidence_level_allowed"] == "L2_input_contract_ready"


def _write_input_contract(tmp_path: Path, body: str) -> None:
    results = tmp_path / ".r2a" / "results"
    results.mkdir(parents=True)
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes\n" + body,
        encoding="utf-8",
    )


def _reduced_core_rows(
    *,
    dataset_status: str = "AVAILABLE",
    query_status: str = "AVAILABLE",
    ground_truth_status: str = "AVAILABLE",
) -> str:
    return (
        f"sift1m_dataset,{dataset_status},sift_base.fvecs,official,SIFT1M reduced target dataset\n"
        f"sift1m_query,{query_status},sift_query.fvecs,official,SIFT1M reduced target query\n"
        f"sift1m_groundtruth,{ground_truth_status},sift_groundtruth.ivecs,official,SIFT1M reduced target ground truth\n"
    )


def test_optional_full_benchmark_datasets_do_not_block_reduced_l3_l4(tmp_path: Path) -> None:
    _write_input_contract(
        tmp_path,
        _reduced_core_rows()
        + "dataset,NEEDS_INPUT,Paper Dataset 2M 200d,paper,Custom synthetic dataset - not downloaded\n"
        + "dataset,NEEDS_INPUT,TripClick,paper,Official download required\n"
        + "dataset,NEEDS_INPUT,LAION-1M,paper,Official download required\n",
    )

    summary = summarize_official_input_integrity(tmp_path)

    assert summary["has_blocking_issue"] is False
    assert summary["all_required_inputs_ok"] is True
    assert summary["missing_or_invalid_inputs"] == []
    assert len(summary["warnings"]) == 3
    assert {item["path"] for item in summary["warnings"]} == {
        "Paper Dataset 2M 200d",
        "TripClick",
        "LAION-1M",
    }


def test_core_dataset_missing_still_blocks_reduced_l3_l4(tmp_path: Path) -> None:
    _write_input_contract(tmp_path, _reduced_core_rows(dataset_status="NEEDS_INPUT"))

    summary = summarize_official_input_integrity(tmp_path)

    assert summary["has_blocking_issue"] is True
    assert summary["all_required_inputs_ok"] is False
    assert any(item["component"] == "sift1m_dataset" for item in summary["missing_or_invalid_inputs"])


def test_core_query_missing_still_blocks_reduced_l3_l4(tmp_path: Path) -> None:
    _write_input_contract(tmp_path, _reduced_core_rows(query_status="NEEDS_INPUT"))

    summary = summarize_official_input_integrity(tmp_path)

    assert summary["has_blocking_issue"] is True
    assert summary["all_required_inputs_ok"] is False
    assert any(item["component"] == "sift1m_query" for item in summary["missing_or_invalid_inputs"])


def test_core_ground_truth_missing_still_blocks_reduced_l3_l4(tmp_path: Path) -> None:
    _write_input_contract(tmp_path, _reduced_core_rows(ground_truth_status="NEEDS_INPUT"))

    summary = summarize_official_input_integrity(tmp_path)

    assert summary["has_blocking_issue"] is True
    assert summary["all_required_inputs_ok"] is False
    assert any(item["component"] == "sift1m_groundtruth" for item in summary["missing_or_invalid_inputs"])


def test_optional_dataset_missing_is_warning_not_blocker_with_explicit_scope(tmp_path: Path) -> None:
    results = tmp_path / ".r2a" / "results"
    results.mkdir(parents=True)
    (results / "input_contract_verification.csv").write_text(
        "component,status,path_or_command,evidence_source,notes,scope,required\n"
        "sift1m_dataset,AVAILABLE,sift_base.fvecs,official,SIFT1M reduced target dataset,core,true\n"
        "sift1m_query,AVAILABLE,sift_query.fvecs,official,SIFT1M reduced target query,core,true\n"
        "sift1m_groundtruth,AVAILABLE,sift_groundtruth.ivecs,official,SIFT1M reduced target ground truth,core,true\n"
        "dataset,NEEDS_INPUT,TripClick,paper,Official download required,optional,false\n",
        encoding="utf-8",
    )

    summary = summarize_official_input_integrity(tmp_path)

    assert summary["has_blocking_issue"] is False
    assert summary["warnings"][0]["path"] == "TripClick"
    assert summary["warnings"][0]["severity"] == "warning"


# ============================================================================
# Variable-length ANN vector file tests (ground truth with varying k)
# ============================================================================


def test_fixed_length_fvecs_with_multiple_records_is_ok(tmp_path: Path) -> None:
    """Fixed-length fvecs with multiple records should pass."""
    path = tmp_path / "database_vectors.fvecs"
    # 2 records, both with dim=3
    _write_fvecs_records(path, [
        (3, [1.0, 2.0, 3.0]),
        (3, [4.0, 5.0, 6.0]),
    ])

    result = validate_input_file(path)

    assert result["integrity_status"] == "OK"
    assert result["record_count_estimate"] == 2
    assert result["dimension"] == 3
    assert result["is_variable_length"] is False
    assert result["min_dimension"] == 3
    assert result["max_dimension"] == 3


def test_fixed_length_ivecs_with_multiple_records_is_ok(tmp_path: Path) -> None:
    """Fixed-length ivecs with multiple records should pass."""
    path = tmp_path / "ground_truth.ivecs"
    # 2 records, both with k=5
    _write_ivecs_records(path, [
        (5, [1, 2, 3, 4, 5]),
        (5, [6, 7, 8, 9, 10]),
    ])

    result = validate_input_file(path)

    assert result["integrity_status"] == "OK"
    assert result["record_count_estimate"] == 2
    assert result["dimension"] == 5
    assert result["is_variable_length"] is False


def test_variable_length_ivecs_is_ok(tmp_path: Path) -> None:
    """Variable-length ivecs (ground truth with varying k) should pass.

    This is the key fix: ground truth files often have variable k because
    filtered search may return fewer than k results for some queries.
    """
    path = tmp_path / "ground_truth.ivecs"
    # 3 records with varying k: 3, 1, 4
    _write_ivecs_records(path, [
        (3, [10, 20, 30]),
        (1, [40]),
        (4, [50, 60, 70, 80]),
    ])

    result = validate_input_file(path)

    assert result["integrity_status"] == "OK"
    assert result["record_count_estimate"] == 3
    assert result["dimension"] == 3  # first record's dimension
    assert result["is_variable_length"] is True
    assert result["min_dimension"] == 1
    assert result["max_dimension"] == 4
    assert "Variable-length" in result["notes"]


def test_incomplete_dimension_header_is_size_inconsistent(tmp_path: Path) -> None:
    """File with incomplete dimension header should fail."""
    path = tmp_path / "ground_truth.ivecs"
    # Valid record + incomplete dimension header (only 1-3 bytes)
    data = struct.pack("<i", 2) + struct.pack("<ii", 1, 2)  # valid record
    data += b"\x01\x02"  # incomplete 4-byte header
    path.write_bytes(data)

    result = validate_input_file(path)

    assert result["integrity_status"] == "SIZE_INCONSISTENT"
    assert "incomplete dimension header" in result["notes"]


def test_incomplete_data_is_size_inconsistent(tmp_path: Path) -> None:
    """Record claiming dim=N but with insufficient data should fail."""
    path = tmp_path / "ground_truth.ivecs"
    # Record claims dim=10 but only provides 2 values
    path.write_bytes(struct.pack("<i", 10) + struct.pack("<ii", 1, 2))

    result = validate_input_file(path)

    assert result["integrity_status"] == "SIZE_INCONSISTENT"
    assert "incomplete data" in result["notes"]


def test_zero_dimension_is_format_invalid(tmp_path: Path) -> None:
    """Record with dim=0 should fail."""
    path = tmp_path / "ground_truth.ivecs"
    path.write_bytes(struct.pack("<i", 0))

    result = validate_input_file(path)

    assert result["integrity_status"] == "FORMAT_INVALID"
    assert "invalid dimension" in result["notes"]


def test_negative_dimension_is_format_invalid(tmp_path: Path) -> None:
    """Record with dim<0 should fail."""
    path = tmp_path / "ground_truth.ivecs"
    path.write_bytes(struct.pack("<i", -5))

    result = validate_input_file(path)

    assert result["integrity_status"] == "FORMAT_INVALID"


def test_excessive_dimension_is_format_invalid(tmp_path: Path) -> None:
    """Record with dim>=1_000_000 should fail."""
    path = tmp_path / "ground_truth.ivecs"
    path.write_bytes(struct.pack("<i", 1_000_000))

    result = validate_input_file(path)

    assert result["integrity_status"] == "FORMAT_INVALID"
    assert "exceeds maximum" in result["notes"]


def test_trailing_data_is_size_inconsistent(tmp_path: Path) -> None:
    """File with trailing data after complete records should fail.

    Note: trailing bytes < 4 are detected as incomplete dimension header.
    This is correct behavior - the parser tries to read the next record's header.
    """
    path = tmp_path / "ground_truth.ivecs"
    # 1 valid record + trailing byte
    data = struct.pack("<i", 2) + struct.pack("<ii", 1, 2) + b"\xFF"
    path.write_bytes(data)

    result = validate_input_file(path)

    assert result["integrity_status"] == "SIZE_INCONSISTENT"
    # Trailing bytes are detected as incomplete dimension header
    assert "incomplete" in result["notes"].lower()


def test_run3_style_variable_length_ground_truth_em_is_ok(tmp_path: Path) -> None:
    """Simulate run_20260611_183905_d5f6bc7a gt100_em.ivecs structure.

    Original: dim=100, dim=52, dim=16 (varying k values)
    """
    path = tmp_path / "gt100_em.ivecs"
    _write_ivecs_records(path, [
        (100, list(range(100))),
        (52, list(range(52))),
        (16, list(range(16))),
    ])

    result = validate_input_file(path)

    assert result["integrity_status"] == "OK"
    assert result["record_count_estimate"] == 3
    assert result["is_variable_length"] is True
    assert result["min_dimension"] == 16
    assert result["max_dimension"] == 100


def test_run3_style_variable_length_ground_truth_emis_is_ok(tmp_path: Path) -> None:
    """Simulate run_20260611_183905_d5f6bc7a gt10_emis.ivecs structure.

    Original: dim=100, dim=63, dim=4, etc.
    """
    path = tmp_path / "gt10_emis.ivecs"
    _write_ivecs_records(path, [
        (100, list(range(100))),
        (63, list(range(63))),
        (4, list(range(4))),
    ])

    result = validate_input_file(path)

    assert result["integrity_status"] == "OK"
    assert result["is_variable_length"] is True
    assert result["min_dimension"] == 4
    assert result["max_dimension"] == 100


def test_variable_length_fvecs_is_ok(tmp_path: Path) -> None:
    """Variable-length fvecs (unusual but valid)."""
    path = tmp_path / "vectors.fvecs"
    _write_fvecs_records(path, [
        (2, [1.0, 2.0]),
        (4, [1.0, 2.0, 3.0, 4.0]),
    ])

    result = validate_input_file(path)

    assert result["integrity_status"] == "OK"
    assert result["is_variable_length"] is True
