"""
core/types.py — All shared dataclasses and enums for Black Flag.

Every module in the project imports from here; nothing here imports from
any other black_flag module (zero internal deps).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ---------------------------------------------------------------------------
# Enumerations (kept as plain string literals for JSON round-trip simplicity)
# ---------------------------------------------------------------------------

IssueCategory = Literal[
    "package-manager",
    "hardcoded-path",
    "library-name",
    "shell-ism",
    "env-assumption",
    "service-name",
]

IssueSeverity = Literal["error", "warning", "info"]

VerificationMethod = Literal["docker-test", "static", "manual"]

TargetDistro = Literal["ubuntu", "fedora", "arch"]

Stage = Literal["prepare", "build", "test"]

AdaptationSource = Literal["ai", "deterministic"]

TargetStatus = Literal["COMPATIBLE", "FAILED", "UNSUPPORTED", "UNVERIFIED"]


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


@dataclass
class PortabilityIssue:
    """A single portability problem detected by the static analyzer."""

    category: IssueCategory
    severity: IssueSeverity
    source_file: str                 # relative path from source root
    line: int                        # 1-based; 0 = file-level issue
    affected_targets: list[str]      # subset of ["ubuntu", "fedora", "arch"]
    explanation: str
    suggested_primitive: str | None  # hint for the planner; AI may override
    verification_method: VerificationMethod


# ---------------------------------------------------------------------------
# Adaptation planning
# ---------------------------------------------------------------------------


@dataclass
class PrimitiveApplication:
    """A single primitive chosen by the planner for a set of issues."""

    primitive_id: str          # e.g. "pkg_manager_call"
    params: dict               # primitive-specific parameters
    issue_ids: list[int]       # indices into the issues list this addresses
    rationale: str             # AI-provided or deterministic explanation


@dataclass
class AdaptationPlan:
    """Ordered list of primitive applications returned by the AI planner."""

    applications: list[PrimitiveApplication]
    source: AdaptationSource   # "ai" or "deterministic"


@dataclass
class RepairAction:
    """Repair instruction returned by the AI diagnosis agent."""

    action: Literal["apply", "give_up"]
    primitive_id: str | None   # None when action == "give_up"
    params: dict               # primitive-specific parameters
    rationale: str


# ---------------------------------------------------------------------------
# Applied diffs (working-tree tracking)
# ---------------------------------------------------------------------------


@dataclass
class AppliedDiff:
    """Record of a single primitive application on the working tree."""

    sequence: int              # 001, 002, …
    primitive_id: str
    params: dict
    target_file: str           # relative path within working tree
    diff_text: str             # unified diff (before vs. after)
    reason: str
    affected_targets: list[str]
    verification_method: VerificationMethod
    iteration: int             # which repair-loop iteration produced this


# ---------------------------------------------------------------------------
# Build / test matrix
# ---------------------------------------------------------------------------


@dataclass
class StageResult:
    """Result of a single PREPARE / BUILD / TEST stage inside a Docker container."""

    distro: str
    stage: Stage
    exit_code: int
    stdout: str
    stderr: str
    elapsed_s: float


@dataclass
class BuildMatrix:
    """Aggregated results for all distros and stages in one loop iteration."""

    results: list[StageResult]
    iteration: int

    def passed(self, distro: str) -> bool:
        """True when every stage for *distro* exited 0."""
        return all(r.exit_code == 0 for r in self.results if r.distro == distro)

    def all_passed(self) -> bool:
        """True when every stage for every distro exited 0."""
        return all(r.exit_code == 0 for r in self.results)

    def failed_results(self) -> list[StageResult]:
        return [r for r in self.results if r.exit_code != 0]

    def first_failure(self, distro: str) -> StageResult | None:
        """Return the first failed stage for *distro*, or None."""
        for r in self.results:
            if r.distro == distro and r.exit_code != 0:
                return r
        return None


# ---------------------------------------------------------------------------
# Compatibility manifest
# ---------------------------------------------------------------------------


@dataclass
class TargetResult:
    """Per-distro compatibility summary stored in the manifest."""

    status: TargetStatus
    prepare: bool | None = None   # None = not run
    build: bool | None = None
    test: bool | None = None


@dataclass
class CompatibilityManifest:
    """Full output of one `black-flag adapt` run."""

    name: str
    version: str
    description: str
    portability_score: float              # 0.0 – 1.0 (after adaptation)
    portability_score_before: float       # 0.0 – 1.0 (before adaptation)
    verified: bool                        # True = Docker matrix was run
    targets: dict[str, TargetResult]      # keyed by distro name
    applied_adaptations: list[AppliedDiff]
    issues: list[PortabilityIssue]
    ai_summary: str
    ai_provider: str                      # "watsonx" | "deterministic"
    compatibility_cache_key: str          # "sha256:<hex>"
    created_at: str                       # ISO-8601
