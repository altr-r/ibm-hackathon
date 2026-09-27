"""
tests/test_watsonx.py — Watsonx integration tests.

All network access is mocked (httpx.post is monkeypatched). These tests never
require live IBM credentials and never print secrets.

Covers:
  1. Factory -> DeterministicProvider when no key
  2. Factory -> WatsonxProvider when key present
  3. IAM token request succeeds (mocked)
  4. IAM token failure -> safe deterministic fallback
  5. text/chat request succeeds (mocked)
  6. 401 -> refresh IAM token and retry once
  7. Malformed model JSON -> safe fallback
  8. Invalid primitive IDs rejected
  9. Mode A returns a valid AdaptationPlan
 10. Mode B returns a valid RepairAction
 11. No credentials leak into status/details
 12. Fallback plan matches deterministic plan
"""
from __future__ import annotations

import json

import pytest

from black_flag.core.types import PortabilityIssue, StageResult
from black_flag.primitives.catalog import list_primitive_ids

# A recognizable fake secret used only to assert it never leaks.
_FAKE_KEY = "TESTKEY-DO-NOT-LEAK-abcdef123456"
_IAM_URL_FRAGMENT = "iam.cloud.ibm.com"


# ---------------------------------------------------------------------------
# Mock HTTP helpers
# ---------------------------------------------------------------------------

class _Resp:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, status_code: int, payload=None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _install_post(monkeypatch, iam_resp, chat_resps):
    """Monkeypatch httpx.post with a URL router. Returns a call counter dict."""
    import httpx

    calls = {"iam": 0, "chat": 0}

    def _post(url, *args, **kwargs):
        if _IAM_URL_FRAGMENT in url:
            calls["iam"] += 1
            return iam_resp
        calls["chat"] += 1
        idx = min(calls["chat"] - 1, len(chat_resps) - 1)
        item = chat_resps[idx]
        return item() if callable(item) else item

    monkeypatch.setattr(httpx, "post", _post)
    return calls


def _iam_ok():
    return _Resp(200, {"access_token": "iam-token-xyz", "expires_in": 3600})


def _chat_payload(obj):
    return {"choices": [{"message": {"content": json.dumps(obj)}}]}


def _chat_resp(obj, status=200):
    return _Resp(status, _chat_payload(obj))


@pytest.fixture
def wx_env(monkeypatch):
    """Configure a watsonx environment with a fake key."""
    # These tests exercise the *default* provider selection (no explicit
    # AI_PROVIDER), so clear any ambient AI_PROVIDER to stay hermetic.
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.setenv("WATSONX_API_KEY", _FAKE_KEY)
    monkeypatch.setenv("WATSONX_PROJECT_ID", "test-project-id")
    monkeypatch.setenv("WATSONX_MODEL_ID", "ibm/granite-3-3-8b-instruct")
    monkeypatch.setenv("WATSONX_BASE_URL", "https://us-south.ml.cloud.ibm.com/ml/v1")


def _issue():
    return PortabilityIssue(
        category="package-manager",
        severity="error",
        source_file="setup.sh",
        line=3,
        affected_targets=["fedora", "arch"],
        explanation="apt-get not portable",
        suggested_primitive="pkg_manager_call",
        verification_method="docker-test",
    )


def _stage_result():
    return StageResult(
        distro="fedora",
        stage="prepare",
        exit_code=127,
        stdout="",
        stderr="bash: apt-get: command not found",
        elapsed_s=0.1,
    )


# ---------------------------------------------------------------------------
# 1 & 2 — Factory selection
# ---------------------------------------------------------------------------

