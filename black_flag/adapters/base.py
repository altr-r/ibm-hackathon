"""
adapters/base.py — DistroAdapter abstract base class.

All distro-specific logic is routed through this interface so the rest of
the engine never hard-codes distro names in logic (only in data).
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class DistroAdapter(ABC):
    """Abstract interface for a single target Linux distribution."""

    # Concrete subclasses set this to match the value of $ID in /etc/os-release.
    distro_id: str = ""

    # Human-readable label for display.
    display_name: str = ""

    # Docker image used for build/test matrix runs.
    docker_image: str = ""

    @abstractmethod
    def detect(self) -> bool:
        """Return True if this adapter matches the currently running distro."""

    @abstractmethod
    def package_manager(self) -> str:
        """Return the package manager command name: "apt-get", "dnf", or "pacman"."""

    @abstractmethod
    def install_dependency(self, pkg: str) -> str:
        """
        Return the shell command to install *pkg* non-interactively.

        *pkg* should already be the distro-specific package name (i.e. the
        caller has already run it through the normalization table).
        """

    @abstractmethod
    def query_package(self, generic_name: str) -> bool:
        """Return True if *generic_name* is known in the normalization table."""

    @abstractmethod
    def normalize_library_name(self, generic_name: str) -> str:
        """
        Map *generic_name* to the distro-specific package name.

        Raises KeyError if *generic_name* is not in the normalization table.
        """

    @abstractmethod
    def environment_configuration(self) -> dict[str, str]:
        """Return a dict of environment variables to inject into stage scripts."""

    @abstractmethod
    def verify_dependency(self, pkg: str) -> str:
        """Return a shell command that exits 0 if *pkg* is installed."""

    @abstractmethod
    def uninstall_dependency(self, pkg: str) -> str:
        """Return the shell command to remove *pkg*."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(distro_id={self.distro_id!r})"
