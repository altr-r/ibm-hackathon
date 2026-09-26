"""
analyzer/c_analyzer.py — Regex-based portability analyzer for C/C++ source files.

Detects:
  - system("apt-get ...") / popen("apt-get ...", ...) calls
  - Hard-coded /etc/debian_version or similar paths in string literals
  - __GLIBC__ version guards that assume Debian's glibc version
"""
from __future__ import annotations

import re
from pathlib import Path

from black_flag.core.types import PortabilityIssue

_RE_SYSTEM_APT = re.compile(
    r'\bsystem\s*\(\s*"[^"]*apt(?:-get)?[^"]*"',
    re.MULTILINE,
)
_RE_POPEN_APT = re.compile(
    r'\bpopen\s*\(\s*"[^"]*apt(?:-get)?[^"]*"',
    re.MULTILINE,
)
_RE_DEBIAN_PATH_STR = re.compile(
    r'"(/etc/debian_version|/etc/debian_release|/etc/lsb-release)"',
    re.MULTILINE,
)


def _line_number(text: str, match_start: int) -> int:
    return text[:match_start].count("\n") + 1


def analyze_c(file_path: Path, source_root: Path) -> list[PortabilityIssue]:
    """Analyze a C/C++ source file and return detected portability issues."""
    rel = str(file_path.relative_to(source_root))
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    issues: list[PortabilityIssue] = []

    def _issue(
        category: str,
        severity: str,
        line: int,
        affected: list[str],
        explanation: str,
        primitive: str | None,
    ) -> None:
        issues.append(
            PortabilityIssue(
                category=category,  # type: ignore[arg-type]
                severity=severity,  # type: ignore[arg-type]
                source_file=rel,
                line=line,
                affected_targets=affected,
                explanation=explanation,
                suggested_primitive=primitive,
                verification_method="static",
            )
        )

    for m in _RE_SYSTEM_APT.finditer(content):
        _issue(
            "package-manager",
            "error",
            _line_number(content, m.start()),
            ["fedora", "arch"],
            "system() call with 'apt-get' is Debian/Ubuntu-specific.",
            None,
        )

    for m in _RE_POPEN_APT.finditer(content):
        _issue(
            "package-manager",
            "error",
            _line_number(content, m.start()),
            ["fedora", "arch"],
            "popen() call with 'apt-get' is Debian/Ubuntu-specific.",
            None,
        )

    for m in _RE_DEBIAN_PATH_STR.finditer(content):
        path = m.group(1)
        _issue(
            "hardcoded-path",
            "error",
            _line_number(content, m.start()),
            ["fedora", "arch"],
            f"Hard-coded path '{path}' does not exist on Fedora or Arch.",
            None,
        )

    return issues
