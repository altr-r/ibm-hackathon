"""
tests/test_adaptation_engine.py — Tests for patcher, rollback, planner, and AI providers.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Patcher
# ---------------------------------------------------------------------------

def _demo_tree(tmp: str) -> Path:
    """Create a minimal demo working tree for patcher tests."""
    d = Path(tmp) / "demo"
    d.mkdir()
    (d / "setup.sh").write_text("#!/bin/bash\napt-get install -y libssl-dev\n")
    (d / "app.py").write_text(
        'import os\n'
        'if os.path.exists("/etc/debian_version"):\n'
        '    print("debian")\n'
        'else:\n'
        '    raise RuntimeError("This application requires a Debian-based system.")\n'
        'frontend = os.environ["DEBIAN_FRONTEND"]\n'
    )
    (d / "requirements.txt").write_text("python3-cryptography\n")
    return d


def test_patcher_apply_primitive_shell_compat():
    from black_flag.adaptation_engine.patcher import apply_primitive
    from black_flag.primitives.catalog import get_primitive

    with tempfile.TemporaryDirectory() as tmp:
        tree = _demo_tree(tmp)
        prim = get_primitive("shell_compat")
        diff = apply_primitive(
            primitive=prim,
            params={"strategy": "explicit_bash"},
            target_file_rel="setup.sh",
            working_tree=tree,
            sequence=1,
            rationale="test",
            affected_targets=["fedora", "arch"],
            iteration=0,
        )

    assert diff.primitive_id == "shell_compat"
    assert "#!/usr/bin/env bash" in diff.diff_text
    assert "#!/bin/bash" not in diff.diff_text or "+#!/usr/bin/env bash" in diff.diff_text


def test_patcher_apply_primitive_pkg_manager_call():
    from black_flag.adaptation_engine.patcher import apply_primitive
    from black_flag.primitives.catalog import get_primitive

    with tempfile.TemporaryDirectory() as tmp:
        tree = _demo_tree(tmp)
        prim = get_primitive("pkg_manager_call")
        diff = apply_primitive(
            primitive=prim,
            params={},
            target_file_rel="setup.sh",
            working_tree=tree,
            sequence=2,
            rationale="test",
            affected_targets=["fedora", "arch"],
            iteration=0,
        )

        assert diff.primitive_id == "pkg_manager_call"
        modified = (tree / "setup.sh").read_text()
        assert "_BF_DISTRO" in modified
        assert "dnf install -y" in modified


def test_patcher_writes_patch_file():
    from black_flag.adaptation_engine.patcher import apply_primitive
    from black_flag.primitives.catalog import get_primitive

    with tempfile.TemporaryDirectory() as tmp:
        tree = _demo_tree(tmp)
        prim = get_primitive("shell_compat")
        apply_primitive(
            primitive=prim,
            params={"strategy": "explicit_bash"},
            target_file_rel="setup.sh",
            working_tree=tree,
            sequence=1,
            rationale="test",
            affected_targets=[],
            iteration=0,
        )
        assert (tree / "diffs" / "001-shell_compat.patch").exists()


def test_patcher_raises_on_missing_file():
    from black_flag.adaptation_engine.patcher import apply_primitive
    from black_flag.primitives.catalog import get_primitive

    with tempfile.TemporaryDirectory() as tmp:
        tree = Path(tmp) / "empty"
        tree.mkdir()
        prim = get_primitive("shell_compat")
        with pytest.raises(FileNotFoundError):
            apply_primitive(
                primitive=prim,
                params={"strategy": "explicit_bash"},
                target_file_rel="nonexistent.sh",
                working_tree=tree,
                sequence=1,
                rationale="test",
                affected_targets=[],
                iteration=0,
            )


# ---------------------------------------------------------------------------
# apply_plan affected_targets regression
# ---------------------------------------------------------------------------

def test_apply_plan_affected_targets_are_distro_names():
    """
    Regression: apply_plan must populate AppliedDiff.affected_targets with distro
    names like ['fedora', 'arch'], not issue indices like [0, 2, 3].
    """
    from black_flag.adaptation_engine.patcher import apply_plan
    from black_flag.core.types import AdaptationPlan, PrimitiveApplication, PortabilityIssue

    issues = [
        PortabilityIssue(
            category="shell-ism",
            severity="warning",
            source_file="setup.sh",
            line=1,
            affected_targets=["fedora", "arch"],
            explanation="bash shebang",
            suggested_primitive="shell_compat",
            verification_method="docker-test",
        ),
        PortabilityIssue(
            category="package-manager",
            severity="error",
            source_file="setup.sh",
            line=2,
            affected_targets=["fedora", "arch"],
            explanation="apt-get not portable",
            suggested_primitive="pkg_manager_call",
            verification_method="docker-test",
        ),
    ]
    plan = AdaptationPlan(
        applications=[
            PrimitiveApplication(
                primitive_id="shell_compat",
                params={"strategy": "explicit_bash"},
                issue_ids=[0],
                rationale="fix shebang",
            ),
        ],
        source="deterministic",
    )

    with tempfile.TemporaryDirectory() as tmp:
        tree = _demo_tree(tmp)
        applied, warnings = apply_plan(
            plan_applications=plan.applications,
            working_tree=tree,
            start_sequence=1,
            iteration=0,
            issues=issues,
        )

    assert len(applied) == 1
    diff = applied[0]
    # affected_targets must contain distro names, never integer issue indices
    for target in diff.affected_targets:
        assert isinstance(target, str), (
            f"affected_targets must contain distro name strings, got {type(target)}: {target!r}"
        )
        assert target in ("ubuntu", "fedora", "arch"), (
            f"affected_targets must be valid distro names, got: {target!r}"
        )
    assert "fedora" in diff.affected_targets
    assert "arch" in diff.affected_targets


def test_apply_plan_affected_targets_no_issues_list():
    """Without an issues list, affected_targets falls back to ['fedora', 'arch']."""
    from black_flag.adaptation_engine.patcher import apply_plan
    from black_flag.core.types import AdaptationPlan, PrimitiveApplication

    plan = AdaptationPlan(
        applications=[
            PrimitiveApplication(
                primitive_id="shell_compat",
                params={"strategy": "explicit_bash"},
                issue_ids=[0],
                rationale="fix shebang",
            ),
        ],
        source="deterministic",
    )

    with tempfile.TemporaryDirectory() as tmp:
        tree = _demo_tree(tmp)
        applied, warnings = apply_plan(
            plan_applications=plan.applications,
            working_tree=tree,
            start_sequence=1,
            iteration=0,
            # issues not supplied
        )

    assert len(applied) == 1
    diff = applied[0]
    for target in diff.affected_targets:
        assert isinstance(target, str), f"affected_targets must be strings, got {type(target)}"
        assert target in ("ubuntu", "fedora", "arch"), f"Got unexpected target: {target!r}"


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

def test_rollback_reset_working_tree():
    from black_flag.adaptation_engine.rollback import reset_working_tree

    with tempfile.TemporaryDirectory() as tmp:
        src = _demo_tree(tmp)
        # Corrupt the working tree
        (src / "setup.sh").write_text("# corrupted\n")
        # Reset from original
        original = Path(tmp) / "original"
        shutil.copytree(str(src), str(original))
        # Restore original content to src before corrupting again
        original_content = "#!/bin/bash\napt-get install -y libssl-dev\n"
        (original / "setup.sh").write_text(original_content)
        reset_working_tree(src, original)
        assert (src / "setup.sh").read_text() == original_content


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

def test_planner_validate_drops_unknown_ids():
    from black_flag.adaptation_engine.planner import validate_plan
    from black_flag.core.types import AdaptationPlan, PrimitiveApplication

    plan = AdaptationPlan(
        applications=[
            PrimitiveApplication(
                primitive_id="nonexistent",
                params={},
                issue_ids=[0],
                rationale="test",
            ),
            PrimitiveApplication(
                primitive_id="shell_compat",
                params={"strategy": "explicit_bash"},
                issue_ids=[1],
                rationale="valid",
            ),
        ],
        source="ai",
    )
    validated, warnings = validate_plan(plan)
    assert len(validated.applications) == 1
    assert validated.applications[0].primitive_id == "shell_compat"
    assert len(warnings) == 1


def test_planner_ordering():
    """shell_compat should always come before pkg_manager_call."""
    from black_flag.adaptation_engine.planner import validate_plan
    from black_flag.core.types import AdaptationPlan, PrimitiveApplication

    # Give them in wrong order
    plan = AdaptationPlan(
        applications=[
            PrimitiveApplication(primitive_id="pkg_manager_call", params={}, issue_ids=[0], rationale=""),
            PrimitiveApplication(primitive_id="shell_compat", params={}, issue_ids=[1], rationale=""),
        ],
        source="deterministic",
    )
    validated, _ = validate_plan(plan)
    ids = [a.primitive_id for a in validated.applications]
    assert ids.index("shell_compat") < ids.index("pkg_manager_call")


# ---------------------------------------------------------------------------
# Deterministic AI provider
# ---------------------------------------------------------------------------

def test_deterministic_provider_plan():
    from black_flag.ai.deterministic import DeterministicProvider
    from black_flag.core.types import PortabilityIssue
    from black_flag.primitives.catalog import list_primitive_ids

    provider = DeterministicProvider()
    issues = [
        PortabilityIssue(
            category="package-manager",
            severity="error",
            source_file="setup.sh",
            line=3,
            affected_targets=["fedora", "arch"],
            explanation="apt-get not available",
            suggested_primitive="pkg_manager_call",
            verification_method="docker-test",
        ),
        PortabilityIssue(
            category="shell-ism",
            severity="warning",
            source_file="setup.sh",
            line=1,
            affected_targets=["fedora", "arch"],
            explanation="bash shebang",
            suggested_primitive="shell_compat",
            verification_method="docker-test",
        ),
    ]
    plan = provider.plan_adaptations(issues, list_primitive_ids())
    prim_ids = [a.primitive_id for a in plan.applications]
    assert "pkg_manager_call" in prim_ids
    assert "shell_compat" in prim_ids
    assert plan.source == "deterministic"


def test_deterministic_provider_diagnose_apt_not_found():
    from black_flag.ai.deterministic import DeterministicProvider
    from black_flag.core.types import StageResult
    from black_flag.primitives.catalog import list_primitive_ids

    provider = DeterministicProvider()
    failed = StageResult(
        distro="fedora",
        stage="prepare",
        exit_code=127,
        stdout="",
        stderr="bash: apt-get: command not found\n",
        elapsed_s=0.1,
    )
    action = provider.diagnose_failure(failed, [], list_primitive_ids())
    assert action.action == "apply"
    assert action.primitive_id == "pkg_manager_call"


def test_deterministic_provider_diagnose_debian_frontend():
    from black_flag.ai.deterministic import DeterministicProvider
    from black_flag.core.types import StageResult
    from black_flag.primitives.catalog import list_primitive_ids

    provider = DeterministicProvider()
    failed = StageResult(
        distro="fedora",
        stage="test",
        exit_code=1,
        stdout="",
        stderr="KeyError: 'DEBIAN_FRONTEND'\n",
        elapsed_s=0.1,
    )
    action = provider.diagnose_failure(failed, [], list_primitive_ids())
    assert action.action == "apply"
    assert action.primitive_id == "env_var_portability"


def test_deterministic_provider_give_up_on_unknown_error():
    from black_flag.ai.deterministic import DeterministicProvider
    from black_flag.core.types import StageResult
    from black_flag.primitives.catalog import list_primitive_ids

    provider = DeterministicProvider()
    failed = StageResult(
        distro="fedora",
        stage="build",
        exit_code=1,
        stdout="",
        stderr="some completely unknown error xyz789\n",
        elapsed_s=0.1,
    )
    action = provider.diagnose_failure(failed, [], list_primitive_ids())
    assert action.action == "give_up"
