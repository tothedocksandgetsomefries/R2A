from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from r2a.core.paths import report_path
from r2a.tools.reproduction_levels import download_budget_gb


def enforce_planner_contract(repo_path: str | Path, state: dict[str, Any]) -> list[str]:
    repo = Path(repo_path)
    task_path = report_path(repo, "task")
    contract_path = report_path(repo, "experiment_contract")
    verdict = _latest_verdict(state)
    budget = download_budget_gb(state)
    warnings: list[str] = []

    if verdict == "NEEDS_OFFICIAL_INPUT" and budget > 0:
        for path in (task_path, contract_path):
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if _needs_official_input_network_override(text):
                path.write_text(_append_network_override(text, budget), encoding="utf-8")
                warnings.append(f"Planner contract guard added official-input network override to {path.name}.")
    return warnings


def _latest_verdict(state: dict[str, Any]) -> str:
    feedback_path = str(state.get("latest_review_feedback_path", "") or "")
    if feedback_path:
        try:
            data = json.loads(Path(feedback_path).read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            data = {}
        if isinstance(data, dict) and data.get("verdict"):
            return str(data["verdict"])
    return str(state.get("reviewer_verdict", "") or "")


def _needs_official_input_network_override(text: str) -> bool:
    lowered = text.lower()
    if "planner contract guard override" in lowered:
        return False
    has_missing_input_context = "needs_official_input" in lowered or "official input" in lowered or "query files" in lowered or "ground truth" in lowered
    has_download_contradiction = any(
        marker in lowered
        for marker in (
            "network access: `not authorized`",
            "network access: not authorized",
            "data download budget for this iteration: `0gb`",
            "data download budget for this iteration: 0gb",
            "new data download budget: `0gb`",
            "new data download budget: 0gb",
            "conditional download permission: `not authorized`",
            "conditional download permission: not authorized",
            "do not browse the web",
            "no network",
        )
    )
    missing_network_scope = "official_input_contract_acquisition_with_network" not in lowered
    return has_missing_input_context and (has_download_contradiction or missing_network_scope)


def _append_network_override(text: str, budget: int) -> str:
    override = f"""

## Planner Contract Guard Override

The previous reviewer verdict was `NEEDS_OFFICIAL_INPUT` and the configured download budget is `{budget}GB`.
This override supersedes contradictory local-only or `0GB` download language above.

- Scope: `official_input_contract_acquisition_with_network`.
- Evidence level remains near `L2_input_contract_ready`; do not claim `L3_official_reduced_run` until official query files and ground truth are actually located.
- Engineer may use bounded network commands to inspect official/paper-linked sources: paper project pages, artifact repositories, releases, dataset pages, metadata, and documented storage links.
- Engineer may download only official or paper-linked query files, ground truth, experiment scripts, metadata, or the smallest reduced subset within `{budget}GB`.
- Do not download full-scale benchmarks, large external baselines, or persistent caches without explicit user approval.
- If the paper does not specify the missing inputs and bounded official/paper-linked network search cannot locate them, stop early with `NEEDS_OFFICIAL_INPUT,missing_official_input_after_network_search` and do not rerun unrelated build/runtime smoke.
"""
    return f"{text.rstrip()}\n{override.strip()}\n"
