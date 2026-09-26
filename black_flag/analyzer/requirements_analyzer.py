"""
analyzer/requirements_analyzer.py — Portability analyzer for requirements.txt files.

Detects Debian-specific package names that are only installable via apt-get,
not via pip.  These are typically lines like 'python3-cryptography' that
reference the distro package name instead of the canonical PyPI name.
"""
from __future__ import annotations

from pathlib import Path

from black_flag.core.types import PortabilityIssue

# Mapping from Debian-specific pip names to generic/canonical names
_DEBIAN_PIP_NAMES: dict[str, str] = {
    "python3-cryptography": "cryptography",
    "python-cryptography": "cryptography",   # also sometimes used as Debian name
}


def analyze_requirements(file_path: Path, source_root: Path) -> list[PortabilityIssue]:
    """Analyze a requirements.txt file and return portability issues."""
    rel = str(file_path.relative_to(source_root))
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    issues: list[PortabilityIssue] = []
    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Extract the package name (before any version specifier)
        pkg_name = stripped.split("==")[0].split(">=")[0].split("<=")[0].split("!=")[0].strip()
        if pkg_name in _DEBIAN_PIP_NAMES:
            canonical = _DEBIAN_PIP_NAMES[pkg_name]
            issues.append(
                PortabilityIssue(
                    category="library-name",
                    severity="warning",
                    source_file=rel,
                    line=lineno,
                    affected_targets=["fedora", "arch"],
                    explanation=(
                        f"'{pkg_name}' is a Debian system package name, not a PyPI package. "
                        f"Use '{canonical}' for cross-distro pip compatibility."
                    ),
                    suggested_primitive="requirements_normalize",
                    verification_method="docker-test",
                )
            )

    return issues
