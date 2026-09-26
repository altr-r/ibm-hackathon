"""
adaptation_engine/planner.py — Validate and order the AI-produced AdaptationPlan.

The planner:
  1. Receives the raw AdaptationPlan from the AI provider.
  2. Validates every primitive_id against the catalog.
  3. Drops invalid IDs with a warning (never raises).
  4. Returns a validated AdaptationPlan safe to hand to the patcher.

Ordering rules (applied after validation):
  - PRIM-004 (shell_compat) always runs before PRIM-001 (pkg_manager_call)
    on .sh files, because normalizing the shebang first avoids patching
    a file that will be changed again.
  - Other primitives maintain their AI-specified order.
"""
from __future__ import annotations

from black_flag.core.types import AdaptationPlan, PrimitiveApplication
from black_flag.primitives.catalog import get_primitive, list_primitive_ids


# Primitives that should run before others when on the same file
_ORDERING_PRIORITY: dict[str, int] = {
    "shell_compat": 0,       # shebang fix first
    "pkg_manager_call": 1,   # then package manager dispatch
    "package_name_remap": 2, # then individual package renames
    "distro_path_check": 3,  # Python path fixes
    "env_var_portability": 4,# Python env var fixes
    "requirements_normalize": 5,
    "service_name_remap": 6,
}


def validate_plan(plan: AdaptationPlan) -> tuple[AdaptationPlan, list[str]]:
    """
    Validate and sort an AdaptationPlan.

    Returns (validated_plan, warnings).
    """
    valid_ids = set(list_primitive_ids())
    warnings: list[str] = []
    valid_apps: list[PrimitiveApplication] = []

    for app in plan.applications:
        if app.primitive_id not in valid_ids:
            warnings.append(
                f"Dropped unknown primitive ID: {app.primitive_id!r}. "
                f"Valid IDs: {sorted(valid_ids)}"
            )
            continue
        valid_apps.append(app)

    # Sort by ordering priority (lower = earlier)
    valid_apps.sort(key=lambda a: _ORDERING_PRIORITY.get(a.primitive_id, 99))

    return AdaptationPlan(applications=valid_apps, source=plan.source), warnings
