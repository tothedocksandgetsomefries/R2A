from pathlib import Path
import json
import os
from types import SimpleNamespace

from r2a.workspace.manager import _default_workspace_base, create_workspace
from r2a.workspace.manifest import read_workspace_manifest, workspace_manifest_exists


def test_create_workspace_creates_required_directories(tmp_path: Path) -> None:
    result = create_workspace(tmp_path, goal="add HNSW oversampling baseline")
    workspace_dir = Path(result["workspace_dir"])
    repo_path = Path(result["repo_path"])

    assert workspace_dir.exists()
    assert (workspace_dir / "paper").is_dir()
    assert (workspace_dir / "repo").is_dir()
    assert (workspace_dir / ".r2a").is_dir()
    assert (workspace_dir / ".r2a" / "logs").is_dir()
    assert (workspace_dir / ".r2a" / "results").is_dir()
    assert (repo_path / ".r2a").is_dir()
    assert (repo_path / ".r2a" / "logs").is_dir()
    assert (repo_path / ".r2a" / "results").is_dir()
    assert (repo_path / ".git").is_dir()
    assert (repo_path / ".r2a" / "logs" / "git_init.log").exists()


def test_default_workspace_base_uses_env_override(monkeypatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-workspaces"
    monkeypatch.setenv("R2A_WORKSPACE_BASE", str(custom))

    assert _default_workspace_base() == custom


def test_default_workspace_base_preserves_explicit_legacy_e_drive(monkeypatch) -> None:
    monkeypatch.setenv("R2A_WORKSPACE_BASE", "C:/R2A_WORKSPACES_SAMPLE")

    assert str(_default_workspace_base()).replace("\\", "/") == "C:/R2A_WORKSPACES_SAMPLE"


def test_default_workspace_base_is_not_personal_e_drive(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("R2A_WORKSPACE_BASE", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))

    default = _default_workspace_base()

    assert str(default).replace("\\", "/") != "C:/R2A_WORKSPACES_SAMPLE"
    if os.name == "nt":
        assert default == tmp_path / "LocalAppData" / "R2A" / "workspaces"
    else:
        assert default == Path.home() / ".r2a" / "workspaces"


def test_create_workspace_writes_workspace_manifest(tmp_path: Path) -> None:
    result = create_workspace(tmp_path, goal="manifest check")
    assert workspace_manifest_exists(result["workspace_dir"])
    manifest = read_workspace_manifest(result["workspace_dir"])
    assert manifest is not None
    assert manifest["workspace_id"] == result["run_id"]
    assert manifest["status"] == "created"


def test_create_workspace_writes_metadata(tmp_path: Path) -> None:
    result = create_workspace(tmp_path, goal="add HNSW oversampling baseline")
    metadata_path = Path(result["metadata_path"])

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    assert metadata["goal"] == "add HNSW oversampling baseline"
    assert result["goal"] == "add HNSW oversampling baseline"
    assert metadata["repo_path"] == result["repo_path"]
    assert metadata["workspace_dir"] == result["workspace_dir"]


def test_create_workspace_copies_source_repo_and_ignores_runtime_dirs(tmp_path: Path) -> None:
    source = tmp_path / "source_repo"
    source.mkdir()
    (source / "main.py").write_text("print('hello')\n", encoding="utf-8")
    for ignored in (".git", ".venv", "__pycache__", ".r2a"):
        ignored_dir = source / ignored
        ignored_dir.mkdir()
        (ignored_dir / "ignored.txt").write_text("ignore me\n", encoding="utf-8")

    result = create_workspace(tmp_path / "runs", goal="demo", source_repo_path=source, copy_repo=True)
    repo_path = Path(result["repo_path"])

    assert (repo_path / "main.py").exists()
    assert not (repo_path / ".git" / "ignored.txt").exists()
    assert not (repo_path / ".venv" / "ignored.txt").exists()
    assert not (repo_path / "__pycache__" / "ignored.txt").exists()
    assert not (repo_path / ".r2a" / "ignored.txt").exists()
    assert (source / "main.py").exists()
    assert (source / ".git" / "ignored.txt").exists()


def test_create_workspace_copies_paper(tmp_path: Path) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4")

    result = create_workspace(tmp_path / "runs", goal="demo", paper_file_path=paper)

    copied = Path(result["paper_path"])
    assert copied.exists()
    assert copied.name == "paper.pdf"
    assert copied.read_bytes() == b"%PDF-1.4"


def test_create_workspace_clones_github_repo_when_url_is_provided(tmp_path: Path, monkeypatch) -> None:
    def fake_run(command, capture_output, text, check, **kwargs):
        target = Path(command[-1])
        if command[:2] == ["git", "clone"]:
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir()
            (target / "main.py").write_text("print('from github')\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="cloned\n", stderr="")

    monkeypatch.setattr("r2a.workspace.manager.shutil.which", lambda name: "git")
    monkeypatch.setattr("r2a.workspace.manager.subprocess.run", fake_run)

    result = create_workspace(tmp_path / "runs", goal="demo", github_repo_url="https://github.com/example/repo.git")
    repo_path = Path(result["repo_path"])
    metadata = json.loads(Path(result["metadata_path"]).read_text(encoding="utf-8"))

    assert (repo_path / "main.py").exists()
    assert result["repo_download"]["status"] == "ok"
    assert metadata["github_repo_url"] == "https://github.com/example/repo.git"
    assert (repo_path / ".r2a" / "logs" / "github_clone.log").exists()


def test_create_workspace_downloads_dataset_under_limit(tmp_path: Path, monkeypatch) -> None:
    class FakeResponse:
        headers = {"Content-Length": "11"}

        def __init__(self, body: bytes = b"hello data\n") -> None:
            self.body = body
            self.sent = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size: int) -> bytes:
            if self.sent:
                return b""
            self.sent = True
            return self.body

    def fake_urlopen(request, timeout):
        if getattr(request, "get_method", lambda: "GET")() == "HEAD":
            return FakeResponse(b"")
        return FakeResponse()

    monkeypatch.setattr("r2a.workspace.manager.urlopen", fake_urlopen)

    result = create_workspace(tmp_path / "runs", goal="demo", dataset_urls=["https://example.com/data.csv"])

    downloads = result["dataset_downloads"]
    assert downloads[0]["status"] == "ok"
    assert Path(downloads[0]["path"]).read_bytes() == b"hello data\n"


def test_create_workspace_skips_dataset_over_limit(tmp_path: Path, monkeypatch) -> None:
    class FakeHeadResponse:
        headers = {"Content-Length": str(11 * 1024**3)}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr("r2a.workspace.manager.urlopen", lambda request, timeout: FakeHeadResponse())

    result = create_workspace(tmp_path / "runs", goal="demo", dataset_urls=["https://example.com/large.bin"], max_dataset_download_gb=10)

    assert result["dataset_downloads"][0]["status"] == "skipped"
    assert result["dataset_downloads"][0]["path"] == ""
