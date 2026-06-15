from __future__ import annotations

from r2a.tools.prompt_loader import load_prompt


def test_planner_v2_prompt_contains_json_self_check_rules() -> None:
    prompt = load_prompt("planner_v2")

    required = [
        "Before final answer, internally validate the JSON.",
        "Return only the final JSON object.",
        "Do not output Markdown fences.",
        "Do not output explanations.",
        "Do not output comments.",
        "All keys and strings must use double quotes.",
        "Every object field must be separated by commas.",
        "No trailing commas.",
        "All braces and brackets must be closed.",
        "tasks must be non-empty.",
        "stop_conditions must be non-empty.",
        "The output must conform to PlannerOutput schema.",
        "Do not output the checklist or your validation process.",
    ]

    for line in required:
        assert line in prompt


def test_planner_v2_prompt_still_forbids_browsing_tools_and_commands() -> None:
    prompt = load_prompt("planner_v2")

    assert "Do not call tools." in prompt
    assert "Do not browse the web." in prompt
    assert "Do not execute commands." in prompt
    assert "Do not write files." in prompt


def test_planner_v2_prompt_does_not_introduce_retry_contract() -> None:
    prompt = load_prompt("planner_v2").lower()

    assert "retry the planner" not in prompt
    assert "planner retry" not in prompt
    assert "rerun planner" not in prompt
    assert "workflow graph" not in prompt
