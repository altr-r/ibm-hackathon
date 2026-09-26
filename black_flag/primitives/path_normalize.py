"""
primitives/path_normalize.py — PRIM-002: distro_path_check

Replaces a Debian-specific /etc/debian_version path check in Python source
with a portable helper that reads /etc/os-release instead.

Before:
    if os.path.exists("/etc/debian_version"):
        with open("/etc/debian_version") as f:
            print(f"Debian version: {f.read().strip()}")
    else:
        raise RuntimeError("This application requires a Debian-based system. ...")

After:
    def _bf_detect_distro() -> str:
        import os as _os
        try:
            with _os.open("/etc/os-release", _os.O_RDONLY) as _fd:
                ...
        except OSError:
            return "unknown"
    ...
    _bf_distro = _bf_detect_distro()
    if _bf_distro in ("ubuntu", "debian"):
        print(f"Distro: {_bf_distro}")
    # (the RuntimeError raise is removed; behaviour becomes portable)
"""
from __future__ import annotations

import re

from .base import AdaptationPrimitive, PrimitiveNotApplicable

# -------------------------------------------------------------------------
# Patterns we handle
# -------------------------------------------------------------------------

# Match the os.path.exists("/etc/debian_version") check
_RE_EXISTS = re.compile(
    r'os\.path\.exists\s*\(\s*["\'](?P<path>/etc/debian[_\-][^"\']+)["\']\s*\)'
)

# Match the open("/etc/debian_version") call (with or without mode arg)
_RE_OPEN = re.compile(
    r'open\s*\(\s*["\'](?P<path>/etc/debian[_\-][^"\']+)["\']\s*(?:,\s*["\'][^"\']*["\'])?\s*\)'
)

# The helper we inject once at the top of the file (after the module docstring / imports)
_BF_HELPER = '''\
def _bf_detect_distro() -> str:
    """Black Flag portability helper: return the distro ID from /etc/os-release."""
    import os as _bf_os
    try:
        with open("/etc/os-release") as _bf_f:
            for _bf_line in _bf_f:
                if _bf_line.startswith("ID="):
                    return _bf_line.strip().split("=", 1)[1].strip('"').lower()
    except OSError:
        pass
    return "unknown"

'''

# Replacement for the entire if/else block that raises RuntimeError
# We match the raise RuntimeError line so we can replace it with a portable branch.
_RE_RAISE_DEBIAN = re.compile(
    r'raise\s+RuntimeError\s*\([^)]*[Dd]ebian[^)]*\)',
    re.DOTALL,
)


class DistroPpathCheckPrimitive(AdaptationPrimitive):
    id = "distro_path_check"
    description = "Replace /etc/debian_version checks with portable /etc/os-release detection"
    supported_file_types = [".py"]
    safe_to_auto_apply = True

    def matches(self, file_content: str, file_ext: str) -> bool:
        if file_ext not in self.supported_file_types:
            return False
        return bool(_RE_EXISTS.search(file_content) or _RE_OPEN.search(file_content))

    def apply(self, file_content: str, params: dict, adapters: dict) -> str:
        if not self.matches(file_content, ".py"):
            raise PrimitiveNotApplicable(
                "distro_path_check: no /etc/debian_version reference found"
            )

        result = file_content

        # 1. Replace os.path.exists("/etc/debian_version") with _bf_detect_distro() == target
        result = _RE_EXISTS.sub(
            lambda m: '_bf_detect_distro() in ("ubuntu", "debian")',
            result,
        )

        # 2. Replace open("/etc/debian_version") with open("/etc/os-release")
        result = _RE_OPEN.sub(
            lambda m: 'open("/etc/os-release")',
            result,
        )

        # 3. Replace raise RuntimeError("...Debian...") with a print fallback
        result = _RE_RAISE_DEBIAN.sub(
            'print(f"[black-flag] Running on distro: {_bf_detect_distro()}")',
            result,
        )

        # 4. Inject the helper function (once) — insert after the last top-level import block.
        # Check for the *definition* specifically; calls like _bf_detect_distro() are already
        # present in the text after steps 1-3, so checking for any occurrence would skip
        # injection every time.
        if "def _bf_detect_distro" not in result:
            insert_after = _find_import_end(result)
            result = result[:insert_after] + _BF_HELPER + result[insert_after:]

        return result


def _find_import_end(source: str) -> int:
    """Return the character offset just after the last top-level import statement."""
    lines = source.splitlines(keepends=True)
    last_import_end = 0
    offset = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) or stripped == "":
            if stripped.startswith(("import ", "from ")):
                last_import_end = offset + len(line)
        elif last_import_end > 0:
            # First non-import, non-blank line after imports
            break
        offset += len(line)
    return last_import_end if last_import_end > 0 else 0
