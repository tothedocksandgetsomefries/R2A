from __future__ import annotations

import os
from typing import Mapping


DEFAULT_STAGE_API_KEY_ENV = {
    "codex": "OPENAI_API_KEY",
    "codex_review": "OPENAI_API_KEY",
    "ai_reader": "OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "claude_reader": "ANTHROPIC_API_KEY",
    "claude_review": "ANTHROPIC_API_KEY",
}


def build_stage_env(
    *,
    stage: str,
    backend: str,
    stage_api_keys: Mapping[str, str] | None = None,
    stage_api_key_env_vars: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    """Return a subprocess env with the stage API key injected, if configured."""
    if backend in {"openclaw", "openclaw_reader", "openclaw_review"}:
        return None
    key = (stage_api_keys or {}).get(stage, "").strip()
    if not key:
        return None
    env_var = (stage_api_key_env_vars or {}).get(stage, "").strip() or DEFAULT_STAGE_API_KEY_ENV.get(backend, "")
    if not env_var:
        return None
    env = os.environ.copy()
    env[env_var] = key
    return env
