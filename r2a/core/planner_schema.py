from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Valid blocker categories
BLOCKER_CATEGORIES = {
    "SAFE_BUILD_COMPATIBILITY",
    "TOOLCHAIN_OR_ENVIRONMENT",
    "MISSING_ARTIFACT_OR_DATA",
    "API_OR_ALGORITHM_SEMANTICS",
    "RESULT_MISMATCH",
    "SCHEMA_OR_REPORTING",
    "NEEDS_MANUAL_APPROVAL",
    "OTHER",
}

# Category normalization mappings
CATEGORY_NORMALIZATION = {
    # Runtime/environment/toolchain issues
    "RUNTIME_DLL_COMPATIBILITY": "TOOLCHAIN_OR_ENVIRONMENT",
    "RUNTIME_ERROR": "TOOLCHAIN_OR_ENVIRONMENT",
    "DLL_MISSING": "TOOLCHAIN_OR_ENVIRONMENT",
    "CUDA_ERROR": "TOOLCHAIN_OR_ENVIRONMENT",
    "PYTHON_VERSION": "TOOLCHAIN_OR_ENVIRONMENT",
    "COMPILER_ERROR": "TOOLCHAIN_OR_ENVIRONMENT",
    "BUILD_ERROR": "TOOLCHAIN_OR_ENVIRONMENT",
    "ENVIRONMENT_ERROR": "TOOLCHAIN_OR_ENVIRONMENT",
    "DEPENDENCY_MISSING": "TOOLCHAIN_OR_ENVIRONMENT",
    "IMPORT_ERROR": "TOOLCHAIN_OR_ENVIRONMENT",
    # Data/artifact issues
    "DATASET_MISSING": "MISSING_ARTIFACT_OR_DATA",
    "DATA_MISSING": "MISSING_ARTIFACT_OR_DATA",
    "ARTIFACT_MISSING": "MISSING_ARTIFACT_OR_DATA",
    "CHECKPOINT_MISSING": "MISSING_ARTIFACT_OR_DATA",
    "FILE_NOT_FOUND": "MISSING_ARTIFACT_OR_DATA",
    # Schema/format issues
    "CSV_ERROR": "SCHEMA_OR_REPORTING",
    "FORMAT_ERROR": "SCHEMA_OR_REPORTING",
    "REPORT_ERROR": "SCHEMA_OR_REPORTING",
    "VALIDATION_ERROR": "SCHEMA_OR_REPORTING",
    # Approval/permission issues
    "MANUAL_APPROVAL": "NEEDS_MANUAL_APPROVAL",
    "PERMISSION_DENIED": "NEEDS_MANUAL_APPROVAL",
    "AUTHORIZATION_REQUIRED": "NEEDS_MANUAL_APPROVAL",
}


def normalize_blocker_category(category: str) -> str:
    """Normalize blocker category to valid enum value.

    Unknown categories are mapped to OTHER instead of causing validation failure.
    """
    if category in BLOCKER_CATEGORIES:
        return category
    # Check normalization mapping
    normalized = CATEGORY_NORMALIZATION.get(category)
    if normalized and normalized in BLOCKER_CATEGORIES:
        return normalized
    # Fuzzy matching for common patterns
    category_lower = category.lower()
    if any(token in category_lower for token in ["runtime", "dll", "cuda", "python", "compiler", "build", "environment", "toolchain", "import"]):
        return "TOOLCHAIN_OR_ENVIRONMENT"
    if any(token in category_lower for token in ["dataset", "data", "artifact", "missing", "file"]):
        return "MISSING_ARTIFACT_OR_DATA"
    if any(token in category_lower for token in ["schema", "csv", "format", "report", "validation"]):
        return "SCHEMA_OR_REPORTING"
    if any(token in category_lower for token in ["manual", "approval", "permission", "auth"]):
        return "NEEDS_MANUAL_APPROVAL"
    # Default to OTHER for unknown categories
    return "OTHER"


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    source: str
    status: Literal["SUPPORTED", "GAP", "INFERRED", "CONFLICT"]
    notes: str = ""


class BlockingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_id: str
    category: Literal[
        "SAFE_BUILD_COMPATIBILITY",
        "TOOLCHAIN_OR_ENVIRONMENT",
        "MISSING_ARTIFACT_OR_DATA",
        "API_OR_ALGORITHM_SEMANTICS",
        "RESULT_MISMATCH",
        "SCHEMA_OR_REPORTING",
        "NEEDS_MANUAL_APPROVAL",
        "OTHER",
    ]
    description: str
    evidence_source: str
    severity: Literal["BLOCKING", "NON_BLOCKING"]
    suggested_resolution: str = ""

    @field_validator("category", mode="before")
    @classmethod
    def _normalize_category(cls, value: str) -> str:
        """Normalize category to valid enum value before validation."""
        if isinstance(value, str):
            return normalize_blocker_category(value)
        return value


class PlannerTask(BaseModel):
    """Planner task with minimal required fields.

    Only `actions` is required - all other fields are optional with defaults.
    This ensures Planner output is accepted as long as tasks have executable actions.
    """
    model_config = ConfigDict(extra="forbid")

    task_id: str = ""  # Optional - auto-generated if missing
    title: str = ""  # Optional - not required for execution
    objective: str = ""  # Optional - not required for execution
    rationale: str = ""  # Optional - not required for execution
    actions: list[str]  # REQUIRED - must have at least one executable action
    depends_on: list[str] = Field(default_factory=list)
    run_if: str | None = None
    expected_outputs: list[str] = Field(default_factory=list)  # Optional
    acceptance_criteria: list[str] = Field(default_factory=list)  # Optional
    stop_conditions: list[str] = Field(default_factory=list)  # Optional
    allowed_write_paths: list[str] = Field(default_factory=list)
    allow_network: bool = False
    allow_docker: bool = False
    requires_manual_approval: bool = False

    @field_validator("actions")
    @classmethod
    def _non_empty_actions(cls, value: list[str]) -> list[str]:
        """Actions is the ONLY required field - must have at least one executable action."""
        if not value:
            raise ValueError("actions must not be empty - every task needs at least one executable action")
        return value

    @field_validator("task_id", mode="before")
    @classmethod
    def _auto_task_id(cls, value: Any) -> str:
        """Auto-generate task_id if missing."""
        if not value or not str(value).strip():
            return f"T_auto_{id(value)}"
        return str(value)


class PlannerOutput(BaseModel):
    """Planner output with minimal required fields.

    Only `tasks` with executable actions is required.
    `contract_mode` and `max_evidence_level_allowed` are ALWAYS overwritten by system.
    """
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"]
    iteration: int = Field(ge=1)
    planning_mode: Literal["initial", "iterative_progress"]
    iteration_strategy: Literal[
        "FIX_AND_PROGRESS",
        "PROGRESS_ONLY",
        "BLOCKED_OR_NEEDS_APPROVAL",
    ]
    objective: str = ""  # Optional - not required for execution
    contract_mode: Literal[  # Will be OVERWRITTEN by enforce_system_contract_mode()
        "verification_only",
        "smoke",
        "official_reduced",
        "full_benchmark",
    ] = "verification_only"
    max_evidence_level_allowed: str = "L2_input_contract_ready"  # Will be OVERWRITTEN by system
    current_status_summary: str = ""  # Optional
    completed_capabilities: list[str] = Field(default_factory=list)  # Optional
    blocking_issues: list[BlockingIssue] = Field(default_factory=list)  # Optional
    evidence_used: list[EvidenceItem] = Field(default_factory=list)  # Optional
    evidence_gaps: list[EvidenceItem] = Field(default_factory=list)  # Optional
    tasks: list[PlannerTask]  # REQUIRED - must have at least one valid task
    claim_restrictions: list[str] = Field(default_factory=list)  # Optional
    manual_approval_points: list[str] = Field(default_factory=list)  # Optional
    preserve_outputs: list[str] = Field(default_factory=list)  # Optional
    planner_notes: list[str] = Field(default_factory=list)  # Optional

    @field_validator("tasks")
    @classmethod
    def _at_least_one_valid_task(cls, value: list[PlannerTask]) -> list[PlannerTask]:
        """Tasks is REQUIRED - must have at least one task with executable actions."""
        if not value:
            raise ValueError("tasks must not be empty - Planner must produce at least one task")
        # Check if at least one task has actions
        tasks_with_actions = [t for t in value if t.actions]
        if not tasks_with_actions:
            raise ValueError("at least one task must have non-empty actions")
        return value

    @model_validator(mode="after")
    def _coherent_mode_and_strategy(self) -> "PlannerOutput":
        # Relax mode/iteration check - auto-correct instead of failing
        if self.planning_mode == "initial" and self.iteration > 1:
            self.planning_mode = "iterative_progress"
        if self.planning_mode == "iterative_progress" and self.iteration <= 1:
            self.iteration = 2
        if self.iteration_strategy == "BLOCKED_OR_NEEDS_APPROVAL" and not (
            self.blocking_issues or self.manual_approval_points
        ):
            # Auto-correct instead of failing
            self.iteration_strategy = "FIX_AND_PROGRESS"
        return self


