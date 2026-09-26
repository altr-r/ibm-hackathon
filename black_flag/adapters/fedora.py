"""
adapters/fedora.py — DistroAdapter for Fedora (and RHEL-compatible distros).

Uses dnf for package management.
Targets fedora:41 (currently supported as of 2025).
"""
from __future__ import annotations

import os

from .base import DistroAdapter
from .normalization import NORMALIZATION_TABLE, resolve_package


class DnfAdapter(DistroAdapter):
    distro_id = "fedora"
    display_name = "Fedora 41"
    docker_image = "fedora:41"

    def detect(self) -> bool:
        """Return True when /etc/os-release reports ID=fedora, ID=rhel, or ID=centos."""
        os_release = "/etc/os-release"
        if not os.path.exists(os_release):
            return False
        with open(os_release) as f:
            for line in f:
                if line.startswith("ID="):
                    distro = line.strip().split("=", 1)[1].strip('"').lower()
                    return distro in ("fedora", "rhel", "centos", "rocky", "almalinux")
        return False

    def package_manager(self) -> str:
        return "dnf"

    def install_dependency(self, pkg: str) -> str:
        return f"dnf install -y {pkg}"

    def query_package(self, generic_name: str) -> bool:
        return generic_name in NORMALIZATION_TABLE

    def normalize_library_name(self, generic_name: str) -> str:
        result = resolve_package(generic_name, self.distro_id)
        if result is None:
            raise KeyError(
                f"Generic package {generic_name!r} is not in the normalization table"
            )
        return result

    def environment_configuration(self) -> dict[str, str]:
        return {}

    def verify_dependency(self, pkg: str) -> str:
        return f"rpm -q {pkg}"

    def uninstall_dependency(self, pkg: str) -> str:
        return f"dnf remove -y {pkg}"
