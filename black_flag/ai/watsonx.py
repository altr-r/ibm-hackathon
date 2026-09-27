"""
ai/watsonx.py — WatsonxProvider: IBM watsonx.ai via /ml/v1/text/chat.

Two-stage authentication:
  1. Exchange the user's IBM Cloud API key (WATSONX_API_KEY) for a short-lived
     IAM access token at https://iam.cloud.ibm.com/identity/token
  2. Use the IAM access token as the Bearer credential for watsonx.ai inference

The IAM token is cached in-memory; on HTTP 401 a single refresh + retry is
performed automatically.

If any part of the request chain fails (IAM, network, parse, validation) the
call falls back to DeterministicProvider.  WatsonxProvider methods therefore
never raise.

Required environment variables:
  WATSONX_API_KEY    — IBM Cloud API key (NOT the IAM access token)
  WATSONX_BASE_URL   — https://us-south.ml.cloud.ibm.com/ml/v1
  WATSONX_MODEL_ID   — e.g. ibm/granite-3-8b-instruct
  WATSONX_PROJECT_ID — watsonx.ai project id

All request/response status is exposed via the ``last_call`` dict so the CLI
can print a clear success / failure line.
"""
from __future__ import annotations

import json
import os
import re
import time
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
_DEFAULT_TEMPERATURE = 0.0   # low temperature for JSON fidelity
_API_TIMEOUT = 45            # seconds per HTTP call
_IAM_TOKEN_URL = "https://iam.cloud.ibm.com/identity/token"
_IAM_TOKEN_GRACE_S = 60      # refresh token 60s before stated expiry


def _sanitize_env_str(raw: str | None) -> str:
    """Strip a possible ``NAME=`` prefix from users who paste env-set lines."""
    if not raw:
        return ""
    val = raw.strip()
    # Strip a single leading ``KEY=`` if the user e.g. pasted
    # ``WATSONX_BASE_URL=https://...`` directly into the env var.
    m = re.match(r"^[A-Z][A-Z0-9_]+=(.+)$", val)
    if m:
        val = m.group(1).strip()
    return val.strip().strip('"').strip("'")


def _extract_json(raw: str) -> Any | None:
    """Robust JSON extraction: fences, trailing garbage, surrounding prose."""
    if not raw:
        return None
    cleaned = raw.strip()
    # Markdown fences with optional language tag
    if "```" in cleaned:
        parts = re.split(r"```(?:json)?\s*", cleaned, maxsplit=2)
        if len(parts) >= 2:
            inner = parts[1].rstrip("`").strip()
            cleaned = inner
    # Find the first '{' and last '}' / first '[' and last ']' for wrapper.
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


