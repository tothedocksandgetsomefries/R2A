from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

from r2a.core.paths import artifact_dir, report_path
from r2a.core.user_hints import user_hints_from_state, write_user_hints_artifact


SOURCE_DIR = "source"
SOURCE_ARTIFACT_DIR = "artifacts"
SOURCE_SUFFIXES = {
    ".py",
    ".cpp",
    ".c",
    ".cc",
    ".h",
    ".hpp",
    ".java",
    ".rs",
    ".go",
    ".js",
    ".ts",
    ".m",
    ".cu",
    ".sh",
    ".ps1",
    ".bat",
}
CONFIG_NAMES = {
    "CMakeLists.txt",
    "Makefile",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "environment.yml",
    "environment.yaml",
    "package.json",
    "Cargo.toml",
    "pom.xml",
}
IGNORED_ROOTS = {".git", ".r2a", "results", "__pycache__", ".pytest_cache", ".venv", "venv"}

# Candidate taxonomy for source acquisition.
SOURCE_CANDIDATE_TYPES = {
    "official_implementation_repo",
    "official_base_repo",
    "dependency_repo",
    "benchmark_repo",
    "dataset_or_model",
    "documentation",
    "unknown_candidate",
}

LEGACY_CLASSIFICATION_BY_TYPE = {
    "official_implementation_repo": "official",
    "official_base_repo": "related_or_dependency",
    "dependency_repo": "related_or_dependency",
    "benchmark_repo": "benchmark",
    "dataset_or_model": "dataset_or_model",
    "documentation": "documentation",
    "unknown_candidate": "unknown",
}

# Strong phrases for paper-specific implementation repositories. Weak artifact
# language is handled separately so "Artifact URL: Not available" cannot promote
# nearby baseline/dependency links.
OFFICIAL_CODE_PHRASES = [
    "code is available",
    "code available",
    "source code is available",
    "source available",
    "our code",
    "our implementation",
    "implementation is available",
    "code can be found",
    "source can be found",
    "code repository",
    "github repository",
    "official implementation",
    "official code",
    "our github",
    "project page",
]

WEAK_ARTIFACT_PHRASES = [
    "artifact",
    "artifact url",
    "reproducibility",
    "reproducible",
]

DEPENDENCY_OR_BASELINE_PHRASES = [
    "baseline",
    "we compare with",
    "compared with",
    "built on",
    "based on",
    "dependency",
    "dependencies",
    "using",
    "we use",
    "leveraging",
    "library",
    "tool",
    "framework",
    "prior work",
    "related work",
]

BENCHMARK_PHRASES = [
    "benchmark",
    "evaluation on",
    "evaluated on",
    "experiment on",
]

DOCUMENTATION_PHRASES = [
    "documentation",
    "docs",
    "readme",
]

DATASET_OR_MODEL_PATTERNS = [
    r"huggingface\.co/datasets",
    r"huggingface\.co/[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$",
]

# Known dependency/base repositories that should not be promoted to paper-specific
# implementation repos without exact branch/path/commit evidence.
KNOWN_BASE_REPOS = {
    "facebookresearch/faiss",
}

KNOWN_DEPENDENCY_REPOS = {
    "microsoft/diskann",
    "milvus-io/milvus",
    "weaviate/weaviate",
    "pinecone-io/pinecone",
    "qdrant/qdrant",
    "chroma-core/chroma",
    "lancedb/lancedb",
}


def acquire_source(state: dict[str, Any], workspace: str | Path | None = None) -> dict[str, Any]:
    """Discover or acquire the source artifact before Planner runs."""
    repo = Path(str(state.get("repo_path", "") or workspace or ".")).resolve()
    user_hints = user_hints_from_state(state)
    state = {**state, "user_hints": user_hints}
    user_hints_path = write_user_hints_artifact(repo, user_hints)
    result = discover_source(state, workspace=workspace)
    path = report_path(repo, "source_acquisition")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_source_verification_csv(repo, result)
    metadata = dict(state.get("metadata", {}) or {})
    metadata["source_acquisition"] = result
    metadata["user_hints"] = user_hints
    return {
        **state,
        "source_acquisition": result,
        "source_acquisition_path": str(path),
        "user_hints_path": str(user_hints_path),
        "metadata": metadata,
    }


