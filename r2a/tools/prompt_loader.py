from __future__ import annotations

from importlib import resources
class PromptNotFoundError(FileNotFoundError):
    """Raised when a named R2A prompt markdown file cannot be found."""


def load_prompt(name: str) -> str:
    prompt_name = name if name.endswith(".md") else f"{name}.md"
    try:
        return resources.files("r2a.prompts").joinpath(prompt_name).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PromptNotFoundError(f"Prompt not found: r2a/prompts/{prompt_name}") from exc


def render_prompt(name: str, variables: dict[str, str]) -> str:
    rendered = load_prompt(name)
    for key, value in variables.items():
        rendered = rendered.replace("{{" + key + "}}", str(value))
    return rendered
