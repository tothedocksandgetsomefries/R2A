from __future__ import annotations

from dataclasses import dataclass
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

from r2a.core.paths import artifact_dir
from r2a.tools.wsl import DEFAULT_WSL_CACHE_DIR, DEFAULT_WSL_DISTRO, wsl_bash_command
from r2a.tools.process_tree import run_command_with_timeout


@dataclass(frozen=True)
class RuntimeCommand:
    stage: str
    command: list[str]
    returncode: int
    duration_sec: float
    stdout_tail: str
    stderr_tail: str


@dataclass(frozen=True)
class EngineerRuntimeResult:
    commands: list[RuntimeCommand]
    generated_files: list[str]
    successful_stages: list[str]
    failed_stages: list[str]

    @property
    def had_progress(self) -> bool:
        return bool(self.successful_stages or self.generated_files)


def run_engineer_runtime(
    repo_path: str | Path,
    *,
    timeout: int = 10800,
    iteration: int = 1,
    execution_environment: str = "windows",
    wsl_distro: str = DEFAULT_WSL_DISTRO,
    wsl_cache_dir: str = DEFAULT_WSL_CACHE_DIR,
) -> EngineerRuntimeResult:
    repo = Path(repo_path)
    r2a_dir = artifact_dir(repo)
    results_dir = r2a_dir / "results"
    logs_dir = r2a_dir / "logs"
    results_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    progress_path = results_dir / "engineer_progress.json"
    previous_progress = _load_previous_progress(progress_path)

    commands: list[RuntimeCommand] = []
    generated_files: list[str] = []
    successful_stages: list[str] = []
    failed_stages: list[str] = []
    stage_details: dict[str, dict[str, str | bool | int]] = {}

    dependency_path = results_dir / "dependency_setup.csv"
    if _can_reuse_stage(previous_progress, "dependency_check") and dependency_path.exists():
        commands.append(_reuse_command("dependency_check", dependency_path))
        successful_stages.append("dependency_check")
        generated_files.append(str(dependency_path))
        stage_details["dependency_check"] = {
            "status": "SKIPPED_REUSED",
            "evidence": str(dependency_path),
            "iteration": iteration,
            "reused_from_previous": True,
        }
    else:
        dependency_rows, dependency_commands = _dependency_probe(
            execution_environment=execution_environment,
            wsl_distro=wsl_distro,
            wsl_cache_dir=wsl_cache_dir,
        )
        commands.extend(dependency_commands)
        _write_csv(
            dependency_path,
            ("package", "command", "status", "version", "evidence_source", "notes"),
            dependency_rows,
        )
        generated_files.append(str(dependency_path))
        if any(row["status"] == "OK" for row in dependency_rows):
            successful_stages.append("dependency_check")
            stage_details["dependency_check"] = {
                "status": "OK",
                "evidence": str(dependency_path),
                "iteration": iteration,
                "reused_from_previous": False,
            }
        else:
            failed_stages.append("dependency_check")
            stage_details["dependency_check"] = {
                "status": "FAILED",
                "evidence": str(dependency_path),
                "iteration": iteration,
                "reused_from_previous": False,
            }

    build_rows: list[dict[str, str]] = []
    cmake_source = _find_cmake_source(repo)
    if cmake_source is None:
        build_rows.append(
            {
                "status": "BLOCKED",
                "command": "cmake configure",
                "exit_code": "NA",
                "duration_sec": "0",
                "component": "cmake_configure",
                "notes": "No CMakeLists.txt found in repo root or .r2a/artifacts/*.",
            }
        )
        failed_stages.append("cmake_configure")
        stage_details["cmake_configure"] = {
            "status": "BLOCKED",
            "evidence": "",
            "iteration": iteration,
            "reused_from_previous": False,
        }
    else:
        build_dir = cmake_source / _build_dir_name(execution_environment)
        if _can_reuse_stage(previous_progress, "cmake_configure") and _cmake_configure_evidence_exists(build_dir):
            commands.append(_reuse_command("cmake_configure", build_dir))
            build_rows.append(
                {
                    "status": "SKIPPED_REUSED",
                    "command": f"reuse {build_dir}",
                    "exit_code": "0",
                    "duration_sec": "0",
                    "component": "cmake_configure",
                    "notes": f"Reused previous CMake configure evidence: {build_dir / 'CMakeCache.txt'}",
                }
            )
            successful_stages.append("cmake_configure")
            stage_details["cmake_configure"] = {
                "status": "SKIPPED_REUSED",
                "source": str(cmake_source),
                "build_dir": str(build_dir),
                "evidence": str(build_dir / "CMakeCache.txt"),
                "iteration": iteration,
                "reused_from_previous": True,
            }
        else:
            configure = _run_cmake_configure(
                cmake_source,
                timeout=min(max(60, timeout // 8), 300),
                execution_environment=execution_environment,
                wsl_distro=wsl_distro,
                wsl_cache_dir=wsl_cache_dir,
            )
            commands.append(configure)
            build_rows.append(
                {
                    "status": "OK" if configure.returncode == 0 else "FAILED",
                    "command": _format_command(configure.command),
                    "exit_code": str(configure.returncode),
                    "duration_sec": f"{configure.duration_sec:.2f}",
                    "component": "cmake_configure",
                    "notes": _cmake_notes(cmake_source, configure),
                }
            )
            if configure.returncode == 0:
                successful_stages.append("cmake_configure")
                stage_details["cmake_configure"] = {
                    "status": "OK",
                    "source": str(cmake_source),
                    "build_dir": str(build_dir),
                    "evidence": str(build_dir / "CMakeCache.txt"),
                    "iteration": iteration,
                    "reused_from_previous": False,
                }
            else:
                failed_stages.append("cmake_configure")
                stage_details["cmake_configure"] = {
                    "status": "FAILED",
                    "source": str(cmake_source),
                    "build_dir": str(build_dir),
                    "evidence": "",
                    "iteration": iteration,
                    "reused_from_previous": False,
                }

    build_path = results_dir / "build_smoke.csv"
    _merge_write_csv(
        build_path,
        ("status", "command", "exit_code", "duration_sec", "component", "notes"),
        build_rows,
    )
    generated_files.append(str(build_path))
    combined_stage_details = {
        **_previous_stage_details(previous_progress),
        **_detect_checkpoint_stages(repo, cmake_source),
        **stage_details,
    }
    resolved_stages = _resolved_failed_stages(repo, failed_stages, combined_stage_details)
    active_failed_stages = [stage for stage in failed_stages if stage not in resolved_stages]
    for stage in resolved_stages:
        detail = dict(combined_stage_details.get(stage, {}))
        detail["status"] = "RESOLVED"
        detail.setdefault("evidence", str(build_path))
        detail.setdefault("notes", "Earlier failure has later OK/RESOLVED evidence.")
        combined_stage_details[stage] = detail

    progress = {
        "iteration": iteration,
        "successful_stages": successful_stages,
        "failed_stages": active_failed_stages,
        "resolved_stages": resolved_stages,
        "resolved_failures": resolved_stages,
        "generated_files": generated_files,
        "stages": combined_stage_details,
        "commands": [
            {
                "stage": command.stage,
                "command": command.command,
                "returncode": command.returncode,
                "duration_sec": command.duration_sec,
                "stdout_tail": command.stdout_tail,
                "stderr_tail": command.stderr_tail,
            }
            for command in commands
        ],
        "execution_environment": execution_environment,
        "wsl_distro": wsl_distro if execution_environment == "wsl" else "",
        "wsl_cache_dir": wsl_cache_dir if execution_environment == "wsl" else "",
    }
    progress_path.write_text(json.dumps(progress, indent=2), encoding="utf-8")
    generated_files.append(str(progress_path))

    runtime_log = logs_dir / "engineer_runtime.log"
    runtime_log.write_text(_render_runtime_log(commands), encoding="utf-8")
    generated_files.append(str(runtime_log))

    return EngineerRuntimeResult(commands, generated_files, successful_stages, active_failed_stages)


def _dependency_probe(
    *,
    execution_environment: str = "windows",
    wsl_distro: str = DEFAULT_WSL_DISTRO,
    wsl_cache_dir: str = DEFAULT_WSL_CACHE_DIR,
) -> tuple[list[dict[str, str]], list[RuntimeCommand]]:
    if execution_environment == "wsl":
        specs = [
            ("python", ["python3", "--version"], "python3"),
            ("pip", ["python3", "-m", "pip", "--version"], "python3"),
            ("cmake", ["cmake", "--version"], "cmake"),
            ("ninja", ["ninja", "--version"], "ninja"),
            ("make", ["make", "--version"], "make"),
            ("gcc", ["gcc", "--version"], "gcc"),
            ("g++", ["g++", "--version"], "g++"),
        ]
    else:
        specs = [
            ("python", [sys.executable, "--version"], sys.executable),
            ("pip", [sys.executable, "-m", "pip", "--version"], sys.executable),
            ("cmake", [_resolve_tool("cmake"), "--version"], _resolve_tool("cmake")),
            ("ninja", [_resolve_tool("ninja"), "--version"], _resolve_tool("ninja")),
            ("mingw32-make", [_resolve_tool("mingw32-make"), "--version"], _resolve_tool("mingw32-make")),
            ("make", [_resolve_tool("make"), "--version"], _resolve_tool("make")),
            ("gcc", [_resolve_tool("gcc"), "--version"], _resolve_tool("gcc")),
            ("g++", [_resolve_tool("g++"), "--version"], _resolve_tool("g++")),
        ]
    rows: list[dict[str, str]] = []
    commands: list[RuntimeCommand] = []
    for package, command, executable in specs:
        if not executable:
            rows.append(
                {
                    "package": package,
                    "command": package,
                    "status": "MISSING",
                    "version": "",
                    "evidence_source": "PATH",
                    "notes": "Executable not found.",
                }
            )
            continue
        outcome = _run_command_with_env(
            "dependency_check",
            command,
            cwd=Path.cwd(),
            timeout=20,
            execution_environment=execution_environment,
            wsl_distro=wsl_distro,
            wsl_cache_dir=wsl_cache_dir,
        )
        commands.append(outcome)
        version = (outcome.stdout_tail or outcome.stderr_tail).splitlines()[0] if (outcome.stdout_tail or outcome.stderr_tail) else ""
        rows.append(
            {
                "package": package,
                "command": _format_command(command),
                "status": "OK" if outcome.returncode == 0 else "FAILED",
                "version": version,
                "evidence_source": str(executable),
                "notes": outcome.stderr_tail if outcome.returncode != 0 else "",
            }
        )
    return rows, commands


def _find_cmake_source(repo: Path) -> Path | None:
    candidates: list[Path] = []
    artifacts = artifact_dir(repo) / "artifacts"
    if artifacts.exists():
        candidates.extend(path for path in sorted(artifacts.iterdir()) if (path / "CMakeLists.txt").exists())
    if (repo / "CMakeLists.txt").exists():
        candidates.append(repo)
    return candidates[0] if candidates else None


def _detect_checkpoint_stages(repo: Path, cmake_source: Path | None) -> dict[str, dict[str, str | bool | int]]:
    stages: dict[str, dict[str, str | bool | int]] = {}
    if cmake_source is not None:
        source_stage: dict[str, str | bool | int] = {
            "status": "OK",
            "path": str(cmake_source),
        }
        commit = _git_commit(cmake_source)
        if commit:
            source_stage["commit"] = commit
        stages["source_artifact"] = source_stage

    core_library = _find_first_existing(repo, ("*.a", "*.lib"), path_hint="build")
    if core_library is not None:
        stages["core_build"] = {
            "status": "OK",
            "evidence": str(core_library),
            "size_bytes": core_library.stat().st_size,
        }

    smoke_executable = _find_first_existing(repo, ("smoke_test.exe", "smoke_test"), path_hint="build")
    if smoke_executable is not None:
        stages["smoke_test"] = {
            "status": "OK",
            "evidence": str(smoke_executable),
        }

    reduced_status = _reduced_experiment_checkpoint(repo)
    if reduced_status:
        stages["reduced_experiment"] = reduced_status
    return stages


def _git_commit(path: Path) -> str:
    git_dir = path / ".git"
    if not git_dir.exists():
        return ""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(path),
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


def _find_first_existing(repo: Path, patterns: tuple[str, ...], *, path_hint: str = "") -> Path | None:
    roots = [artifact_dir(repo) / "artifacts", repo]
    for root in roots:
        if not root.exists():
            continue
        for pattern in patterns:
            for path in sorted(root.rglob(pattern)):
                if not path.is_file():
                    continue
                if path_hint and path_hint.lower() not in str(path).lower():
                    continue
                return path
    return None


def _reduced_experiment_checkpoint(repo: Path) -> dict[str, str | bool | int]:
    path = artifact_dir(repo) / "results" / "reduced_metrics.csv"
    if not path.exists():
        path = repo / "results" / "reduced_metrics.csv"
    if not path.exists():
        return {}
    rows = _read_csv_rows_any(path)
    if not rows:
        return {"status": "UNKNOWN", "evidence": str(path), "notes": "CSV exists but could not be parsed."}
    statuses = {str(row.get("status", "")).strip().upper() for row in rows}
    blocking = {"BLOCKED", "FAILED", "NOT_RUN", "NEEDS_CLARIFICATION"}
    if statuses and statuses.issubset(blocking):
        return {"status": "BLOCKED", "evidence": str(path), "notes": "Reduced metrics exist but contain only blocked or not-run rows."}
    return {"status": "OK", "evidence": str(path), "notes": "Reduced metrics CSV contains at least one measured or non-blocked row."}


def _run_cmake_configure(
    source: Path,
    *,
    timeout: int,
    execution_environment: str = "windows",
    wsl_distro: str = DEFAULT_WSL_DISTRO,
    wsl_cache_dir: str = DEFAULT_WSL_CACHE_DIR,
) -> RuntimeCommand:
    cmake = "cmake" if execution_environment == "wsl" else _resolve_tool("cmake")
    if not cmake:
        return RuntimeCommand("cmake_configure", ["cmake", "--version"], 127, 0, "", "cmake executable not found")
    generator = "" if execution_environment == "wsl" else _cmake_generator()
    build_dir = source / _build_dir_name(execution_environment)
    command = [
        cmake,
        "-S",
        str(source),
        "-B",
        str(build_dir),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
    ]
    if generator:
        command.extend(["-G", generator])
    if execution_environment != "wsl":
        command.extend(_cmake_toolchain_args(generator))
    return _run_command_with_env(
        "cmake_configure",
        command,
        cwd=source,
        timeout=timeout,
        execution_environment=execution_environment,
        wsl_distro=wsl_distro,
        wsl_cache_dir=wsl_cache_dir,
    )


def _run_command_with_env(
    stage: str,
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    execution_environment: str,
    wsl_distro: str,
    wsl_cache_dir: str,
) -> RuntimeCommand:
    try:
        return _run_command(
            stage,
            command,
            cwd=cwd,
            timeout=timeout,
            execution_environment=execution_environment,
            wsl_distro=wsl_distro,
            wsl_cache_dir=wsl_cache_dir,
        )
    except TypeError as exc:
        if "unexpected keyword argument" not in str(exc):
            raise
        return _run_command(stage, command, cwd=cwd, timeout=timeout)


def _cmake_notes(source: Path, outcome: RuntimeCommand) -> str:
    build_dir = source / ("build_wsl" if any("build_wsl" in part for part in outcome.command) else _build_dir_name())
    if outcome.returncode == 0:
        evidence = []
        for name in ("CMakeCache.txt", "Makefile", "build.ninja", "compile_commands.json"):
            path = build_dir / name
            if path.exists():
                evidence.append(str(path))
        return "Configured build directory. Evidence: " + "; ".join(evidence)
    return (outcome.stderr_tail or outcome.stdout_tail or "CMake configure failed.")[:1000]


def _build_dir_name(execution_environment: str = "windows") -> str:
    if execution_environment == "wsl":
        return "build_wsl"
    if _cmake_generator() == "MinGW Makefiles":
        return "build_mingw"
    if _cmake_generator() == "Ninja":
        return "build_ninja"
    return "build_r2a"


def _cmake_generator() -> str:
    if _resolve_tool("mingw32-make") and _resolve_tool("gcc") and _resolve_tool("g++"):
        return "MinGW Makefiles"
    if _resolve_tool("ninja"):
        return "Ninja"
    if _resolve_tool("mingw32-make"):
        return "MinGW Makefiles"
    return ""


def _cmake_toolchain_args(generator: str) -> list[str]:
    args: list[str] = []
    if generator == "Ninja":
        ninja = _resolve_tool("ninja")
        if ninja:
            args.append(f"-DCMAKE_MAKE_PROGRAM={ninja}")
    elif generator == "MinGW Makefiles":
        make = _resolve_tool("mingw32-make")
        if make:
            args.append(f"-DCMAKE_MAKE_PROGRAM={make}")

    c_compiler = _resolve_tool("gcc")
    cxx_compiler = _resolve_tool("g++")
    if c_compiler:
        args.append(f"-DCMAKE_C_COMPILER={c_compiler}")
    if cxx_compiler:
        args.append(f"-DCMAKE_CXX_COMPILER={cxx_compiler}")
    return args


def _resolve_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    scripts_dir = Path(sys.executable).parent
    suffixes = (".exe", ".cmd", ".bat", "") if sys.platform.startswith("win") else ("",)
    for suffix in suffixes:
        candidate = scripts_dir / f"{name}{suffix}"
        if candidate.exists():
            return str(candidate)
    return ""


def _run_command(
    stage: str,
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    execution_environment: str = "windows",
    wsl_distro: str = DEFAULT_WSL_DISTRO,
    wsl_cache_dir: str = DEFAULT_WSL_CACHE_DIR,
) -> RuntimeCommand:
    start = time.monotonic()
    executed_command = (
        wsl_bash_command(command, cwd=cwd, distro=wsl_distro, cache_dir=wsl_cache_dir)
        if execution_environment == "wsl"
        else command
    )
    try:
        completed = run_command_with_timeout(
            executed_command,
            cwd=str(cwd),
            input_text="",
            timeout=timeout,
        )
        return RuntimeCommand(
            stage,
            executed_command,
            int(completed.returncode),
            time.monotonic() - start,
            _tail(completed.stdout),
            _tail(completed.stderr),
        )
    except subprocess.TimeoutExpired as exc:
        return RuntimeCommand(
            stage,
            command,
            124,
            time.monotonic() - start,
            _tail(_coerce_text(exc.stdout)),
            _tail(_coerce_text(exc.stderr) + f"\nTimed out after {timeout} seconds."),
        )
    except OSError as exc:
        return RuntimeCommand(stage, command, 127, time.monotonic() - start, "", str(exc))


def _load_previous_progress(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _previous_stage_details(progress: dict) -> dict[str, dict[str, str | bool | int]]:
    stages = progress.get("stages")
    if not isinstance(stages, dict):
        return {}
    details: dict[str, dict[str, str | bool | int]] = {}
    for name, value in stages.items():
        if isinstance(name, str) and isinstance(value, dict):
            details[name] = {
                str(key): item
                for key, item in value.items()
                if isinstance(item, (str, bool, int))
            }
    return details


def _resolved_failed_stages(repo: Path, failed_stages: list[str], stage_details: dict[str, dict[str, str | bool | int]]) -> list[str]:
    resolved: list[str] = []
    for stage in failed_stages:
        detail = stage_details.get(stage, {})
        if str(detail.get("status", "")).upper() in {"OK", "RESOLVED", "SKIPPED_REUSED"}:
            resolved.append(stage)
            continue
        if stage == "cmake_configure" and _build_smoke_resolves_cmake(repo):
            resolved.append(stage)
    return resolved


def _build_smoke_resolves_cmake(repo: Path) -> bool:
    path = artifact_dir(repo) / "results" / "build_smoke.csv"
    rows = _read_csv_rows_any(path)
    for row in rows:
        status = str(row.get("status", "")).strip().upper()
        component = " ".join(str(row.get(key, "")) for key in ("component", "command", "notes")).lower()
        if status in {"OK", "RESOLVED", "SKIPPED_REUSED"} and ("cmake" in component or "fdann" in component):
            return True
    return False


def _can_reuse_stage(progress: dict, stage: str) -> bool:
    stages = progress.get("stages")
    if isinstance(stages, dict):
        stage_info = stages.get(stage)
        if isinstance(stage_info, dict) and stage_info.get("status") in {"OK", "SKIPPED_REUSED"}:
            return True
    successful = progress.get("successful_stages")
    return isinstance(successful, list) and stage in successful


def _cmake_configure_evidence_exists(build_dir: Path) -> bool:
    return (build_dir / "CMakeCache.txt").exists() and any(
        (build_dir / name).exists()
        for name in ("Makefile", "build.ninja", "compile_commands.json", "CMakeFiles")
    )


def _reuse_command(stage: str, evidence: Path) -> RuntimeCommand:
    return RuntimeCommand(stage, ["reuse", str(evidence)], 0, 0, f"Reused previous evidence: {evidence}", "")


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _merge_write_csv(path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    existing_rows = _read_csv_rows(path, fieldnames)
    merged = list(existing_rows)
    seen = {_row_key(row, fieldnames) for row in merged}
    for row in rows:
        normalized = {key: row.get(key, "") for key in fieldnames}
        key = _row_key(normalized, fieldnames)
        if key not in seen:
            merged.append(normalized)
            seen.add(key)
    _write_csv(path, fieldnames, merged)


def _read_csv_rows(path: Path, fieldnames: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or any(field not in reader.fieldnames for field in fieldnames):
                return []
            return [{field: row.get(field, "") for field in fieldnames} for row in reader]
    except OSError:
        return []


def _read_csv_rows_any(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except OSError:
        return []


def _row_key(row: dict[str, str], fieldnames: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(row.get(field, "") for field in fieldnames)


def _render_runtime_log(commands: list[RuntimeCommand]) -> str:
    blocks = []
    for command in commands:
        blocks.append(
            "\n".join(
                [
                    f"stage: {command.stage}",
                    f"command: {_format_command(command.command)}",
                    f"returncode: {command.returncode}",
                    f"duration_sec: {command.duration_sec:.2f}",
                    "stdout_tail:",
                    command.stdout_tail or "(empty)",
                    "stderr_tail:",
                    command.stderr_tail or "(empty)",
                ]
            )
        )
    return "\n\n---\n\n".join(blocks) + ("\n" if blocks else "")


def _format_command(command: list[str]) -> str:
    return " ".join(str(part) for part in command)


def _tail(text: str, max_lines: int = 40) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-max_lines:])


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
