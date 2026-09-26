"""
adapters/arch.py — DistroAdapter for Arch Linux.

Uses pacman for package management.
Note: Arch containers require a keyring update before installing packages:
  pacman -Sy archlinux-keyring --noconfirm
This is baked into the generated prepare.sh scripts by script_gen.py.
"""
from __future__ import annotations

import os

from .base import DistroAdapter
from .normalization import NORMALIZATION_TABLE, resolve_package


class PacmanAdapter(DistroAdapter):
    distro_id = "arch"
    display_name = "Arch Linux"
    docker_image = "archlinux:latest"

    def detect(self) -> bool:
        """Return True when /etc/os-release reports ID=arch."""
        os_release = "/etc/os-release"
        if not os.path.exists(os_release):
            return False
        with open(os_release) as f:
            for line in f:
                if line.startswith("ID="):
                    distro = line.strip().split("=", 1)[1].strip('"').lower()
                    return distro == "arch"
        return False

    def package_manager(self) -> str:
        return "pacman"

    def install_dependency(self, pkg: str) -> str:
        return f"pacman -S --noconfirm {pkg}"

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
        return f"pacman -Q {pkg}"

    def uninstall_dependency(self, pkg: str) -> str:
        return f"pacman -R --noconfirm {pkg}"