def planner_output_schema_json() -> str:
    return PlannerOutput.model_json_schema()


# System-enforced contract mode calculation
# This is the ONLY place that determines contract_mode based on user permissions.

def calculate_system_contract_mode(
    allow_full_benchmark: bool,
    allow_official_dataset_download: bool,
    download_budget_gb: int = 0,
) -> str:
    """Calculate contract_mode based on user permissions.

    This is the authoritative contract_mode calculation.
    Planner model output is NEVER used to determine contract_mode.

    Rules:
    - allow_full_benchmark = true -> "full_benchmark"
    - allow_official_dataset_download = true (with budget > 0) -> "official_reduced"
    - otherwise -> "verification_only"
    """
    if allow_full_benchmark:
        return "full_benchmark"
    if allow_official_dataset_download and download_budget_gb > 0:
        return "official_reduced"
    return "verification_only"


def enforce_system_contract_mode(
    planner_output: PlannerOutput,
    allowed_scope: dict[str, Any] | None,
) -> PlannerOutput:
    """Enforce system-determined contract_mode and target level.

    This function MUST be called after parsing Planner output.
    It overwrites model-generated contract_mode with system-determined value.

    Args:
        planner_output: The Planner output (from model or template)
        allowed_scope: The system-determined allowed_scope containing contract_mode and max_target_level

    Returns:
        PlannerOutput with contract_mode and max_evidence_level_allowed enforced by system.
    """
    if not allowed_scope:
        return planner_output

    data = planner_output.model_dump()

    # Enforce contract_mode from system
    system_contract = allowed_scope.get("contract_mode")
    if system_contract and system_contract in ["verification_only", "smoke", "official_reduced", "full_benchmark"]:
        model_contract = data.get("contract_mode", "")
        if model_contract != system_contract:
            # Log normalization warning in planner_notes
            notes = list(data.get("planner_notes", []))
            notes.append(
                f"contract_mode normalization: model output '{model_contract}' replaced by system value '{system_contract}'"
            )
            data["planner_notes"] = notes
        data["contract_mode"] = system_contract

    # Enforce max_evidence_level_allowed from system
    system_max_level = allowed_scope.get("max_target_level")
    if system_max_level:
        model_max_level = data.get("max_evidence_level_allowed", "")
        if model_max_level != system_max_level:
            notes = list(data.get("planner_notes", []))
            notes.append(
                f"max_evidence_level normalization: model output '{model_max_level}' replaced by system value '{system_max_level}'"
            )
            data["planner_notes"] = notes
        data["max_evidence_level_allowed"] = system_max_level

    return PlannerOutput.model_validate(data)
