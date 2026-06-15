from pathlib import Path


def test_readme_documents_docker_as_manual_guidance_only() -> None:
    text = Path("README.md").read_text(encoding="utf-8")
    compact = " ".join(text.split())

    assert "## Docker Guidance (Manual Only)" in text
    assert "does not currently provide an automatic Docker runner" in text
    assert "manual environment option" in text
    assert "Do not use Docker to bypass download approval" in compact
    assert ".r2a/results/docker_runtime_smoke.csv" in text