def discover_source(state: dict[str, Any], workspace: str | Path | None = None) -> dict[str, Any]:
    repo = Path(str(state.get("repo_path", "") or workspace or ".")).resolve()
    if not isinstance(state.get("user_hints"), dict):
        state = {**state, "user_hints": user_hints_from_state(state)}
    user_hint_candidates = _user_hint_source_candidates(state, repo)

    # PRIORITY 1: Check for existing source in artifacts/source directory
    artifact_source = artifact_dir(repo) / SOURCE_ARTIFACT_DIR / SOURCE_DIR
    if _has_meaningful_source(artifact_source):
        return _available_source(
            repo,
            source_type="official",
            local_path=artifact_source,
            repo_url=_git_remote(artifact_source),
            found_in="artifacts/source",
            confidence="high",
        )

    # PRIORITY 2: Check for existing source in workspace root
    existing = _existing_source(repo)
    if existing:
        user_hint_selected = user_hint_candidates[0] if user_hint_candidates else None
        return _available_source(
            repo,
            source_type="user_provided_hint" if user_hint_selected else "local_workspace",
            local_path=existing,
            repo_url=_git_remote(existing),
            found_in="workspace_user_provided_hint" if user_hint_selected else "workspace",
            confidence="medium" if user_hint_selected else "medium",
            warnings=["Using existing local workspace source; official provenance may still need Manager verification."],
            candidates=_mark_selected_candidate(user_hint_candidates, user_hint_selected) if user_hint_selected else None,
            selected_source=_selected_source_payload(user_hint_selected) if user_hint_selected else None,
        )

    # PRIORITY 3: Check for explicit source path in state
    explicit_source = _explicit_source_path(state)
    if explicit_source and explicit_source.exists() and _has_meaningful_source(explicit_source):
        user_hint_selected = user_hint_candidates[0] if user_hint_candidates else None
        return _available_source(
            repo,
            source_type="user_provided_hint" if user_hint_selected else "local_path",
            local_path=explicit_source,
            repo_url=_git_remote(explicit_source),
            found_in="state.source_repo_path:user_provided_hint" if user_hint_selected else "state.source_repo_path",
            confidence="high",
            candidates=_mark_selected_candidate(user_hint_candidates, user_hint_selected) if user_hint_selected else None,
            selected_source=_selected_source_payload(user_hint_selected) if user_hint_selected else None,
        )

    # PRIORITY 4: Extract and classify candidate URLs from paper/user hints
    all_candidates = _extract_all_candidate_urls(state, repo)
    classified_candidates = _classify_candidate_urls(all_candidates, repo)

    # Find high-confidence official source candidate
    official_candidate = _select_official_candidate(classified_candidates)

    if official_candidate and official_candidate.get("candidate_type") == "official_implementation_repo":
        # Only clone if we have high-confidence official source
        clone_result = _clone_source(
            official_candidate["url"],
            repo,
            source_type="official" if official_candidate.get("origin") != "user_provided_hint" else "user_provided_hint",
            found_in=str(official_candidate.get("origin") or "paper_or_workspace"),
            confidence=str(official_candidate.get("confidence") or "high"),
        )
        selected_candidates = _mark_selected_candidate(classified_candidates, official_candidate)

        # CRITICAL FIX: Check both 'source_status' (new) and 'status' (legacy)
        if clone_result.get("source_status") == "available" or clone_result.get("status") == "available":
            # Add selection_reason to the result
            result = clone_result
            result["candidates"] = selected_candidates
            result["candidate_type"] = official_candidate.get("candidate_type")
            result["selected_source"] = _selected_source_payload(official_candidate)
            return result

        # Clone failed
        if clone_result.get("source_status") == "clone_failed" or clone_result.get("status") == "clone_failed":
            return _clone_failed_source(
                repo,
                repo_url=str(clone_result.get("repo_url") or official_candidate["url"]),
                message=clone_result.get("message", "git clone failed"),
                exit_code=clone_result.get("exit_code"),
                candidates=selected_candidates,
                selected_source=_selected_source_payload(official_candidate),
                selection_reason=official_candidate.get("selection_reason") or "Official implementation source candidate",
                source_url_normalization=clone_result.get("source_url_normalization"),
                source_type="official" if official_candidate.get("origin") != "user_provided_hint" else "user_provided_hint",
            )

        # Legacy fallback
        return _clone_failed_source(
            repo,
            repo_url=str(clone_result.get("repo_url") or official_candidate["url"]),
            message=clone_result.get("message", "git clone failed"),
            candidates=selected_candidates,
            selected_source=_selected_source_payload(official_candidate),
            source_url_normalization=clone_result.get("source_url_normalization"),
            source_type="official" if official_candidate.get("origin") != "user_provided_hint" else "user_provided_hint",
        )

    # No high-confidence official source found - do not clone
    base_repo_candidates = [
        candidate
        for candidate in classified_candidates
        if candidate.get("candidate_type") in {"official_base_repo", "dependency_repo"}
    ]
    if base_repo_candidates:
        return _missing_source(
            repo,
            reason_code="BASE_REPO_AVAILABLE_IMPLEMENTATION_MISSING",
            message=(
                "Candidate base/dependency repositories were found, but no paper-specific official "
                "implementation repository was identified. Provide the exact paper implementation URL, "
                "fork, branch, commit, artifact package, or local source path."
            ),
            candidates=_mark_selected_candidate(classified_candidates, None),
            source_status="base_repo_available_implementation_missing",
            source_type="missing",
            base_repo_candidates=base_repo_candidates,
            warnings=[
                "Base/dependency repositories are not treated as official implementation evidence without paper-specific branch/path/commit evidence."
            ],
        )
    return _missing_source(
        repo,
        reason_code="OFFICIAL_SOURCE_NOT_FOUND",
        message="No high-confidence official source URL found in paper artifacts. "
                f"Found {len(classified_candidates)} candidate URLs but none classified as official_implementation_repo. "
                "Please provide the official source repository URL.",
        candidates=_mark_selected_candidate(classified_candidates, None),
    )


