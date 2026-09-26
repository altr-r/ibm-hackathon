"""
primitives/service_names.py — PRIM-007: service_name_remap

Replaces hard-coded Debian service names in systemctl / service commands
with a distro-dispatch block.

Example (apache2 → httpd on Fedora/RHEL):
  Before: systemctl start apache2
  After:
    _BF_SVC_APACHE2=$(. /etc/os-release && case "$ID" in
        ubuntu|debian) echo apache2 ;;
        fedora|rhel)   echo httpd ;;
        arch)          echo httpd ;;
        *) echo apache2 ;;
    esac)
    systemctl start "$_BF_SVC_APACHE2"
"""
from __future__ import annotations

import re

from black_flag.adapters.normalization import resolve_service, SERVICE_NAME_TABLE
from .base import AdaptationPrimitive, PrimitiveNotApplicable

# All known Debian service names
_DEBIAN_SERVICES = set(SERVICE_NAME_TABLE.keys())

_RE_SYSTEMCTL = re.compile(
    r"^([ \t]*)(systemctl\s+(?:start|stop|enable|disable|restart|status)\s+)("
    + "|".join(re.escape(s) for s in _DEBIAN_SERVICES)
    + r")\b(.*)",
    re.MULTILINE,
)
_RE_SERVICE_CMD = re.compile(
    r"^([ \t]*)(service\s+)("
    + "|".join(re.escape(s) for s in _DEBIAN_SERVICES)
    + r")(\s+(?:start|stop|restart|status).*)",
    re.MULTILINE,
)

_SVC_DISPATCH_TEMPLATE = """\
{indent}_BF_SVC_{SVC_UPPER}=$(. /etc/os-release && case "$ID" in
{indent}  ubuntu|debian) echo {ubuntu_svc} ;;
{indent}  fedora|rhel)   echo {fedora_svc} ;;
{indent}  arch)          echo {arch_svc} ;;
{indent}  *) echo {ubuntu_svc} ;;
{indent}esac)
{indent}{cmd_prefix}"$_BF_SVC_{SVC_UPPER}"{suffix}"""


class ServiceNameRemapPrimitive(AdaptationPrimitive):
    id = "service_name_remap"
    description = "Replace Debian-specific service names with portable distro-dispatch references"
    supported_file_types = [".sh", ".bash"]
    safe_to_auto_apply = True

    def matches(self, file_content: str, file_ext: str) -> bool:
        if file_ext not in self.supported_file_types:
            return False
        return bool(
            _RE_SYSTEMCTL.search(file_content)
            or _RE_SERVICE_CMD.search(file_content)
        )

    def apply(self, file_content: str, params: dict, adapters: dict) -> str:
        debian_name = params.get("debian_name", "")

        # If no specific name, apply to all found services
        result = self._replace_pattern(content=file_content, pattern=_RE_SYSTEMCTL, debian_name=debian_name)
        result = self._replace_pattern(content=result, pattern=_RE_SERVICE_CMD, debian_name=debian_name)

        if result == file_content:
            raise PrimitiveNotApplicable(
                f"service_name_remap: no Debian service name found"
                + (f" matching '{debian_name}'" if debian_name else "")
            )
        return result

    def _replace_pattern(self, content: str, pattern: re.Pattern, debian_name: str) -> str:
        def _replace(m: re.Match) -> str:
            indent = m.group(1)
            cmd_prefix = m.group(2)
            svc = m.group(3)
            suffix = m.group(4) if m.lastindex >= 4 else ""

            if debian_name and svc != debian_name:
                return m.group(0)  # Not the service we want

            ubuntu_svc = resolve_service(svc, "ubuntu") or svc
            fedora_svc = resolve_service(svc, "fedora") or svc
            arch_svc = resolve_service(svc, "arch") or svc

            return _SVC_DISPATCH_TEMPLATE.format(
                indent=indent,
                SVC_UPPER=svc.upper().replace("-", "_"),
                ubuntu_svc=ubuntu_svc,
                fedora_svc=fedora_svc,
                arch_svc=arch_svc,
                cmd_prefix=cmd_prefix,
                suffix=suffix,
            )

        return pattern.sub(_replace, content)
