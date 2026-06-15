from __future__ import annotations

import hashlib
import os
from pathlib import Path

from r2a.core.paths import resolve_repo_path


R2A_RUNTIME_ROOT_ENV = "R2A_RUNTIME_ROOT"


def runtime_root() -> Path:
    configured = os.environ.get(R2A_RUNTIME_ROOT_ENV, "").strip()
    if configured:
        return _safe_root(Path(configured))
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return _safe_root(Path(local_app_data) / "R2A" / "runtime")
    return _safe_root(Path.home() / ".r2a_runtime")


def repo_runtime_dir(repo_path: str | Path) -> Path:
    repo = resolve_repo_path(repo_path)
    digest = hashlib.sha256(str(repo).lower().encode("utf-8", errors="surrogateescape")).hexdigest()[:16]
    return runtime_root() / "repos" / digest


def runtime_runs_dir(repo_path: str | Path) -> Path:
    return repo_runtime_dir(repo_path) / "runs"


def run_record_path(repo_path: str | Path, run_id: str) -> Path:
    return _safe_child(runtime_runs_dir(repo_path), f"{_safe_run_id(run_id)}.json")


def run_result_path(repo_path: str | Path, run_id: str) -> Path:
    return _safe_child(runtime_runs_dir(repo_path), f"{_safe_run_id(run_id)}.result.json")


def latest_run_pointer_path(repo_path: str | Path) -> Path:
    return repo_runtime_dir(repo_path) / "latest_run_id.txt"


def web_runtime_dir() -> Path:
    """Return the web-specific runtime directory under runtime root."""
    return runtime_root() / "web"


def active_run_pointer_path() -> Path:
    """Return the path to the active_run.json pointer file.

    This file stores the current run associated with the web UI session,
    enabling fast recovery without scanning all runtime records.
    """
    return web_runtime_dir() / "active_run.json"


def _safe_root(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not str(resolved):
        raise ValueError("Runtime root resolved to an empty path.")
    return resolved


def _safe_child(parent: Path, name: str) -> Path:
    child = (parent / name).resolve()
    parent_resolved = parent.resolve()
    if parent_resolved != child and parent_resolved not in child.parents:
        raise ValueError(f"Runtime path escapes its root: {child}")
    return child


def _safe_run_id(run_id: str) -> str:
    cleaned = "".join(ch for ch in str(run_id) if ch.isalnum() or ch in {"_", "-"})
    if not cleaned or cleaned != str(run_id):
        raise ValueError(f"Invalid run id: {run_id!r}")
    return cleaned
