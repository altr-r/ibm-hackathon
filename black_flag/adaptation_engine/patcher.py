"""
adaptation_engine/patcher.py — Apply primitives to the working tree.

The patcher:
  1. Reads the target file from the working tree.
  2. Calls primitive.apply() on the file content.
  3. Writes the modified content back.
  4. Computes a unified diff (before vs. after).
  5. Writes the diff as a .patch file in <working_tree>/diffs/.
  6. Returns an AppliedDiff record.

All file modifications go through this module — never directly through
primitive.apply() — so every change is tracked and reversible.
"""
from __future__ import annotations

import difflib
import shutil
import tempfile
from pathlib import Path

from black_flag.core.types import AppliedDiff, PortabilityIssue
from black_flag.primitives.base import AdaptationPrimitive, PrimitiveNotApplicable
from black_flag.runtime.detector import get_all_adapters


def _build_adapters_dict() -> dict:
    """Return {distro_id: adapter} for all supported distros."""
    return {a.distro_id: a for a in get_all_adapters()}


def apply_primitive(
    *,
    primitive: AdaptationPrimitive,
    params: dict,
    target_file_rel: str,   # relative path within working tree
    working_tree: Path,
    sequence: int,
    rationale: str,
    affected_targets: list[str],
    iteration: int,
) -> AppliedDiff:
    """
    Apply *primitive* to *target_file_rel* inside *working_tree*.

    Returns an AppliedDiff. Raises PrimitiveNotApplicable if the pattern
    is not found (the file is left unchanged in that case).
    """
    target_path = working_tree / target_file_rel
    if not target_path.exists():
        raise FileNotFoundError(f"Target file not found: {target_path}")

    before = target_path.read_text(encoding="utf-8", errors="replace")
    ext = target_path.suffix.lower()

    adapters = _build_adapters_dict()
    after = primitive.apply(before, params, adapters)

    if after == before:
        raise PrimitiveNotApplicable(
            f"{primitive.id}: apply() returned unchanged content for {target_file_rel!r}"
        )

    after_lf = after.replace("\r\n", "\n").replace("\r", "\n")

    with open(target_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(after_lf)

    before_split = before.replace("\r\n", "\n").splitlines(keepends=True)
    after_split = after_lf.splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            before_split,
            after_split,
            fromfile=f"a/{target_file_rel}",
            tofile=f"b/{target_file_rel}",
        )
    )
    diff_text = "".join(diff_lines)

    diffs_dir = working_tree / "diffs"
    diffs_dir.mkdir(exist_ok=True)
    patch_name = f"{sequence:03d}-{primitive.id}.patch"
    with open(diffs_dir / patch_name, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(diff_text)

    return AppliedDiff(
        sequence=sequence,
        primitive_id=primitive.id,
        params=params,
        target_file=target_file_rel,
        diff_text=diff_text,
        reason=rationale,
        affected_targets=affected_targets,
        verification_method="docker-test",
        iteration=iteration,
    )


def apply_plan(
    *,
    plan_applications: list,  # list[PrimitiveApplication] (avoid circular import)
    working_tree: Path,
    start_sequence: int = 1,
    iteration: int = 0,
    issues: list | None = None,  # list[PortabilityIssue] — used to resolve affected_targets
) -> tuple[list[AppliedDiff], list[str]]:
    """
    Apply a full AdaptationPlan to the working tree.

    Returns (applied_diffs, warnings).
    Skips (and logs a warning for) any primitive whose pattern is not found.

    *issues* is the list of PortabilityIssue returned by run_analysis().  When
    supplied, affected_targets on each AppliedDiff is populated with the union of
    distro names from the referenced issues rather than the raw integer issue_ids.
    """
    from black_flag.primitives.catalog import get_primitive

    applied: list[AppliedDiff] = []
    warnings: list[str] = []
    seq = start_sequence
    issues_list = issues or []

    for prim_app in plan_applications:
        try:
            primitive = get_primitive(prim_app.primitive_id)
        except KeyError as e:
            warnings.append(str(e))
            continue

        target_file = prim_app.params.get("file", "")
        if not target_file:
            # Scan working tree for the first matching file
            target_file = _find_matching_file(primitive, working_tree)
            if not target_file:
                warnings.append(
                    f"{primitive.id}: no matching file found in working tree"
                )
                continue

        # Derive affected_targets from the referenced issues (distro names, not indices)
        affected: list[str] = []
        for idx in prim_app.issue_ids:
            if 0 <= idx < len(issues_list):
                for distro in issues_list[idx].affected_targets:
                    if distro not in affected:
                        affected.append(distro)
        # Fall back to a sensible default if no issues were supplied or matched
        if not affected:
            affected = ["fedora", "arch"]

        try:
            diff = apply_primitive(
                primitive=primitive,
                params=prim_app.params,
                target_file_rel=target_file,
                working_tree=working_tree,
                sequence=seq,
                rationale=prim_app.rationale,
                affected_targets=affected,
                iteration=iteration,
            )
            applied.append(diff)
            seq += 1
        except PrimitiveNotApplicable as e:
            warnings.append(f"Skipped {primitive.id}: {e}")
        except FileNotFoundError as e:
            warnings.append(f"Skipped {primitive.id}: {e}")

    return applied, warnings


def _find_matching_file(primitive: AdaptationPrimitive, working_tree: Path) -> str | None:
    """Return the relative path of the first file in the working tree that the primitive matches."""
    for path in sorted(working_tree.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in primitive.supported_file_types:
            continue
        # Skip diffs dir
        if "diffs" in path.parts or "bf_scripts" in path.parts:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if primitive.matches(content, path.suffix.lower()):
            return str(path.relative_to(working_tree))
    return None
