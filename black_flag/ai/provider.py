"""
ai/provider.py — AIProvider abstract base class.

All AI integration is accessed through this interface.
Concrete implementations:
  - DeterministicProvider: rule-based; no API calls; always available
  - WatsonxProvider: IBM watsonx.ai via OpenAI-compatible /v1/chat/completions

Mode A (plan_adaptations): given issues + catalog → AdaptationPlan
Mode B (diagnose_failure): given build failure + applied diffs → RepairAction
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from black_flag.core.types import (
    AdaptationPlan,
    AppliedDiff,
    PortabilityIssue,
    RepairAction,
    StageResult,
)


class AIProvider(ABC):
    """Abstract interface for all AI/LLM providers."""

    #: Human-readable name for display (e.g. "watsonx", "deterministic")
    name: str = ""

    @abstractmethod
    def plan_adaptations(
        self,
        issues: list[PortabilityIssue],
        catalog: list[str],
        targets: list[str] | None = None,
    ) -> AdaptationPlan:
        """
        Mode A — Given a list of portability issues and available primitive IDs,
        return an ordered AdaptationPlan.

        Must never raise; returns a minimal deterministic plan on any failure.
        """

    @abstractmethod
    def diagnose_failure(
        self,
        failed_result: StageResult,
        applied_diffs: list[AppliedDiff],
        catalog: list[str],
    ) -> RepairAction:
        """
        Mode B — Given a failed build/test stage result and the diffs applied
        so far, return a RepairAction (apply a primitive or give up).

        Must never raise; returns give_up on any failure.
        """

    @abstractmethod
    def summarize(
        self,
        issues: list[PortabilityIssue],
        applied_diffs: list[AppliedDiff],
        passed_targets: list[str],
        failed_targets: list[str],
    ) -> str:
        """
        Generate a human-readable summary for the manifest ai_summary field.

        Must never raise; returns an empty string on failure.
        """
