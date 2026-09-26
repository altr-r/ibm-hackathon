"""
primitives/shell_compat.py — PRIM-004: shell_compat

Two strategies:

  explicit_bash: #!/bin/bash → #!/usr/bin/env bash
    Declares an explicit bash dependency rather than assuming /bin/bash location.
    This is the safe default — it keeps bash-isms working while fixing the path.

  posix_convert: [[ -z "$X" ]] → [ -z "$X" ]
    Converts simple bash [[ ]] test expressions to POSIX sh equivalents.
    Only handles: -z, -n, -f, -d, -e, -s tests on a single operand.
    Arithmetic tests and regex (=~) are NOT converted.
"""
from __future__ import annotations

import re

from .base import AdaptationPrimitive, PrimitiveNotApplicable

_RE_BIN_BASH_SHEBANG = re.compile(r"^#!/bin/bash\b")

# [[ simple_unary_test ]] — only safe to POSIX-fy
_RE_DBL_BRACKET_SIMPLE = re.compile(
    r'\[\[\s*(-z|-n|-f|-d|-e|-s)\s+"?\$\{?(\w+)\}?"?\s*\]\]|'
    r'\[\[\s*"?\$\{?(\w+)\}?"?\s*(-z|-n|-f|-d|-e|-s)\s*\]\]'
)


class ShellCompatPrimitive(AdaptationPrimitive):
    id = "shell_compat"
    description = "Normalize bash-specific shell constructs for portability"
    supported_file_types = [".sh", ".bash"]
    safe_to_auto_apply = True

    def matches(self, file_content: str, file_ext: str) -> bool:
        if file_ext not in self.supported_file_types:
            return False
        return bool(
            _RE_BIN_BASH_SHEBANG.match(file_content)
            or _RE_DBL_BRACKET_SIMPLE.search(file_content)
        )

    def apply(self, file_content: str, params: dict, adapters: dict) -> str:
        strategy = params.get("strategy", "explicit_bash")

        if strategy == "explicit_bash":
            return self._apply_explicit_bash(file_content)
        elif strategy == "posix_convert":
            return self._apply_posix_convert(file_content)
        else:
            raise ValueError(
                f"shell_compat: unknown strategy {strategy!r}. "
                "Valid options: 'explicit_bash', 'posix_convert'"
            )

    def _apply_explicit_bash(self, content: str) -> str:
        if not _RE_BIN_BASH_SHEBANG.match(content):
            raise PrimitiveNotApplicable(
                "shell_compat[explicit_bash]: no '#!/bin/bash' shebang found"
            )
        return _RE_BIN_BASH_SHEBANG.sub("#!/usr/bin/env bash", content, count=1)

    def _apply_posix_convert(self, content: str) -> str:
        if not _RE_DBL_BRACKET_SIMPLE.search(content):
            raise PrimitiveNotApplicable(
                "shell_compat[posix_convert]: no simple [[ ]] construct found"
            )

        def _replace(m: re.Match) -> str:
            flag = m.group(1)
            var = m.group(2)
            return f'[ {flag} "${{{var}}}" ]'

        return _RE_DBL_BRACKET_SIMPLE.sub(_replace, content)
