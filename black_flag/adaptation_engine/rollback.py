"""
adaptation_engine/rollback.py — Working-tree rollback utilities.

Black Flag uses a copy-based rollback strategy (no git dependency):
  - Source is copied to a temp directory at the start of `adapt`.
  - All patches are applied in-place on the copy.
  - On repair loop iteration: wipe the temp dir copy and re-apply only the
    diffs from the best-so-far state.

This module provides helpers for:
  - Creating the initial working tree copy
  - Resetting the working tree to the original source
  - Re-applying a list of previously recorded diffs
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from black_flag.core.types import AppliedDiff


def create_working_tree(source_dir: Path) -> Path:
    """
    Copy *source_dir* into a fresh temp directory and return its path.

    The caller is responsible for cleaning up the directory when done
    (typically with shutil.rmtree).
    """
    tmp = Path(tempfile.mkdtemp(prefix="blackflag-"))
    dst = tmp / source_dir.name
    shutil.copytree(str(source_dir), str(dst), dirs_exist_ok=False)
    return dst


def reset_working_tree(working_tree: Path, source_dir: Path) -> None:
    """
    Reset *working_tree* to the original *source_dir* contents.

    Wipes *working_tree* in-place and re-copies from *source_dir*.
    The diffs/ and bf_scripts/ subdirectories are always removed.
    """
    # Remove everything inside the working tree
    for child in list(working_tree.iterdir()):
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()

    # Re-copy from source
    shutil.copytree(str(source_dir), str(working_tree), dirs_exist_ok=True)


def reapply_diffs(
    diffs: list[AppliedDiff],
    working_tree: Path,
    source_dir: Path,
) -> tuple[list[AppliedDiff], list[str]]:
    """
    Reset *working_tree* to *source_dir* and re-apply a list of diffs.

    Returns (successfully_applied, warnings).
    Used at the start of each repair iteration to restore a known-good state.
    """
    from black_flag.adaptation_engine.patcher import apply_primitive
    from black_flag.primitives.catalog import get_primitive
    from black_flag.primitives.base import PrimitiveNotApplicable

    reset_working_tree(working_tree, source_dir)

    applied: list[AppliedDiff] = []
    warnings: list[str] = []

    for diff in diffs:
        try:
            primitive = get_primitive(diff.primitive_id)
        except KeyError as e:
            warnings.append(str(e))
            continue
        try:
            new_diff = apply_primitive(
                primitive=primitive,
                params=diff.params,
                target_file_rel=diff.target_file,
                working_tree=working_tree,
                sequence=diff.sequence,
                rationale=diff.reason,
                affected_targets=diff.affected_targets,
                iteration=diff.iteration,
            )
            applied.append(new_diff)
        except (PrimitiveNotApplicable, FileNotFoundError) as e:
            warnings.append(f"Re-apply failed for {diff.primitive_id}: {e}")

    return applied, warnings
