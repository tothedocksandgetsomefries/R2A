import json
from pathlib import Path

from r2a.tools.engineer_runtime import run_engineer_runtime


def test_engineer_runtime_writes_progress_without_cmake_project(tmp_path: Path) -> None:
    result = run_engineer_runtime(tmp_path, timeout=60)

    results_dir = tmp_path / ".r2a" / "results"
    assert (results_dir / "dependency_setup.csv").exists()
    assert (results_dir / "build_smoke.csv").exists()
    assert (results_dir / "engineer_progress.json").exists()
    assert (tmp_path / ".r2a" / "logs" / "engineer_runtime.log").exists()
    assert result.generated_files
    assert "dependency_check" in result.successful_stages


def test_engineer_runtime_records_cmake_configure_when_project_exists(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / ".r2a" / "artifacts" / "demo"
    source.mkdir(parents=True)
    (source / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\nproject(demo)\n", encoding="utf-8")

    from r2a.tools import engineer_runtime

    def fake_run(stage, command, *, cwd, timeout):
        if stage == "cmake_configure":
            build_dir = source / "build_r2a"
            build_dir.mkdir()
            (build_dir / "CMakeCache.txt").write_text("ok\n", encoding="utf-8")
            return engineer_runtime.RuntimeCommand(stage, command, 0, 0.1, "configured", "")
        return engineer_runtime.RuntimeCommand(stage, command, 0, 0.1, "ok", "")

    monkeypatch.setattr(engineer_runtime, "_resolve_tool", lambda name: name)
    monkeypatch.setattr(engineer_runtime, "_cmake_generator", lambda: "")
    monkeypatch.setattr(engineer_runtime, "_run_command", fake_run)

    result = run_engineer_runtime(tmp_path, timeout=60)

    assert "cmake_configure" in result.successful_stages
    build_text = (tmp_path / ".r2a" / "results" / "build_smoke.csv").read_text(encoding="utf-8")
    assert "cmake_configure" in build_text
    assert "OK" in build_text


def test_engineer_runtime_prefers_explicit_mingw_toolchain(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / ".r2a" / "artifacts" / "demo"
    source.mkdir(parents=True)
    (source / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\nproject(demo)\n", encoding="utf-8")

    from r2a.tools import engineer_runtime

    tools = {
        "cmake": "C:/Tools/cmake.exe",
        "ninja": "C:/Tools/ninja.exe",
        "mingw32-make": "D:/mingw64/bin/mingw32-make.exe",
        "gcc": "D:/mingw64/bin/gcc.exe",
        "g++": "D:/mingw64/bin/g++.exe",
        "make": "",
    }
    captured: dict[str, list[str]] = {}

    def fake_run(stage, command, *, cwd, timeout):
        if stage == "cmake_configure":
            captured["command"] = command
            build_dir = source / "build_mingw"
            build_dir.mkdir()
            (build_dir / "CMakeCache.txt").write_text("ok\n", encoding="utf-8")
            return engineer_runtime.RuntimeCommand(stage, command, 0, 0.1, "configured", "")
        return engineer_runtime.RuntimeCommand(stage, command, 0, 0.1, "ok", "")

    monkeypatch.setattr(engineer_runtime, "_resolve_tool", lambda name: tools.get(name, name))
    monkeypatch.setattr(engineer_runtime, "_run_command", fake_run)

    result = run_engineer_runtime(tmp_path, timeout=60)

    command = captured["command"]
    assert "cmake_configure" in result.successful_stages
    assert command[command.index("-G") + 1] == "MinGW Makefiles"
    assert "-DCMAKE_POLICY_VERSION_MINIMUM=3.5" in command
    assert "-DCMAKE_MAKE_PROGRAM=D:/mingw64/bin/mingw32-make.exe" in command
    assert "-DCMAKE_C_COMPILER=D:/mingw64/bin/gcc.exe" in command
    assert "-DCMAKE_CXX_COMPILER=D:/mingw64/bin/g++.exe" in command


def test_engineer_runtime_reuses_successful_progress_without_rerunning(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / ".r2a" / "artifacts" / "demo"
    build_dir = source / "build_r2a"
    results_dir = tmp_path / ".r2a" / "results"
    build_dir.mkdir(parents=True)
    results_dir.mkdir(parents=True)
    (source / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\nproject(demo)\n", encoding="utf-8")
    (build_dir / "CMakeCache.txt").write_text("configured\n", encoding="utf-8")
    (build_dir / "Makefile").write_text("all:\n", encoding="utf-8")
    (results_dir / "dependency_setup.csv").write_text(
        "package,command,status,version,evidence_source,notes\npython,python --version,OK,Python 3,test,\n",
        encoding="utf-8",
    )
    (results_dir / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\nOK,cmake -S demo -B build_r2a,0,1.0,cmake_configure,configured\n",
        encoding="utf-8",
    )
    (results_dir / "engineer_progress.json").write_text(
        json.dumps(
            {
                "successful_stages": ["dependency_check", "cmake_configure"],
                "stages": {
                    "dependency_check": {"status": "OK", "evidence": str(results_dir / "dependency_setup.csv")},
                    "cmake_configure": {"status": "OK", "build_dir": str(build_dir), "evidence": str(build_dir / "CMakeCache.txt")},
                },
            }
        ),
        encoding="utf-8",
    )

    from r2a.tools import engineer_runtime

    def fail_run(*args, **kwargs):
        raise AssertionError("runtime should reuse previous successful stages")

    monkeypatch.setattr(engineer_runtime, "_cmake_generator", lambda: "")
    monkeypatch.setattr(engineer_runtime, "_run_command", fail_run)

    result = run_engineer_runtime(tmp_path, timeout=60, iteration=2)

    assert [command.command[0] for command in result.commands] == ["reuse", "reuse"]
    assert result.failed_stages == []
    build_text = (results_dir / "build_smoke.csv").read_text(encoding="utf-8")
    assert "OK,cmake -S demo -B build_r2a" in build_text
    assert "SKIPPED_REUSED" in build_text
    progress = json.loads((results_dir / "engineer_progress.json").read_text(encoding="utf-8"))
    assert progress["iteration"] == 2
    assert progress["stages"]["dependency_check"]["status"] == "SKIPPED_REUSED"
    assert progress["stages"]["cmake_configure"]["status"] == "SKIPPED_REUSED"


def test_engineer_runtime_records_checkpoint_stage_hints(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / ".r2a" / "artifacts" / "demo"
    build_dir = source / "build_r2a"
    results_dir = tmp_path / ".r2a" / "results"
    build_dir.mkdir(parents=True)
    (build_dir / "src").mkdir()
    results_dir.mkdir(parents=True)
    (source / "CMakeLists.txt").write_text("cmake_minimum_required(VERSION 3.20)\nproject(demo)\n", encoding="utf-8")
    (build_dir / "src" / "libdemo.a").write_bytes(b"library")
    (build_dir / "smoke_test.exe").write_bytes(b"exe")
    (results_dir / "reduced_metrics.csv").write_text(
        "metric,status,value,notes\nrecall,NOT_RUN,,blocked\nlatency,BLOCKED,,blocked\n",
        encoding="utf-8",
    )

    from r2a.tools import engineer_runtime

    def fake_run(stage, command, *, cwd, timeout):
        if stage == "cmake_configure":
            (build_dir / "CMakeCache.txt").write_text("ok\n", encoding="utf-8")
            (build_dir / "Makefile").write_text("all:\n", encoding="utf-8")
        return engineer_runtime.RuntimeCommand(stage, command, 0, 0.1, "ok", "")

    monkeypatch.setattr(engineer_runtime, "_resolve_tool", lambda name: name)
    monkeypatch.setattr(engineer_runtime, "_cmake_generator", lambda: "")
    monkeypatch.setattr(engineer_runtime, "_run_command", fake_run)

    run_engineer_runtime(tmp_path, timeout=60)

    progress = json.loads((results_dir / "engineer_progress.json").read_text(encoding="utf-8"))
    assert progress["stages"]["source_artifact"]["status"] == "OK"
    assert progress["stages"]["core_build"]["status"] == "OK"
    assert progress["stages"]["smoke_test"]["status"] == "OK"
    assert progress["stages"]["reduced_experiment"]["status"] == "BLOCKED"


def test_engineer_runtime_marks_resolved_cmake_failure_not_active(tmp_path: Path, monkeypatch) -> None:
    results_dir = tmp_path / ".r2a" / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "dependency_setup.csv").write_text(
        "package,command,status,version,evidence_source,notes\npython,python --version,OK,Python 3,test,\n",
        encoding="utf-8",
    )
    (results_dir / "build_smoke.csv").write_text(
        "status,command,exit_code,duration_sec,component,notes\n"
        "RESOLVED,cmake configure,NA,0,cmake_configure,prior blocker resolved by later FDANN CMake build\n",
        encoding="utf-8",
    )
    (results_dir / "engineer_progress.json").write_text(
        json.dumps(
            {
                "successful_stages": ["dependency_check"],
                "failed_stages": ["cmake_configure"],
                "stages": {
                    "dependency_check": {"status": "OK", "evidence": str(results_dir / "dependency_setup.csv")},
                    "cmake_configure": {"status": "BLOCKED", "evidence": ""},
                },
            }
        ),
        encoding="utf-8",
    )

    from r2a.tools import engineer_runtime

    def fail_if_run(*args, **kwargs):
        raise AssertionError("dependency probe should be reused")

    monkeypatch.setattr(engineer_runtime, "_run_command", fail_if_run)

    result = run_engineer_runtime(tmp_path, timeout=60, iteration=8)

    progress = json.loads((results_dir / "engineer_progress.json").read_text(encoding="utf-8"))
    assert result.failed_stages == []
    assert progress["failed_stages"] == []
    assert progress["resolved_stages"] == ["cmake_configure"]
    assert progress["stages"]["cmake_configure"]["status"] == "RESOLVED"