class WatsonxProvider(AIProvider):
    name = "watsonx"

    #: Status record for the most recent watsonx call (used by CLI for display).
    #: Shape:
    #:   {"mode": "plan"|"diagnose"|"summarize"|None,
    #:    "status": "success"|"failed"|"fallback"|"skipped",
    #:    "detail": str,
    #:    "fallback_used": bool}
    last_call: dict[str, Any] = {
        "mode": None,
        "status": "skipped",
        "detail": "",
        "fallback_used": False,
    }

    def __init__(self) -> None:
        self._api_key = _sanitize_env_str(os.environ.get("WATSONX_API_KEY", ""))
        base = _sanitize_env_str(os.environ.get("WATSONX_BASE_URL", _DEFAULT_BASE_URL)) or _DEFAULT_BASE_URL
        self._base_url = base.rstrip("/")
        self._model_id = _sanitize_env_str(os.environ.get("WATSONX_MODEL_ID", _DEFAULT_MODEL)) or _DEFAULT_MODEL
        self._project_id = _sanitize_env_str(os.environ.get("WATSONX_PROJECT_ID", ""))
        self._fallback = DeterministicProvider()
        self._iam_token: str | None = None
        self._iam_expires_at: float = 0.0
        self._iam_refreshed_once_this_call: bool = False

    # ------------------------------------------------------------------
    # IAM token management
    # ------------------------------------------------------------------

    def _get_iam_token(self) -> str | None:
        """Return a valid IAM access token, requesting/refreshing as needed.

        Returns ``None`` on any failure (network, parse, bad credentials).
        """
        now = time.time()
        if (
            self._iam_token
            and now < self._iam_expires_at - _IAM_TOKEN_GRACE_S
        ):
            return self._iam_token
        if not self._api_key:
            return None
        import httpx  # lazy

        try:
            resp = httpx.post(
                _IAM_TOKEN_URL,
                data={
                    "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
                    "apikey": self._api_key,
                },
                headers={"Accept": "application/json"},
                timeout=_API_TIMEOUT,
            )
            if resp.status_code != 200:
                # Swallow the body (may contain redacted error) — caller falls back.
                return None
            data = resp.json()
            access_token = data.get("access_token")
            if not access_token:
                return None
            expires_in = int(data.get("expires_in", 3600))
            self._iam_token = access_token
            self._iam_expires_at = now + expires_in
            return self._iam_token
        except Exception:
            return None

    def _invalidate_iam_token(self) -> None:
        self._iam_token = None
        self._iam_expires_at = 0.0

    # ------------------------------------------------------------------
    # watsonx text/chat
    # ------------------------------------------------------------------

    def _chat(self, messages: list[dict]) -> tuple[str | None, str]:
        """Call watsonx text/chat. Returns (content_or_None, status_detail).

        The returned content is the raw string returned by the model
        (or ``None``).  ``status_detail`` is a safe, non-secret short
        description suitable for CLI display.
        """
        import httpx  # lazy

        token = self._get_iam_token()
        if token is None:
            return None, "iam-token-unavailable"

        url = f"{self._base_url}/text/chat?version=2024-05-31"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body: dict[str, Any] = {
            "model_id": self._model_id,
            "messages": messages,
            "parameters": {
                "max_new_tokens": _DEFAULT_MAX_TOKENS,
                "temperature": _DEFAULT_TEMPERATURE,
                "decoding_method": "greedy",
            },
        }
        if self._project_id:
            body["project_id"] = self._project_id

        def _do_request() -> httpx.Response | None:
            try:
                return httpx.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=_API_TIMEOUT,
                )
            except Exception:
                return None

        resp = _do_request()

        # 401: refresh the IAM token and retry exactly once
        if (
            resp is not None
            and resp.status_code == 401
            and not self._iam_refreshed_once_this_call
        ):
            self._iam_refreshed_once_this_call = True
            self._invalidate_iam_token()
            new_tok = self._get_iam_token()
            if new_tok is not None:
                headers["Authorization"] = f"Bearer {new_tok}"
                resp = _do_request()

        if resp is None:
            return None, "network-error"
        if resp.status_code != 200:
            # Surface the non-secret IBM error code (e.g.
            # ``no_associated_service_instance_error``) to aid diagnosis.
            # Never include headers, tokens, or the request body.
            err_code = ""
            try:
                payload = resp.json()
                errors = payload.get("errors") or []
                if errors and isinstance(errors, list):
                    err_code = str(errors[0].get("code", ""))
            except Exception:
                err_code = ""
            detail = f"http-{resp.status_code}"
            if err_code:
                detail = f"{detail}:{err_code}"
            return None, detail
        try:
            data = resp.json()
        except Exception:
            return None, "bad-json-payload"

        # Watsonx /text/chat response shape:
        # choices[0].message.content  OR  results[0].generated_text
        choices = data.get("choices") or []
        if choices and isinstance(choices, list):
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if content:
                return content, "ok"
        results = data.get("results") or []
        if results and isinstance(results, list):
            text = results[0].get("generated_text")
            if text:
                return text, "ok"
        return None, "empty-response"

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_mode_a(self, raw: str) -> AdaptationPlan | None:
        data = _extract_json(raw)
        if not isinstance(data, dict):
            return None
        primitives = data.get("primitives", [])
        if not isinstance(primitives, list):
            return None
        valid_ids = set(list_primitive_ids())
        apps: list[PrimitiveApplication] = []
        for item in primitives:
            if not isinstance(item, dict):
                continue
            pid = item.get("primitive_id", "")
            if not isinstance(pid, str) or pid not in valid_ids:
                continue
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
            return RepairAction(
                action="give_up",
                primitive_id=None,
                params={},
                rationale=reason,
            )
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
        return RepairAction(
            action="apply",
            primitive_id=pid,
            params=params,
            rationale=rationale,
        )

    def _set_last_call(
        self,
        mode: str,
        status: str,
        detail: str = "",
        fallback_used: bool = False,
    ) -> None:
        self.last_call = {
            "mode": mode,
            "status": status,
            "detail": detail,
            "fallback_used": fallback_used,
        }

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def base_url(self) -> str:
        return self._base_url

    def is_configured(self) -> bool:
        return bool(self._api_key)

    def plan_adaptations(
        self,
        issues: list[PortabilityIssue],
        catalog: list[str],
        targets: list[str] | None = None,
    ) -> AdaptationPlan:
        self._iam_refreshed_once_this_call = False
        targets = targets or ["ubuntu", "fedora", "arch"]
        if not self.is_configured():
            self._set_last_call("plan", "skipped", "no-api-key", fallback_used=True)
            return self._fallback.plan_adaptations(issues, catalog, targets)
        messages = build_mode_a_prompt(issues, catalog, targets)
        raw, detail = self._chat(messages)
        if raw:
            parsed = self._parse_mode_a(raw)
            if parsed and parsed.applications:
                self._set_last_call("plan", "success", detail, fallback_used=False)
                return parsed
            fb_reason = detail if raw is None else "invalid-json"
            self._set_last_call("plan", "failed", fb_reason, fallback_used=True)
            return self._fallback.plan_adaptations(issues, catalog, targets)
        self._set_last_call("plan", "failed", detail, fallback_used=True)
        return self._fallback.plan_adaptations(issues, catalog, targets)

    def diagnose_failure(
        self,
        failed_result: StageResult,
        applied_diffs: list[AppliedDiff],
        catalog: list[str],
    ) -> RepairAction:
        self._iam_refreshed_once_this_call = False
        if not self.is_configured():
            self._set_last_call("diagnose", "skipped", "no-api-key", fallback_used=True)
            return self._fallback.diagnose_failure(failed_result, applied_diffs, catalog)
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
        self._iam_refreshed_once_this_call = False
        default_summary = self._fallback.summarize(
            issues, applied_diffs, passed_targets, failed_targets
        )
        if not self.is_configured():
            self._set_last_call("summarize", "skipped", "no-api-key", fallback_used=True)
            return default_summary
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a concise technical writer. Summarize the portability "
                    "result in 1-2 short sentences. Mention how many issues were "
                    "detected, how many adaptations were applied, and which "
                    "targets are now compatible."
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
                        "applied_diffs": [
                            {
                                "primitive_id": d.primitive_id,
                                "target_file": d.target_file,
                            }
                            for d in applied_diffs
                        ],
                    }
                ),
            },
        ]
        raw, _detail = self._chat(messages)
        if raw and len(raw.strip()) > 10:
            text = raw.strip()
            # Strip potential markdown fences even in the short summary
            if "```" in text:
                text = text.replace("```", "").strip()
            self._set_last_call("summarize", "success", fallback_used=False)
            return text
        self._set_last_call("summarize", "failed", fallback_used=True)
        return default_summary
