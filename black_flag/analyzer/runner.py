"""
analyzer/runner.py — Orchestrate all analyzers and aggregate results.

Walks the source directory, dispatches files to the appropriate analyzer
by extension, deduplicates issues with identical (category, file, line),
and computes the portability score.
"""
from __future__ import annotations

from pathlib import Path

from black_flag.core.types import PortabilityIssue

# File extensions → analyzer functions
_PYTHON_EXTS = {".py"}
_SHELL_EXTS = {".sh", ".bash"}
_C_EXTS = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"}

# Directories / files to always skip
_SKIP_DIRS = {
    ".git", "__pycache__", ".pytest_cache", "node_modules",
    ".tox", ".venv", "venv", "env", ".env", "site-packages",
    "bf_scripts",   # Black Flag's own generated scripts
}
_SKIP_FILES = {"setup.py"}  # setuptools boilerplate, not user code


def run_analysis(source_dir: Path) -> list[PortabilityIssue]:
    """
    Walk *source_dir* recursively and return all detected portability issues.

    Issues are deduplicated by (source_file, line, category).
    """
    from black_flag.analyzer.python_analyzer import analyze_python
    from black_flag.analyzer.shell_analyzer import analyze_shell
    from black_flag.analyzer.c_analyzer import analyze_c

    issues: list[PortabilityIssue] = []
    seen: set[tuple[str, int, str]] = set()

    for path in sorted(source_dir.rglob("*")):
        # Skip directories in the skip list
        if any(skip in path.parts for skip in _SKIP_DIRS):
            continue
        if not path.is_file():
            continue
        if path.name in _SKIP_FILES:
            continue

        ext = path.suffix.lower()

        if ext in _PYTHON_EXTS:
            new = analyze_python(path, source_dir)
        elif ext in _SHELL_EXTS:
            new = analyze_shell(path, source_dir)
        elif ext in _C_EXTS:
            new = analyze_c(path, source_dir)
        else:
            continue

        for issue in new:
            key = (issue.source_file, issue.line, issue.category)
            if key not in seen:
                seen.add(key)
                issues.append(issue)

    return issues


# ---------------------------------------------------------------------------
# Portability score formula
# ---------------------------------------------------------------------------

_SEVERITY_WEIGHT = {"error": 1.0, "warning": 0.4, "info": 0.1}
_TARGET_WEIGHT = {1: 0.5, 2: 0.75, 3: 1.0}
_MAX_PENALTY_FLOOR = 5.0


def compute_score(issues: list[PortabilityIssue]) -> float:
    """
    Compute a 0.0 – 1.0 portability score from the issue list.

    Formula:
        weighted_penalty = Σ severity_weight[s] × target_weight[len(affected)]
        score = 1.0 - weighted_penalty / max(weighted_penalty, MAX_PENALTY_FLOOR)

    AI output does NOT affect the score; only detected issues do.
    """
    if not issues:
        return 1.0

    penalty = sum(
        _SEVERITY_WEIGHT.get(i.severity, 0.1)
        * _TARGET_WEIGHT.get(min(len(i.affected_targets), 3), 1.0)
        for i in issues
    )
    score = 1.0 - penalty / max(penalty, _MAX_PENALTY_FLOOR)
    return round(max(0.0, min(1.0, score)), 4)
