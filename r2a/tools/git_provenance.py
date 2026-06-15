from __future__ import annotations

from dataclasses import dataclass
import csv
from pathlib import Path
import re
import subprocess

from r2a.core.paths import artifact_dir
from r2a.tools.csv_schemas import canonicalize_row


@dataclass(frozen=True)
class GitProvenance:
    path: Path
    origin: str
    branch: str
    commit: str


def read_git_provenance(path: str | Path) -> GitProvenance | None:
    repo = Path(path)
    commit = _git(repo, "rev-parse", "HEAD")
    if not commit:
        return None
    return GitProvenance(
        path=repo,
        origin=_git(repo, "remote", "get-url", "origin"),
        branch=_git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
        commit=commit,
    )


def provenance_csv_issues(repo_path: str | Path, result_csvs: list[Path]) -> dict[str, list[str]]:
    repo = Path(repo_path)
    source_rows = _rows_from_named_csv(result_csvs, "source_verification.csv")
    manifest_rows = _rows_from_named_csv(result_csvs, "command_manifest.csv")
    git_repos = _discover_artifact_git_repos(repo)
    errors: list[str] = []
    warnings: list[str] = []
    summary_lines: list[str] = [
        f"source_verification rows checked: {len(source_rows)}",
        f"command_manifest rows checked: {len(manifest_rows)}",
        f"local artifact git repos discovered: {len(git_repos)}",
    ]

    for row in source_rows:
        actual = _match_source_row(repo, row, git_repos)
        source_label = _source_label(row)
        if actual is None:
            source_path = _first_present(row, ("source_path", "artifact_path", "path"))
            if source_path and _any_existing_path_candidate(repo, source_path):
                warnings.append(f"Source provenance could not be checked against a local git checkout: {source_label}.")
            continue
        summary_lines.append(f"actual source HEAD: {source_label or actual.path} -> {actual.commit}")
        recorded_commit = _first_present(row, ("commit", "actual_commit", "artifact_hash"))
        if not recorded_commit:
            warnings.append(f"source_verification.csv does not record actual commit for {source_label or actual.path}; actual HEAD is {actual.commit}.")
        elif _clean_commit(recorded_commit) != actual.commit:
            message = (
                f"source_verification.csv commit mismatch for {source_label or actual.path}: "
                f"recorded={recorded_commit}; actual={actual.commit}; path={actual.path}."
            )
            if _row_claims_verified_source(row):
                errors.append(message)
            else:
                warnings.append(message)
        recorded_origin = _first_present(row, ("artifact_url", "source_url", "repo_url"))
        if recorded_origin and actual.origin and _normalize_url(recorded_origin) != _normalize_url(actual.origin):
            warnings.append(
                f"source_verification.csv origin mismatch for {source_label or actual.path}: recorded={recorded_origin}; actual={actual.origin}."
            )
        recorded_branch = _first_present(row, ("branch",))
        if recorded_branch and actual.branch and actual.branch != "HEAD" and recorded_branch != actual.branch:
            warnings.append(
                f"source_verification.csv branch mismatch for {source_label or actual.path}: recorded={recorded_branch}; actual={actual.branch}."
            )

    for row in manifest_rows:
        artifact_path = _first_present(row, ("artifact_path", "source_path"))
        if not artifact_path:
            continue
        actual = _match_path(repo, artifact_path, git_repos)
        if actual is None:
            continue
        artifact_hash = _first_present(row, ("artifact_hash", "commit"))
        command_id = _first_present(row, ("command_id",)) or "<unknown>"
        if not artifact_hash:
            warnings.append(f"command_manifest.csv row {command_id} references git repo {artifact_path} but does not record actual HEAD {actual.commit}.")
        elif _looks_like_commit(artifact_hash) and _clean_commit(artifact_hash) != actual.commit:
            warnings.append(
                f"command_manifest.csv row {command_id} git commit mismatch for {artifact_path}: "
                f"recorded={artifact_hash}; actual={actual.commit}; path={actual.path}."
            )

    return {"errors": errors, "warnings": warnings, "summary_lines": summary_lines}


def _discover_artifact_git_repos(repo: Path) -> list[GitProvenance]:
    candidates: list[Path] = [repo]
    artifacts = artifact_dir(repo) / "artifacts"
    if artifacts.exists():
        for marker in artifacts.rglob(".git"):
            candidates.append(marker.parent)
    output: list[GitProvenance] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        provenance = read_git_provenance(resolved)
        if provenance is not None:
            output.append(provenance)
    return output


def _match_source_row(repo: Path, row: dict[str, str], git_repos: list[GitProvenance]) -> GitProvenance | None:
    source_path = _first_present(row, ("source_path", "artifact_path", "path"))
    if source_path:
        matched = _match_path(repo, source_path, git_repos)
        if matched is not None:
            return matched
    url = _first_present(row, ("artifact_url", "source_url", "repo_url"))
    if url:
        normalized = _normalize_url(url)
        for provenance in git_repos:
            if _normalize_url(provenance.origin) == normalized:
                return provenance
    return None


def _match_path(repo: Path, value: str, git_repos: list[GitProvenance]) -> GitProvenance | None:
    for candidate in _path_candidates(repo, value):
        provenance = read_git_provenance(candidate)
        if provenance is not None:
            return provenance
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        for existing in git_repos:
            try:
                existing_resolved = existing.path.resolve()
            except OSError:
                continue
            if resolved == existing_resolved:
                return existing
    return None


def _path_candidates(repo: Path, value: str) -> list[Path]:
    raw = Path(str(value).strip().strip('"'))
    if not str(raw):
        return []
    if raw.is_absolute():
        return [raw]
    r2a_dir = artifact_dir(repo)
    return [
        repo / raw,
        r2a_dir / raw,
        r2a_dir / "artifacts" / raw,
    ]


def _any_existing_path_candidate(repo: Path, value: str) -> bool:
    return any(candidate.exists() for candidate in _path_candidates(repo, value))


def _rows_from_named_csv(paths: list[Path], name: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        if path.name.lower() != name:
            continue
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows.extend(
                    {str(key): str(value) for key, value in canonicalize_row(path.name, row).items()}
                    for row in csv.DictReader(handle)
                )
        except (OSError, csv.Error):
            continue
    return rows


def _first_present(row: dict[str, str], columns: tuple[str, ...]) -> str:
    normalized = {_normalize_column(key): value for key, value in row.items()}
    for column in columns:
        value = row.get(column, normalized.get(_normalize_column(column), ""))
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _source_label(row: dict[str, str]) -> str:
    return _first_present(row, ("artifact_url", "source_url", "repo_url", "source_path", "artifact_path"))


def _row_claims_verified_source(row: dict[str, str]) -> bool:
    status = _first_present(row, ("status", "access_status", "verdict", "result")).upper()
    text = " ".join(str(value).upper() for value in row.values())
    return status in {"PASS", "OK", "READY", "FOUND", "VERIFIED", "CLONED"} or "VERIFIED" in text


def _looks_like_commit(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{7,40}", str(value).strip()))


def _clean_commit(value: str) -> str:
    match = re.search(r"[0-9a-fA-F]{7,40}", str(value))
    return match.group(0).lower() if match else str(value).strip().lower()


def _normalize_url(value: str) -> str:
    text = str(value or "").strip().lower().replace("\\", "/")
    if text.endswith(".git"):
        text = text[:-4]
    return text.rstrip("/")


def _normalize_column(value: str) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


def _git(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""
