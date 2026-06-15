from __future__ import annotations


TOOL_CALL_PARSE_FAILURE = "TOOL_CALL_PARSE_FAILURE"
BACKEND_TRANSIENT_FAILURE = "BACKEND_TRANSIENT_FAILURE"
NODE_DEP0190_WARNING = "NODE_DEP0190_WARNING"
CCR_ADAPTER_FAILURE = "CCR_ADAPTER_FAILURE"
MODEL_TOOL_USE_FORMAT_FAILURE = "MODEL_TOOL_USE_FORMAT_FAILURE"
PERMISSION_MODE_FAILURE = "PERMISSION_MODE_FAILURE"
TOOL_ALLOWLIST_VIOLATION = "TOOL_ALLOWLIST_VIOLATION"
LONG_CONTEXT_TOOL_USE_FAILURE = "LONG_CONTEXT_TOOL_USE_FAILURE"
DEEPSEEK_REASONING_CONTENT_FAILURE = "DEEPSEEK_REASONING_CONTENT_FAILURE"
ANTHROPIC_BETA_HEADER_FAILURE = "ANTHROPIC_BETA_HEADER_FAILURE"
GATEWAY_BETA_COMPATIBILITY_FAILURE = "GATEWAY_BETA_COMPATIBILITY_FAILURE"
AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
BACKEND_AUTH_FAILURE = "BACKEND_AUTH_FAILURE"

TOOL_CALL_PARSE_MARKERS = (
    "tool call could not be parsed",
    "retry also failed",
    "could not be parsed",
    "tool call parse",
)
PERMISSION_MODE_MARKERS = (
    "permission mode",
    "permission denied",
    "access is denied",
    "operation not permitted",
)
TOOL_ALLOWLIST_MARKERS = (
    "not allowed to use tool",
    "tool is not allowed",
    "tool not allowed",
    "not in allowedtools",
    "disallowed tool",
)
LONG_CONTEXT_MARKERS = (
    "context length",
    "maximum context",
    "too many tokens",
    "prompt is too long",
    "context window",
)
CCR_ADAPTER_MARKERS = (
    "claude-code-router",
    "ccr",
    "router adapter",
)
DEEPSEEK_REASONING_CONTENT_MARKERS = (
    "reasoning_content",
    "thinking mode",
    "must be passed back",
    "missing reasoning",
)
ANTHROPIC_BETA_HEADER_MARKERS = (
    "anthropic-beta",
    "extra inputs are not permitted",
    "context_management",
    "input_examples",
    "unexpected value(s) for the `anthropic-beta` header",
)
AUTHENTICATION_MARKERS = (
    "not logged in",
    "please run /login",
    "login required",
    "authentication required",
    "please login",
)


def classify_backend_error(stdout: str, stderr: str, backend: str | None = None) -> dict[str, object]:
    text = f"{stdout or ''}\n{stderr or ''}".lower()
    normalized_backend = (backend or "").strip().lower() or "unknown"

    if any(marker in text for marker in TOOL_CALL_PARSE_MARKERS):
        detail = MODEL_TOOL_USE_FORMAT_FAILURE
        if any(marker in text for marker in CCR_ADAPTER_MARKERS):
            detail = CCR_ADAPTER_FAILURE
        if any(marker in text for marker in DEEPSEEK_REASONING_CONTENT_MARKERS):
            detail = DEEPSEEK_REASONING_CONTENT_FAILURE
        elif any(marker in text for marker in LONG_CONTEXT_MARKERS):
            detail = LONG_CONTEXT_TOOL_USE_FAILURE
        return {
            "is_backend_failure": True,
            "transient_backend_failure": True,
            "failure_category": TOOL_CALL_PARSE_FAILURE,
            "failure_detail": detail,
            "failure_scope": BACKEND_TRANSIENT_FAILURE,
            "backend": normalized_backend,
            "suggested_action": _suggested_action_for_detail(detail),
            "user_message": (
                "Claude Code tool-call parse failure; this is a backend execution issue, "
                "not a paper reproduction failure."
            ),
        }

    if any(marker in text for marker in DEEPSEEK_REASONING_CONTENT_MARKERS):
        return _failure_result(
            normalized_backend,
            DEEPSEEK_REASONING_CONTENT_FAILURE,
            "disable_deepseek_thinking_or_use_ccr_deepseek_transformer",
        )
    if any(marker in text for marker in ANTHROPIC_BETA_HEADER_MARKERS):
        return _failure_result(
            normalized_backend,
            GATEWAY_BETA_COMPATIBILITY_FAILURE,
            "forward_anthropic_beta_or_set_CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS",
            failure_detail=ANTHROPIC_BETA_HEADER_FAILURE,
        )
    if any(marker in text for marker in LONG_CONTEXT_MARKERS):
        return _failure_result(normalized_backend, LONG_CONTEXT_TOOL_USE_FAILURE, "shorten_prompt_or_summarize_context")
    if any(marker in text for marker in TOOL_ALLOWLIST_MARKERS):
        return _failure_result(normalized_backend, TOOL_ALLOWLIST_VIOLATION, "review_stage_allowed_tools")
    if any(marker in text for marker in PERMISSION_MODE_MARKERS):
        return _failure_result(normalized_backend, PERMISSION_MODE_FAILURE, "review_permission_mode")
    if any(marker in text for marker in AUTHENTICATION_MARKERS):
        return {
            "is_backend_failure": True,
            "transient_backend_failure": False,
            "failure_category": AUTHENTICATION_FAILURE,
            "failure_detail": AUTHENTICATION_FAILURE,
            "failure_scope": BACKEND_AUTH_FAILURE,
            "backend": normalized_backend,
            "suggested_action": "login_configured_executor",
            "user_message": (
                "Backend authentication failure; the configured executor is not logged in for this process."
            ),
        }

    result: dict[str, object] = {
        "is_backend_failure": False,
        "transient_backend_failure": False,
        "failure_category": "",
        "failure_detail": "",
        "failure_scope": "",
        "backend": normalized_backend,
        "suggested_action": "",
        "user_message": "",
    }
    if "[dep0190]" in text or "dep0190" in text:
        result["backend_warning"] = NODE_DEP0190_WARNING
    return result


def _suggested_action_for_detail(detail: str) -> str:
    if detail == DEEPSEEK_REASONING_CONTENT_FAILURE:
        return "disable_deepseek_thinking_or_use_ccr_deepseek_transformer"
    if detail == LONG_CONTEXT_TOOL_USE_FAILURE:
        return "shorten_prompt_or_summarize_context"
    return "retry_same_stage_once"


def _failure_result(backend: str, category: str, suggested_action: str, *, failure_detail: str | None = None) -> dict[str, object]:
    return {
        "is_backend_failure": True,
        "transient_backend_failure": True,
        "failure_category": category,
        "failure_detail": failure_detail or category,
        "failure_scope": BACKEND_TRANSIENT_FAILURE,
        "backend": backend,
        "suggested_action": suggested_action,
        "user_message": f"Backend execution issue: {category}; this is not a paper reproduction failure.",
    }
