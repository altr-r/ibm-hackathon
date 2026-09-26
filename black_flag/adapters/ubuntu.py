"""
adapters/ubuntu.py — DistroAdapter for Ubuntu (and Debian-compatible distros).

Uses apt-get for package management.
"""
from __future__ import annotations

import os

from .base import DistroAdapter
from .normalization import NORMALIZATION_TABLE, resolve_package


class AptAdapter(DistroAdapter):
    distro_id = "ubuntu"
    display_name = "Ubuntu 22.04"
    docker_image = "ubuntu:22.04"

    def detect(self) -> bool:
        """Return True when /etc/os-release reports ID=ubuntu or ID=debian."""
        os_release = "/etc/os-release"
        if not os.path.exists(os_release):
            return False
        with open(os_release) as f:
            for line in f:
                if line.startswith("ID="):
                    distro = line.strip().split("=", 1)[1].strip('"').lower()
                    return distro in ("ubuntu", "debian")
        return False

    def package_manager(self) -> str:
        return "apt-get"

    def install_dependency(self, pkg: str) -> str:
        return f"apt-get install -y {pkg}"

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
        return {"DEBIAN_FRONTEND": "noninteractive"}

    def verify_dependency(self, pkg: str) -> str:
        return f"dpkg -l {pkg} | grep -q '^ii'"

    def uninstall_dependency(self, pkg: str) -> str:
        return f"apt-get remove -y {pkg}"
