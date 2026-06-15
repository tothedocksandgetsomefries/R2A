from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
import subprocess
import time

from r2a.core.paths import artifact_dir
from r2a.tools.docker_policy import validate_docker_command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run audited, bounded R2A Docker commands.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--cache-dir", default="")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("preflight")

    image = subparsers.add_parser("image-check")
    image.add_argument("--image", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--tag", required=True)
    build.add_argument("--dockerfile", required=True)
    build.add_argument("--context", required=True)

    smoke = subparsers.add_parser("run-smoke")
    smoke.add_argument("--image", required=True)
    smoke.add_argument("--component", default="docker_smoke")
    smoke.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    if args.action == "preflight":
        return _preflight(repo, args.timeout)
    if args.action == "image-check":
        return _image_check(repo, args.image, args.timeout)
    if args.action == "build":
        command = ["docker", "build", "-t", args.tag, "-f", args.dockerfile, args.context]
        return _run_build(repo, command, args.tag, args.dockerfile, args.context, args.timeout, args.cache_dir)
    if args.action == "run-smoke":
        inner = args.command or []
        command = ["docker", "run", "--rm", args.image, *inner]
        return _run_smoke(repo, command, args.image, args.component, args.timeout, args.cache_dir)
    raise AssertionError(args.action)


def _preflight(repo: Path, timeout: int) -> int:
    status = 0
    for command in (["docker", "--version"], ["docker", "version"], ["docker", "info"], ["docker", "images"]):
        result = _run_logged(repo, command, timeout=timeout, log_prefix="docker_preflight")
        _append_manifest(repo, command_id="docker_preflight", command=command, result=result, artifact_path="")
        if result["exit_code"] != 0:
            status = int(result["exit_code"])
    return status


def _image_check(repo: Path, image: str, timeout: int) -> int:
    command = ["docker", "image", "inspect", image]
    result = _run_logged(repo, command, timeout=timeout, log_prefix="docker_image_inspect")
    _append_manifest(repo, command_id="docker_image_check", command=command, result=result, artifact_path="")
    return int(result["exit_code"])


def _run_build(repo: Path, command: list[str], image_tag: str, dockerfile: str, context_dir: str, timeout: int, cache_dir: str) -> int:
    policy = validate_docker_command(command, repo, cache_dir=cache_dir or None)
    if not policy.allowed:
        log_path = _write_policy_log(repo, "docker_build", command, policy.reason)
        _append_docker_build(repo, image_tag, dockerfile, context_dir, command, 126, 0, log_path, "", "BLOCKED", policy.reason)
        _append_reproduction_status(repo, status="BLOCKED", reason="TOOLCHAIN_OR_ENVIRONMENT", evidence_source=str(log_path), next_action=policy.reason)
        return 126
    started = time.monotonic()
    result = _run_logged(repo, command, timeout=timeout, log_prefix="docker_build")
    image_id = _docker_image_id(image_tag)
    duration = float(result["duration_sec"])
    status = "OK" if result["exit_code"] == 0 else ("TIMEOUT" if result["exit_code"] == 124 else "FAILED")
    _append_docker_build(repo, image_tag, dockerfile, context_dir, command, result["exit_code"], duration, result["log_path"], image_id, status, "")
    _append_manifest(repo, command_id="docker_build", command=command, result=result, artifact_path=image_tag, artifact_hash=image_id)
    if result["exit_code"] != 0:
        _append_reproduction_status(repo, status="BLOCKED", reason="TOOLCHAIN_OR_ENVIRONMENT", evidence_source=str(result["log_path"]), next_action="Inspect Docker build log, daemon status, disk/network/CUDA constraints, then rerun bounded docker build only if the contract still authorizes it.")
    return int(result["exit_code"])


def _run_smoke(repo: Path, command: list[str], image_tag: str, component: str, timeout: int, cache_dir: str) -> int:
    policy = validate_docker_command(command, repo, cache_dir=cache_dir or None)
    if not policy.allowed:
        log_path = _write_policy_log(repo, "docker_runtime_smoke", command, policy.reason)
        _append_docker_smoke(repo, image_tag, command, 126, 0, component, log_path, "BLOCKED", policy.reason)
        _append_reproduction_status(repo, status="BLOCKED", reason="TOOLCHAIN_OR_ENVIRONMENT", evidence_source=str(log_path), next_action=policy.reason)
        return 126
    if not _image_exists(image_tag):
        log_path = _write_policy_log(repo, "docker_runtime_smoke", command, f"Image is not local and docker pull is not authorized by docker_runner: {image_tag}")
        _append_docker_smoke(repo, image_tag, command, 126, 0, component, log_path, "NEEDS_INPUT_OR_BUDGET", "Image is not local and pulling is not authorized by this bounded smoke helper.")
        _append_reproduction_status(repo, status="NEEDS_INPUT_OR_BUDGET", reason="TOOLCHAIN_OR_ENVIRONMENT", evidence_source=str(log_path), next_action=f"Authorize a bounded docker pull or provide local image `{image_tag}`.")
        return 126
    result = _run_logged(repo, command, timeout=timeout, log_prefix="docker_runtime_smoke")
    status = "OK" if result["exit_code"] == 0 else ("TIMEOUT" if result["exit_code"] == 124 else "FAILED")
    _append_docker_smoke(repo, image_tag, command, result["exit_code"], result["duration_sec"], component, result["log_path"], status, "")
    _append_manifest(repo, command_id="docker_runtime_smoke", command=command, result=result, artifact_path=image_tag, artifact_hash=_docker_image_id(image_tag))
    if result["exit_code"] != 0:
        _append_reproduction_status(repo, status="BLOCKED", reason="TOOLCHAIN_OR_ENVIRONMENT", evidence_source=str(result["log_path"]), next_action="Inspect Docker runtime smoke log and decide whether the failure is command syntax, image contents, GPU/CUDA, or environment.")
    return int(result["exit_code"])


def _run_logged(repo: Path, command: list[str], *, timeout: int, log_prefix: str) -> dict[str, str | int | float]:
    logs_dir = artifact_dir(repo) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{log_prefix}_{time.time_ns()}.log"
    start = time.monotonic()
    try:
        completed = subprocess.run(command, cwd=str(repo), stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)
        exit_code = int(completed.returncode)
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = _coerce_text(exc.stdout)
        stderr = _coerce_text(exc.stderr) + f"\nTimed out after {timeout} seconds."
    duration = time.monotonic() - start
    log_path.write_text(
        "command: " + " ".join(command) + "\n"
        f"exit_code: {exit_code}\n"
        f"duration_sec: {duration:.2f}\n\n"
        "stdout:\n" + (stdout or "(empty)") + "\n\n"
        "stderr:\n" + (stderr or "(empty)") + "\n",
        encoding="utf-8",
    )
    return {"exit_code": exit_code, "duration_sec": round(duration, 2), "log_path": str(log_path)}


def _append_docker_build(repo: Path, image_tag: str, dockerfile: str, context_dir: str, command: list[str], exit_code: int, duration: float, log_path: str | Path, image_id: str, status: str, notes: str) -> None:
    _append_csv(
        artifact_dir(repo) / "results" / "docker_build.csv",
        ("image_tag", "dockerfile", "context_dir", "command", "exit_code", "duration_sec", "log_path", "image_id", "status", "notes"),
        {"image_tag": image_tag, "dockerfile": dockerfile, "context_dir": context_dir, "command": " ".join(command), "exit_code": str(exit_code), "duration_sec": f"{float(duration):.2f}", "log_path": str(log_path), "image_id": image_id, "status": status, "notes": notes},
    )


def _append_reproduction_status(repo: Path, *, status: str, reason: str, evidence_source: str, next_action: str) -> None:
    _append_csv(
        artifact_dir(repo) / "results" / "reproduction_status.csv",
        ("status", "reason", "evidence_source", "next_action"),
        {"status": status, "reason": reason, "evidence_source": evidence_source, "next_action": next_action},
    )


def _append_docker_smoke(repo: Path, image_tag: str, command: list[str], exit_code: int, duration: float, component: str, log_path: str | Path, status: str, notes: str) -> None:
    _append_csv(
        artifact_dir(repo) / "results" / "docker_runtime_smoke.csv",
        ("image_tag", "command", "exit_code", "duration_sec", "component", "log_path", "status", "notes"),
        {"image_tag": image_tag, "command": " ".join(command), "exit_code": str(exit_code), "duration_sec": f"{float(duration):.2f}", "component": component, "log_path": str(log_path), "status": status, "notes": notes},
    )


def _append_manifest(repo: Path, *, command_id: str, command: list[str], result: dict[str, str | int | float], artifact_path: str, artifact_hash: str = "") -> None:
    _append_csv(
        artifact_dir(repo) / "results" / "command_manifest.csv",
        ("command_id", "command", "exit_code", "duration_sec", "log_path", "artifact_path", "artifact_hash", "input_provenance", "notes"),
        {"command_id": command_id, "command": " ".join(command), "exit_code": str(result["exit_code"]), "duration_sec": str(result["duration_sec"]), "log_path": str(result["log_path"]), "artifact_path": artifact_path, "artifact_hash": artifact_hash, "input_provenance": "docker image/context", "notes": "docker_runner"},
    )


def _append_csv(path: Path, fieldnames: tuple[str, ...], row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def _write_policy_log(repo: Path, prefix: str, command: list[str], reason: str) -> str:
    logs_dir = artifact_dir(repo) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    path = logs_dir / f"{prefix}_blocked_{time.time_ns()}.log"
    path.write_text("command: " + " ".join(command) + f"\nblocked: {reason}\n", encoding="utf-8")
    return str(path)


def _docker_image_id(tag: str) -> str:
    try:
        completed = subprocess.run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=20, check=False)
    except Exception:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _image_exists(tag: str) -> bool:
    return bool(_docker_image_id(tag))


def _coerce_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
