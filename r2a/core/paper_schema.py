from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Confidence = Literal["HIGH", "MEDIUM", "LOW"]


class PaperMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    paper_url: str | None = None


class ArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    kind: Literal["source_repo", "dataset", "weights", "project_page", "unknown"]
    confidence: Confidence
    evidence_source: str


class DatasetReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    source_url: str | None = None
    confidence: Confidence
    notes: str = ""


class PaperOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2.0"]
    metadata: PaperMetadata
    problem_setting: str = ""
    method_summary: str = ""
    artifact_references: list[ArtifactReference] = Field(default_factory=list)
    dataset_references: list[DatasetReference] = Field(default_factory=list)
    baselines: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    parse_quality: Literal["HIGH_CONFIDENCE", "PARTIAL", "LOW_CONFIDENCE"]
    quality_reasons: list[str] = Field(default_factory=list)
