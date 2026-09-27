"""
tests/test_ollama.py — OllamaProvider integration tests.

All network access is mocked (httpx.post / httpx.get are monkeypatched), so
these tests never require a running Ollama instance and never touch the
network. No secrets are printed.

Covers the 16 required scenarios:
   1. successful Mode A request
   2. successful Mode B request
   3. valid AdaptationPlan parsing
   4. valid RepairAction parsing
   5. malformed JSON
   6. invalid JSON schema
   7. unsupported primitive
   8. missing required fields
   9. Ollama connection failure
  10. Ollama timeout
  11. deterministic fallback
  12. factory selects OllamaProvider
  13. default Ollama URL
  14. default Granite model
  15. provider does not require Watsonx credentials
  16. raw secrets are never logged
"""
from __future__ import annotations

import json

import pytest

from black_flag.core.types import PortabilityIssue, StageResult
from black_flag.primitives.catalog import list_primitive_ids

# A recognizable fake secret used only to assert it never leaks.
_FAKE_SECRET = "OLLAMA-DO-NOT-LEAK-supersecretvalue123"


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


def _chat_resp(content, status: int = 200) -> _Resp:
    """Build an Ollama /api/chat response wrapping *content* in message.content."""
    if not isinstance(content, str):
        content = json.dumps(content)
    return _Resp(status, {"message": {"role": "assistant", "content": content}, "done": True})


def _raise(exc: Exception):
    """Return a callable that raises *exc* (used to simulate transport errors)."""
    def _f():
        raise exc
    return _f


def _install_post(monkeypatch, resps):
    """Monkeypatch httpx.post with a sequential response router."""
    import httpx

    calls = {"chat": 0}

    def _post(url, *args, **kwargs):
        calls["chat"] += 1
        idx = min(calls["chat"] - 1, len(resps) - 1)
        item = resps[idx]
        return item() if callable(item) else item

    monkeypatch.setattr(httpx, "post", _post)
    return calls


def _install_get(monkeypatch, resp):
    """Monkeypatch httpx.get (used by is_available -> /api/tags)."""
    import httpx

    def _get(url, *args, **kwargs):
        return resp() if callable(resp) else resp

    monkeypatch.setattr(httpx, "get", _get)


@pytest.fixture
def ollama_env(monkeypatch):
    """Configure a clean Ollama environment with NO Watsonx credentials."""
    monkeypatch.delenv("WATSONX_API_KEY", raising=False)
    monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)
    monkeypatch.delenv("WATSONX_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "ollama")


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
# 1 & 3 — successful Mode A request / valid AdaptationPlan parsing
# ---------------------------------------------------------------------------

def test_mode_a_success_returns_ai_plan(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    model_obj = {
        "primitives": [
            {
                "primitive_id": "pkg_manager_call",
                "issue_ids": [0],
                "params": {"file": "setup.sh"},
                "rationale": "dispatch package manager per distro",
            }
        ]
    }
    calls = _install_post(monkeypatch, [_chat_resp(model_obj)])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])

    assert plan.source == "ai"
    assert len(plan.applications) == 1
    app = plan.applications[0]
    assert app.primitive_id == "pkg_manager_call"
    assert app.params == {"file": "setup.sh"}
    assert app.issue_ids == [0]
    assert app.rationale == "dispatch package manager per distro"
    assert provider.last_call["status"] == "success"
    assert provider.last_call["fallback_used"] is False
    assert calls["chat"] == 1


