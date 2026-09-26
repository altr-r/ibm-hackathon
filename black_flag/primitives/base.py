"""
primitives/base.py — AdaptationPrimitive abstract base class.

Every primitive:
  - Has a unique string ID used in plans and diffs.
  - Implements matches() to check applicability without modifying.
  - Implements apply() to return modified content; raises PrimitiveNotApplicable
    if the required pattern is not found (never silently partial-applies).
  - Operates only on the file content string — no filesystem access.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class PrimitiveNotApplicable(Exception):
    """Raised when a primitive's required pattern is absent in the file content."""


class AdaptationPrimitive(ABC):
    """Abstract base for all adaptation primitives."""

    #: Unique slug used in plans, diffs, and the CLI
    id: str = ""

    #: Human-readable description shown in CLI output
    description: str = ""

    #: File extensions this primitive can operate on (e.g. [".sh", ".py"])
    supported_file_types: list[str] = []

    #: If False the primitive requires --force to auto-apply
    safe_to_auto_apply: bool = True

    @abstractmethod
    def matches(self, file_content: str, file_ext: str) -> bool:
        """
        Return True if this primitive is applicable to the given file content.

        Must not raise; must not modify state.
        """

    @abstractmethod
    def apply(
        self,
        file_content: str,
        params: dict,
        adapters: dict,  # distro_id → DistroAdapter
    ) -> str:
        """
        Apply the primitive to *file_content* and return the modified content.

        Raises PrimitiveNotApplicable if the required pattern is not found.
        The params dict contains primitive-specific parameters validated by the
        planner before this is called.
        """
