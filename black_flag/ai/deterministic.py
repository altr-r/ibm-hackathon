"""
ai/deterministic.py — DeterministicProvider: rule-based AI fallback.

No API calls. Uses category → primitive mapping for Mode A and
stderr pattern → repair primitive for Mode B.

This provider is always available and produces the same output as the
LLM provider for the demo application's known issues.
"""
from __future__ import annotations

import re

from black_flag.core.types import (
    AdaptationPlan,
    AdaptationSource,
    AppliedDiff,
    PortabilityIssue,
    PrimitiveApplication,
    RepairAction,
    StageResult,
)
from black_flag.primitives.catalog import primitive_for_category
from .provider import AIProvider

# ---------------------------------------------------------------------------
# Mode A: category → primitive mapping
# ---------------------------------------------------------------------------

_CATEGORY_PRIMITIVE_MAP: dict[str, str] = {
    "package-manager":  "pkg_manager_call",
    "library-name":     "package_name_remap",
    "hardcoded-path":   "distro_path_check",
    "env-assumption":   "env_var_portability",
    "shell-ism":        "shell_compat",
    "service-name":     "service_name_remap",
}

# requirements.txt library-name issues use requirements_normalize, not package_name_remap
# This is handled by checking the source_file extension in plan_adaptations below.

# Default params for each primitive when derived deterministically
def _default_params(primitive_id: str, issue: PortabilityIssue) -> dict:
    if primitive_id == "shell_compat":
        return {"strategy": "explicit_bash"}
    if primitive_id == "env_var_portability":
        return {"default": "noninteractive"}
    if primitive_id == "pkg_manager_call":
        return {}
    if primitive_id == "distro_path_check":
        return {}
    if primitive_id == "package_name_remap":
        return {}
    if primitive_id == "requirements_normalize":
        return {}
    if primitive_id == "service_name_remap":
        return {}
    return {}


# ---------------------------------------------------------------------------
# Mode B: stderr pattern → repair primitive
# ---------------------------------------------------------------------------

_STDERR_REPAIR_RULES: list[tuple[re.Pattern, str, dict, str]] = [
    (
        re.compile(r"apt(?:-get)?\s*:?\s*command\s+not\s+found", re.IGNORECASE),
        "pkg_manager_call",
        {},
        "apt-get not found; replacing with distro-dispatch block",
    ),
    (
        re.compile(r"apt(?:-get)?\s*:?\s*not\s+found", re.IGNORECASE),
        "pkg_manager_call",
        {},
        "apt-get not found; replacing with distro-dispatch block",
    ),
    (
        re.compile(r"/etc/debian_version.*no\s+such\s+file", re.IGNORECASE),
        "distro_path_check",
        {},
        "/etc/debian_version missing; replacing with /etc/os-release detection",
    ),
    (
        re.compile(r"RuntimeError.*[Dd]ebian"),
        "distro_path_check",
        {},
        "RuntimeError raised for non-Debian system; replacing with portable path check",
    ),
    (
        re.compile(r"KeyError.*DEBIAN_FRONTEND", re.IGNORECASE),
        "env_var_portability",
        {"default": "noninteractive"},
        "DEBIAN_FRONTEND not set; replacing with os.environ.get()",
    ),
    (
        re.compile(r"No package .+ found", re.IGNORECASE),
        "package_name_remap",
        {},
        "Package not found; remapping Debian package name",
    ),
    (
        re.compile(r"No match for argument: .+", re.IGNORECASE),
        "package_name_remap",
        {},
        "dnf: No match for package; remapping Debian package name",
    ),
    (
        re.compile(r"target not found: .+", re.IGNORECASE),
        "package_name_remap",
        {},
        "pacman: target not found; remapping Debian package name",
    ),
]


class DeterministicProvider(AIProvider):
    name = "deterministic"

    def plan_adaptations(
        self,
        issues: list[PortabilityIssue],
        catalog: list[str],
        targets: list[str] | None = None,
    ) -> AdaptationPlan:
        """
        Build an AdaptationPlan from category → primitive rules.

        De-duplicates primitives: if multiple issues map to the same primitive,
        one PrimitiveApplication covering all relevant issue indices is emitted.
        """
        # Group issues by primitive_id
        prim_to_issues: dict[str, list[int]] = {}
        for idx, issue in enumerate(issues):
            prim_id = _CATEGORY_PRIMITIVE_MAP.get(issue.category)
            # requirements.txt library-name issues should use requirements_normalize
            if prim_id == "package_name_remap" and issue.source_file.endswith(".txt"):
                prim_id = "requirements_normalize"
            if prim_id and prim_id in catalog:
                prim_to_issues.setdefault(prim_id, []).append(idx)

        # Build plan in ordering priority order
        from black_flag.adaptation_engine.planner import _ORDERING_PRIORITY
        ordered = sorted(prim_to_issues.keys(), key=lambda p: _ORDERING_PRIORITY.get(p, 99))

        applications: list[PrimitiveApplication] = []
        for prim_id in ordered:
            issue_ids = prim_to_issues[prim_id]
            first_issue = issues[issue_ids[0]]
            params = _default_params(prim_id, first_issue)
            applications.append(
                PrimitiveApplication(
                    primitive_id=prim_id,
                    params=params,
                    issue_ids=issue_ids,
                    rationale=f"Deterministic rule: {first_issue.category} → {prim_id}",
                )
            )

        return AdaptationPlan(applications=applications, source="deterministic")

    def diagnose_failure(
        self,
        failed_result: StageResult,
        applied_diffs: list[AppliedDiff],
        catalog: list[str],
    ) -> RepairAction:
        """
        Match known stderr patterns to a repair primitive.
        Returns give_up if no pattern matches.
        """
        combined = (failed_result.stdout or "") + "\n" + (failed_result.stderr or "")

        for pattern, prim_id, params, rationale in _STDERR_REPAIR_RULES:
            if pattern.search(combined):
                # Don't re-apply a primitive that's already been applied
                already_applied = {d.primitive_id for d in applied_diffs}
                if prim_id in already_applied:
                    continue
                if prim_id not in catalog:
                    continue
                return RepairAction(
                    action="apply",
                    primitive_id=prim_id,
                    params=params,
                    rationale=rationale,
                )

        return RepairAction(
            action="give_up",
            primitive_id=None,
            params={},
            rationale=(
                f"No deterministic repair rule matched for {failed_result.distro} "
                f"{failed_result.stage} failure. "
                f"stderr: {(failed_result.stderr or '')[:200]!r}"
            ),
        )

    def summarize(
        self,
        issues: list[PortabilityIssue],
        applied_diffs: list[AppliedDiff],
        passed_targets: list[str],
        failed_targets: list[str],
    ) -> str:
        n_issues = len(issues)
        n_diffs = len(applied_diffs)
        passed_str = ", ".join(passed_targets) or "none"
        failed_str = ", ".join(failed_targets) or "none"
        return (
            f"{n_issues} portability issue(s) detected. "
            f"{n_diffs} adaptation(s) applied via deterministic rules. "
            f"Compatible targets: {passed_str}. "
            f"Failed targets: {failed_str}."
        )
