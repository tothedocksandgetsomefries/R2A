from __future__ import annotations

from fnmatch import fnmatch
import hashlib
from pathlib import Path
import shutil
import subprocess
from typing import Any

FILESYSTEM_SNAPSHOT_EXCLUDED_DIRS = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", ".pytest_cache", "__pycache__"}
FILESYSTEM_CONTENT_HASH_LIMIT_BYTES = 5 * 1024 * 1024


def snapshot_stage_changes(repo_path: str | Path) -> dict[str, Any]:
    """Capture stage-visible changes before a non-Engineer AI stage starts.

    Git is preferred because it is fast and matches normal project review
    semantics. For uploaded zip/plain directories, fall back to a filesystem
    snapshot so stage boundaries remain enforceable instead of failing closed.
    """
    repo = Path(repo_path)
    result = _git_changed_files(repo)
    if not result["guard_available"]:
        return _filesystem_snapshot(repo, git_error=result["error"])
    dirty_files = result["changed_files"]
    signatures = {path: _path_signature(repo, path) for path in dirty_files}
    return {
        "guard_available": True,
        "guard_backend": "git",
        "error": "",
        "warning": "",
        "dirty_files": dirty_files,
        "changed_files": dirty_files,
        "dirty_file_signatures": signatures,
    }


def check_stage_allowed_modifications(
    repo_path: str | Path,
    stage: str,
    allowed_patterns: list[str],
    baseline: dict[str, Any] | set[str] | None = None,
) -> dict[str, Any]:
    repo = Path(repo_path)
    baseline_was_provided = baseline is not None
    baseline_snapshot = _normalize_baseline_snapshot(baseline)
    if baseline_was_provided and not baseline_snapshot["guard_available"]:
        return {
            "stage": stage,
            "allowed_patterns": allowed_patterns,
            "baseline_changed_files": baseline_snapshot["dirty_files"],
            "changed_files": [],
            "stage_changed_files": [],
            "signature_changed_files": [],
            "unexpected_modifications": [],
            "ok": False,
            "guard_available": False,
            "guard_backend": baseline_snapshot.get("guard_backend", "unavailable"),
            "error": baseline_snapshot["error"],
            "warning": "Stage guard could not verify modifications",
        }
    current_snapshot = snapshot_stage_changes(repo)
    if not current_snapshot["guard_available"]:
        return {
            "stage": stage,
            "allowed_patterns": allowed_patterns,
            "baseline_changed_files": baseline_snapshot["dirty_files"],
            "changed_files": [],
            "stage_changed_files": [],
            "signature_changed_files": [],
            "unexpected_modifications": [],
            "ok": False,
            "guard_available": False,
            "guard_backend": current_snapshot.get("guard_backend", "unavailable"),
            "error": current_snapshot["error"],
            "warning": "Stage guard could not verify modifications",
        }
    changed_files = current_snapshot["dirty_files"]
    baseline_files = set(baseline_snapshot["dirty_files"])
    stage_changed_files = sorted(set(changed_files) - baseline_files)
    signature_changed_files = _signature_changed_files(baseline_snapshot, current_snapshot)
    stage_touched_files = sorted(set(stage_changed_files) | set(signature_changed_files))
    unexpected = [path for path in stage_touched_files if not _is_allowed(path, allowed_patterns)]
    failure_category = "STAGE_BOUNDARY_VIOLATION" if unexpected else ""
    execution_status = f"{stage.upper()}_FORBIDDEN_WRITE" if unexpected else ""
    return {
        "stage": stage,
        "allowed_patterns": allowed_patterns,
        "baseline_changed_files": baseline_snapshot["dirty_files"],
        "changed_files": changed_files,
        "stage_changed_files": stage_touched_files,
        "new_dirty_files": stage_changed_files,
        "signature_changed_files": signature_changed_files,
        "unexpected_modifications": unexpected,
        "ok": not unexpected,
        "guard_available": True,
        "guard_backend": current_snapshot.get("guard_backend", "git"),
        "error": current_snapshot.get("error", ""),
        "warning": _stage_guard_warning(current_snapshot, unexpected),
        "failure_category": failure_category,
        "execution_status": execution_status,
    }


def _git_changed_files(repo: Path) -> dict[str, Any]:
    if shutil.which("git") is None:
        return _unavailable("git executable not found")
    try:
        completed = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return _unavailable("git executable not found")
    except Exception as exc:
        return _unavailable(f"git status failed: {type(exc).__name__}: {exc}")
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        stdout = (completed.stdout or "").strip()
        detail = stderr or stdout or f"exit code {completed.returncode}"
        return _unavailable(f"git status failed: {detail}")
    files: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path.replace("\\", "/"))
    return {
        "guard_available": True,
        "changed_files": files,
        "error": "",
    }


def _unavailable(error: str) -> dict[str, Any]:
    return {
        "guard_available": False,
        "changed_files": [],
        "error": error,
    }


