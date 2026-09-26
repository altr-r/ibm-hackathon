"""
primitives/env_portability.py — PRIM-003: env_var_portability

Replaces direct os.environ["DEBIAN_FRONTEND"] subscript access (which raises
KeyError on non-Debian systems) with os.environ.get("DEBIAN_FRONTEND", default).

Before:
    frontend = os.environ["DEBIAN_FRONTEND"]

After:
    frontend = os.environ.get("DEBIAN_FRONTEND", "noninteractive")

Handles all variables in the configured set of Debian-specific env vars.
Does NOT modify downstream logic that branches on the retrieved value —
such uses are flagged as WARNING for manual review by the analyzer.
"""
from __future__ import annotations

import re

from .base import AdaptationPrimitive, PrimitiveNotApplicable

# Debian-specific environment variables and their sensible defaults
_DEBIAN_DEFAULTS: dict[str, str] = {
    "DEBIAN_FRONTEND": "noninteractive",
    "DEBIAN_PRIORITY": "critical",
    "DPKG_OPTIONS": "",
}

# Match  os.environ["VAR"]  but NOT  os.environ.get(
_RE_ENVIRON_SUBSCRIPT = re.compile(
    r'os\.environ\s*\[\s*["\'](?P<var>DEBIAN_FRONTEND|DEBIAN_PRIORITY|DPKG_OPTIONS)["\']\s*\]'
)


class EnvVarPortabilityPrimitive(AdaptationPrimitive):
    id = "env_var_portability"
    description = "Replace os.environ['DEBIAN_*'] subscripts with safe .get() calls"
    supported_file_types = [".py"]
    safe_to_auto_apply = True

    def matches(self, file_content: str, file_ext: str) -> bool:
        if file_ext not in self.supported_file_types:
            return False
        return bool(_RE_ENVIRON_SUBSCRIPT.search(file_content))

    def apply(self, file_content: str, params: dict, adapters: dict) -> str:
        if not self.matches(file_content, ".py"):
            raise PrimitiveNotApplicable(
                "env_var_portability: no os.environ['DEBIAN_*'] subscript found"
            )

        def _replace(m: re.Match) -> str:
            var = m.group("var")
            default = params.get("default") or _DEBIAN_DEFAULTS.get(var, "")
            default_repr = f'"{default}"' if default else '""'
            return f'os.environ.get("{var}", {default_repr})'

        result = _RE_ENVIRON_SUBSCRIPT.sub(_replace, file_content)

        if result == file_content:
            raise PrimitiveNotApplicable(
                "env_var_portability: substitution produced no change"
            )
        return result