def read_source_acquisition(repo: str | Path) -> dict[str, Any]:
    return _read_json(report_path(repo, "source_acquisition"))


def _available_source(
    repo: Path,
    *,
    source_type: str,
    local_path: Path,
    repo_url: str,
    found_in: str,
    confidence: str,
    warnings: list[str] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    selected_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    branch, commit = _git_branch_commit(local_path)
    # CRITICAL FIX: Filter empty warnings
    filtered_warnings = [w for w in (warnings or []) if w and w.strip()]
    result = {
        "schema_version": 2,
        "source_status": "available",
        "source_type": source_type,
        "repo_url": repo_url or "",
        "local_path": str(local_path),
        "commit": commit,
        "branch": branch,
        "provenance": {
            "found_in": found_in,
            "confidence": confidence,
            "evidence": str(local_path),
        },
        "blockers": [],
        "warnings": filtered_warnings,
    }
    if candidates:
        result["candidates"] = candidates
    if selected_source:
        result["selected_source"] = selected_source
    return result


def _missing_source(
    repo: Path,
    *,
    reason_code: str,
    message: str,
    candidates: list[dict[str, Any]],
    warnings: list[str] | None = None,
    source_status: str = "not_found",
    source_type: str = "unknown",
    base_repo_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # CRITICAL FIX: Filter empty warnings
    filtered_warnings = [w for w in (warnings or []) if w and w.strip()]
    return {
        "schema_version": 2,
        "source_status": source_status,
        "source_type": source_type,
        "repo_url": None,
        "local_path": None,
        "commit": None,
        "branch": None,
        "provenance": {
            "found_in": None,
            "confidence": "low",
            "evidence": f"{len(candidates)} candidate URLs found but none classified as official",
        },
        "blockers": [
            {
                "blocker_id": "missing_source:source_acquisition",
                "type": "missing_source",
                "reason_code": reason_code,
                "requires_user_input": True,
                "retryable": False,
                "source": "source_acquisition",
                "last_message": message,
                "required_inputs": ["official_source_url_or_local_source_path"],
            }
        ],
        "candidates": candidates,
        "base_repo_candidates": base_repo_candidates or [],
        "selected_source": None,
        "selection_reason": "No high-confidence official implementation repository URL found",
        "warnings": filtered_warnings,
    }


def _clone_failed_source(
    repo: Path,
    *,
    repo_url: str,
    message: str,
    exit_code: int | None = None,
    candidates: list[dict[str, Any]] | None = None,
    selected_source: dict[str, Any] | None = None,
    selection_reason: str = "",
    source_url_normalization: Any = None,
    source_type: str = "official",
) -> dict[str, Any]:
    """Return a clone_failed status when URL was found but clone did not produce usable source."""
    result = {
        "schema_version": 2,
        "source_status": "clone_failed",
        "source_type": source_type,
        "repo_url": repo_url,
        "local_path": None,
        "commit": None,
        "branch": None,
        "provenance": {
            "found_in": "paper_or_workspace",
            "confidence": "medium",
            "evidence": repo_url,
        },
        "blockers": [
            {
                "blocker_id": "clone_failed:source_acquisition",
                "type": "missing_source",
                "reason_code": "OFFICIAL_SOURCE_CLONE_FAILED",
                "requires_user_input": True,
                "retryable": True,
                "source": "source_acquisition",
                "last_message": message,
                "required_inputs": ["official_source_url_or_local_source_path"],
                "details": {
                    "exit_code": exit_code,
                    "repo_url": repo_url,
                    "source_url_normalization": source_url_normalization or {},
                },
            }
        ],
        "candidates": candidates or [],
        "selected_source": selected_source
        or {
            "url": repo_url,
            "candidate_type": "official_implementation_repo",
            "classification": "official",
            "confidence": "high",
            "selection_reason": selection_reason or "High-confidence official source candidate",
        },
        "warnings": [f"Failed to clone from {repo_url}: {message}"],
    }
    if source_url_normalization:
        result["source_url_normalization"] = source_url_normalization
    return result


def _clone_source(
    url: str,
    repo: Path,
    *,
    source_type: str = "official",
    found_in: str = "paper_or_workspace",
    confidence: str = "high",
) -> dict[str, Any]:
    normalization = _normalize_github_clone_url(url)
    clone_url = str(normalization.get("repo_url") or url)
    branch = str(normalization.get("branch") or "")
    if shutil.which("git") is None:
        return {
            "status": "missing",
            "message": "git executable was not found on PATH.",
            "repo_url": clone_url,
            "source_url_normalization": normalization,
        }
    target = artifact_dir(repo) / SOURCE_ARTIFACT_DIR / SOURCE_DIR
    target.parent.mkdir(parents=True, exist_ok=True)

    # CRITICAL FIX: Check if target already exists and is usable BEFORE attempting clone
    if target.exists() and _has_meaningful_source(target):
        result = _available_source(repo, source_type=source_type, local_path=target, repo_url=clone_url, found_in=found_in, confidence=confidence)
        result["source_url_normalization"] = normalization
        return result

    # Clean up incomplete/invalid target if exists
    if target.exists():
        shutil.rmtree(target)

    # Attempt git clone
    command = ["git", "clone", "--depth", "1"]
    if branch:
        command.extend(["--branch", branch])
    command.extend([clone_url, str(target)])
    log_dir = artifact_dir(repo) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    try:
        completed = subprocess.run(command, cwd=str(repo), capture_output=True, text=True, check=False, timeout=120)
    except Exception as exc:
        (log_dir / "source_acquisition_git_clone.log").write_text(
            f"$ {' '.join(command)}\n\n"
            f"ORIGINAL_URL: {url}\n\n"
            f"NORMALIZED_REPO_URL: {clone_url}\n\n"
            f"NORMALIZED_BRANCH: {branch}\n\n"
            f"NORMALIZATION_REASON: {normalization.get('reason', '')}\n\n"
            f"NORMALIZATION_SUBPATH: {normalization.get('subpath', '') or ''}\n\n"
            f"TARGET_PATH: {target}\n\n"
            f"ERROR:\n{type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
        # Even on exception, check if target became usable
        if target.exists() and _has_meaningful_source(target):
            result = _available_source(
                repo,
                source_type=source_type,
                local_path=target,
                repo_url=clone_url,
                found_in=found_in,
                confidence="medium",
                warnings=[f"git clone raised {type(exc).__name__} but source directory is usable"]
            )
            result["source_url_normalization"] = normalization
            return result
        return {
            "status": "clone_failed",
            "message": f"{type(exc).__name__}: {exc}",
            "repo_url": clone_url,
            "source_url_normalization": normalization,
        }

    # CRITICAL FIX: Enhanced logging with all diagnostic fields
    source_usable = _has_meaningful_source(target)
    log_content = (
        f"$ {' '.join(command)}\n\n"
        f"COMMAND: {' '.join(command)}\n\n"
        f"ORIGINAL_URL: {url}\n\n"
        f"NORMALIZED_REPO_URL: {clone_url}\n\n"
        f"NORMALIZED_BRANCH: {branch}\n\n"
        f"NORMALIZATION_REASON: {normalization.get('reason', '')}\n\n"
        f"NORMALIZATION_SUBPATH: {normalization.get('subpath', '') or ''}\n\n"
        f"CWD: {repo}\n\n"
        f"TARGET_PATH: {target}\n\n"
        f"TIMEOUT_SECONDS: 120\n\n"
        f"EXIT_CODE: {completed.returncode}\n\n"
        f"SOURCE_USABLE_AFTER_CLONE: {source_usable}\n\n"
        f"STDOUT:\n{completed.stdout}\n\n"
        f"STDERR:\n{completed.stderr}\n"
    )
    (log_dir / "source_acquisition_git_clone.log").write_text(log_content, encoding="utf-8")

    # CRITICAL FIX: Directory usability takes precedence over returncode
    if source_usable:
        warnings = []
        if completed.returncode != 0:
            warnings.append(f"git clone returned non-zero exit code ({completed.returncode}) but source directory is usable")
        return _available_source(
            repo,
            source_type=source_type,
            local_path=target,
            repo_url=clone_url,
            found_in=found_in,
            confidence=confidence if completed.returncode == 0 else "medium",
            warnings=warnings
        ) | {"source_url_normalization": normalization}

    # Directory not usable - return clone_failed (not missing)
    if completed.returncode != 0:
        return {
            "status": "clone_failed",
            "message": completed.stderr.strip() or completed.stdout.strip() or "git clone failed",
            "repo_url": clone_url,
            "exit_code": completed.returncode,
            "source_url_normalization": normalization,
        }

    # returncode == 0 but directory not usable
    return {
        "status": "clone_failed",
        "message": "git clone succeeded but source directory lacks meaningful content",
        "repo_url": clone_url,
        "exit_code": completed.returncode,
        "source_url_normalization": normalization,
    }


def _write_source_verification_csv(repo: Path, result: dict[str, Any]) -> None:
    if result.get("source_status") != "available":
        return
    results = artifact_dir(repo) / "results"
    results.mkdir(parents=True, exist_ok=True)
    path = results / "source_verification.csv"
    if path.exists():
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("status", "artifact_url", "source_path", "branch", "commit", "tag", "readme_found", "build_docs_found", "experiment_scripts_found", "data_scripts_found", "notes"),
        )
        writer.writeheader()
        local_path = Path(str(result.get("local_path") or ""))
        writer.writerow(
            {
                "status": "PASS",
                "artifact_url": result.get("repo_url") or result.get("source_type", ""),
                "source_path": str(local_path),
                "branch": result.get("branch") or "",
                "commit": result.get("commit") or "",
                "tag": "",
                "readme_found": "yes" if _find_readmes(local_path) else "not_checked",
                "build_docs_found": "not_checked",
                "experiment_scripts_found": "not_checked",
                "data_scripts_found": "not_checked",
                "notes": "Source acquisition confirmed a local source artifact before Planner.",
            }
        )


def _existing_source(repo: Path) -> Path | None:
    artifact_source = artifact_dir(repo) / SOURCE_ARTIFACT_DIR / SOURCE_DIR
    if _has_meaningful_source(artifact_source):
        return artifact_source
    return repo if _has_meaningful_source(repo) else None


def _explicit_source_path(state: dict[str, Any]) -> Path | None:
    for key in ("source_repo_path", "official_source_path", "local_source_path"):
        value = str(state.get(key, "") or "").strip()
        if value:
            return Path(value).expanduser().resolve()
    metadata = state.get("metadata")
    if isinstance(metadata, dict):
        value = str(metadata.get("source_repo_path", "") or "").strip()
        if value:
            return Path(value).expanduser().resolve()
    return None


def _extract_all_candidate_urls(state: dict[str, Any], repo: Path) -> list[dict[str, Any]]:
    """Extract candidate URLs with provenance from state/user hints/paper artifacts."""
    candidates: list[dict[str, Any]] = []

    for key in ("official_source_url", "github_repo_url", "source_url"):
        value = str(state.get(key, "") or "").strip()
        if value:
            candidates.append(_raw_candidate(value, origin="user_provided_hint", context=f"state.{key}"))

    user_hints = state.get("user_hints")
    if isinstance(user_hints, dict):
        for url in _string_list(user_hints.get("source_urls")):
            candidates.append(_raw_candidate(url, origin="user_provided_hint", context="state.user_hints.source_urls"))

    metadata = state.get("metadata")
    if isinstance(metadata, dict):
        value = str(metadata.get("github_repo_url", "") or metadata.get("source_url", "") or "").strip()
        if value:
            candidates.append(_raw_candidate(value, origin="user_provided_hint", context="state.metadata.github_repo_url/source_url"))
        hints = metadata.get("user_hints")
        if isinstance(hints, dict):
            for url in _string_list(hints.get("source_urls")):
                candidates.append(_raw_candidate(url, origin="user_provided_hint", context="state.metadata.user_hints.source_urls"))

    output = _read_json(report_path(repo, "paper_output"))
    for url in _urls_from_object(output):
        candidates.append(_raw_candidate(url, origin="paper_artifact", context="PAPER_OUTPUT.json"))

    for key in ("paper_reproduction_card", "paper_context", "paper", "paper_evidence", "paper_sections", "paper_text"):
        text = _read_text(report_path(repo, key))
        if not text:
            continue
        for url in re.findall(r"https?://[^\s,;)\]]+", text):
            candidates.append(_raw_candidate(url, origin="paper_artifact", context=f"{key}: {_url_context(text, url)}"))

    deduped: dict[str, dict[str, Any]] = {}
    for item in candidates:
        url = _strip_trailing_url_punctuation(str(item.get("url") or ""))
        if not url:
            continue
        existing = deduped.get(url)
        if existing:
            if existing.get("origin") != "user_provided_hint" and item.get("origin") == "user_provided_hint":
                existing["origin"] = "user_provided_hint"
            contexts = [*list(existing.get("contexts", [])), *_string_list(item.get("contexts"))]
            existing["contexts"] = list(dict.fromkeys(contexts))
            continue
        item["url"] = url
        deduped[url] = item
    return list(deduped.values())


def _classify_candidate_urls(urls: list[str] | list[dict[str, Any]], repo: Path) -> list[dict[str, Any]]:
    """Classify each candidate URL using the source candidate taxonomy."""
    classified = []
    paper_context = _load_paper_context(repo)

    for raw in urls:
        if isinstance(raw, dict):
            url = str(raw.get("url") or "").strip()
            origin = str(raw.get("origin") or "paper_artifact")
            seed_contexts = _string_list(raw.get("contexts"))
        else:
            url = str(raw or "").strip()
            origin = "paper_artifact"
            seed_contexts = []
        if not url:
            continue
        contexts = [*seed_contexts, *paper_context.get("url_contexts", {}).get(url, [])]
        if not _looks_like_git_url(url):
            candidate_type = _candidate_type_from_legacy(_classify_non_git_url(url))
            classified.append(
                _classified_candidate(
                    url=url,
                    candidate_type=candidate_type,
                    origin=origin,
                    confidence="low",
                    contexts=contexts,
                    evidence=[f"Non-git URL classified as {candidate_type}"],
                )
            )
            continue

        classification_result = _classify_git_url(url, paper_context, origin=origin, contexts=contexts)
        classified.append(
            _classified_candidate(
                url=url,
                candidate_type=classification_result["candidate_type"],
                origin=origin,
                confidence=classification_result["confidence"],
                contexts=contexts,
                evidence=classification_result["evidence"],
                selection_reason=classification_result.get("selection_reason", ""),
            )
        )

    return classified


def _user_hint_source_candidates(state: dict[str, Any], repo: Path) -> list[dict[str, Any]]:
    hints = state.get("user_hints") if isinstance(state.get("user_hints"), dict) else {}
    raw = [
        _raw_candidate(url, origin="user_provided_hint", context="state.user_hints.source_urls")
        for url in _string_list(hints.get("source_urls"))
    ]
    return _classify_candidate_urls(raw, repo) if raw else []


def _classify_non_git_url(url: str) -> str:
    """Classify non-git URLs (dataset/model/docs)."""
    url_lower = url.lower()

    # Check for dataset/model patterns
    for pattern in DATASET_OR_MODEL_PATTERNS:
        if re.search(pattern, url_lower):
            return "dataset_or_model"

    # Check for documentation
    if any(phrase in url_lower for phrase in ["docs", "documentation", "readme"]):
        return "documentation"

    return "unknown"


def _classify_git_url(
    url: str,
    paper_context: dict[str, Any],
    *,
    origin: str = "paper_artifact",
    contexts: list[str] | None = None,
) -> dict[str, Any]:
    """Classify a git URL based on provenance and paper context."""
    url_lower = url.lower()
    result = {
        "candidate_type": "unknown_candidate",
        "confidence": "low",
        "evidence": [],
        "selection_reason": "",
    }
    contexts = contexts if contexts is not None else paper_context.get("url_contexts", {}).get(url, [])
    context_blob = "\n".join(contexts).lower()

    if origin == "user_provided_hint":
        result["candidate_type"] = "official_implementation_repo"
        result["confidence"] = "medium"
        result["evidence"] = ["User-provided source repository hint; requires source inspection and is not verified paper evidence."]
        result["selection_reason"] = "User-provided source repository hint has priority over low-confidence paper-derived URLs."
        return result

    owner_repo = _github_owner_repo_key(url_lower)
    is_known_base = owner_repo in KNOWN_BASE_REPOS
    is_known_dependency = owner_repo in KNOWN_DEPENDENCY_REPOS or any(dep in url_lower for dep in KNOWN_DEPENDENCY_REPOS)

    if any(marker in url_lower for marker in ("/docs", "-docs", "documentation")):
        result["candidate_type"] = "documentation"
        result["confidence"] = "medium"
        result["evidence"] = ["Repository URL appears to be documentation, not implementation source."]
        return result

    if is_known_base or is_known_dependency:
        if is_known_base and _base_repo_context(context_blob):
            result["candidate_type"] = "official_base_repo"
            result["confidence"] = "high"
            result["evidence"] = [f"Known base repository referenced by paper context: {owner_repo}"]
        else:
            result["candidate_type"] = "dependency_repo"
            result["confidence"] = "high"
            result["evidence"] = [f"Known dependency or baseline repository: {owner_repo or url}"]
        result["selection_reason"] = "Known base/dependency repository requires paper-specific branch/path/commit evidence before official implementation selection."
        return result

    # Check for official code phrases near the URL
    official_matches = []
    for phrase in OFFICIAL_CODE_PHRASES:
        for context in contexts:
            context_lower = context.lower()
            if phrase in context_lower:
                official_matches.append(f"Found phrase '{phrase}' near URL")

    if official_matches or _strong_artifact_match(context_blob):
        if not _negative_artifact_context(context_blob):
            result["candidate_type"] = "official_implementation_repo"
            result["confidence"] = "high"
            result["evidence"] = official_matches or ["Found paper-specific artifact/source availability context near URL"]
            result["selection_reason"] = result["evidence"][0]
            return result

    weak_artifact_matches = []
    for phrase in WEAK_ARTIFACT_PHRASES:
        if phrase in context_blob:
            weak_artifact_matches.append(f"Weak artifact phrase '{phrase}' near URL is not sufficient for official implementation classification")
    if weak_artifact_matches:
        result["candidate_type"] = "unknown_candidate"
        result["confidence"] = "low"
        result["evidence"] = weak_artifact_matches
        return result

    # Check for dependency/baseline phrases near the URL
    dependency_matches = []
    for phrase in DEPENDENCY_OR_BASELINE_PHRASES:
        for context in contexts:
            context_lower = context.lower()
            if phrase in context_lower:
                dependency_matches.append(f"Found phrase '{phrase}' near URL")

    if dependency_matches:
        result["candidate_type"] = "dependency_repo"
        result["confidence"] = "medium"
        result["evidence"] = dependency_matches
        return result

    # Check for benchmark phrases
    for phrase in BENCHMARK_PHRASES:
        for context in contexts:
            context_lower = context.lower()
            if phrase in context_lower:
                result["candidate_type"] = "benchmark_repo"
                result["confidence"] = "medium"
                result["evidence"] = [f"Found phrase '{phrase}' near URL"]
                return result

    for phrase in DOCUMENTATION_PHRASES:
        for context in contexts:
            context_lower = context.lower()
            if phrase in context_lower:
                result["candidate_type"] = "documentation"
                result["confidence"] = "medium"
                result["evidence"] = [f"Found phrase '{phrase}' near URL"]
                return result

    # Default: unknown classification
    result["evidence"] = ["No classification keywords found in context"]
    return result


def _candidate_type_from_legacy(classification: str) -> str:
    return {
        "dataset_or_model": "dataset_or_model",
        "documentation": "documentation",
        "benchmark": "benchmark_repo",
        "official": "official_implementation_repo",
        "related_or_dependency": "dependency_repo",
    }.get(classification, "unknown_candidate")


def _classified_candidate(
    *,
    url: str,
    candidate_type: str,
    origin: str,
    confidence: str,
    contexts: list[str],
    evidence: list[str],
    selection_reason: str = "",
) -> dict[str, Any]:
    candidate_type = candidate_type if candidate_type in SOURCE_CANDIDATE_TYPES else "unknown_candidate"
    evidence_span = _first_non_empty(contexts)
    reason = selection_reason or _first_non_empty(evidence) or f"Classified as {candidate_type}"
    return {
        "url": url,
        "candidate_type": candidate_type,
        "source_type": candidate_type,
        "classification": LEGACY_CLASSIFICATION_BY_TYPE[candidate_type],
        "origin": origin or "paper_artifact",
        "confidence": confidence,
        "evidence": evidence or [reason],
        "evidence_span": evidence_span,
        "context": evidence_span,
        "selection_reason": reason,
        "selected": False,
        "why_selected": "",
        "why_not_selected": _why_not_selected(candidate_type, origin, confidence),
    }


def _raw_candidate(url: str, *, origin: str, context: str) -> dict[str, Any]:
    return {
        "url": url,
        "origin": origin,
        "contexts": [context] if context else [],
    }


def _mark_selected_candidate(
    candidates: list[dict[str, Any]],
    selected: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    selected_url = str((selected or {}).get("url") or "")
    marked: list[dict[str, Any]] = []
    for candidate in candidates:
        item = dict(candidate)
        if selected_url and item.get("url") == selected_url:
            item["selected"] = True
            item["why_selected"] = item.get("selection_reason") or "Selected as source candidate."
            item["why_not_selected"] = ""
        else:
            item["selected"] = False
            item["why_selected"] = ""
            item["why_not_selected"] = item.get("why_not_selected") or _why_not_selected(
                str(item.get("candidate_type") or "unknown_candidate"),
                str(item.get("origin") or ""),
                str(item.get("confidence") or ""),
            )
        marked.append(item)
    return marked


def _selected_source_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": candidate.get("url", ""),
        "candidate_type": candidate.get("candidate_type", "unknown_candidate"),
        "classification": candidate.get("classification", "unknown"),
        "origin": candidate.get("origin", ""),
        "confidence": candidate.get("confidence", ""),
        "evidence_span": candidate.get("evidence_span", ""),
        "selection_reason": candidate.get("selection_reason", ""),
        "why_selected": candidate.get("selection_reason", ""),
    }


def _why_not_selected(candidate_type: str, origin: str, confidence: str) -> str:
    if candidate_type == "official_base_repo":
        return "Base repository is not a paper-specific implementation repo without exact branch/path/commit evidence."
    if candidate_type == "dependency_repo":
        return "Dependency/baseline repository is not selected as official implementation."
    if candidate_type in {"benchmark_repo", "dataset_or_model", "documentation"}:
        return f"{candidate_type} is not a source implementation repository."
    if candidate_type == "official_implementation_repo" and confidence == "low":
        return "Official implementation evidence is too weak."
    if origin == "user_provided_hint":
        return "User-provided hint requires inspection before paper-evidence claims."
    return "No sufficient evidence for paper-specific official implementation selection."


def _github_owner_repo_key(url: str) -> str:
    try:
        parsed = urlsplit(_strip_trailing_url_punctuation(url))
    except ValueError:
        return ""
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return ""
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return ""
    repo_name = parts[1].removesuffix(".git")
    return f"{parts[0].lower()}/{repo_name.lower()}"


def _base_repo_context(context_blob: str) -> bool:
    return any(
        phrase in context_blob
        for phrase in (
            "implemented in faiss",
            "faiss codebase",
            "modified faiss",
            "implemented in the faiss",
            "built on faiss",
            "based on faiss",
        )
    )


def _strong_artifact_match(context_blob: str) -> bool:
    if _negative_artifact_context(context_blob):
        return False
    if "artifact" not in context_blob:
        return False
    return any(marker in context_blob for marker in ("source", "code", "repository", "implementation", "available at", "available from"))


def _negative_artifact_context(context_blob: str) -> bool:
    return any(
        marker in context_blob
        for marker in (
            "artifact url: not available",
            "artifact url not available",
            "artifact: not available",
            "not available",
            "no official code",
            "no official source",
            "no repository link",
            "implementation unavailable",
        )
    )


def _url_context(text: str, url: str, *, radius: int = 200) -> str:
    match = re.search(re.escape(url), text)
    if not match:
        return ""
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return text[start:end]


def _first_non_empty(values: list[str]) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _load_paper_context(repo: Path) -> dict[str, Any]:
    """Load paper artifacts and extract URL contexts."""
    context = {
        "url_contexts": {},
        "full_text": ""
    }

    # Load paper text artifacts
    for key in ("paper_reproduction_card", "paper_context", "paper", "paper_evidence", "paper_sections", "paper_text"):
        text = _read_text(report_path(repo, key))
        if text:
            context["full_text"] += text + "\n"

            # Extract contexts around each URL
            urls = re.findall(r"https?://[^\s,;)\]]+", text)
            for url in set(urls):
                url_clean = url.rstrip(".,)")
                if url_clean not in context["url_contexts"]:
                    context["url_contexts"][url_clean] = []

                # Extract surrounding context (200 chars before and after)
                for match in re.finditer(re.escape(url), text):
                    start = max(0, match.start() - 200)
                    end = min(len(text), match.end() + 200)
                    surrounding = text[start:end]
                    context["url_contexts"][url_clean].append(surrounding)

    return context


def _select_official_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the best implementation source candidate from classified URLs."""
    # Priority 1: high-confidence paper-derived official implementation.
    for candidate in candidates:
        if (
            candidate.get("candidate_type") == "official_implementation_repo"
            and candidate.get("origin") != "user_provided_hint"
            and candidate.get("confidence") == "high"
        ):
            return candidate

    # Priority 2: user-provided source hints. They are selected for inspection,
    # but not treated as verified paper evidence.
    for candidate in candidates:
        if (
            candidate.get("candidate_type") == "official_implementation_repo"
            and candidate.get("origin") == "user_provided_hint"
            and candidate.get("confidence") in {"high", "medium"}
        ):
            return candidate

    # Priority 3: medium-confidence paper implementation candidates, if any.
    for candidate in candidates:
        if (
            candidate.get("candidate_type") == "official_implementation_repo"
            and candidate.get("origin") != "user_provided_hint"
            and candidate.get("confidence") == "medium"
        ):
            return candidate

    # No suitable candidate found
    return None


def _urls_from_object(value: Any) -> list[str]:
    urls: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in {"url", "repo_url", "artifact_url", "source_url"} and isinstance(item, str):
                urls.append(item)
            else:
                urls.extend(_urls_from_object(item))
    elif isinstance(value, list):
        for item in value:
            urls.extend(_urls_from_object(item))
    elif isinstance(value, str) and value.startswith(("http://", "https://", "git@")):
            urls.append(value)
    return urls


def _normalize_github_clone_url(url: str) -> dict[str, Any]:
    """Normalize GitHub page URLs into cloneable repo URL plus optional branch."""
    original = str(url or "").strip()
    cleaned = _strip_trailing_url_punctuation(original)
    parsed = urlsplit(cleaned)
    if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return {
            "original_url": original,
            "cleaned_url": cleaned,
            "repo_url": cleaned,
            "branch": None,
            "subpath": None,
            "normalized": False,
            "reason": "non_github_url",
            "diagnostic": "",
        }

    parts = [unquote(part) for part in parsed.path.split("/") if part]
    query_or_fragment = bool(parsed.query or parsed.fragment)
    if len(parts) < 2:
        clean_url = urlunsplit((parsed.scheme, "github.com", parsed.path.rstrip("/"), "", ""))
        return {
            "original_url": original,
            "cleaned_url": clean_url,
            "repo_url": clean_url,
            "branch": None,
            "subpath": None,
            "normalized": clean_url != original or query_or_fragment,
            "reason": "github_incomplete_url",
            "diagnostic": "GitHub URL does not include owner/repo.",
        }

    owner, repo_name = parts[0], parts[1]
    repo_url = urlunsplit((parsed.scheme, "github.com", f"/{owner}/{repo_name}", "", ""))
    branch: str | None = None
    subpath: str | None = None
    reason = "github_repo_root"
    diagnostic = ""

    if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
        view_kind = parts[2]
        branch = parts[3]
        remaining = parts[4:]
        subpath = "/".join(remaining) if remaining else None
        reason = f"github_{view_kind}_url"
        if view_kind == "tree" and remaining:
            reason = "github_tree_url_ambiguous_branch"
            diagnostic = "Tree URL has extra path segments; using the first segment after /tree/ as branch and recording the rest as subpath."
        elif view_kind == "blob" and subpath:
            diagnostic = "Blob URL subpath is recorded for diagnostics but ignored for clone checkout."
    elif len(parts) > 2:
        subpath = "/".join(parts[2:])
        reason = "github_repo_subpage"
        diagnostic = "GitHub repo subpage normalized to repo root for clone."

    normalized = (
        repo_url != original
        or cleaned != original
        or query_or_fragment
        or branch is not None
        or reason != "github_repo_root"
    )
    return {
        "original_url": original,
        "cleaned_url": cleaned,
        "repo_url": repo_url,
        "branch": branch,
        "subpath": subpath,
        "normalized": normalized,
        "reason": reason,
        "diagnostic": diagnostic,
    }


def _strip_trailing_url_punctuation(url: str) -> str:
    cleaned = str(url or "").strip().strip("<>\"'")
    while cleaned and cleaned[-1] in ".,;)]}":
        cleaned = cleaned[:-1]
    return cleaned


def _looks_like_git_url(url: str) -> bool:
    lowered = url.lower()
    return "github.com/" in lowered or lowered.endswith(".git") or lowered.startswith("git@")


def _has_meaningful_source(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        try:
            relative = item.relative_to(path)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] in IGNORED_ROOTS:
            continue
        if item.name in CONFIG_NAMES or item.suffix.lower() in SOURCE_SUFFIXES:
            return True
    return False


def _find_readmes(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(item for item in path.glob("README*") if item.is_file())


def _git_remote(path: Path) -> str:
    return _git_output(path, ["remote", "get-url", "origin"])


def _git_branch_commit(path: Path) -> tuple[str, str]:
    return (
        _git_output(path, ["rev-parse", "--abbrev-ref", "HEAD"]),
        _git_output(path, ["rev-parse", "HEAD"]),
    )


def _git_output(path: Path, args: list[str]) -> str:
    if shutil.which("git") is None or not (path / ".git").exists():
        return ""
    completed = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, check=False)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    except OSError:
        return ""
