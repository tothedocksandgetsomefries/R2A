from __future__ import annotations

from importlib import resources
from pathlib import Path
from typing import Any

from r2a.tools.markdown_utils import render_template, utc_timestamp


def load_template(name: str) -> str:
    return resources.files("r2a.templates").joinpath(name).read_text(encoding="utf-8")


def write_report(
    path: Path,
    template_name: str,
    values: dict[str, Any],
    *,
    force: bool = True,
) -> Path:
    if path.exists() and not force:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(values)
    data.setdefault("generated_at", utc_timestamp())
    content = render_template(load_template(template_name), data)
    path.write_text(content, encoding="utf-8")
    return path
