from __future__ import annotations

from pathlib import Path
import csv
import shutil
import subprocess
import sys

from r2a.core.config import DEFAULT_CLAUDE_EXECUTABLE
from r2a.core.paths import artifact_dir
from r2a.tools.backend_errors import classify_backend_error
from r2a.tools.codex_cli import CodexCliCheckResult
from r2a.tools.codex_runner import CodexRunResult, build_codex_exec_prompt
from r2a.tools.evidence_levels import normalize_status
from r2a.tools.process_tree import run_command_with_timeout
from r2a.tools.stage_guard import check_stage_allowed_modifications, snapshot_stage_changes
from r2a.tools.wsl import DEFAULT_WSL_CACHE_DIR, DEFAULT_WSL_DISTRO, wsl_cache_exports


BASE_ALLOWED_CLAUDE_TOOLS = [
        "Read",
        "Write",
        "Edit",
        "MultiEdit",
        "Bash(pwd)",
        "Bash(ls *)",
        "Bash(dir *)",
        "Bash(Get-ChildItem *)",
        "Bash(Test-Path *)",
        "Bash(rg *)",
        "Bash(git status *)",
        "Bash(git rev-parse *)",
        "Bash(git log *)",
        "Bash(git show *)",
        "Bash(git ls-remote *)",
        "Bash(git clone *)",
        "Bash(git submodule *)",
        "Bash(curl *)",
        "Bash(wget *)",
        "Bash(hf *)",
        "Bash(huggingface-cli *)",
        "Bash(where *)",
        "Bash(Get-Command *)",
        "Bash(python *)",
        "Bash(py *)",
        "Bash(python -m pip show *)",
        "Bash(python -m pip install *)",
        "Bash(py -m pip show *)",
        "Bash(py -m pip install *)",
        "Bash(pip show *)",
        "Bash(pip --version *)",
        "Bash(pip install *)",
        "Bash(python -m pytest *)",
        "Bash(pytest *)",
        "Bash(make --version *)",
        "Bash(mingw32-make --version *)",
        "Bash(ninja --version *)",
        "Bash(cmake --version *)",
        "Bash(gcc --version *)",
        "Bash(g++ --version *)",
        "Bash(cmake -S *)",
        "Bash(cmake --build *)",
        "Bash(make *)",
        "Bash(mingw32-make *)",
        "Bash(ninja *)",
        "Bash(gcc *)",
        "Bash(g++ *)",
        "Bash(wsl *)",
        "Bash(docker --version)",
        "Bash(docker version)",
        "Bash(docker info)",
        "Bash(docker images)",
        "Bash(docker image inspect *)",
        "Bash(docker ps)",
        "Bash(docker build -t r2a-* -f *)",
        "Bash(docker build -t fanns-benchmark:* -f *)",
        "Bash(docker run --rm *)",
        "Bash(docker run --rm --gpus all *)",
]

CLAUDE_ENGINEER_IDLE_TIMEOUT_SECONDS = 900

DISALLOWED_CLAUDE_TOOLS = ",".join(
    [
        "Bash(git reset --hard *)",
        "Bash(git checkout -- *)",
        "Bash(pip uninstall *)",
        "Bash(python -m pip uninstall *)",
        "Bash(py -m pip uninstall *)",
        "Bash(rm -rf *)",
        "Bash(del /s *)",
        "Bash(Remove-Item -Recurse *)",
        "Bash(curl *wiki*)",
        "Bash(wget *wiki*)",
        "Bash(docker system prune *)",
        "Bash(docker container prune *)",
        "Bash(docker image prune *)",
        "Bash(docker volume prune *)",
        "Bash(docker network prune *)",
        "Bash(docker volume rm *)",
        "Bash(docker image rm *)",
        "Bash(docker rmi *)",
        "Bash(docker rm *)",
        "Bash(docker compose down -v *)",
        "Bash(docker compose rm *)",
        "Bash(docker builder prune *)",
        "Bash(docker buildx prune *)",
        "Bash(docker login *)",
        "Bash(docker push *)",
        "Bash(docker pull *)",
        "Bash(docker run *--privileged*)",
        "Bash(docker run * /:/host*)",
        "Bash(docker run * C:\\:/host*)",
    ]
)


