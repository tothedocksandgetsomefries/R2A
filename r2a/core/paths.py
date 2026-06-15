from __future__ import annotations

from pathlib import Path

from r2a.core.config import ARTIFACT_DIRNAME, REPORT_FILENAMES


def resolve_repo_path(repo_path: str | Path) -> Path:
    return Path(repo_path).expanduser().resolve()


def ensure_repo_dir(repo_path: str | Path) -> Path:
    path = resolve_repo_path(repo_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def require_repo_dir(repo_path: str | Path) -> Path:
    path = resolve_repo_path(repo_path)
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"Repository path does not exist: {path}")
    return path


def artifact_dir(repo_path: str | Path) -> Path:
    return resolve_repo_path(repo_path) / ARTIFACT_DIRNAME


def ensure_artifact_dir(repo_path: str | Path) -> Path:
    path = artifact_dir(repo_path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def report_path(repo_path: str | Path, report_key: str) -> Path:
    try:
        filename = REPORT_FILENAMES[report_key]
    except KeyError as exc:
        known = ", ".join(sorted(REPORT_FILENAMES))
        raise KeyError(f"Unknown report key '{report_key}'. Known keys: {known}") from exc
    return artifact_dir(repo_path) / filename


def runs_dir(repo_path: str | Path) -> Path:
    return artifact_dir(repo_path) / "runs"


def latest_dir(repo_path: str | Path) -> Path:
    return artifact_dir(repo_path) / "latest"


def run_dir(repo_path: str | Path, run_id: str) -> Path:
    return runs_dir(repo_path) / str(run_id)


def run_manifest_path(repo_path: str | Path, run_id: str) -> Path:
    return run_dir(repo_path, run_id) / "RUN_MANIFEST.json"


def latest_run_manifest_path(repo_path: str | Path) -> Path:
    return latest_dir(repo_path) / "RUN_MANIFEST.json"


def iteration_dir(repo_path: str | Path, iteration: int) -> Path:
    return runs_dir(repo_path) / f"iter_{int(iteration):03d}"


def iteration_state_path(repo_path: str | Path) -> Path:
    return artifact_dir(repo_path) / "ITERATION_STATE.json"
