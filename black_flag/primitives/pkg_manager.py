"""
primitives/pkg_manager.py — PRIM-001: pkg_manager_call

Replaces a bare package-manager install command (apt-get, dnf, pacman, yum)
with a distro-dispatch case block that works on all three supported targets.

Input line example:
  apt-get install -y libssl-dev python3-cryptography

Output block:
  _BF_DISTRO=$(. /etc/os-release && echo "$ID")
  case "$_BF_DISTRO" in
    ubuntu|debian) apt-get install -y libssl-dev python3-cryptography ;;
    fedora|rhel)   dnf install -y openssl-devel python3-cryptography ;;
    arch)          pacman -S --noconfirm openssl python-cryptography ;;
    *) echo "Unsupported distro: $_BF_DISTRO" && exit 1 ;;
  esac

The primitive:
  1. Parses the original install line to extract package names.
  2. Resolves each package name to distro-specific names via normalization table.
  3. Generates the dispatch block.
  4. Replaces only the matched line(s); does not alter surrounding script logic.
"""
from __future__ import annotations

import re

from black_flag.adapters.normalization import resolve_package, NORMALIZATION_TABLE
from .base import AdaptationPrimitive, PrimitiveNotApplicable

# Match any package manager install invocation
_PM_CALLS = [
    re.compile(r"^(?!#)[ \t]*(sudo[ \t]+)?apt(?:-get)?[ \t]+install[ \t]+-y[ \t]+(.+)$", re.MULTILINE),
    re.compile(r"^(?!#)[ \t]*(sudo[ \t]+)?apt(?:-get)?[ \t]+install[ \t]+(.+)$", re.MULTILINE),
    re.compile(r"^(?!#)[ \t]*(sudo[ \t]+)?dnf[ \t]+install[ \t]+-y[ \t]+(.+)$", re.MULTILINE),
    re.compile(r"^(?!#)[ \t]*(sudo[ \t]+)?dnf[ \t]+install[ \t]+(.+)$", re.MULTILINE),
    re.compile(r"^(?!#)[ \t]*(sudo[ \t]+)?yum[ \t]+install[ \t]+-y[ \t]+(.+)$", re.MULTILINE),
    re.compile(r"^(?!#)[ \t]*(sudo[ \t]+)?yum[ \t]+install[ \t]+(.+)$", re.MULTILINE),
    re.compile(r"^(?!#)[ \t]*(sudo[ \t]+)?pacman[ \t]+-[A-Za-z]*S[A-Za-z]*[ \t]+(.+)$", re.MULTILINE),
]

# Strip common flags from the package name list
_FLAG_RE = re.compile(r"-\w+")

_DISPATCH_TEMPLATE = """\
_BF_DISTRO=$(. /etc/os-release && echo "$ID")
case "$_BF_DISTRO" in
  ubuntu|debian) {ubuntu_cmd} ;;
  fedora|rhel)   {fedora_cmd} ;;
  arch)          {arch_cmd} ;;
  *) echo "[black-flag] Unsupported distro: $_BF_DISTRO" && exit 1 ;;
esac"""


def _extract_packages(pkg_str: str) -> list[str]:
    """Parse a space-separated package list, stripping flags like -y."""
    return [p for p in pkg_str.split() if not p.startswith("-")]


# Build reverse map: ubuntu_pkg_name -> generic_name
_UBUNTU_TO_GENERIC: dict[str, str] = {}
for _generic, _distro_map in NORMALIZATION_TABLE.items():
    _ubuntu_name = _distro_map.get("ubuntu") or _distro_map.get("debian")
    if _ubuntu_name:
        _UBUNTU_TO_GENERIC[_ubuntu_name] = _generic


def _resolve_packages(packages: list[str], distro: str) -> list[str]:
    """Resolve each package name to its distro-specific equivalent if known.

    Tries: direct generic lookup, then ubuntu->generic reverse lookup.
    """
    result = []
    for pkg in packages:
        # Try direct generic key first
        resolved = resolve_package(pkg, distro)
        if resolved:
            result.append(resolved)
            continue
        # Try reverse-mapping: pkg is a Debian/Ubuntu package name
        generic = _UBUNTU_TO_GENERIC.get(pkg)
        if generic:
            resolved = resolve_package(generic, distro)
            if resolved:
                result.append(resolved)
                continue
        result.append(pkg)
    return result


class PkgManagerCallPrimitive(AdaptationPrimitive):
    id = "pkg_manager_call"
    description = "Replace distro-specific package manager calls with a portable distro-dispatch block"
    supported_file_types = [".sh", ".bash"]
    safe_to_auto_apply = True

    def matches(self, file_content: str, file_ext: str) -> bool:
        if file_ext not in self.supported_file_types:
            return False
        return any(r.search(file_content) for r in _PM_CALLS)

    def apply(self, file_content: str, params: dict, adapters: dict) -> str:
        """
        Replace the first matching package manager install line with a dispatch block.
        Raises PrimitiveNotApplicable if no match is found.
        """
        for pattern in _PM_CALLS:
            m = pattern.search(file_content)
            if m:
                original_line = m.group(0)
                pkg_str = m.group(2) if m.lastindex >= 2 else ""
                raw_packages = _extract_packages(pkg_str)

                ubuntu_pkgs = _resolve_packages(raw_packages, "ubuntu")
                fedora_pkgs = _resolve_packages(raw_packages, "fedora")
                arch_pkgs = _resolve_packages(raw_packages, "arch")

                # Build distro-specific commands
                ubuntu_cmd = f"apt-get install -y {' '.join(ubuntu_pkgs)}"
                fedora_cmd = f"dnf install -y {' '.join(fedora_pkgs)}"
                arch_cmd = f"pacman -S --noconfirm {' '.join(arch_pkgs)}"

                dispatch = _DISPATCH_TEMPLATE.format(
                    ubuntu_cmd=ubuntu_cmd,
                    fedora_cmd=fedora_cmd,
                    arch_cmd=arch_cmd,
                )

                # Preserve any leading whitespace / indentation from the matched line
                indent = re.match(r"^([ \t]*)", original_line).group(1)
                if indent:
                    dispatch = "\n".join(indent + ln for ln in dispatch.splitlines())

                return file_content.replace(original_line, dispatch, 1)

        raise PrimitiveNotApplicable(
            "pkg_manager_call: no package manager install command found"
        )
