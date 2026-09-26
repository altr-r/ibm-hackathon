"""
analyzer/shell_analyzer.py — Regex-based portability analyzer for shell scripts.

Detects:
  - Direct apt-get / yum / dnf / pacman calls (package manager portability)
  - Bash-specific shebang  #!/bin/bash  (path assumption)
  - Bash-ism  [[ ... ]]  test syntax
  - Hard-coded Debian-specific package names in install commands
  - Service names that differ across distros (apache2, mysql, etc.)
"""
from __future__ import annotations

import re
from pathlib import Path

from black_flag.core.types import PortabilityIssue

# -------------------------------------------------------------------------
# Compiled regexes
# -------------------------------------------------------------------------

# Package manager calls — not inside comments
_RE_APT_CALL = re.compile(
    r"^(?!#)\s*(sudo\s+)?apt(?:-get)?\s+(install|remove|purge|update|upgrade)",
    re.MULTILINE,
)
_RE_YUM_CALL = re.compile(
    r"^(?!#)\s*(sudo\s+)?yum\s+(install|remove|erase|update)",
    re.MULTILINE,
)
_RE_DNF_CALL = re.compile(
    r"^(?!#)\s*(sudo\s+)?dnf\s+(install|remove|erase|update)",
    re.MULTILINE,
)
_RE_PACMAN_CALL = re.compile(
    r"^(?!#)\s*(sudo\s+)?pacman\s+-[A-Za-z]*[Ssy]",
    re.MULTILINE,
)

# Bash-specific shebang
_RE_BASH_SHEBANG = re.compile(r"^#!/bin/bash\b")

# Bash [[  ]] test construct — matches [[ anywhere on a line (e.g. after 'if ')
_RE_BASH_DBL_BRACKET = re.compile(r"\[\[", re.MULTILINE)

# Debian-specific package names commonly misused in install commands
_DEBIAN_PKG_NAMES = {
    "libssl-dev": "ssl-dev",
    "python3-cryptography": "python-cryptography",
    "libbz2-dev": "bzip2-dev",
    "libsqlite3-dev": "sqlite3-dev",
    "libreadline-dev": "readline-dev",
    "libffi-dev": "libffi-dev",
    "zlib1g-dev": "zlib-dev",
    "libncurses-dev": "ncurses-dev",
}

_RE_DEBIAN_PKG = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in _DEBIAN_PKG_NAMES) + r")\b"
)

# Debian service names
_DEBIAN_SERVICES = {"apache2", "networking", "mysql"}
_RE_SYSTEMCTL = re.compile(
    r"\bsystemctl\s+(?:start|stop|enable|disable|restart|status)\s+("
    + "|".join(_DEBIAN_SERVICES)
    + r")\b"
)
_RE_SERVICE_CMD = re.compile(
    r"\bservice\s+("
    + "|".join(_DEBIAN_SERVICES)
    + r")\s+(?:start|stop|restart|status)\b"
)


def _line_number(text: str, match_start: int) -> int:
    """Return 1-based line number for the character offset *match_start*."""
    return text[:match_start].count("\n") + 1


def analyze_shell(file_path: Path, source_root: Path) -> list[PortabilityIssue]:
    """Analyze a shell script and return all detected portability issues."""
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
                verification_method="docker-test",
            )
        )

    # ------------------------------------------------------------------
    # Bash shebang  #!/bin/bash
    # ------------------------------------------------------------------
    if _RE_BASH_SHEBANG.match(content):
        _issue(
            "shell-ism",
            "warning",
            1,
            ["fedora", "arch"],
            "Shebang '#!/bin/bash' assumes bash is at /bin/bash. "
            "Use '#!/usr/bin/env bash' for portability.",
            "shell_compat",
        )

    # ------------------------------------------------------------------
    # [[ ]] bash-ism
    # ------------------------------------------------------------------
    dbl_bracket_reported = False
    for m in _RE_BASH_DBL_BRACKET.finditer(content):
        if not dbl_bracket_reported:
            _issue(
                "shell-ism",
                "warning",
                _line_number(content, m.start()),
                ["fedora", "arch"],
                "'[[ ... ]]' is bash-specific syntax. Use '[ ... ]' for POSIX portability.",
                "shell_compat",
            )
            dbl_bracket_reported = True
        break

    # ------------------------------------------------------------------
    # apt-get / apt install
    # ------------------------------------------------------------------
    for m in _RE_APT_CALL.finditer(content):
        _issue(
            "package-manager",
            "error",
            _line_number(content, m.start()),
            ["fedora", "arch"],
            f"'apt' is Debian/Ubuntu-specific. Fedora uses 'dnf', Arch uses 'pacman'.",
            "pkg_manager_call",
        )

    # ------------------------------------------------------------------
    # yum install  (legacy RHEL — also non-portable)
    # ------------------------------------------------------------------
    for m in _RE_YUM_CALL.finditer(content):
        _issue(
            "package-manager",
            "error",
            _line_number(content, m.start()),
            ["ubuntu", "arch"],
            "'yum' is RHEL/CentOS-specific. Ubuntu uses 'apt-get', Arch uses 'pacman'.",
            "pkg_manager_call",
        )

    # ------------------------------------------------------------------
    # dnf install  (Fedora-specific, though less fragile than apt)
    # ------------------------------------------------------------------
    for m in _RE_DNF_CALL.finditer(content):
        _issue(
            "package-manager",
            "error",
            _line_number(content, m.start()),
            ["ubuntu", "arch"],
            "'dnf' is Fedora/RHEL-specific. Ubuntu uses 'apt-get', Arch uses 'pacman'.",
            "pkg_manager_call",
        )

    # ------------------------------------------------------------------
    # pacman (Arch-specific)
    # ------------------------------------------------------------------
    for m in _RE_PACMAN_CALL.finditer(content):
        _issue(
            "package-manager",
            "error",
            _line_number(content, m.start()),
            ["ubuntu", "fedora"],
            "'pacman' is Arch-specific. Ubuntu uses 'apt-get', Fedora uses 'dnf'.",
            "pkg_manager_call",
        )

    # ------------------------------------------------------------------
    # Debian-specific package names in install commands
    # ------------------------------------------------------------------
    for m in _RE_DEBIAN_PKG.finditer(content):
        pkg = m.group(1)
        generic = _DEBIAN_PKG_NAMES[pkg]
        _issue(
            "library-name",
            "warning",
            _line_number(content, m.start()),
            ["fedora", "arch"],
            f"Package name '{pkg}' is Debian-specific (generic: '{generic}'). "
            "Fedora and Arch use different names.",
            "package_name_remap",
        )

    # ------------------------------------------------------------------
    # Service names
    # ------------------------------------------------------------------
    for m in _RE_SYSTEMCTL.finditer(content):
        svc = m.group(1)
        _issue(
            "service-name",
            "warning",
            _line_number(content, m.start()),
            ["fedora", "arch"],
            f"Service name '{svc}' is Debian-specific (e.g. Fedora uses 'httpd' for apache2).",
            "service_name_remap",
        )
    for m in _RE_SERVICE_CMD.finditer(content):
        svc = m.group(1)
        _issue(
            "service-name",
            "warning",
            _line_number(content, m.start()),
            ["fedora", "arch"],
            f"Service name '{svc}' is Debian-specific.",
            "service_name_remap",
        )

    return issues