def _filesystem_snapshot(repo: Path, *, git_error: str) -> dict[str, Any]:
    if not repo.exists() or not repo.is_dir():
        return {
            "guard_available": False,
            "guard_backend": "unavailable",
            "changed_files": [],
            "dirty_files": [],
            "dirty_file_signatures": {},
            "error": f"filesystem snapshot failed: repository path is not a directory: {repo}",
            "warning": "Stage guard could not verify modifications",
        }
    signatures: dict[str, str] = {}
    try:
        for path in sorted(item for item in repo.rglob("*") if item.is_file()):
            relative = path.relative_to(repo).as_posix()
            if _is_excluded_from_filesystem_snapshot(relative):
                continue
            signatures[relative] = _file_signature(path)
    except Exception as exc:
        return {
            "guard_available": False,
            "guard_backend": "unavailable",
            "changed_files": [],
            "dirty_files": [],
            "dirty_file_signatures": {},
            "error": f"filesystem snapshot failed after git unavailable ({git_error}): {type(exc).__name__}: {exc}",
            "warning": "Stage guard could not verify modifications",
        }
    files = sorted(signatures)
    return {
        "guard_available": True,
        "guard_backend": "filesystem",
        "changed_files": files,
        "dirty_files": files,
        "dirty_file_signatures": signatures,
        "error": git_error,
        "warning": f"Git status unavailable; using filesystem snapshot fallback: {git_error}",
    }


def _normalize_baseline_snapshot(baseline: dict[str, Any] | set[str] | None) -> dict[str, Any]:
    if baseline is None:
        return {"guard_available": True, "guard_backend": "manual", "error": "", "dirty_files": [], "changed_files": [], "dirty_file_signatures": {}}
    if isinstance(baseline, set):
        files = sorted(baseline)
        return {"guard_available": True, "guard_backend": "manual", "error": "", "dirty_files": files, "changed_files": files, "dirty_file_signatures": {}}
    files = baseline.get("dirty_files", baseline.get("changed_files", []))
    return {
        "guard_available": bool(baseline.get("guard_available", True)),
        "guard_backend": str(baseline.get("guard_backend", "git")),
        "error": str(baseline.get("error", "")),
        "dirty_files": list(files or []),
        "changed_files": list(files or []),
        "dirty_file_signatures": dict(baseline.get("dirty_file_signatures", {})),
    }


def _signature_changed_files(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    current_files = set(current["dirty_files"])
    current_signatures = current["dirty_file_signatures"]
    changed: list[str] = []
    for path, before_signature in baseline["dirty_file_signatures"].items():
        if path not in current_files:
            after_signature = "<clean-or-untracked>"
        else:
            after_signature = current_signatures.get(path)
        if after_signature != before_signature:
            changed.append(path)
    return sorted(changed)


def _path_signature(repo: Path, git_path: str) -> str:
    path = repo / git_path
    if not path.exists():
        return "<missing>"
    if path.is_dir():
        hasher = hashlib.sha256()
        for child in sorted(item for item in path.rglob("*") if item.is_file()):
            relative = child.relative_to(repo).as_posix()
            hasher.update(relative.encode("utf-8", errors="surrogateescape"))
            hasher.update(b"\0")
            hasher.update(_file_signature(child).encode("utf-8"))
            hasher.update(b"\0")
        return f"dir:{hasher.hexdigest()}"
    return _file_signature(path)


def _file_signature(path: Path) -> str:
    stat = path.stat()
    if stat.st_size > FILESYSTEM_CONTENT_HASH_LIMIT_BYTES:
        return f"large:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_excluded_from_filesystem_snapshot(relative_path: str) -> bool:
    parts = Path(relative_path).parts
    return any(part in FILESYSTEM_SNAPSHOT_EXCLUDED_DIRS for part in parts)


def _stage_guard_warning(current_snapshot: dict[str, Any], unexpected: list[str]) -> str:
    if unexpected:
        return "Stage guard detected unexpected modifications"
    if current_snapshot.get("guard_backend") == "filesystem":
        return str(current_snapshot.get("warning", "") or "Stage guard used filesystem snapshot fallback")
    return ""


def _is_allowed(path: str, allowed_patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    if _is_backend_attempt_log(normalized) or _is_stage_archive_log(normalized):
        return True
    for pattern in allowed_patterns:
        clean_pattern = pattern.replace("\\", "/").lstrip("./")
        if clean_pattern.endswith("/"):
            if normalized.startswith(clean_pattern):
                return True
        if normalized == clean_pattern or fnmatch(normalized, clean_pattern):
            return True
    return False


def _is_backend_attempt_log(path: str) -> bool:
    return (
        fnmatch(path, "r2a/logs/claude_*_attempt_*_stdout.log")
        or fnmatch(path, "r2a/logs/claude_*_attempt_*_stderr.log")
        or fnmatch(path, "r2a/runs/iter_*/logs/claude_*_attempt_*_stdout.log")
        or fnmatch(path, "r2a/runs/iter_*/logs/claude_*_attempt_*_stderr.log")
        or fnmatch(path, ".r2a/logs/claude_*_attempt_*_stdout.log")
        or fnmatch(path, ".r2a/logs/claude_*_attempt_*_stderr.log")
        or fnmatch(path, ".r2a/runs/iter_*/logs/claude_*_attempt_*_stdout.log")
        or fnmatch(path, ".r2a/runs/iter_*/logs/claude_*_attempt_*_stderr.log")
    )


def _is_stage_archive_log(path: str) -> bool:
    return any(
        fnmatch(path, pattern)
        for stage in ("paper", "planner", "manager", "reviewer", "final")
        for stream in ("stdout", "stderr")
        for pattern in (
            f"r2a/runs/iter_*/logs/{stage}_{stream}.log",
            f".r2a/runs/iter_*/logs/{stage}_{stream}.log",
        )
    )
