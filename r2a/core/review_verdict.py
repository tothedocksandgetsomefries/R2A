from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from r2a.core.final_decision import UNASSESSED
from r2a.core.paths import report_path
from r2a.core.reviewer_level_judgment import LEVEL_LABELS, LEVEL_SEMANTICS, is_valid_level
from r2a.core.verdicts import VALID_VERDICTS, is_pass_like_verdict, normalize_verdict
from r2a.tools.reproduction_levels import infer_level_from_verdict, normalize_level


DEFAULT_REVIEW_VERDICT_SOURCE = "reviewer_structured_verdict"


@dataclass(frozen=True)
class ReviewVerdictValidation:
    valid: bool
    payload: dict[str, Any]
    errors: list[str]
    warnings: list[str]


def review_verdict_path(repo_path: str | Path) -> Path:
    return report_path(repo_path, "review_verdict")


def normalize_verdict_token(value: object) -> str:
    text = str(value or "").strip()
    changed = True
    while changed and text:
        changed = False
        before = text
        text = _strip_wrapping(text, "**", "**")
        text = _strip_wrapping(text, "`", "`")
        text = _strip_wrapping(text, '"', '"')
        text = _strip_wrapping(text, "'", "'")
        text = _strip_wrapping(text, "\u201c", "\u201d")
        text = _strip_wrapping(text, "\u2018", "\u2019")
        changed = text != before
    return text.strip().upper()


def load_review_verdict(path_or_repo: str | Path) -> ReviewVerdictValidation:
    path = Path(path_or_repo)
    if path.is_dir():
        path = review_verdict_path(path)
    if not path.exists():
        return ReviewVerdictValidation(False, {}, ["REVIEW_VERDICT.json missing."], [])
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return ReviewVerdictValidation(False, {}, [f"REVIEW_VERDICT.json is not valid JSON: {exc}"], [])
    if not isinstance(data, dict):
        return ReviewVerdictValidation(False, {}, ["REVIEW_VERDICT.json must be a JSON object."], [])
    return validate_review_verdict(data)


def validate_review_verdict(data: dict[str, Any]) -> ReviewVerdictValidation:
    errors: list[str] = []
    warnings: list[str] = []
    payload = dict(data)

    verdict = normalize_verdict_token(payload.get("verdict"))
    payload["verdict"] = verdict
    if verdict not in VALID_VERDICTS:
        errors.append(f"verdict must be one of VALID_VERDICTS; got {payload.get('verdict')!r}.")

    accepted_level = _normalize_accepted_level(payload.get("accepted_level"))
    payload["accepted_level"] = accepted_level
    if accepted_level != UNASSESSED and not is_valid_level(accepted_level):
        errors.append(f"accepted_level must be a valid L0-L6 level or {UNASSESSED}; got {accepted_level!r}.")

    level_valid = payload.get("level_valid")
    if not isinstance(level_valid, bool):
        errors.append("level_valid must be a bool.")
        level_valid = False
    payload["level_valid"] = bool(level_valid)

    target_reached = payload.get("target_reached")
    if not isinstance(target_reached, bool):
        errors.append("target_reached must be a bool.")
        target_reached = False
    payload["target_reached"] = bool(target_reached)

    target_level = payload.get("target_level")
    if target_level not in {None, ""}:
        normalized_target = normalize_level(str(target_level), "")
        if not normalized_target:
            errors.append(f"target_level must be a valid L0-L6 level when present; got {target_level!r}.")
        else:
            payload["target_level"] = normalized_target

    evidence_files = payload.get("evidence_files", [])
    if not isinstance(evidence_files, list) or not all(isinstance(item, str) for item in evidence_files):
        errors.append("evidence_files must be list[str].")
        evidence_files = []
    payload["evidence_files"] = evidence_files

    limitations = payload.get("limitations", [])
    if limitations is None:
        limitations = []
    if not isinstance(limitations, list) or not all(isinstance(item, str) for item in limitations):
        errors.append("limitations must be list[str] when present.")
        limitations = []
    payload["limitations"] = limitations

    needs_fix_reasons = payload.get("needs_fix_reasons", [])
    if needs_fix_reasons is None:
        needs_fix_reasons = []
    if not isinstance(needs_fix_reasons, list) or not all(isinstance(item, str) for item in needs_fix_reasons):
        errors.append("needs_fix_reasons must be list[str].")
        needs_fix_reasons = []
    payload["needs_fix_reasons"] = needs_fix_reasons

    if is_pass_like_verdict(verdict):
        inferred = infer_level_from_verdict(verdict)
        if not inferred or not is_valid_level(inferred):
            errors.append(f"PASS-like verdict {verdict} does not map to a valid reproduction level.")
        if accepted_level == UNASSESSED:
            errors.append("PASS-like verdict must include a non-UNASSESSED accepted_level.")
        if payload["level_valid"] is not True:
            errors.append("PASS-like verdict must set level_valid=true.")
        non_warning_reasons = [item for item in needs_fix_reasons if not _is_warning_reason(item)]
        if non_warning_reasons:
            errors.append("PASS-like verdict needs_fix_reasons must be empty or warning-only.")
    elif verdict == "NEEDS_FIX":
        if accepted_level.startswith("L4"):
            errors.append("NEEDS_FIX accepted_level must not be L4.")
        if accepted_level != UNASSESSED:
            errors.append(f"NEEDS_FIX accepted_level should be {UNASSESSED}.")
        if payload["level_valid"] is not False:
            errors.append("NEEDS_FIX must set level_valid=false.")
    else:
        if payload["level_valid"] and accepted_level == UNASSESSED:
            errors.append("Non-pass verdict with level_valid=true must include a valid accepted_level.")

    payload.setdefault("schema_version", 1)
    payload.setdefault("source", DEFAULT_REVIEW_VERDICT_SOURCE)
    payload.setdefault("backend", "")

    return ReviewVerdictValidation(not errors, payload, errors, warnings)