def test_factory_deterministic_when_no_key(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("WATSONX_API_KEY", raising=False)
    from black_flag.ai.factory import get_provider
    from black_flag.ai.deterministic import DeterministicProvider

    provider = get_provider()
    assert isinstance(provider, DeterministicProvider)
    assert provider.name == "deterministic"


def test_factory_watsonx_when_key(wx_env):
    from black_flag.ai.factory import get_provider
    from black_flag.ai.watsonx import WatsonxProvider

    provider = get_provider()
    assert isinstance(provider, WatsonxProvider)
    assert provider.name == "watsonx"
    assert provider.is_configured() is True


def test_factory_no_ai_flag_forces_deterministic(wx_env):
    from black_flag.ai.factory import get_provider
    from black_flag.ai.deterministic import DeterministicProvider

    provider = get_provider(no_ai=True)
    assert isinstance(provider, DeterministicProvider)


# ---------------------------------------------------------------------------
# 3 — IAM token acquisition
# ---------------------------------------------------------------------------

def test_iam_token_success(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    _install_post(monkeypatch, _iam_ok(), [_chat_resp({"primitives": []})])
    provider = WatsonxProvider()
    token = provider._get_iam_token()
    assert token == "iam-token-xyz"


def test_iam_token_cached(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    calls = _install_post(monkeypatch, _iam_ok(), [_chat_resp({"primitives": []})])
    provider = WatsonxProvider()
    provider._get_iam_token()
    provider._get_iam_token()
    # Second call served from cache -> only one IAM request.
    assert calls["iam"] == 1


# ---------------------------------------------------------------------------
# 4 — IAM failure -> deterministic fallback
# ---------------------------------------------------------------------------

def test_iam_failure_falls_back(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    _install_post(monkeypatch, _Resp(400, {"errors": [{"code": "invalid_api_key"}]}), [])
    provider = WatsonxProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"
    assert provider.last_call["status"] == "failed"
    assert provider.last_call["fallback_used"] is True
    assert provider.last_call["detail"] == "iam-token-unavailable"


# ---------------------------------------------------------------------------
# 5 & 9 — Mode A chat success returns a valid AdaptationPlan
# ---------------------------------------------------------------------------

def test_mode_a_chat_success_returns_ai_plan(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    model_obj = {
        "primitives": [
            {"primitive_id": "pkg_manager_call", "issue_ids": [0], "params": {}, "rationale": "dispatch pkg mgr"}
        ]
    }
    calls = _install_post(monkeypatch, _iam_ok(), [_chat_resp(model_obj)])
    provider = WatsonxProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])

    assert plan.source == "ai"
    assert len(plan.applications) == 1
    assert plan.applications[0].primitive_id == "pkg_manager_call"
    assert provider.last_call["status"] == "success"
    assert provider.last_call["fallback_used"] is False
    assert calls["chat"] == 1


def test_mode_a_handles_markdown_fences(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    fenced = "```json\n" + json.dumps(
        {"primitives": [{"primitive_id": "shell_compat", "issue_ids": [0], "params": {"strategy": "explicit_bash"}, "rationale": "shebang"}]}
    ) + "\n```"
    chat = _Resp(200, {"choices": [{"message": {"content": fenced}}]})
    _install_post(monkeypatch, _iam_ok(), [chat])
    provider = WatsonxProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "ai"
    assert plan.applications[0].primitive_id == "shell_compat"


# ---------------------------------------------------------------------------
# 6 — 401 refresh + retry once
# ---------------------------------------------------------------------------

def test_401_refreshes_token_and_retries_once(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    model_obj = {"primitives": [{"primitive_id": "pkg_manager_call", "issue_ids": [0], "params": {}, "rationale": "r"}]}
    calls = _install_post(
        monkeypatch,
        _iam_ok(),
        [_Resp(401, {"errors": [{"code": "unauthorized"}]}), _chat_resp(model_obj)],
    )
    provider = WatsonxProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])

    assert plan.source == "ai"
    assert calls["chat"] == 2      # original + one retry
    assert calls["iam"] == 2       # initial token + one refresh
    assert provider.last_call["status"] == "success"


def test_401_only_retried_once(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    calls = _install_post(
        monkeypatch,
        _iam_ok(),
        [_Resp(401, {"errors": [{"code": "unauthorized"}]})],  # always 401
    )
    provider = WatsonxProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"
    assert calls["chat"] == 2      # gave up after a single retry
    assert provider.last_call["status"] == "failed"


# ---------------------------------------------------------------------------
# 7 — Malformed JSON -> safe fallback
# ---------------------------------------------------------------------------

def test_malformed_json_falls_back(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    chat = _Resp(200, {"choices": [{"message": {"content": "this is not json at all"}}]})
    _install_post(monkeypatch, _iam_ok(), [chat])
    provider = WatsonxProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"
    assert provider.last_call["status"] == "failed"
    assert provider.last_call["fallback_used"] is True


# ---------------------------------------------------------------------------
# 8 — Invalid primitive IDs rejected
# ---------------------------------------------------------------------------

def test_invalid_primitive_ids_rejected(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    model_obj = {
        "primitives": [
            {"primitive_id": "rm_rf_everything", "issue_ids": [0], "params": {}, "rationale": "malicious"},
            {"primitive_id": "pkg_manager_call", "issue_ids": [0], "params": {}, "rationale": "valid"},
        ]
    }
    _install_post(monkeypatch, _iam_ok(), [_chat_resp(model_obj)])
    provider = WatsonxProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    ids = [a.primitive_id for a in plan.applications]
    assert "rm_rf_everything" not in ids
    assert ids == ["pkg_manager_call"]


def test_all_invalid_ids_fall_back(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    model_obj = {"primitives": [{"primitive_id": "bogus", "issue_ids": [0], "params": {}, "rationale": "x"}]}
    _install_post(monkeypatch, _iam_ok(), [_chat_resp(model_obj)])
    provider = WatsonxProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"


# ---------------------------------------------------------------------------
# 10 — Mode B returns a valid RepairAction
# ---------------------------------------------------------------------------

def test_mode_b_apply(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    model_obj = {"action": "apply", "primitive_id": "package_name_remap", "params": {}, "rationale": "remap pkg"}
    _install_post(monkeypatch, _iam_ok(), [_chat_resp(model_obj)])
    provider = WatsonxProvider()
    repair = provider.diagnose_failure(_stage_result(), [], list_primitive_ids())
    assert repair.action == "apply"
    assert repair.primitive_id == "package_name_remap"
    assert provider.last_call["status"] == "success"


def test_mode_b_give_up(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    model_obj = {"action": "give_up", "reason": "outside catalog"}
    _install_post(monkeypatch, _iam_ok(), [_chat_resp(model_obj)])
    provider = WatsonxProvider()
    repair = provider.diagnose_failure(_stage_result(), [], list_primitive_ids())
    assert repair.action == "give_up"
    assert provider.last_call["status"] == "success"


def test_mode_b_invalid_primitive_falls_back(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    model_obj = {"action": "apply", "primitive_id": "not_a_primitive", "params": {}, "rationale": "x"}
    _install_post(monkeypatch, _iam_ok(), [_chat_resp(model_obj)])
    provider = WatsonxProvider()
    repair = provider.diagnose_failure(_stage_result(), [], list_primitive_ids())
    # Falls back to deterministic, which matches apt-get -> pkg_manager_call
    assert repair.action == "apply"
    assert repair.primitive_id == "pkg_manager_call"
    assert provider.last_call["fallback_used"] is True


# ---------------------------------------------------------------------------
# 11 — No credentials leak
# ---------------------------------------------------------------------------

def test_no_credentials_in_status(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider

    _install_post(monkeypatch, _iam_ok(), [_Resp(403, {"errors": [{"code": "no_associated_service_instance_error"}]})])
    provider = WatsonxProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])

    blob = json.dumps(provider.last_call) + json.dumps(
        [a.rationale for a in plan.applications]
    )
    assert _FAKE_KEY not in blob
    assert "iam-token-xyz" not in blob
    assert provider.last_call["detail"] == "http-403:no_associated_service_instance_error"


# ---------------------------------------------------------------------------
# 12 — Fallback matches deterministic provider
# ---------------------------------------------------------------------------

def test_fallback_matches_deterministic(wx_env, monkeypatch):
    from black_flag.ai.watsonx import WatsonxProvider
    from black_flag.ai.deterministic import DeterministicProvider

    _install_post(monkeypatch, _Resp(500, None, text="boom"), [])
    provider = WatsonxProvider()
    issues = [_issue()]
    catalog = list_primitive_ids()
    wx_plan = provider.plan_adaptations(issues, catalog, ["fedora"])
    det_plan = DeterministicProvider().plan_adaptations(issues, catalog, ["fedora"])
    assert [a.primitive_id for a in wx_plan.applications] == [a.primitive_id for a in det_plan.applications]
    assert wx_plan.source == "deterministic"
