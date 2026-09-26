"""
ai/prompts.py — Prompt templates for Black Flag AI integration.

Mode A (plan_adaptations): structured JSON prompt to select primitives.
Mode B (diagnose_failure): structured JSON prompt to diagnose a build failure.

Both prompts instruct the model to respond with strict JSON conforming to
the specified schema. The planner validates all primitive_id values against
the catalog before using them — invalid IDs are simply dropped.
"""
from __future__ import annotations

import json

from black_flag.core.types import AppliedDiff, PortabilityIssue, StageResult


_MODE_A_SYSTEM = """\
You are a Linux portability expert. Your task is to select the minimal set of
adaptation primitives that will make the given application portable across
Ubuntu, Fedora, and Arch Linux.

You MUST respond with valid JSON only — no prose, no markdown, no code fences.
The JSON must conform exactly to this schema:

{
  "primitives": [
    {
      "primitive_id": "<one of the available_primitives>",
      "params": { ... },
      "issue_ids": [<integers>],
      "rationale": "<one sentence>"
    }
  ]
}

Rules:
- Only use primitive IDs from the available_primitives list.
- Order primitives so that shell fixes (shell_compat) come before package manager fixes.
- Each primitive must address at least one issue_id.
- params must be valid for the primitive (see per-primitive notes below).

Per-primitive params:
  shell_compat:          {"strategy": "explicit_bash" | "posix_convert"}
  pkg_manager_call:      {}  (or {"file": "<filename>"} to target a specific file)
  distro_path_check:     {}  (or {"path": "/etc/debian_version"})
  env_var_portability:   {"default": "<default_value>"}
  package_name_remap:    {"debian_name": "<package>"}
  requirements_normalize: {}  (or {"old_entry": "...", "new_entry": "..."})
  service_name_remap:    {"debian_name": "<service>"}
"""

_MODE_B_SYSTEM = """\
You are a Linux portability expert. A build or test stage has failed.
Your task is to select one adaptation primitive to repair the failure,
or declare give_up if the failure is outside the primitive catalog.

You MUST respond with valid JSON only — no prose, no markdown, no code fences.

If you can repair it:
{"primitive_id": "<one of available_primitives>", "params": {...}, "rationale": "<one sentence>"}

If the failure is outside the catalog:
{"action": "give_up", "reason": "<brief explanation>"}

Rules:
- Only use primitive IDs from the available_primitives list.
- Do not suggest a primitive that has already been applied (see applied_diffs).
- Focus on the stderr/stdout for the actual error, not surface symptoms.
"""


def build_mode_a_prompt(
    issues: list[PortabilityIssue],
    catalog: list[str],
    targets: list[str],
) -> list[dict]:
    """Build the messages list for a Mode A (plan) API call."""
    issues_json = json.dumps(
        [
            {
                "id": i,
                "category": issue.category,
                "severity": issue.severity,
                "source_file": issue.source_file,
                "line": issue.line,
                "affected_targets": issue.affected_targets,
                "explanation": issue.explanation,
                "suggested_primitive": issue.suggested_primitive,
            }
            for i, issue in enumerate(issues)
        ],
        indent=2,
    )

    user_content = json.dumps(
        {
            "task": "plan_adaptations",
            "targets": targets,
            "available_primitives": catalog,
            "issues": json.loads(issues_json),
        },
        indent=2,
    )

    return [
        {"role": "system", "content": _MODE_A_SYSTEM},
        {"role": "user", "content": user_content},
    ]


def build_mode_b_prompt(
    failed_result: StageResult,
    applied_diffs: list[AppliedDiff],
    catalog: list[str],
) -> list[dict]:
    """Build the messages list for a Mode B (diagnose) API call."""
    diffs_summary = [
        {"primitive_id": d.primitive_id, "target_file": d.target_file}
        for d in applied_diffs
    ]

    user_content = json.dumps(
        {
            "task": "diagnose_failure",
            "distro": failed_result.distro,
            "stage": failed_result.stage,
            "exit_code": failed_result.exit_code,
            "stdout": (failed_result.stdout or "")[-2000:],  # last 2000 chars
            "stderr": (failed_result.stderr or "")[-2000:],
            "applied_diffs_so_far": diffs_summary,
            "available_primitives": catalog,
        },
        indent=2,
    )

    return [
        {"role": "system", "content": _MODE_B_SYSTEM},
        {"role": "user", "content": user_content},
    ]