def build_review_verdict_payload(
    *,
    verdict: str,
    accepted_level: str | None,
    level_valid: bool,
    target_level: str | None = None,
    target_reached: bool = False,
    evidence_files: list[str] | None = None,
    limitations: list[str] | None = None,
    needs_fix_reasons: list[str] | None = None,
    backend: str = "",
    source: str = DEFAULT_REVIEW_VERDICT_SOURCE,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "verdict": normalize_verdict_token(verdict),
        "accepted_level": _normalize_accepted_level(accepted_level),
        "level_valid": bool(level_valid),
        "target_level": normalize_level(target_level, "") if target_level else "",
        "target_reached": bool(target_reached),
        "evidence_files": list(evidence_files or []),
        "limitations": list(limitations or []),
        "needs_fix_reasons": list(needs_fix_reasons or []),
        "backend": backend,
        "source": source,
    }


def build_evidence_decision_from_review_verdict(
    validation: ReviewVerdictValidation,
    *,
    iteration: int,
    backend: str = "",
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload = dict(validation.payload)
    verdict = normalize_verdict(payload.get("verdict"))
    accepted_level = _normalize_accepted_level(payload.get("accepted_level"))
    level_valid = bool(payload.get("level_valid") and is_pass_like_verdict(verdict) and accepted_level != UNASSESSED)
    source = str(payload.get("source") or DEFAULT_REVIEW_VERDICT_SOURCE)
    warnings = [*validation.warnings, *list(extra_warnings or [])]

    return {
        "schema_version": 1,
        "current_reproduction_level": accepted_level,
        "level_label": LEVEL_LABELS.get(accepted_level, accepted_level if accepted_level != UNASSESSED else None),
        "level_semantics": LEVEL_SEMANTICS.get(accepted_level, ""),
        "level_reasoning": _level_reasoning(payload, validation.errors),
        "supporting_artifacts": list(payload.get("evidence_files", []) or []),
        "remaining_gaps": list(payload.get("needs_fix_reasons", []) or []),
        "verdict": verdict,
        "iteration": int(iteration),
        "level_source": source if level_valid else "unassessed",
        "level_valid": level_valid,
        "backend": str(payload.get("backend") or backend or ""),
        "target_level": payload.get("target_level", ""),
        "target_reached": bool(payload.get("target_reached", False)),
        "limitations": list(payload.get("limitations", []) or []),
        "warnings": warnings,
        "review_verdict_source": source,
    }


def extract_review_verdict_json(text: str) -> dict[str, Any] | None:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")

    tagged = re.search(
        r"<REVIEW_VERDICT_JSON>\s*(\{[\s\S]*?\})\s*</REVIEW_VERDICT_JSON>",
        normalized,
        flags=re.IGNORECASE,
    )
    if tagged:
        parsed = _parse_json_object(tagged.group(1))
        if parsed is not None:
            return parsed

    heading_block = re.search(
        r"(?im)^#{1,6}\s+Machine\s+Verdict\s+JSON\s*$[\s\S]*?```(?:json)?\s*([\s\S]*?)\s*```",
        normalized,
    )
    if heading_block:
        parsed = _parse_json_object(heading_block.group(1))
        if parsed is not None:
            return parsed

    for match in re.finditer(r"```(?:json)?\s*([\s\S]*?)\s*```", normalized, flags=re.IGNORECASE):
        parsed = _parse_json_object(match.group(1))
        if parsed is not None and ("verdict" in parsed or "accepted_level" in parsed):
            return parsed

    return None


def write_review_verdict(path: str | Path, payload: dict[str, Any]) -> ReviewVerdictValidation:
    validation = validate_review_verdict(payload)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(validation.payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return validation


def _normalize_accepted_level(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.upper() == UNASSESSED:
        return UNASSESSED
    normalized = normalize_level(text, "")
    return normalized or text


def _level_reasoning(payload: dict[str, Any], errors: list[str]) -> str:
    if errors:
        return "Structured REVIEW_VERDICT.json rejected: " + "; ".join(errors)
    verdict = normalize_verdict(payload.get("verdict"))
    limitations = [str(item) for item in payload.get("limitations", []) or [] if str(item).strip()]
    if verdict == "NEEDS_FIX":
        reasons = [str(item) for item in payload.get("needs_fix_reasons", []) or [] if str(item).strip()]
        return "; ".join(reasons) or "Reviewer structured verdict requires fixes."
    if limitations:
        return "Reviewer structured verdict accepted with limitations: " + "; ".join(limitations)
    return "Reviewer structured verdict accepted."


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(raw.strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _strip_wrapping(text: str, prefix: str, suffix: str) -> str:
    stripped = text.strip()
    if stripped.startswith(prefix) and stripped.endswith(suffix) and len(stripped) >= len(prefix) + len(suffix):
        return stripped[len(prefix) : len(stripped) - len(suffix)].strip()
    return stripped


def _is_warning_reason(value: str) -> bool:
    text = str(value or "").strip().lower()
    return not text or text.startswith("warning") or text.startswith("[warning]")
