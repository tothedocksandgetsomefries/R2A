from pathlib import Path

from r2a.tools.csv_checker import check_csv_file
from r2a.tools.csv_writer import write_csv_rows


def test_write_csv_rows_quotes_commas_in_input_contract_notes(tmp_path: Path) -> None:
    path = tmp_path / "input_contract_verification.csv"

    write_csv_rows(
        path,
        ("component", "status", "path_or_command", "evidence_source", "notes"),
        [
            {
                "component": "artifact_results",
                "status": "OK",
                "path_or_command": ".r2a/artifacts/results",
                "evidence_source": "repo",
                "notes": "Repo ships pre-computed medium results (19 JSON), params, plots.",
            }
        ],
    )

    assert check_csv_file(path) == []


def test_malformed_input_contract_csv_reports_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "input_contract_verification.csv"
    path.write_text(
        "component,status,path_or_command,evidence_source,notes\n"
        "artifact_results,OK,.r2a/artifacts/results,repo,Repo ships JSON, params, plots\n",
        encoding="utf-8",
    )

    issues = check_csv_file(path)

    assert any(issue.level == "warning" and "CSV_PARSE_ERROR" in issue.message for issue in issues)
    assert any("CSV parse issue (non-fatal)" in issue.message for issue in issues)
