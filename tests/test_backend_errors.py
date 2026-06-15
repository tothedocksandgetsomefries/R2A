from r2a.tools.backend_errors import classify_backend_error


def test_tool_call_parse_failure_is_backend_transient_failure() -> None:
    result = classify_backend_error(
        "The model's tool call could not be parsed (retry also failed).",
        "",
        backend="claude",
    )

    assert result["is_backend_failure"] is True
    assert result["transient_backend_failure"] is True
    assert result["failure_category"] == "TOOL_CALL_PARSE_FAILURE"
    assert result["failure_detail"] == "MODEL_TOOL_USE_FORMAT_FAILURE"
    assert result["failure_scope"] == "BACKEND_TRANSIENT_FAILURE"
    assert result["suggested_action"] == "retry_same_stage_once"


def test_retry_also_failed_is_backend_transient_failure() -> None:
    result = classify_backend_error("retry also failed", "", backend="claude")

    assert result["failure_category"] == "TOOL_CALL_PARSE_FAILURE"
    assert result["failure_scope"] == "BACKEND_TRANSIENT_FAILURE"


def test_dep0190_warning_is_not_backend_failure() -> None:
    result = classify_backend_error(
        "",
        "[DEP0190] DeprecationWarning: Passing args to a child process with shell option true.",
        backend="claude",
    )

    assert result["is_backend_failure"] is False
    assert result["backend_warning"] == "NODE_DEP0190_WARNING"


def test_plain_stderr_is_not_backend_failure() -> None:
    result = classify_backend_error("", "command exited with code 1", backend="claude")

    assert result["is_backend_failure"] is False
    assert result["failure_category"] == ""


def test_ccr_parse_failure_gets_adapter_detail() -> None:
    result = classify_backend_error("ccr: The model's tool call could not be parsed", "", backend="claude")

    assert result["failure_category"] == "TOOL_CALL_PARSE_FAILURE"
    assert result["failure_detail"] == "CCR_ADAPTER_FAILURE"


def test_long_context_failure_is_classified() -> None:
    result = classify_backend_error("", "maximum context length exceeded", backend="claude")

    assert result["is_backend_failure"] is True
    assert result["failure_category"] == "LONG_CONTEXT_TOOL_USE_FAILURE"


def test_tool_allowlist_failure_is_classified() -> None:
    result = classify_backend_error("", "tool is not allowed by allowedTools", backend="claude")

    assert result["is_backend_failure"] is True
    assert result["failure_category"] == "TOOL_ALLOWLIST_VIOLATION"


def test_deepseek_reasoning_content_failure_is_classified() -> None:
    result = classify_backend_error(
        "",
        "The reasoning_content in the thinking mode must be passed back to the API.",
        backend="claude",
    )

    assert result["is_backend_failure"] is True
    assert result["failure_category"] == "DEEPSEEK_REASONING_CONTENT_FAILURE"
    assert result["suggested_action"] == "disable_deepseek_thinking_or_use_ccr_deepseek_transformer"


def test_anthropic_beta_gateway_failure_is_classified() -> None:
    result = classify_backend_error(
        "",
        "API Error: 400 Extra inputs are not permitted: context_management. Missing anthropic-beta.",
        backend="claude",
    )

    assert result["is_backend_failure"] is True
    assert result["failure_category"] == "GATEWAY_BETA_COMPATIBILITY_FAILURE"
    assert result["failure_detail"] == "ANTHROPIC_BETA_HEADER_FAILURE"
