"""
ai/watsonx.py — WatsonxProvider: IBM watsonx.ai via OpenAI-compatible endpoint.

Uses /v1/chat/completions with structured JSON prompts. Falls back to
DeterministicProvider on any network error, timeout, or JSON parse failure.

Required environment variables:
  WATSONX_API_KEY   — IBM Cloud API key (or any bearer token)
  WATSONX_BASE_URL  — Base URL, e.g. https://us-south.ml.cloud.ibm.com/ml/v1
  WATSONX_MODEL_ID  — Model ID, e.g. ibm/granite-3-8b-instruct
  WATSONX_PROJECT_ID — IBM Cloud project ID

If WATSONX_API_KEY is not set, this provider will not be instantiated.
The factory returns DeterministicProvider instead.
"""
from __future__ import annotations

import json
import os
from typing import Any

from black_flag.core.types import (
    AdaptationPlan,
    AdaptationSource,
    AppliedDiff,
    PortabilityIssue,
    PrimitiveApplication,
    RepairAction,
    StageResult,
)
from .provider import AIProvider
from .prompts import build_mode_a_prompt, build_mode_b_prompt
from .deterministic import DeterministicProvider
from black_flag.primitives.catalog import list_primitive_ids

# ---------------------------------------------------------------------------
# Default Watsonx parameters
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "ibm/granite-3-8b-instruct"
_DEFAULT_BASE_URL = "https://us-south.ml.cloud.ibm.com/ml/v1"
_DEFAULT_MAX_TOKENS = 1024
_DEFAULT_TEMPERATURE = 0.0   # deterministic output for JSON
_API_TIMEOUT = 30            # seconds


class WatsonxProvider(AIProvider):
    name = "watsonx"

    def __init__(self) -> None:
        self._api_key = os.environ.get("WATSONX_API_KEY", "")
        self._base_url = os.environ.get("WATSONX_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
        self._model_id = os.environ.get("WATSONX_MODEL_ID", _DEFAULT_MODEL)
        self._project_id = os.environ.get("WATSONX_PROJECT_ID", "")
        self._fallback = DeterministicProvider()

    def _chat(self, messages: list[dict]) -> str | None:
        """
        POST to the /v1/chat/completions endpoint and return the response text.
        Returns None on any error.
        """
        import httpx  # deferred to avoid import cost when not used

        # For Watsonx we use the OpenAI-compatible endpoint under /ml/v1
        url = f"{self._base_url}/text/chat?version=2024-05-31"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {
            "model_id": self._model_id,
            "messages": messages,
            "parameters": {
                "max_new_tokens": _DEFAULT_MAX_TOKENS,
                "temperature": _DEFAULT_TEMPERATURE,
            },
        }
        if self._project_id:
            body["project_id"] = self._project_id

        try:
            response = httpx.post(
                url,
                headers=headers,
                json=body,
                timeout=_API_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()
            # Handle both OpenAI-style and Watsonx-style response
            choices = data.get("choices") or data.get("results") or []
            if choices:
                choice = choices[0]
                message = choice.get("message") or {}
                return message.get("content") or choice.get("generated_text", "")
        except Exception:
            return None

        return None

    def _parse_mode_a(self, raw: str) -> AdaptationPlan | None:
        """Parse a Mode A (plan) response. Returns None on parse failure."""
        try:
            # Strip any accidental markdown fences
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[1:])
                cleaned = cleaned.rstrip("`").strip()
            data = json.loads(cleaned)
            primitives = data.get("primitives", [])
            valid_ids = set(list_primitive_ids())
            apps = []
            for item in primitives:
                pid = item.get("primitive_id", "")
                if pid not in valid_ids:
                    continue
                apps.append(
                    PrimitiveApplication(
                        primitive_id=pid,
                        params=item.get("params", {}),
                        issue_ids=[int(i) for i in item.get("issue_ids", [])],
                        rationale=item.get("rationale", ""),
                    )
                )
            return AdaptationPlan(applications=apps, source="ai")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def _parse_mode_b(self, raw: str) -> RepairAction | None:
        """Parse a Mode B (diagnose) response. Returns None on parse failure."""
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[1:])
                cleaned = cleaned.rstrip("`").strip()
            data = json.loads(cleaned)
            if data.get("action") == "give_up":
                return RepairAction(
                    action="give_up",
                    primitive_id=None,
                    params={},
                    rationale=data.get("reason", "AI declared give_up"),
                )
            valid_ids = set(list_primitive_ids())
            pid = data.get("primitive_id", "")
            if pid not in valid_ids:
                return None
            return RepairAction(
                action="apply",
                primitive_id=pid,
                params=data.get("params", {}),
                rationale=data.get("rationale", ""),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def plan_adaptations(
        self,
        issues: list[PortabilityIssue],
        catalog: list[str],
        targets: list[str] | None = None,
    ) -> AdaptationPlan:
        messages = build_mode_a_prompt(issues, catalog, targets or ["ubuntu", "fedora", "arch"])
        raw = self._chat(messages)
        if raw:
            result = self._parse_mode_a(raw)
            if result:
                return result
        # Fall back to deterministic
        return self._fallback.plan_adaptations(issues, catalog, targets)

    def diagnose_failure(
        self,
        failed_result: StageResult,
        applied_diffs: list[AppliedDiff],
        catalog: list[str],
    ) -> RepairAction:
        messages = build_mode_b_prompt(failed_result, applied_diffs, catalog)
        raw = self._chat(messages)
        if raw:
            result = self._parse_mode_b(raw)
            if result:
                return result
        # Fall back to deterministic
        return self._fallback.diagnose_failure(failed_result, applied_diffs, catalog)

    def summarize(
        self,
        issues: list[PortabilityIssue],
        applied_diffs: list[AppliedDiff],
        passed_targets: list[str],
        failed_targets: list[str],
    ) -> str:
        n_issues = len(issues)
        n_diffs = len(applied_diffs)
        passed_str = ", ".join(passed_targets) or "none"
        failed_str = ", ".join(failed_targets) or "none"

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a technical writer. Summarize the portability adaptation "
                    "in 1-2 sentences for a developer audience. Be specific about what "
                    "was changed and which targets now work."
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "issues_detected": n_issues,
                    "adaptations_applied": n_diffs,
                    "compatible_targets": passed_targets,
                    "failed_targets": failed_targets,
                    "applied_diffs": [
                        {"primitive_id": d.primitive_id, "file": d.target_file}
                        for d in applied_diffs
                    ],
                }),
            },
        ]
        raw = self._chat(messages)
        if raw and len(raw.strip()) > 10:
            return raw.strip()
        return self._fallback.summarize(issues, applied_diffs, passed_targets, failed_targets)