def check_claude_code_cli(executable: str | None = None, timeout: int = 10) -> CodexCliCheckResult:
    requested = (executable or DEFAULT_CLAUDE_EXECUTABLE).strip().strip('"') or DEFAULT_CLAUDE_EXECUTABLE
    resolved_path = requested if any(separator in requested for separator in ("/", "\\")) else shutil.which(requested)
    if requested.lower() == DEFAULT_CLAUDE_EXECUTABLE and not resolved_path:
        return CodexCliCheckResult(
            available=False,
            executable=requested,
            resolved_path=None,
            version_output="",
            error="Claude Code / Router CLI was not found in PATH.",
            hint="Install/configure Claude Code Router so PowerShell can run `ccr version`, or provide a runnable ccr.cmd/claude.cmd path.",
        )
    candidate = resolved_path or requested
    version_command = [candidate, "version"] if _is_claude_code_router(candidate) else [candidate, "--version"]
    try:
        completed = subprocess.run(
            version_command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return CodexCliCheckResult(False, requested, resolved_path, "", f"FileNotFoundError: {exc}", "Provide a runnable Claude Code Router or Claude Code CLI path.")
    except PermissionError as exc:
        return CodexCliCheckResult(False, requested, resolved_path, "", f"PermissionError: {exc}", "The configured Claude Code / Router CLI path cannot be executed by this process.")
    except subprocess.TimeoutExpired:
        return CodexCliCheckResult(False, requested, resolved_path, "", f"Timed out after {timeout} seconds while running `{' '.join(version_command)}`.", "Verify Claude Code or Claude Code Router manually in PowerShell.")

    version_output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    if completed.returncode == 0:
        return CodexCliCheckResult(True, requested, resolved_path or candidate, version_output, "", "Claude Code / Router CLI is available to this process.")
    return CodexCliCheckResult(
        False,
        requested,
        resolved_path or candidate,
        version_output,
        f"`{' '.join(version_command)}` exited with code {completed.returncode}. {version_output}".strip(),
        "The configured command exists but did not behave like a runnable Claude Code or Claude Code Router CLI.",
    )


def format_claude_code_cli_error(check: CodexCliCheckResult) -> str:
    parts = [
        f"Claude Code / Router CLI is not available: {check.attempted_executable}",
        check.error,
        check.hint,
    ]
    return "\n\n".join(part for part in parts if part)


def run_claude_code_exec(
    repo_path: str | Path,
    task_spec_path: str | Path,
    timeout: int = 10800,
    language: str = "en",
    iteration: int = 1,
    auto_iterate: bool = False,
    claude_executable_path: str | None = None,
    execution_environment: str = "windows",
    wsl_distro: str = DEFAULT_WSL_DISTRO,
    wsl_cache_dir: str = DEFAULT_WSL_CACHE_DIR,
    env: dict[str, str] | None = None,
) -> CodexRunResult:
    repo = Path(repo_path)
    task_path = Path(task_spec_path)
    baseline_changes = snapshot_stage_changes(repo)
    result_csv_signatures_before = _result_csv_signatures(repo)
    engineer_done_signature_before = _file_signature(artifact_dir(repo) / "results" / "ENGINEER_DONE.txt")
    side_effect_signature_before = _engineer_side_effect_signature(repo)
    cli_check = check_claude_code_cli(claude_executable_path)
    attempted_executable = cli_check.attempted_executable
    task_prompt = build_codex_exec_prompt(repo, task_path, language=language)
    wsl_instructions = ""
    if execution_environment == "wsl":
        wsl_instructions = (
            "- When executing project build/test/benchmark commands, use WSL rather than Windows tools.\n"
            f"- WSL distro: `{wsl_distro}`.\n"
            f"- Convert the workspace path to `/mnt/<drive>/...` before running commands. Current repo path in WSL is likely `{_wsl_repo_hint(repo)}`.\n"
            f"- Before WSL commands, set cache environment variables outside WSL home: `{wsl_cache_exports(wsl_cache_dir)}`.\n"
            "- Do not write large datasets, pip caches, Hugging Face caches, or build scratch under `~` in WSL.\n"
        )
    task_prompt += (
        "\nClaude Code Engineer mode:\n"
        "- You are running through Claude Code or Claude Code Router non-interactive print mode.\n"
        "- Use tools to inspect and edit only the workspace repo.\n"
        "- Backend choice affects the execution model, not R2A evidence rules; follow `r2a/prompts/R2A_PROTOCOL.md`, TASK_SPEC.md, and `.r2a/EXPERIMENT_CONTRACT.md`.\n"
        "- Create at least one required CSV under `.r2a/results/` or `results/`.\n"
        "- If you cannot execute the task, create `.r2a/results/reproduction_status.csv` with status `FAIL`, `NOT_RUN`, or `NEEDS_INPUT`.\n"
        "- Before executing, read `.r2a/EXPERIMENT_CONTRACT.md`. It defines the contract mode: `verification_only`, `smoke`, `official_reduced`, or `full_benchmark`.\n"
        "- Follow the Experiment Contract for data downloads, input generation, result labels, and stop conditions.\n"
        "- Official paper-linked datasets or artifact-linked subsets may be downloaded only when the contract explicitly allows them and the estimated total stays within the contract download budget, normally 20GB. Prefer the smallest viable subset.\n"
        "- Full datasets beyond budget, large external baselines, persistent caches, and system-level installs require user approval; write NEEDS_INPUT instead of bypassing the budget.\n"
        "- Do not invent official query files, ground truth files, Kuzu databases, or paper inputs. If official inputs are missing, write `input_contract_verification.csv` and `NEEDS_INPUT`.\n"
        "- Do not treat empty placeholder inputs as official inputs. Required database/query/ground-truth files must be non-empty and lightly parseable; 0-byte .fvecs/.ivecs/.bvecs files must be recorded as `EMPTY_PLACEHOLDER_INPUT` and `NEEDS_INPUT`, and must not produce `reduced_metrics.csv`.\n"
        "- In `verification_only`, do not claim metric experiments; verify source/build/runtime/input blockers and write status artifacts.\n"
        "- Do not inflate smoke tests or unofficial reimplementations into L3/L4 evidence.\n"
        "- Do not write `.r2a/EXECUTION_REPORT.md` directly; R2A will generate it after you exit.\n"
        "- If you need prose details, write `.r2a/results/ENGINEER_NOTES.md`.\n"
        "- Write `.r2a/results/ENGINEER_DONE.txt` as the final file after all CSV/notes artifacts are complete.\n"
        "- `ENGINEER_DONE.txt` must be newly written during this invocation; never rely on a stale completion marker from a previous iteration.\n"
        "- Do not ask the user questions during this run; encode blockers in CSV and optional ENGINEER_NOTES.md.\n"
        f"- Prefer this Python executable for dependency setup and scripts: `{sys.executable}`.\n"
        f"- Engineer execution environment: `{execution_environment}`.\n"
        f"{wsl_instructions}"
        "- You may install small, named dependencies needed for a bounded reduced experiment, such as `cmake`, `ninja`, `numpy`, `pandas`, or a documented project package.\n"
        "- If you use a prebuilt package instead of the paper artifact source commit, label the evidence as `package_smoke`, not full artifact-source reproduction.\n"
        "- Real experiment means running code and writing measured outputs. Source localization alone is not a real experiment.\n"
        "- If `.r2a/results/engineer_progress.json` exists, read it first. Do not repeat successful deterministic runtime stages unless TASK_SPEC.md explicitly requires a rerun.\n"
        "- For iteration 1, perform the first bounded reduced-experiment attempt: source discovery, dependency checks, configure/build smoke, and reduced metrics when feasible.\n"
        "- For iteration > 1, run minimal-fix mode: reuse successful clone/configure/build/smoke evidence, then work only on failed, blocked, NOT_RUN, or Evidence Gap items from REVIEW_REPORT.md, CHECK_REPORT.md, EXECUTION_REPORT.md, and existing result CSVs.\n"
        "- Do not reclone an artifact repository when an authoritative copy already exists under `.r2a/artifacts/`; verify branch/commit and reuse it unless the current TASK_SPEC explicitly asks for a different source.\n"
        "- Do not rerun expensive build targets if prior evidence proves they succeeded and the required output still exists. Focus later iterations on the next missing measured output or blocker.\n"
        "- Use existing `.r2a/results/dependency_setup.csv` and `.r2a/results/build_smoke.csv` as evidence; append or update only when you perform a new action.\n"
        "- Before ending, classify blockers as SAFE_BUILD_COMPATIBILITY, TOOLCHAIN_OR_ENVIRONMENT, MISSING_ARTIFACT_OR_DATA, API_OR_ALGORITHM_SEMANTICS, RESULT_MISMATCH, TIME_BUDGET, or TASK_AMBIGUITY.\n"
        "- For SAFE_BUILD_COMPATIBILITY, attempt up to three focused artifact-only mechanical fixes, such as missing standard-library includes or CMake/toolchain compatibility, then rerun only the smallest failing command.\n"
        "- Do not write `ENGINEER_DONE.txt` as `PARTIAL` or `BLOCKED` immediately after the first configure/build failure. First classify the failure. If it is `SAFE_BUILD_COMPATIBILITY` and TASK_SPEC allows artifact-only patches, use the build-fix budget before ending.\n"
        "- A single-file compile smoke is not enough when TASK_SPEC asks for build/import smoke on a CMake project. Also attempt configure and one minimal build target unless TASK_SPEC explicitly forbids it or the toolchain is missing.\n"
        "- Do not redesign APIs, algorithms, query semantics, metric definitions, or paper methods unless TASK_SPEC.md explicitly authorizes that; record those as API_OR_ALGORITHM_SEMANTICS blockers.\n"
        "- Use source/feature localization CSV headers `component,status,path,symbol_or_command,evidence_source,notes`; do not add `qps` unless throughput/QPS was actually measured.\n"
        "- Write CSV files with Python `csv.DictWriter` or equivalent deterministic quoting. Do not hand-write comma-containing notes, commands, paths, or prose into CSV rows without quoting.\n"
        "- Runtime smoke: do not copy `.exe` files to Temp. Run from repo/artifact/build directories, set PATH for toolchain/build DLLs, use a 60-120 second timeout, and write `.r2a/results/runtime_smoke.csv` with headers `status,command,exit_code,duration_sec,component,evidence_source,notes`.\n"
        "- For Docker tasks, prefer `python -m r2a.tools.docker_runner --repo <repo> --timeout <seconds> ...` so R2A validates safe tags, workspace paths, mounts, timeouts, logs, and CSV provenance before invoking Docker.\n"
        "- Allowed Docker scope: `docker --version`, `docker version`, `docker info`, `docker images`, `docker image inspect <image>`, `docker ps`, bounded `docker build -t r2a-*|fanns-benchmark:* -f <Dockerfile> <context>`, and bounded `docker run --rm ...` smoke commands.\n"
        "- Forbidden Docker actions: prune/rm/rmi/login/push, unapproved large pulls, `--privileged`, root/home/system mounts, and any unbounded long-running build or run.\n"
        "- If a Windows loader/DLL/entry-point error appears, such as a missing DLL or `nanosleep64`, record it in `runtime_smoke.csv`, classify it as `RUNTIME_DLL_COMPATIBILITY`, and stop retrying that runtime path.\n"
        "- Prefer simple, single-tool actions. Avoid multi-tool batches and avoid complex shell quoting in tool arguments.\n"
        "- Before the final answer, ensure all requested files are written. The final answer must be plain text only, with no tool-call JSON or structured tool syntax.\n"
        f"\nCurrent iteration: {iteration}\n"
        f"Auto iterate: {auto_iterate}\n"
    )
    prompt_input_text = task_prompt
    if _is_claude_code_router(attempted_executable):
        prompt_path = _write_prompt_file(repo, task_prompt)
        prompt_input_text = ""
        short_prompt = (
            f"Read `{prompt_path}` and execute those Engineer instructions exactly. "
            "Write requested CSV outputs, optional ENGINEER_NOTES.md, and ENGINEER_DONE.txt in the workspace. "
            "Do not write EXECUTION_REPORT.md directly."
        )
        command = _build_claude_execution_command(attempted_executable, repo, prompt_arg=short_prompt)
    else:
        command = _build_claude_execution_command(attempted_executable, repo)
    if not cli_check.available:
        stderr = format_claude_code_cli_error(cli_check)
        stderr = _append_engineer_guard_warning(repo, baseline_changes, stderr)
        stdout_log, stderr_log = _write_claude_logs(repo, "", stderr, attempted_executable, command)
        return CodexRunResult(command, 127, "", stderr, str(stdout_log), str(stderr_log), "", _tail(stderr), attempted_executable, True, cli_check.error, cli_check.hint)

    try:
        _write_claude_logs(repo, "Claude Code executor started; waiting for subprocess output.\n", "", attempted_executable, command)
        completed = run_command_with_timeout(
            command,
            cwd=str(repo),
            input_text=prompt_input_text,
            timeout=timeout,
            env=env,
            completion_check=lambda: _engineer_completion_observed(repo, result_csv_signatures_before, engineer_done_signature_before),
            completion_grace_seconds=15,
            activity_check=lambda: _engineer_activity_signature(repo),
            idle_timeout_seconds=min(timeout, CLAUDE_ENGINEER_IDLE_TIMEOUT_SECONDS),
        )
    except FileNotFoundError as exc:
        stderr = _append_engineer_guard_warning(repo, baseline_changes, f"FileNotFoundError while invoking `{attempted_executable}`: {exc}")
        stdout_log, stderr_log = _write_claude_logs(repo, "", stderr, attempted_executable, command)
        return CodexRunResult(command, 127, "", stderr, str(stdout_log), str(stderr_log), "", _tail(stderr), attempted_executable, True, str(exc))
    except PermissionError as exc:
        stderr = _append_engineer_guard_warning(repo, baseline_changes, f"PermissionError while invoking `{attempted_executable}`: {exc}")
        stdout_log, stderr_log = _write_claude_logs(repo, "", stderr, attempted_executable, command)
        return CodexRunResult(command, 126, "", stderr, str(stdout_log), str(stderr_log), "", _tail(stderr), attempted_executable, True, str(exc))

    if completed.timed_out:
        _ensure_engineer_done_from_terminal_results(repo)
        stderr = (completed.stderr or "") + f"\nTimeoutExpired: Claude Code executor exceeded {timeout} seconds while invoking `{attempted_executable}`. The process tree was terminated."
        stderr = _append_engineer_guard_warning(repo, baseline_changes, stderr)
        stdout_log, stderr_log = _write_claude_logs(repo, completed.stdout, stderr, attempted_executable, command)
        backend_error = classify_backend_error(completed.stdout, stderr, backend="claude")
        side_effects_detected = _engineer_side_effect_signature(repo) != side_effect_signature_before
        return CodexRunResult(
            command,
            124,
            completed.stdout,
            stderr,
            str(stdout_log),
            str(stderr_log),
            _tail(completed.stdout),
            _tail(stderr),
            attempted_executable,
            True,
            f"Timed out after {timeout} seconds; process tree terminated",
            "",
            backend_error,
            bool(backend_error.get("is_backend_failure", False)),
            bool(backend_error.get("transient_backend_failure", False)),
            str(backend_error.get("failure_category", "")),
            str(backend_error.get("failure_scope", "")),
            str(backend_error.get("suggested_action", "")),
            str(backend_error.get("user_message", "")),
            str(backend_error.get("backend_warning", "")),
            not side_effects_detected
            and bool(backend_error.get("is_backend_failure", False))
            and bool(backend_error.get("transient_backend_failure", False)),
            side_effects_detected,
            _manual_retry_message(backend_error, side_effects_detected),
        )

    stderr = _append_engineer_guard_warning(repo, baseline_changes, completed.stderr)
    stdout_log, stderr_log = _write_claude_logs(repo, completed.stdout, stderr, attempted_executable, command)
    backend_error = classify_backend_error(completed.stdout, stderr, backend="claude")
    side_effects_detected = _engineer_side_effect_signature(repo) != side_effect_signature_before
    suggested_action = "manual_retry_same_stage" if backend_error.get("is_backend_failure") else str(backend_error.get("suggested_action", ""))
    return CodexRunResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=stderr,
        stdout_log_path=str(stdout_log),
        stderr_log_path=str(stderr_log),
        stdout_tail=_tail(completed.stdout),
        stderr_tail=_tail(stderr),
        attempted_executable=attempted_executable,
        skipped=False,
        error="" if completed.returncode == 0 else stderr,
        backend_error=backend_error,
        is_backend_failure=bool(backend_error.get("is_backend_failure", False)),
        transient_backend_failure=bool(backend_error.get("transient_backend_failure", False)),
        backend_failure_category=str(backend_error.get("failure_category", "")),
        backend_failure_scope=str(backend_error.get("failure_scope", "")),
        backend_suggested_action=suggested_action,
        backend_user_message=str(backend_error.get("user_message", "")),
        backend_warning=str(backend_error.get("backend_warning", "")),
        safe_to_retry_likely=not side_effects_detected
        and bool(backend_error.get("is_backend_failure", False))
        and bool(backend_error.get("transient_backend_failure", False)),
        side_effects_detected=side_effects_detected,
        manual_retry_message=_manual_retry_message(backend_error, side_effects_detected),
    )


