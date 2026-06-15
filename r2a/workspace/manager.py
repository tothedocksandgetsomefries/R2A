from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import uuid

from r2a.core.feature_flags import minimal_workflow_defaults
from r2a.workspace.manifest import build_workspace_manifest, write_workspace_manifest

R2A_WORKSPACE_BASE_ENV = "R2A_WORKSPACE_BASE"
DEFAULT_MAX_DATASET_DOWNLOAD_GB = 10
GIB = 1024**3

IGNORE_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    "build",
    ".r2a",
}


def _default_workspace_base() -> Path:
    configured = os.environ.get(R2A_WORKSPACE_BASE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if os.name == "nt" and local_app_data:
        return Path(local_app_data).expanduser() / "R2A" / "workspaces"
    if os.name == "nt":
        return Path.home() / "AppData" / "Local" / "R2A" / "workspaces"
    return Path.home() / ".r2a" / "workspaces"


DEFAULT_WORKSPACE_BASE = _default_workspace_base()


def create_workspace(
    base_dir: str | Path,
    goal: str,
    paper_file_path: str | Path | None = None,
    source_repo_path: str | Path | None = None,
    github_repo_url: str | None = None,
    dataset_urls: list[str] | None = None,
    max_dataset_download_gb: int | float = DEFAULT_MAX_DATASET_DOWNLOAD_GB,
    copy_repo: bool = True,
) -> dict:
    base = Path(base_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now().replace(microsecond=0).isoformat()
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    workspace_dir = base / run_id
    paper_dir = workspace_dir / "paper"
    repo_dir = workspace_dir / "repo"
    workspace_artifact_dir = workspace_dir / ".r2a"
    data_dir = workspace_dir / "data"
    artifact_dir = repo_dir / ".r2a"
    logs_dir = artifact_dir / "logs"
    results_dir = artifact_dir / "results"

    paper_dir.mkdir(parents=True, exist_ok=False)
    repo_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    (workspace_artifact_dir / "logs").mkdir(parents=True, exist_ok=True)
    (workspace_artifact_dir / "results").mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    paper_path = _copy_paper(paper_file_path, paper_dir)
    source_repo = Path(source_repo_path).expanduser().resolve() if source_repo_path else None
    if source_repo is not None and not source_repo.exists():
        raise FileNotFoundError(f"source_repo_path does not exist: {source_repo}")

    repo_download = {"method": "empty", "url": "", "status": "not_requested", "message": ""}
    if github_repo_url and github_repo_url.strip():
        repo_download = _clone_github_repo(github_repo_url.strip(), repo_dir)
        (repo_dir / ".r2a" / "logs").mkdir(parents=True, exist_ok=True)
        (repo_dir / ".r2a" / "results").mkdir(parents=True, exist_ok=True)
        repo_path = repo_dir
    elif source_repo is not None and copy_repo:
        shutil.copytree(source_repo, repo_dir, dirs_exist_ok=True, ignore=_ignore_repo_files)
        (repo_dir / ".r2a" / "logs").mkdir(parents=True, exist_ok=True)
        (repo_dir / ".r2a" / "results").mkdir(parents=True, exist_ok=True)
        repo_path = repo_dir
    elif source_repo is not None:
        repo_path = source_repo
        (repo_path / ".r2a" / "logs").mkdir(parents=True, exist_ok=True)
        (repo_path / ".r2a" / "results").mkdir(parents=True, exist_ok=True)
    else:
        repo_path = repo_dir

    if repo_path == repo_dir:
        _ensure_git_repo(repo_path)

    dataset_downloads = _download_datasets(
        dataset_urls or [],
        data_dir,
        max_bytes=int(float(max_dataset_download_gb) * GIB),
    )

    metadata = {
        "run_id": run_id,
        "created_at": created_at,
        "base_dir": str(base),
        "workspace_dir": str(workspace_dir),
        "paper_path": str(paper_path) if paper_path else "",
        "repo_path": str(repo_path),
        "source_repo_path": str(source_repo) if source_repo else "",
        "github_repo_url": github_repo_url.strip() if github_repo_url else "",
        "repo_download": repo_download,
        "data_dir": str(data_dir),
        "dataset_downloads": dataset_downloads,
        "max_dataset_download_gb": max_dataset_download_gb,
        "goal": goal,
        "copy_repo": copy_repo,
    }
    metadata_path = workspace_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    defaults = minimal_workflow_defaults()
    manifest = build_workspace_manifest(
        workspace_id=run_id,
        workspace_path=workspace_dir,
        paper_path=str(paper_path) if paper_path else "",
        planner_backend=str(defaults.get("planner_backend", "ccr_text")),
        engineer_executor=str(defaults.get("engineer_executor", "mock")),
        paper_backend=str(defaults.get("paper_backend", "preprocess")),
        status="created",
        extra={
            "repo_path": str(repo_path),
            "data_dir": str(data_dir),
            "goal": goal,
            "repo_download": repo_download,
            "dataset_downloads": dataset_downloads,
        },
    )
    write_workspace_manifest(workspace_dir, manifest)

    return {
        "run_id": run_id,
        "workspace_dir": str(workspace_dir),
        "paper_path": str(paper_path) if paper_path else "",
        "repo_path": str(repo_path),
        "data_dir": str(data_dir),
        "metadata_path": str(metadata_path),
        "source_repo_path": str(source_repo) if source_repo else "",
        "github_repo_url": github_repo_url.strip() if github_repo_url else "",
        "dataset_urls": dataset_urls or [],
        "repo_download": repo_download,
        "dataset_downloads": dataset_downloads,
        "goal": goal,
    }


def _copy_paper(paper_file_path: str | Path | None, paper_dir: Path) -> Path | None:
    if paper_file_path is None:
        return None
    source = Path(paper_file_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"paper_file_path does not exist: {source}")
    target_name = source.name or "paper.pdf"
    target = paper_dir / target_name
    shutil.copy2(source, target)
    return target


def _ignore_repo_files(directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in IGNORE_NAMES}


def _clone_github_repo(github_repo_url: str, target_dir: Path) -> dict:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    if shutil.which("git") is None:
        target_dir.mkdir(parents=True, exist_ok=True)
        message = "git executable was not found on PATH."
        _write_workspace_log(target_dir, "github_clone.log", message)
        return {"method": "git_clone", "url": github_repo_url, "status": "failed", "message": message}
    command = ["git", "clone", "--depth", "1", github_repo_url, str(target_dir)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    log_text = f"$ {' '.join(command)}\n\nSTDOUT:\n{completed.stdout}\n\nSTDERR:\n{completed.stderr}\n"
    _write_workspace_log(target_dir, "github_clone.log", log_text)
    status = "ok" if completed.returncode == 0 else "failed"
    return {
        "method": "git_clone",
        "url": github_repo_url,
        "status": status,
        "returncode": completed.returncode,
        "message": completed.stderr.strip() or completed.stdout.strip(),
    }


def _download_datasets(dataset_urls: list[str], data_dir: Path, max_bytes: int) -> list[dict]:
    records: list[dict] = []
    for raw_url in dataset_urls:
        url = raw_url.strip()
        if not url:
            continue
        records.append(_download_dataset(url, data_dir, max_bytes))
    return records


def _download_dataset(url: str, data_dir: Path, max_bytes: int) -> dict:
    data_dir.mkdir(parents=True, exist_ok=True)
    filename = _filename_from_url(url)
    target = data_dir / filename
    try:
        size = _remote_size(url)
        if size is not None and size > max_bytes:
            return {
                "url": url,
                "status": "skipped",
                "reason": f"Remote file is larger than limit ({size} > {max_bytes} bytes).",
                "path": "",
                "bytes": size,
            }
        downloaded = 0
        request = Request(url, headers={"User-Agent": "R2A/0.1"})
        with urlopen(request, timeout=30) as response, target.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    handle.close()
                    target.unlink(missing_ok=True)
                    return {
                        "url": url,
                        "status": "skipped",
                        "reason": f"Downloaded bytes exceeded limit ({downloaded} > {max_bytes} bytes).",
                        "path": "",
                        "bytes": downloaded,
                    }
                handle.write(chunk)
        return {"url": url, "status": "ok", "reason": "", "path": str(target), "bytes": downloaded}
    except Exception as exc:
        target.unlink(missing_ok=True)
        return {"url": url, "status": "failed", "reason": f"{type(exc).__name__}: {exc}", "path": "", "bytes": 0}


def _remote_size(url: str) -> int | None:
    try:
        request = Request(url, method="HEAD", headers={"User-Agent": "R2A/0.1"})
        with urlopen(request, timeout=15) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length else None
    except Exception:
        return None


def _filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    return name or f"dataset_{uuid.uuid4().hex[:8]}"


def _write_workspace_log(repo_dir: Path, filename: str, text: str) -> None:
    logs_dir = repo_dir / ".r2a" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / filename).write_text(text, encoding="utf-8")


def _ensure_git_repo(repo_dir: Path) -> None:
    if (repo_dir / ".git").exists() or shutil.which("git") is None:
        return
    completed = subprocess.run(
        ["git", "init"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    log_text = f"$ git init\n\nSTDOUT:\n{completed.stdout}\n\nSTDERR:\n{completed.stderr}\n"
    _write_workspace_log(repo_dir, "git_init.log", log_text)