def test_mode_a_handles_markdown_fences(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    fenced = "```json\n" + json.dumps(
        {
            "primitives": [
                {
                    "primitive_id": "shell_compat",
                    "issue_ids": [0],
                    "params": {"strategy": "explicit_bash"},
                    "rationale": "shebang fix",
                }
            ]
        }
    ) + "\n```"
    _install_post(monkeypatch, [_chat_resp(fenced)])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "ai"
    assert plan.applications[0].primitive_id == "shell_compat"


# ---------------------------------------------------------------------------
# 2 & 4 — successful Mode B request / valid RepairAction parsing
# ---------------------------------------------------------------------------

def test_mode_b_apply_success(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    model_obj = {
        "action": "apply",
        "primitive_id": "package_name_remap",
        "params": {"debian_name": "libssl-dev"},
        "rationale": "remap Debian SSL package name",
    }
    _install_post(monkeypatch, [_chat_resp(model_obj)])
    provider = OllamaProvider()
    repair = provider.diagnose_failure(_stage_result(), [], list_primitive_ids())
    assert repair.action == "apply"
    assert repair.primitive_id == "package_name_remap"
    assert repair.params == {"debian_name": "libssl-dev"}
    assert provider.last_call["status"] == "success"
    assert provider.last_call["fallback_used"] is False


def test_mode_b_give_up_success(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    model_obj = {"action": "give_up", "reason": "failure outside the primitive catalog"}
    _install_post(monkeypatch, [_chat_resp(model_obj)])
    provider = OllamaProvider()
    repair = provider.diagnose_failure(_stage_result(), [], list_primitive_ids())
    assert repair.action == "give_up"
    assert repair.primitive_id is None
    assert provider.last_call["status"] == "success"


# ---------------------------------------------------------------------------
# 5 — malformed JSON -> deterministic fallback
# ---------------------------------------------------------------------------

def test_malformed_json_falls_back(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    _install_post(monkeypatch, [_chat_resp("this is definitely not json {{{")])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"
    assert provider.last_call["status"] == "failed"
    assert provider.last_call["detail"] == "invalid-json"
    assert provider.last_call["fallback_used"] is True


# ---------------------------------------------------------------------------
# 6 — invalid JSON schema -> deterministic fallback
# ---------------------------------------------------------------------------

def test_invalid_schema_falls_back(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    # Valid JSON but missing the "primitives" key entirely.
    _install_post(monkeypatch, [_chat_resp({"unexpected": "shape", "foo": 42})])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"
    assert provider.last_call["status"] == "failed"
    assert provider.last_call["fallback_used"] is True


def test_primitives_not_a_list_falls_back(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    _install_post(monkeypatch, [_chat_resp({"primitives": "pkg_manager_call"})])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"


# ---------------------------------------------------------------------------
# 7 — unsupported primitive rejected
# ---------------------------------------------------------------------------

def test_unsupported_primitive_dropped_valid_kept(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    model_obj = {
        "primitives": [
            {"primitive_id": "rm_rf_everything", "issue_ids": [0], "params": {}, "rationale": "malicious"},
            {"primitive_id": "pkg_manager_call", "issue_ids": [0], "params": {}, "rationale": "valid"},
        ]
    }
    _install_post(monkeypatch, [_chat_resp(model_obj)])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    ids = [a.primitive_id for a in plan.applications]
    assert "rm_rf_everything" not in ids
    assert ids == ["pkg_manager_call"]
    assert plan.source == "ai"


def test_all_unsupported_primitives_fall_back(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    model_obj = {"primitives": [{"primitive_id": "bogus_primitive", "issue_ids": [0], "params": {}, "rationale": "x"}]}
    _install_post(monkeypatch, [_chat_resp(model_obj)])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"
    assert provider.last_call["fallback_used"] is True


def test_mode_b_unsupported_primitive_falls_back(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    model_obj = {"action": "apply", "primitive_id": "not_a_primitive", "params": {}, "rationale": "x"}
    _install_post(monkeypatch, [_chat_resp(model_obj)])
    provider = OllamaProvider()
    repair = provider.diagnose_failure(_stage_result(), [], list_primitive_ids())
    # Deterministic fallback matches "apt-get: command not found" -> pkg_manager_call
    assert repair.action == "apply"
    assert repair.primitive_id == "pkg_manager_call"
    assert provider.last_call["fallback_used"] is True


# ---------------------------------------------------------------------------
# 8 — missing required fields
# ---------------------------------------------------------------------------

def test_missing_primitive_id_field_skipped(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    model_obj = {
        "primitives": [
            {"issue_ids": [0], "params": {}, "rationale": "no primitive_id here"},
            {"primitive_id": "pkg_manager_call", "issue_ids": [0], "params": {}, "rationale": "ok"},
        ]
    }
    _install_post(monkeypatch, [_chat_resp(model_obj)])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    ids = [a.primitive_id for a in plan.applications]
    assert ids == ["pkg_manager_call"]


def test_missing_optional_fields_get_defaults(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    # Only primitive_id supplied; params/issue_ids/rationale absent.
    model_obj = {"primitives": [{"primitive_id": "distro_path_check"}]}
    _install_post(monkeypatch, [_chat_resp(model_obj)])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "ai"
    app = plan.applications[0]
    assert app.primitive_id == "distro_path_check"
    assert app.params == {}
    assert app.issue_ids == []
    assert app.rationale == ""


def test_all_items_missing_fields_falls_back(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    _install_post(monkeypatch, [_chat_resp({"primitives": [{"params": {}}]})])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"


# ---------------------------------------------------------------------------
# 9 — Ollama connection failure
# ---------------------------------------------------------------------------

def test_connection_failure_falls_back(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    _install_post(monkeypatch, [_raise(RuntimeError("connection refused"))])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"
    assert provider.last_call["status"] == "failed"
    assert provider.last_call["detail"] == "connection-error"
    assert provider.last_call["fallback_used"] is True


def test_http_error_status_falls_back(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    _install_post(monkeypatch, [_Resp(500, {"error": "model not found"})])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"
    assert provider.last_call["detail"].startswith("http-500")


# ---------------------------------------------------------------------------
# 10 — Ollama timeout
# ---------------------------------------------------------------------------

def test_timeout_falls_back(ollama_env, monkeypatch):
    import httpx
    from black_flag.ai.ollama import OllamaProvider

    _install_post(monkeypatch, [_raise(httpx.TimeoutException("timed out"))])
    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])
    assert plan.source == "deterministic"
    assert provider.last_call["status"] == "failed"
    assert provider.last_call["detail"] == "timeout"
    assert provider.last_call["fallback_used"] is True


# ---------------------------------------------------------------------------
# 11 — deterministic fallback matches DeterministicProvider output
# ---------------------------------------------------------------------------

def test_fallback_matches_deterministic(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider
    from black_flag.ai.deterministic import DeterministicProvider

    _install_post(monkeypatch, [_raise(RuntimeError("boom"))])
    provider = OllamaProvider()
    issues = [_issue()]
    catalog = list_primitive_ids()
    ollama_plan = provider.plan_adaptations(issues, catalog, ["fedora"])
    det_plan = DeterministicProvider().plan_adaptations(issues, catalog, ["fedora"])
    assert [a.primitive_id for a in ollama_plan.applications] == [a.primitive_id for a in det_plan.applications]
    assert ollama_plan.source == "deterministic"


# ---------------------------------------------------------------------------
# 12 — factory selects OllamaProvider
# ---------------------------------------------------------------------------

def test_factory_selects_ollama(ollama_env):
    from black_flag.ai.factory import get_provider
    from black_flag.ai.ollama import OllamaProvider

    provider = get_provider()
    assert isinstance(provider, OllamaProvider)
    assert provider.name == "ollama"


def test_factory_explicit_provider_arg(ollama_env, monkeypatch):
    from black_flag.ai.factory import get_provider
    from black_flag.ai.ollama import OllamaProvider
    from black_flag.ai.deterministic import DeterministicProvider

    monkeypatch.delenv("AI_PROVIDER", raising=False)
    assert isinstance(get_provider(provider="ollama"), OllamaProvider)
    assert isinstance(get_provider(provider="deterministic"), DeterministicProvider)


def test_factory_watsonx_still_works(monkeypatch):
    """Regression: AI_PROVIDER=watsonx still constructs WatsonxProvider."""
    from black_flag.ai.factory import get_provider
    from black_flag.ai.watsonx import WatsonxProvider

    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "watsonx")
    monkeypatch.setenv("WATSONX_API_KEY", "fake-key-for-construction")
    provider = get_provider()
    assert isinstance(provider, WatsonxProvider)
    assert provider.name == "watsonx"


def test_factory_default_preserved_without_ai_provider(monkeypatch):
    """Regression: no AI_PROVIDER + no key -> deterministic (original default)."""
    from black_flag.ai.factory import get_provider
    from black_flag.ai.deterministic import DeterministicProvider

    monkeypatch.delenv("AI_PROVIDER", raising=False)
    monkeypatch.delenv("WATSONX_API_KEY", raising=False)
    provider = get_provider()
    assert isinstance(provider, DeterministicProvider)


def test_factory_unknown_provider_is_safe(monkeypatch):
    """An unrecognised AI_PROVIDER value never raises; falls back to default."""
    from black_flag.ai.factory import get_provider
    from black_flag.ai.deterministic import DeterministicProvider

    monkeypatch.delenv("WATSONX_API_KEY", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "does-not-exist")
    provider = get_provider()
    assert isinstance(provider, DeterministicProvider)


# ---------------------------------------------------------------------------
# 13 & 14 — default Ollama URL / default Granite model
# ---------------------------------------------------------------------------

def test_default_base_url(ollama_env):
    from black_flag.ai.ollama import OllamaProvider

    provider = OllamaProvider()
    assert provider.base_url == "http://localhost:11434"


def test_default_model(ollama_env):
    from black_flag.ai.ollama import OllamaProvider

    provider = OllamaProvider()
    assert provider.model_id == "granite4.2:3b"


def test_env_overrides_base_url_and_model(monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:9999/")
    monkeypatch.setenv("OLLAMA_MODEL", "custom-model:tag")
    provider = OllamaProvider()
    assert provider.base_url == "http://127.0.0.1:9999"  # trailing slash stripped
    assert provider.model_id == "custom-model:tag"


# ---------------------------------------------------------------------------
# 15 — provider does not require Watsonx credentials
# ---------------------------------------------------------------------------

def test_no_watsonx_credentials_required(monkeypatch):
    from black_flag.ai.factory import get_provider
    from black_flag.ai.ollama import OllamaProvider

    monkeypatch.delenv("WATSONX_API_KEY", raising=False)
    monkeypatch.delenv("WATSONX_PROJECT_ID", raising=False)
    monkeypatch.delenv("WATSONX_BASE_URL", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "ollama")

    provider = get_provider()
    assert isinstance(provider, OllamaProvider)
    assert provider.is_configured() is True


def test_is_available_true_and_false(monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    provider = OllamaProvider()
    _install_get(monkeypatch, _Resp(200, {"models": []}))
    assert provider.is_available() is True
    _install_get(monkeypatch, _raise(RuntimeError("no server")))
    assert provider.is_available() is False


# ---------------------------------------------------------------------------
# 16 — raw secrets are never logged
# ---------------------------------------------------------------------------

def test_no_secrets_or_raw_response_in_status(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    # Plant a secret in the environment and inside the (failing) model output.
    monkeypatch.setenv("WATSONX_API_KEY", _FAKE_SECRET)
    leaking_content = f"here is a secret {_FAKE_SECRET} and a raw dump"
    _install_post(monkeypatch, [_Resp(403, {"error": leaking_content})])

    provider = OllamaProvider()
    plan = provider.plan_adaptations([_issue()], list_primitive_ids(), ["fedora"])

    blob = json.dumps(provider.last_call)
    assert _FAKE_SECRET not in blob
    # The full raw error text must not be echoed verbatim into the status.
    assert "raw dump" not in blob
    assert provider.last_call["fallback_used"] is True


def test_summarize_uses_free_text_and_falls_back(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    # summarize() must not force JSON format; a short/empty reply falls back.
    _install_post(monkeypatch, [_chat_resp("nope")])  # < 10 chars after strip
    provider = OllamaProvider()
    summary = provider.summarize([_issue()], [], ["ubuntu"], [])
    assert isinstance(summary, str)
    assert summary  # deterministic fallback text is non-empty
    assert provider.last_call["mode"] == "summarize"
    assert provider.last_call["fallback_used"] is True


def test_summarize_success_returns_model_text(ollama_env, monkeypatch):
    from black_flag.ai.ollama import OllamaProvider

    _install_post(monkeypatch, [_chat_resp("Nine issues were detected and six adaptations applied.")])
    provider = OllamaProvider()
    summary = provider.summarize([_issue()], [], ["ubuntu"], [])
    assert summary.startswith("Nine issues")
    assert provider.last_call["status"] == "success"