def _build_claude_execution_command(attempted_executable: str, repo: Path, prompt_arg: str = "") -> list[str]:
    command = [attempted_executable]
    is_router = _is_claude_code_router(attempted_executable)
    if is_router:
        command.append("code")
    command.extend(
        [
            "--permission-mode",
            "auto",
            "--add-dir",
            str(repo),
            "--allowedTools",
            _allowed_claude_tools(),
            "--disallowedTools",
            DISALLOWED_CLAUDE_TOOLS,
            "--output-format",
            "text",
        ]
    )
    if is_router:
        command.extend(["-p", prompt_arg])
    else:
        command.insert(1, "--print")
    return command


def _write_claude_logs(repo_path: Path, stdout: str, stderr: str, attempted_executable: str, command: list[str]) -> tuple[Path, Path]:
    logs_dir = artifact_dir(repo_path) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = logs_dir / "claude_stdout.log"
    stderr_log = logs_dir / "claude_stderr.log"
    header = f"claude_executable_path: {attempted_executable}\ncommand: {command} [...prompt via stdin omitted...]\n\n"
    stdout_log.write_text(header + (stdout or ""), encoding="utf-8")
    stderr_log.write_text(header + (stderr or ""), encoding="utf-8")
    return stdout_log, stderr_log


def _write_prompt_file(repo_path: Path, prompt: str) -> Path:
    logs_dir = artifact_dir(repo_path) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = logs_dir / "claude_engineer_prompt.md"
    prompt_path.write_text(prompt, encoding="utf-8")
    return prompt_path


