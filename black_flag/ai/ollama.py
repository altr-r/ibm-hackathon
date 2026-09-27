"""
ai/ollama.py — OllamaProvider: local Ollama (IBM Granite) via /api/chat.

Ollama is a local model runtime, so this provider needs **no IBM credentials**.
It talks to the local Ollama HTTP API:

    POST {OLLAMA_BASE_URL}/api/chat   (stream=false, format=json)

Configuration (environment variables):
  AI_PROVIDER      — set to "ollama" to select this provider
  OLLAMA_BASE_URL  — default http://localhost:11434
  OLLAMA_MODEL     — default granite4.2:3b

The provider mirrors the WatsonxProvider contract exactly:
  * it reuses the existing AdaptationPlan / RepairAction models,
  * the model may only select IDs from the bounded primitive catalog
    (invalid IDs are rejected),
  * it never executes model output — it only parses structured JSON,
  * on ANY failure (connection, timeout, non-200, malformed JSON,
    schema-invalid, unsupported primitive) it records a safe, non-secret
    status and falls back to DeterministicProvider,
  * its methods therefore never raise.

Request/response status is exposed via the ``last_call`` dict so the CLI and
the web UI can print a clear success / failure line without leaking prompts,
raw responses, or secrets.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from black_flag.core.types import (
    AdaptationPlan,
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
# Default Ollama parameters
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "granite4.2:3b"
_DEFAULT_BASE_URL = "http://localhost:11434"
_NUM_PREDICT = 1024          # max generated tokens
_TEMPERATURE = 0.0           # low temperature for JSON fidelity
# Local CPU inference on a 3B model is slow: a real Mode A planning call was
# measured at ~140s. Keep a generous per-call budget so a real response can
# complete instead of prematurely falling back. Any call that exceeds this
# still falls back safely to the deterministic provider.
_API_TIMEOUT = 300           # seconds per HTTP call (local inference can be slow)
_TAGS_TIMEOUT = 5            # seconds for the availability probe
# granite4.2:3b is a reasoning ("thinking") model. Unless thinking is disabled
# it spends the whole num_predict budget on hidden reasoning and returns an
# empty final message. We only want the direct structured answer, so ask the
# Ollama API to disable thinking. The flag is ignored by non-thinking models.
_DISABLE_THINKING = True


def _sanitize_env_str(raw: str | None) -> str:
    """Strip a possible ``NAME=`` prefix and surrounding quotes."""
    if not raw:
        return ""
    val = raw.strip()
    m = re.match(r"^[A-Z][A-Z0-9_]+=(.+)$", val)
    if m:
        val = m.group(1).strip()
    return val.strip().strip('"').strip("'")


def _extract_json(raw: str) -> Any | None:
    """Robust JSON extraction: markdown fences, trailing prose, wrappers."""
    if not raw:
        return None
    cleaned = raw.strip()
    if "```" in cleaned:
        parts = re.split(r"```(?:json)?\s*", cleaned, maxsplit=2)
        if len(parts) >= 2:
            cleaned = parts[1].rstrip("`").strip()
    candidates: list[str] = []
    for opener, closer in [("{", "}"), ("[", "]")]:
        i = cleaned.find(opener)
        j = cleaned.rfind(closer)
        if 0 <= i < j:
            candidates.append(cleaned[i : j + 1])
    for cand in sorted(candidates, key=len, reverse=True):
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


class OllamaProvider(AIProvider):
    name = "ollama"

    #: Status record for the most recent Ollama call (used by CLI/web display).
    #:   {"mode": "plan"|"diagnose"|"summarize"|None,
    #:    "status": "success"|"failed"|"skipped",
    #:    "detail": str,          # short, non-secret
    #:    "fallback_used": bool}
    last_call: dict[str, Any] = {
        "mode": None,
        "status": "skipped",
        "detail": "",
        "fallback_used": False,
    }

    def __init__(self) -> None:
        base = _sanitize_env_str(os.environ.get("OLLAMA_BASE_URL", _DEFAULT_BASE_URL)) or _DEFAULT_BASE_URL
        self._base_url = base.rstrip("/")
        self._model_id = _sanitize_env_str(os.environ.get("OLLAMA_MODEL", _DEFAULT_MODEL)) or _DEFAULT_MODEL
        self._fallback = DeterministicProvider()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def base_url(self) -> str:
        return self._base_url

    def is_configured(self) -> bool:
        """Ollama needs no credentials; it is always 'configured'."""
        return True

    def is_available(self) -> bool:
        """Return True when the local Ollama server responds to /api/tags."""
        import httpx  # lazy

        try:
            resp = httpx.get(f"{self._base_url}/api/tags", timeout=_TAGS_TIMEOUT)
            return resp.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Ollama /api/chat
    # ------------------------------------------------------------------

    def _chat(self, messages: list[dict], json_format: bool = True) -> tuple[str | None, str]:
        """Call Ollama /api/chat. Returns (content_or_None, safe_status_detail)."""
        import httpx  # lazy

        url = f"{self._base_url}/api/chat"
        body: dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": _TEMPERATURE,
                "num_predict": _NUM_PREDICT,
            },
        }
        if _DISABLE_THINKING:
            # Reasoning models (e.g. granite4.2:3b) otherwise emit only a hidden
            # "thinking" trace and an empty content field within num_predict.
            body["think"] = False
        if json_format:
            body["format"] = "json"
        try:
            resp = httpx.post(url, json=body, timeout=_API_TIMEOUT)
        except httpx.TimeoutException:
            return None, "timeout"
        except Exception:
            return None, "connection-error"

        if resp.status_code != 200:
            # Report ONLY the bounded HTTP status code. Never echo the raw
            # server error body into the status detail: it may contain a prompt
            # echo, model output, or secrets. Safe information only.
            return None, f"http-{resp.status_code}"

        try:
            data = resp.json()
        except Exception:
            return None, "bad-json-payload"

        msg = data.get("message") or {}
        content = msg.get("content")
        if content:
            return content, "ok"
        return None, "empty-response"

    # ------------------------------------------------------------------
    # Response parsing (bounded to the existing primitive catalog)
    # ------------------------------------------------------------------

    def _parse_mode_a(self, raw: str) -> AdaptationPlan | None:
        data = _extract_json(raw)
        if not isinstance(data, dict):
            return None
        primitives = data.get("primitives")
        if not isinstance(primitives, list):
            return None
        valid_ids = set(list_primitive_ids())
        apps: list[PrimitiveApplication] = []
        for item in primitives:
            if not isinstance(item, dict):
                continue
            pid = item.get("primitive_id", "")
            if not isinstance(pid, str) or pid not in valid_ids:
                continue  # reject unsupported primitive IDs
            params = item.get("params", {})
            if not isinstance(params, dict):
                params = {}
            issue_ids_raw = item.get("issue_ids", [])
            if not isinstance(issue_ids_raw, list):
                issue_ids_raw = []
            issue_ids: list[int] = []
            for i in issue_ids_raw:
                try:
                    issue_ids.append(int(i))
                except (TypeError, ValueError):
                    continue
            rationale = item.get("rationale", "")
            if not isinstance(rationale, str):
                rationale = str(rationale) if rationale else ""
            apps.append(
                PrimitiveApplication(
                    primitive_id=pid,
                    params=params,
                    issue_ids=issue_ids,
                    rationale=rationale,
                )
            )
        if not apps:
            return None
        return AdaptationPlan(applications=apps, source="ai")

    def _parse_mode_b(self, raw: str) -> RepairAction | None:
        data = _extract_json(raw)
        if not isinstance(data, dict):
            return None
        action = data.get("action", "apply")
        if action == "give_up":
            reason = data.get("reason", "AI declared give_up")
            if not isinstance(reason, str):
                reason = str(reason) if reason else "AI declared give_up"
            return RepairAction(action="give_up", primitive_id=None, params={}, rationale=reason)
        valid_ids = set(list_primitive_ids())
        pid = data.get("primitive_id", "")
        if not isinstance(pid, str) or pid not in valid_ids:
            return None
        params = data.get("params", {})
        if not isinstance(params, dict):
            params = {}
        rationale = data.get("rationale", "")
        if not isinstance(rationale, str):
            rationale = str(rationale) if rationale else ""
        return RepairAction(action="apply", primitive_id=pid, params=params, rationale=rationale)

    def _set_last_call(self, mode: str, status: str, detail: str = "", fallback_used: bool = False) -> None:
        self.last_call = {
            "mode": mode,
            "status": status,
            "detail": detail,
            "fallback_used": fallback_used,
        }

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def plan_adaptations(
        self,
        issues: list[PortabilityIssue],
        catalog: list[str],
        targets: list[str] | None = None,
    ) -> AdaptationPlan:
        targets = targets or ["ubuntu", "fedora", "arch"]
        messages = build_mode_a_prompt(issues, catalog, targets)
        raw, detail = self._chat(messages)
        if raw:
            parsed = self._parse_mode_a(raw)
            if parsed and parsed.applications:
                self._set_last_call("plan", "success", detail, fallback_used=False)
                return parsed
            self._set_last_call("plan", "failed", "invalid-json", fallback_used=True)
            return self._fallback.plan_adaptations(issues, catalog, targets)
        self._set_last_call("plan", "failed", detail, fallback_used=True)
        return self._fallback.plan_adaptations(issues, catalog, targets)

    def diagnose_failure(
        self,
        failed_result: StageResult,
        applied_diffs: list[AppliedDiff],
        catalog: list[str],
    ) -> RepairAction:
        messages = build_mode_b_prompt(failed_result, applied_diffs, catalog)
        raw, detail = self._chat(messages)
        if raw:
            parsed = self._parse_mode_b(raw)
            if parsed is not None:
                self._set_last_call("diagnose", "success", detail, fallback_used=False)
                return parsed
            self._set_last_call("diagnose", "failed", "invalid-json", fallback_used=True)
            return self._fallback.diagnose_failure(failed_result, applied_diffs, catalog)
        self._set_last_call("diagnose", "failed", detail, fallback_used=True)
        return self._fallback.diagnose_failure(failed_result, applied_diffs, catalog)

    def summarize(
        self,
        issues: list[PortabilityIssue],
        applied_diffs: list[AppliedDiff],
        passed_targets: list[str],
        failed_targets: list[str],
    ) -> str:
        default_summary = self._fallback.summarize(
            issues, applied_diffs, passed_targets, failed_targets
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a concise technical writer. Summarize the portability "
                    "result in 1-2 short sentences. Mention how many issues were "
                    "detected, how many adaptations were applied, and which targets "
                    "are now compatible. Respond with plain text only."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "issues_detected": len(issues),
                        "adaptations_applied": len(applied_diffs),
                        "compatible_targets": passed_targets,
                        "failed_targets": failed_targets,
                    }
                ),
            },
        ]
        # The summary is free text, so do not force JSON format for this call.
        raw, _detail = self._chat(messages, json_format=False)
        if raw and len(raw.strip()) > 10:
            text = raw.strip()
            if "```" in text:
                text = text.replace("```", "").strip()
            self._set_last_call("summarize", "success", fallback_used=False)
            return text
        self._set_last_call("summarize", "failed", fallback_used=True)
        return default_summary
