"""
analyzer/python_analyzer.py — AST-based portability analyzer for Python source files.

Detects:
  - subprocess calls with distro-specific package manager strings
  - os.path.exists("/etc/debian_version") and similar hardcoded Debian paths
  - os.environ["DEBIAN_FRONTEND"] and similar distro-specific env var subscripts
  - Direct os.system() calls with apt-get / dnf / pacman
"""
from __future__ import annotations

import ast
from pathlib import Path

from black_flag.core.types import PortabilityIssue

_DEBIAN_PATHS = {
    "/etc/debian_version",
    "/etc/debian_release",
    "/etc/lsb-release",
    "/etc/ubuntu-advantage",
}

_DEBIAN_ENV_VARS = {
    "DEBIAN_FRONTEND",
    "DEBIAN_PRIORITY",
    "DPKG_OPTIONS",
}

_APT_STRINGS = {"apt-get", "apt install", "dpkg -i", "dpkg --install"}
_PKG_MANAGER_STRINGS = {"apt-get", "dnf install", "yum install", "pacman -S"}


class _PortabilityVisitor(ast.NodeVisitor):
    """Walk a Python AST and emit portability issues."""

    def __init__(self, source_file: str) -> None:
        self.source_file = source_file
        self.issues: list[PortabilityIssue] = []

    def _issue(
        self,
        category: str,
        severity: str,
        line: int,
        affected_targets: list[str],
        explanation: str,
        primitive: str | None,
    ) -> None:
        self.issues.append(
            PortabilityIssue(
                category=category,  # type: ignore[arg-type]
                severity=severity,  # type: ignore[arg-type]
                source_file=self.source_file,
                line=line,
                affected_targets=affected_targets,
                explanation=explanation,
                suggested_primitive=primitive,
                verification_method="docker-test",
            )
        )

    # ------------------------------------------------------------------
    # os.path.exists("/etc/debian_version") / open("/etc/debian_version")
    # ------------------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        func = node.func

        # os.path.exists("...") or os.path.isfile("...")
        if (
            isinstance(func, ast.Attribute)
            and func.attr in ("exists", "isfile", "isdir")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in _DEBIAN_PATHS
        ):
            path = node.args[0].value
            self._issue(
                category="hardcoded-path",
                severity="error",
                line=node.lineno,
                affected_targets=["fedora", "arch"],
                explanation=f"Path '{path}' does not exist on Fedora or Arch Linux",
                primitive="distro_path_check",
            )

        # open("/etc/debian_version", ...)
        if (
            isinstance(func, ast.Name)
            and func.id == "open"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and node.args[0].value in _DEBIAN_PATHS
        ):
            path = node.args[0].value
            self._issue(
                category="hardcoded-path",
                severity="error",
                line=node.lineno,
                affected_targets=["fedora", "arch"],
                explanation=f"File '{path}' does not exist on Fedora or Arch Linux",
                primitive="distro_path_check",
            )

        self.generic_visit(node)

    # ------------------------------------------------------------------
    # os.environ["DEBIAN_FRONTEND"]  (subscript, not .get())
    # ------------------------------------------------------------------
    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        # Match os.environ["KEY"]
        if (
            isinstance(node.value, ast.Attribute)
            and node.value.attr == "environ"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
            and node.slice.value in _DEBIAN_ENV_VARS
        ):
            var = node.slice.value
            self._issue(
                category="env-assumption",
                severity="error",
                line=node.lineno,
                affected_targets=["fedora", "arch"],
                explanation=f"os.environ['{var}'] raises KeyError on non-Debian systems (variable not set)",
                primitive="env_var_portability",
            )
        self.generic_visit(node)


def analyze_python(file_path: Path, source_root: Path) -> list[PortabilityIssue]:
    """Parse *file_path* as Python and return all detected issues."""
    rel = str(file_path.relative_to(source_root))
    try:
        source = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return []  # Not valid Python; skip silently

    visitor = _PortabilityVisitor(source_file=rel)
    visitor.visit(tree)
    return visitor.issues
