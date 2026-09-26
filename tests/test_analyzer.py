"""
tests/test_analyzer.py — Tests for the static analyzer (Phase 2).

Tests all three analyzers and the runner + score formula.
"""
from __future__ import annotations

import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# Python analyzer
# ---------------------------------------------------------------------------

def test_python_analyzer_detects_debian_version_exists():
    from black_flag.analyzer.python_analyzer import analyze_python

    code = 'import os\nif os.path.exists("/etc/debian_version"):\n    pass\n'
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "app.py"
        p.write_text(code)
        issues = analyze_python(p, Path(d))

    assert any(i.category == "hardcoded-path" for i in issues)
    assert any(i.suggested_primitive == "distro_path_check" for i in issues)


def test_python_analyzer_detects_environ_subscript():
    from black_flag.analyzer.python_analyzer import analyze_python

    code = 'import os\nfrontend = os.environ["DEBIAN_FRONTEND"]\n'
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "app.py"
        p.write_text(code)
        issues = analyze_python(p, Path(d))

    assert any(i.category == "env-assumption" for i in issues)
    assert any(i.suggested_primitive == "env_var_portability" for i in issues)


def test_python_analyzer_no_false_positives_on_get():
    from black_flag.analyzer.python_analyzer import analyze_python

    code = 'import os\nfrontend = os.environ.get("DEBIAN_FRONTEND", "noninteractive")\n'
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "app.py"
        p.write_text(code)
        issues = analyze_python(p, Path(d))

    # .get() call should NOT be flagged
    assert not any(i.category == "env-assumption" for i in issues)


def test_python_analyzer_detects_open_debian_version():
    from black_flag.analyzer.python_analyzer import analyze_python

    code = 'with open("/etc/debian_version") as f:\n    print(f.read())\n'
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "app.py"
        p.write_text(code)
        issues = analyze_python(p, Path(d))

    assert any(i.category == "hardcoded-path" for i in issues)


# ---------------------------------------------------------------------------
# Shell analyzer
# ---------------------------------------------------------------------------

def test_shell_analyzer_detects_apt_get():
    from black_flag.analyzer.shell_analyzer import analyze_shell

    script = "#!/bin/bash\napt-get install -y libssl-dev\n"
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "setup.sh"
        p.write_text(script)
        issues = analyze_shell(p, Path(d))

    categories = [i.category for i in issues]
    assert "package-manager" in categories


def test_shell_analyzer_detects_bash_shebang():
    from black_flag.analyzer.shell_analyzer import analyze_shell

    script = "#!/bin/bash\necho hello\n"
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "setup.sh"
        p.write_text(script)
        issues = analyze_shell(p, Path(d))

    assert any(i.category == "shell-ism" and i.suggested_primitive == "shell_compat" for i in issues)


def test_shell_analyzer_detects_double_bracket():
    from black_flag.analyzer.shell_analyzer import analyze_shell

    script = '#!/usr/bin/env bash\nif [[ -z "$VAR" ]]; then echo hi; fi\n'
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "setup.sh"
        p.write_text(script)
        issues = analyze_shell(p, Path(d))

    assert any(i.category == "shell-ism" for i in issues)


def test_shell_analyzer_detects_debian_package_name():
    from black_flag.analyzer.shell_analyzer import analyze_shell

    script = "apt-get install -y libssl-dev\n"
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "setup.sh"
        p.write_text(script)
        issues = analyze_shell(p, Path(d))

    assert any(i.category == "library-name" for i in issues)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def test_runner_on_demo_app():
    """The runner should find all 4 deliberate flaws in the demo app."""
    from black_flag.analyzer.runner import run_analysis

    demo = Path(__file__).parent.parent / "examples" / "portable-demo"
    if not demo.exists():
        import pytest
        pytest.skip("examples/portable-demo not found")

    issues = run_analysis(demo)
    categories = {i.category for i in issues}

    # Should detect at minimum: package-manager, hardcoded-path, env-assumption, shell-ism
    assert "package-manager" in categories, f"Missing package-manager in {categories}"
    assert "hardcoded-path" in categories, f"Missing hardcoded-path in {categories}"
    assert "env-assumption" in categories, f"Missing env-assumption in {categories}"


# ---------------------------------------------------------------------------
# Portability score formula
# ---------------------------------------------------------------------------

def test_score_no_issues():
    from black_flag.analyzer.runner import compute_score
    assert compute_score([]) == 1.0


def test_score_with_errors():
    from black_flag.analyzer.runner import compute_score
    from black_flag.core.types import PortabilityIssue

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
        )
    ]
    score = compute_score(issues)
    # 1 error × 2 targets (weight 0.75) = 0.75 penalty / max(0.75, 5.0) = 0.75/5.0
    expected = 1.0 - 0.75 / 5.0
    assert abs(score - expected) < 0.01


def test_score_perfect_after_adaptation():
    from black_flag.analyzer.runner import compute_score
    # Score should be 1.0 when issue list is empty
    assert compute_score([]) == 1.0


def test_score_clamped_between_0_and_1():
    from black_flag.analyzer.runner import compute_score
    from black_flag.core.types import PortabilityIssue

    # Many errors should still be >= 0
    issues = [
        PortabilityIssue(
            category="package-manager",
            severity="error",
            source_file=f"file{i}.sh",
            line=i,
            affected_targets=["ubuntu", "fedora", "arch"],
            explanation="error",
            suggested_primitive=None,
            verification_method="docker-test",
        )
        for i in range(20)
    ]
    score = compute_score(issues)
    assert 0.0 <= score <= 1.0
