from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKSPACE_MANIFEST_FILENAME = "WORKSPACE_MANIFEST.json"
WORKSPACE_MANIFEST_RELATIVE = Path(".r2a") / WORKSPACE_MANIFEST_FILENAME


def workspace_manifest_path(workspace_dir: str | Path) -> Path:
    return Path(workspace_dir).expanduser().resolve() / WORKSPACE_MANIFEST_RELATIVE


def workspace_manifest_exists(workspace_dir: str | Path) -> bool:
    return workspace_manifest_path(workspace_dir).is_file()


def build_workspace_manifest(
    *,
    workspace_id: str,
    workspace_path: str | Path,
    paper_path: str,
    planner_backend: str = "ccr_text",
    engineer_executor: str = "mock",
    paper_backend: str = "preprocess",
    status: str = "created",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workspace_id": workspace_id,
        "workspace_path": str(Path(workspace_path).expanduser().resolve()),
        "paper_path": paper_path,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": status,
        "paper_backend": paper_backend,
        "planner_backend": planner_backend,
        "engineer_executor": engineer_executor,
    }
    if extra:
        payload.update(extra)
    return payload


def write_workspace_manifest(workspace_dir: str | Path, manifest: dict[str, Any]) -> Path:
    path = workspace_manifest_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_workspace_manifest(workspace_dir: str | Path) -> dict[str, Any] | None:
    path = workspace_manifest_path(workspace_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def workspace_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    workspace_dir = str(manifest.get("workspace_path", "") or "")
    repo_path = str(manifest.get("repo_path", "") or "")
    if not repo_path and workspace_dir:
        candidate = Path(workspace_dir) / "repo"
        if candidate.is_dir():
            repo_path = str(candidate)
    metadata_path = str(Path(workspace_dir) / "metadata.json") if workspace_dir else ""
    return {
        "run_id": str(manifest.get("workspace_id", manifest.get("run_id", ""))),
        "workspace_dir": workspace_dir,
        "paper_path": str(manifest.get("paper_path", "")),
        "repo_path": repo_path,
        "data_dir": str(manifest.get("data_dir", "")),
        "metadata_path": metadata_path,
        "goal": str(manifest.get("goal", "")),
        "repo_download": manifest.get("repo_download", {}),
        "dataset_downloads": manifest.get("dataset_downloads", []),
    }
