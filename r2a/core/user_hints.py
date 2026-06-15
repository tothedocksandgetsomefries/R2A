from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


URL_PATTERN = re.compile(r"https?://[^\s,;)\]]+")

METRIC_KEYWORDS = ("recall", "qps", "latency", "throughput", "accuracy", "f1", "auc", "map", "ndcg")
EXPERIMENT_KEYWORDS = ("baseline", "ablation", "smoke", "reduced", "docker", "cuda", "cpu", "full benchmark")


def build_user_hints(
    *,
    text: str = "",
    source_urls: Any = None,
    dataset_urls: Any = None,
    model_weight_urls: Any = None,
    other_urls: Any = None,
    origin: str = "user_provided_hint",
) -> dict[str, Any]:
    """Build structured, auditable optional user guidance without verifying it."""
    raw_text = str(text or "")
    extracted = _classify_urls_from_text(raw_text)
    source = [*_string_list(source_urls), *extracted["source_urls"]]
    datasets = [*_string_list(dataset_urls), *extracted["dataset_urls"]]
    weights = [*_string_list(model_weight_urls), *extracted["model_weight_urls"]]
    other = [*_string_list(other_urls), *extracted["other_urls"]]
    return {
        "schema_version": 1,
        "text": raw_text.strip(),
        "source_urls": _dedupe_clean_urls(source),
        "dataset_urls": _dedupe_clean_urls(datasets),
        "model_weight_urls": _dedupe_clean_urls(weights),
        "other_urls": _dedupe_clean_urls(other),
        "preferred_metrics": _keywords_in_text(raw_text, METRIC_KEYWORDS),
        "preferred_experiments": _keywords_in_text(raw_text, EXPERIMENT_KEYWORDS),
        "origin": origin,
        "verification_note": (
            "User Guidance is optional user-provided context. It is not verified paper evidence "
            "unless independently confirmed by source inspection, paper artifacts, or execution evidence."
        ),
    }


def normalize_user_hints(value: Any, *, fallback_text: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        return build_user_hints(
            text=str(value.get("text", fallback_text) or fallback_text or ""),
            source_urls=value.get("source_urls"),
            dataset_urls=value.get("dataset_urls"),
            model_weight_urls=value.get("model_weight_urls"),
            other_urls=value.get("other_urls"),
            origin=str(value.get("origin") or "user_provided_hint"),
        )
    return build_user_hints(text=fallback_text)


def user_hints_from_state(state: dict[str, Any]) -> dict[str, Any]:
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    raw = state.get("user_hints") or metadata.get("user_hints")
    source_urls: list[str] = []
    dataset_urls: list[str] = []
    model_urls: list[str] = []
    other_urls: list[str] = []
    for key in ("official_source_url", "github_repo_url", "source_url"):
        source_urls.extend(_string_list(state.get(key)))
    for key in ("github_repo_url", "source_url"):
        source_urls.extend(_string_list(metadata.get(key)))
    source_urls.extend(_string_list(state.get("optional_source_urls")))
    dataset_urls.extend(_string_list(state.get("dataset_urls")))
    dataset_urls.extend(_string_list(metadata.get("dataset_urls")))
    for item in metadata.get("dataset_downloads", []) or []:
        if isinstance(item, dict):
            dataset_urls.extend(_string_list(item.get("url")))
    model_urls.extend(_string_list(state.get("model_weight_urls")))
    model_urls.extend(_string_list(metadata.get("model_weight_urls")))

    normalized = normalize_user_hints(raw, fallback_text=str(state.get("guidance") or state.get("goal") or ""))
    return build_user_hints(
        text=normalized.get("text", ""),
        source_urls=[*normalized.get("source_urls", []), *source_urls],
        dataset_urls=[*normalized.get("dataset_urls", []), *dataset_urls],
        model_weight_urls=[*normalized.get("model_weight_urls", []), *model_urls],
        other_urls=[*normalized.get("other_urls", []), *other_urls],
        origin=str(normalized.get("origin") or "user_provided_hint"),
    )


def format_user_hints_markdown(user_hints: dict[str, Any] | None) -> str:
    hints = normalize_user_hints(user_hints or {})
    lines = [
        "User Guidance is optional user-provided context.",
        "Use it when relevant.",
        "If it provides source repository URLs, dataset URLs, model weight URLs, or important paper input locations, treat them as high-priority user-provided hints.",
        "Do not treat user guidance as verified paper evidence unless independently confirmed.",
        "If irrelevant, ignore it.",
        "Do not use it to bypass network/download authorization.",
        "Do not use it to expand L4 reduced scope into full reproduction.",
    ]
    if hints.get("text"):
        lines.append(f"Raw guidance: {hints['text']}")
    for label, key in (
        ("Source URL hints", "source_urls"),
        ("Dataset URL hints", "dataset_urls"),
        ("Model weight URL hints", "model_weight_urls"),
        ("Other URL hints", "other_urls"),
        ("Preferred metrics", "preferred_metrics"),
        ("Preferred experiments", "preferred_experiments"),
    ):
        values = hints.get(key) or []
        if values:
            lines.append(f"{label}: " + ", ".join(str(value) for value in values))
    return "\n".join(lines)


def write_user_hints_artifact(repo: str | Path, user_hints: dict[str, Any]) -> Path:
    from r2a.core.paths import report_path

    path = report_path(repo, "user_hints")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalize_user_hints(user_hints), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _classify_urls_from_text(text: str) -> dict[str, list[str]]:
    result = {"source_urls": [], "dataset_urls": [], "model_weight_urls": [], "other_urls": []}
    for raw_url in URL_PATTERN.findall(text or ""):
        url = _strip_url(raw_url)
        lowered = url.lower()
        around = _context_for_url(text, raw_url).lower()
        if _looks_like_source_url(lowered):
            result["source_urls"].append(url)
        elif "dataset" in lowered or "datasets" in lowered or "dataset" in around or "data url" in around:
            result["dataset_urls"].append(url)
        elif "weight" in lowered or "checkpoint" in lowered or "model" in lowered or "weights" in around or "checkpoint" in around:
            result["model_weight_urls"].append(url)
        else:
            result["other_urls"].append(url)
    return result


def _looks_like_source_url(url: str) -> bool:
    return "github.com/" in url or "gitlab.com/" in url or url.endswith(".git") or url.startswith("git@")


def _context_for_url(text: str, url: str, *, radius: int = 80) -> str:
    index = text.find(url)
    if index < 0:
        return ""
    return text[max(0, index - radius) : min(len(text), index + len(url) + radius)]


def _keywords_in_text(text: str, keywords: tuple[str, ...]) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in keywords if keyword in lowered]


def _dedupe_clean_urls(values: list[str]) -> list[str]:
    return list(dict.fromkeys(_strip_url(value) for value in values if _strip_url(value)))


def _strip_url(value: Any) -> str:
    cleaned = str(value or "").strip().strip("<>\"'")
    while cleaned and cleaned[-1] in ".,;)]}":
        cleaned = cleaned[:-1]
    return cleaned


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", "\n").replace(",", "\n").splitlines() if item.strip()]
    return []