def _allowed_claude_tools() -> str:
    tools = list(BASE_ALLOWED_CLAUDE_TOOLS)
    python_executable = Path(sys.executable)
    scripts_dir = python_executable.parent
    dynamic_commands = [
        python_executable,
        scripts_dir / "pip.exe",
        scripts_dir / "cmake.exe",
        scripts_dir / "ninja.exe",
        scripts_dir / "pytest.exe",
    ]
    project_venv_scripts = Path.cwd() / ".venv" / "Scripts"
    dynamic_commands.extend(
        [
            project_venv_scripts / "python.exe",
            project_venv_scripts / "pip.exe",
            project_venv_scripts / "cmake.exe",
            project_venv_scripts / "ninja.exe",
            project_venv_scripts / "pytest.exe",
        ]
    )
    for name in ("cmake", "ninja", "mingw32-make", "gcc", "g++"):
        resolved = shutil.which(name)
        if resolved:
            dynamic_commands.append(Path(resolved))
    for command in dynamic_commands:
        tools.append(f"Bash({command} *)")
    return ",".join(tools)


def _result_csv_signatures(repo: Path) -> dict[Path, tuple[int, int]]:
    signatures: dict[Path, tuple[int, int]] = {}
    for directory in (repo / "results", artifact_dir(repo) / "results"):
        if not directory.exists():
            continue
        for path in directory.glob("*.csv"):
            try:
                stat = path.stat()
            except OSError:
                continue
            signatures[path] = (stat.st_mtime_ns, stat.st_size)
    return signatures


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_size)


