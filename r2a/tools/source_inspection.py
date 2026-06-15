from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from r2a.core.paths import report_path
from r2a.tools.source_acquisition import read_source_acquisition


ENVIRONMENT_FILES = (
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "environment.yml",
    "environment.yaml",
    "Pipfile",
    "package.json",
    "Cargo.toml",
    "CMakeLists.txt",
    "Makefile",
)
ENTRYPOINT_NAMES = {
    "main.py",
    "run.py",
    "train.py",
    "eval.py",
    "evaluate.py",
    "test.py",
    "benchmark.py",
    "demo.py",
    "inference.py",
}
IGNORED_ROOTS = {".git", ".r2a", "results", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules"}


def inspect_source(state: dict[str, Any], workspace: str | Path | None = None) -> dict[str, Any]:
    repo = Path(str(state.get("repo_path", "") or workspace or ".")).resolve()
    result = build_source_inspection(state, workspace=workspace)
    path = report_path(repo, "source_inspection")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    metadata = dict(state.get("metadata", {}) or {})
    metadata["source_inspection"] = result
    return {
        **state,
        "source_inspection": result,
        "source_inspection_path": str(path),
        "metadata": metadata,
    }


def build_source_inspection(state: dict[str, Any], workspace: str | Path | None = None) -> dict[str, Any]:
    repo = Path(str(state.get("repo_path", "") or workspace or ".")).resolve()
    acquisition = _source_acquisition(state, repo)
    source_path = Path(str(acquisition.get("local_path") or repo)).resolve()
    if acquisition.get("source_status") != "available" or not source_path.exists() or not _has_files(source_path):
        return _blocked(source_path)

    readmes = _relative_files(source_path, ["README*"])
    environment_files = _relative_named_files(source_path, ENVIRONMENT_FILES)
    entrypoints = _entrypoints(source_path)
    test_commands = _test_commands(source_path)
    demo_commands = _demo_commands(entrypoints)
    readme_text = "\n".join(_read_text(source_path / item) for item in readmes[:3])
    dataset_requirements = _dataset_requirements(readme_text)
    checkpoint_requirements = _checkpoint_requirements(readme_text)
    languages = _languages(source_path)
    frameworks = _frameworks(source_path, readme_text)
    supports_l2 = bool(environment_files or test_commands or entrypoints)
    dataset_blocked = any(item.get("required") and not item.get("available") for item in dataset_requirements)
    return {
        "schema_version": 1,
        "inspection_status": "complete",
        "repo_root": str(source_path),
        "language": languages,
        "frameworks": frameworks,
        "readme_files": readmes,
        "environment_files": environment_files,
        "entrypoints": entrypoints,
        "test_commands": test_commands,
        "demo_commands": demo_commands,
        "dataset_requirements": dataset_requirements,
        "checkpoint_requirements": checkpoint_requirements,
    # NOTE: supports is now advisory hints, not hard caps.
    # Static inspection cannot reliably determine runtime feasibility.
    # Planner and Manager should use actual execution evidence.
    "supports": {
        "L1_static_source_verification": True,
        "L2_build_or_smoke": supports_l2,
        "L3_reduced_experiment": "unknown" if (bool(entrypoints) and dataset_blocked) else bool(entrypoints),
        "L4_reduced_paper_aligned": "unknown" if (bool(entrypoints) and dataset_blocked) else bool(entrypoints),
    },
        "planner_hints": _planner_hints(environment_files, entrypoints, dataset_requirements),
        "blockers": [],
        "warnings": [],
    }


def read_source_inspection(repo: str | Path) -> dict[str, Any]:
    return _read_json(report_path(repo, "source_inspection"))


def _blocked(source_path: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "inspection_status": "blocked",
        "repo_root": str(source_path),
        "language": [],
        "frameworks": [],
        "readme_files": [],
        "environment_files": [],
        "entrypoints": [],
        "test_commands": [],
        "demo_commands": [],
        "dataset_requirements": [],
        "checkpoint_requirements": [],
        "supports": {
            "L1_static_source_verification": False,
            "L2_build_or_smoke": False,
            "L3_reduced_experiment": False,
            "L4_reduced_paper_aligned": False,
        },
        "planner_hints": ["Source repo is missing or empty; do not plan source-dependent execution."],
        "blockers": [
            {
                "blocker_id": "empty_repo:source_inspection",
                "type": "empty_repo",
                "reason_code": "SOURCE_REPO_EMPTY_OR_MISSING",
                "requires_user_input": True,
                "retryable": False,
                "source": "source_inspection",
                "last_message": "Source repo is empty or missing before Planner.",
                "required_inputs": ["official_source_url_or_local_source_path"],
            }
        ],
        "warnings": [],
    }


def _source_acquisition(state: dict[str, Any], repo: Path) -> dict[str, Any]:
    direct = state.get("source_acquisition")
    if isinstance(direct, dict):
        return direct
    return read_source_acquisition(repo)


def _has_files(path: Path) -> bool:
    if not path.exists():
        return False
    for item in path.rglob("*"):
        if item.is_file() and not _ignored(item, path):
            return True
    return False


def _relative_files(root: Path, patterns: list[str]) -> list[str]:
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(root.rglob(pattern))
    return _relative_sorted(root, [item for item in paths if item.is_file() and not _ignored(item, root)])


def _relative_named_files(root: Path, names: tuple[str, ...]) -> list[str]:
    wanted = set(names)
    return _relative_sorted(root, [item for item in root.rglob("*") if item.is_file() and item.name in wanted and not _ignored(item, root)])


def _entrypoints(root: Path) -> list[str]:
    candidates = [item for item in root.rglob("*.py") if item.name in ENTRYPOINT_NAMES and not _ignored(item, root)]
    scripts = [item for item in root.rglob("*.sh") if not _ignored(item, root)]
    return _relative_sorted(root, candidates + scripts)[:30]


def _test_commands(root: Path) -> list[str]:
    commands: list[str] = []
    if any(item.name.startswith("test_") and item.suffix == ".py" for item in root.rglob("*.py") if not _ignored(item, root)):
        commands.append("python -m pytest")
    if (root / "package.json").exists():
        text = _read_text(root / "package.json")
        if '"test"' in text:
            commands.append("npm test")
    if (root / "Makefile").exists():
        commands.append("make test")
    return commands


def _demo_commands(entrypoints: list[str]) -> list[str]:
    return [f"python {path}" for path in entrypoints if Path(path).name in {"demo.py", "main.py", "run.py"}][:5]


def _dataset_requirements(text: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    lowered = text.lower()
    if any(token in lowered for token in ("dataset", "data set", "ground truth", "query")):
        output.append(
            {
                "name": _first_dataset_name(text),
                "source": "README",
                "required": True,
                "available": False,
                "notes": "Dataset/input is mentioned in source documentation; availability must be verified before L3/L4.",
            }
        )
    return output


def _checkpoint_requirements(text: str) -> list[dict[str, Any]]:
    lowered = text.lower()
    if not any(token in lowered for token in ("checkpoint", "pretrained", "weights", "model zoo")):
        return []
    return [
        {
            "name": "checkpoint_or_pretrained_weights",
            "source": "README",
            "required": True,
            "available": False,
            "notes": "Checkpoint/weights are mentioned but not acquired by SourceInspection.",
        }
    ]


def _languages(root: Path) -> list[str]:
    suffixes = {item.suffix.lower() for item in root.rglob("*") if item.is_file() and not _ignored(item, root)}
    languages = []
    if ".py" in suffixes:
        languages.append("python")
    if suffixes & {".cpp", ".cc", ".c", ".h", ".hpp"}:
        languages.append("cpp")
    if suffixes & {".js", ".ts"}:
        languages.append("javascript")
    if ".rs" in suffixes:
        languages.append("rust")
    if ".go" in suffixes:
        languages.append("go")
    return languages


def _frameworks(root: Path, text: str) -> list[str]:
    combined = text.lower()
    for name in ("requirements.txt", "pyproject.toml", "setup.py", "package.json"):
        combined += "\n" + _read_text(root / name).lower()
    frameworks = []
    for marker, label in (("torch", "pytorch"), ("tensorflow", "tensorflow"), ("jax", "jax"), ("faiss", "faiss"), ("sklearn", "scikit-learn"), ("cmake", "cmake")):
        if marker in combined:
            frameworks.append(label)
    return list(dict.fromkeys(frameworks))


def _planner_hints(environment_files: list[str], entrypoints: list[str], dataset_requirements: list[dict[str, Any]]) -> list[str]:
    hints = []
    if environment_files:
        hints.append("Prefer environment/build verification before benchmark execution.")
    if entrypoints:
        hints.append("Use discovered entrypoints only when task scope and inputs allow it.")
    if any(item.get("required") and not item.get("available") for item in dataset_requirements):
        hints.append("Dataset is required for L3/L4; keep contract_mode verification_only until official inputs are available.")
    if not hints:
        hints.append("No executable entrypoint was identified; Planner should restrict scope to static verification.")
    return hints


def _first_dataset_name(text: str) -> str:
    for line in text.splitlines():
        lowered = line.lower()
        if any(token in lowered for token in ("dataset", "ground truth", "query")):
            return line.strip()[:120] or "dataset"
    return "dataset"


def _relative_sorted(root: Path, paths: list[Path]) -> list[str]:
    output = []
    for item in paths:
        try:
            output.append(str(item.relative_to(root)).replace("\\", "/"))
        except ValueError:
            output.append(str(item))
    return sorted(dict.fromkeys(output))


def _ignored(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return bool(relative.parts and relative.parts[0] in IGNORED_ROOTS)


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
