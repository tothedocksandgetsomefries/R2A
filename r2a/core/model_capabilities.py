from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityProfile:
    tool_calls: bool = True
    write_tool: bool = True
    edit_tool: bool = True
    multi_edit_tool: bool = True
    bash_tool: bool = False
    streaming: bool = True
    streaming_tool_calls: bool = True
    thinking: bool = True
    thinking_with_tools: bool = False
    structured_output: bool = True
    json_schema: str = "partial"
    anthropic_messages_native: bool = False
    anthropic_beta_headers: str = "partial"
    context_management_beta: bool = False
    count_tokens_endpoint: bool = False

    def to_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class StagePolicy:
    require_tool_calls: bool = False
    require_structured_output: bool = False
    allow_thinking: bool | str = False
    allow_bash: bool = False
    require_transaction: bool = False

    def to_dict(self) -> dict[str, object]:
        return self.__dict__.copy()


STAGE_POLICIES: dict[str, StagePolicy] = {
    "planner": StagePolicy(
        require_tool_calls=True,
        require_structured_output=True,
        allow_thinking=False,
        allow_bash=False,
        require_transaction=True,
    ),
    "reviewer": StagePolicy(
        require_structured_output=True,
        allow_thinking=False,
        allow_bash=False,
        require_transaction=True,
    ),
    "engineer": StagePolicy(require_tool_calls=True, allow_thinking="configurable", allow_bash=True),
    "manager": StagePolicy(require_structured_output=False, allow_thinking=False, allow_bash=False),
}


def default_gateway_capability_profile() -> CapabilityProfile:
    return CapabilityProfile()


def stage_policy(stage: str) -> StagePolicy:
    return STAGE_POLICIES.get(stage, StagePolicy())


def check_stage_policy_compatibility(stage: str, profile: CapabilityProfile | None = None) -> dict[str, object]:
    profile = profile or default_gateway_capability_profile()
    policy = stage_policy(stage)
    errors: list[str] = []
    if policy.require_tool_calls and not profile.tool_calls:
        errors.append("tool_calls")
    if policy.require_structured_output and not profile.structured_output:
        errors.append("structured_output")
    if policy.allow_bash is False and profile.bash_tool and stage in {"planner", "reviewer"}:
        errors.append("bash_tool_not_allowed_for_stage")
    if policy.allow_thinking is False and profile.thinking_with_tools:
        errors.append("thinking_with_tools_not_allowed_for_stage")
    return {
        "ok": not errors,
        "stage": stage,
        "policy": policy.to_dict(),
        "capability_profile": profile.to_dict(),
        "errors": errors,
    }
