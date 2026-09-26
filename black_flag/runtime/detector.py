"""
runtime/detector.py — Detect the running Linux distribution.

Reads /etc/os-release and returns the matching DistroAdapter, or None if
the running distro is not one of the three supported targets.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from black_flag.adapters.base import DistroAdapter


_OS_RELEASE_PATH = "/etc/os-release"


def _read_os_release() -> dict[str, str]:
    """Parse /etc/os-release into a plain dict. Returns {} if the file is absent."""
    result: dict[str, str] = {}
    if not os.path.exists(_OS_RELEASE_PATH):
        return result
    with open(_OS_RELEASE_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip().strip('"')
    return result


def detect_distro_id() -> str:
    """
    Return the raw distro ID string from /etc/os-release (e.g. "ubuntu", "fedora", "arch").
    Returns "unknown" if the file is absent or the ID field is missing.
    """
    fields = _read_os_release()
    return fields.get("ID", "unknown").lower()


def detect_adapter() -> "DistroAdapter | None":
    """
    Return the DistroAdapter for the currently running distro, or None if
    the distro is not one of the three supported targets.

    Imports are deferred to avoid circular dependencies at module load time.
    """
    # Deferred imports — adapters import from normalization, not from here
    from black_flag.adapters.ubuntu import AptAdapter
    from black_flag.adapters.fedora import DnfAdapter
    from black_flag.adapters.arch import PacmanAdapter

    candidates: list[DistroAdapter] = [AptAdapter(), DnfAdapter(), PacmanAdapter()]
    for adapter in candidates:
        if adapter.detect():
            return adapter
    return None


def get_all_adapters() -> list["DistroAdapter"]:
    """Return one instance of every supported DistroAdapter."""
    from black_flag.adapters.ubuntu import AptAdapter
    from black_flag.adapters.fedora import DnfAdapter
    from black_flag.adapters.arch import PacmanAdapter

    return [AptAdapter(), DnfAdapter(), PacmanAdapter()]


def adapter_for_distro(distro_id: str) -> "DistroAdapter | None":
    """Return the adapter whose distro_id matches *distro_id*, or None."""
    for adapter in get_all_adapters():
        if adapter.distro_id == distro_id:
            return adapter
    return None
