"""
web/schemas.py — Pydantic request/response models for the Black Flag web API.

These models define the contract between the vanilla-JS frontend and the
FastAPI backend. They carry only safe, non-secret data: portability issues,
pipeline stage states, log lines, compatibility results, and package metadata.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

# The three supported target distributions (mirrors the engine's adapters).
VALID_TARGETS: tuple[str, ...] = ("ubuntu", "fedora", "arch")


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    """Body for POST /api/projects/analyze."""

    source: str = Field(
        default="demo",
        description="'demo' for the built-in example, or an upload_id from /api/projects/upload.",
    )


class SelectRequest(BaseModel):
    """Body for POST /api/projects/select — resolve lightweight project metadata."""

    source: str = Field(default="demo")


class AdaptRequest(BaseModel):
    """Body for POST /api/projects/adapt — start a background adaptation job."""

    source: str = Field(default="demo")
    targets: list[str] = Field(default_factory=lambda: list(VALID_TARGETS))
    provider: Optional[str] = Field(
        default=None,
        description="Force an AI provider ('ollama' | 'watsonx' | 'deterministic'). "
                    "When omitted the server environment/factory default is used.",
    )
    max_iterations: int = Field(default=3, ge=1, le=6)
    dry_run: bool = Field(
        default=False,
        description="Analyze + plan + adapt without running the Docker matrix.",
    )


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

class IssueOut(BaseModel):
    category: str
    severity: str
    source_file: str
    line: int
    affected_targets: list[str]
    explanation: str
    suggested_primitive: Optional[str] = None
    verification_method: str


class ProjectMeta(BaseModel):
    name: str
    file_count: int
    project_type: Optional[str] = None


class AnalyzeResponse(ProjectMeta):
    score: float
    issue_count: int
    issues: list[IssueOut]


class UploadResponse(ProjectMeta):
    upload_id: str


class StageOut(BaseModel):
    id: str
    label: str
    state: str  # pending | running | complete | failed | skipped


class LogLine(BaseModel):
    ts: str
    level: str  # info | warn | error
    message: str


class JobStatusResponse(BaseModel):
    job_id: str
    kind: str
    status: str  # pending | running | complete | failed
    stage: Optional[str] = None
    stages: list[StageOut]
    created_at: str
    updated_at: str


class JobResponse(JobStatusResponse):
    logs: list[LogLine] = Field(default_factory=list)
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    version: str
    provider: str