def _engineer_completion_observed(
    repo: Path,
    before: dict[Path, tuple[int, int]],
    done_before: tuple[int, int] | None = None,
) -> bool:
    done_path = artifact_dir(repo) / "results" / "ENGINEER_DONE.txt"
    done_signature = _file_signature(done_path)
    if done_signature is None or done_signature == done_before:
        return False
    for path, signature in _result_csv_signatures(repo).items():
        if before.get(path) != signature:
            return True
    return False


def _engineer_activity_signature(repo: Path) -> tuple[tuple[str, int, int], ...]:
    roots = [
        artifact_dir(repo) / "results",
        artifact_dir(repo) / "logs",
        artifact_dir(repo) / "artifacts",
        repo / "results",
    ]
    signatures: list[tuple[str, int, int]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            signatures.append((str(path.relative_to(repo)), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(signatures))


def _engineer_side_effect_signature(repo: Path) -> tuple[tuple[str, int, int], ...]:
    roots = [
        artifact_dir(repo) / "results",
        artifact_dir(repo) / "artifacts",
        artifact_dir(repo) / "experiments",
        repo / "results",
    ]
    signatures: list[tuple[str, int, int]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            signatures.append((str(path.relative_to(repo)), stat.st_mtime_ns, stat.st_size))
    return tuple(sorted(signatures))


def _manual_retry_message(backend_error: dict[str, object], side_effects_detected: bool) -> str:
    if not backend_error.get("is_backend_failure"):
        return ""
    if not backend_error.get("transient_backend_failure"):
        return "Fix the backend configuration or authentication before retrying the Engineer stage."
    if side_effects_detected:
        return "Manual inspection required before retry; possible Engineer side effects were detected. Do not blindly retry the Engineer stage."
    return "Manual retry recommended; no Engineer result/artifact side effects were detected by the lightweight signature check."


def _ensure_engineer_done_from_terminal_results(repo: Path) -> bool:
    done_path = artifact_dir(repo) / "results" / "ENGINEER_DONE.txt"
    if done_path.exists() and done_path.read_text(encoding="utf-8", errors="replace").strip():
        return False
    status = _terminal_status_from_results(repo)
    if not status:
        return False
    done_path.parent.mkdir(parents=True, exist_ok=True)
    done_path.write_text(f"{status}\n", encoding="utf-8")
    notes_path = artifact_dir(repo) / "results" / "ENGINEER_NOTES.md"
    note = (
        "\n\n## R2A Fallback Completion\n\n"
        f"R2A wrote `ENGINEER_DONE.txt` as `{status}` because terminal result CSV evidence existed "
        "but the external Engineer process did not complete cleanly.\n"
    )
    existing = notes_path.read_text(encoding="utf-8", errors="replace") if notes_path.exists() else "# ENGINEER_NOTES\n"
    notes_path.write_text(existing.rstrip() + note, encoding="utf-8")
    return True


def _terminal_status_from_results(repo: Path) -> str:
    fallback = ""
    for directory in (artifact_dir(repo) / "results", repo / "results"):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.csv")):
            for row in _read_csv_rows(path):
                status = normalize_status(_first_present(row, ("status", "verdict", "result")))
                if status == "FAIL":
                    return "FAIL"
                if status in {"NEEDS_INPUT", "NOT_RUN"}:
                    fallback = fallback or status
    return fallback


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _first_present(row: dict[str, str], columns: tuple[str, ...]) -> str:
    for column in columns:
        value = row.get(column)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _is_claude_code_router(executable: str) -> bool:
    return Path(executable).stem.lower() == "ccr"


def _wsl_repo_hint(repo: Path) -> str:
    text = str(repo).replace("\\", "/")
    if len(text) >= 3 and text[1:3] == ":/":
        return f"/mnt/{text[0].lower()}/{text[3:]}"
    return text


def _append_engineer_guard_warning(repo: Path, baseline_changes: set[str], stderr: str) -> str:
    guard = check_stage_allowed_modifications(repo, "engineer", ["*"], baseline_changes)
    if guard.get("guard_available", True):
        return stderr or ""
    message = f"Stage Guard: {guard.get('warning', 'Stage guard could not verify modifications')}: {guard.get('error', '')}"
    return f"{(stderr or '').rstrip()}\n\n{message}\n" if stderr else f"{message}\n"


def _tail(text: str, max_lines: int = 80) -> str:
    lines = (text or "").splitlines()
    return "\n".join(lines[-max_lines:])
