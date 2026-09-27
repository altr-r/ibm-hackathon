"""
tests/test_web.py — Black Flag web API tests.

These use FastAPI's TestClient (in-process; no live server). They never require
Docker or a running Ollama instance:

  * analysis / adapt jobs are exercised in dry-run + deterministic mode,
  * provider reporting is checked for safety (no secrets),
  * uploads are checked for path-traversal rejection.

Covers the required web scenarios:
  - /api/health
  - /api/info
  - invalid request handling
  - project analysis route
  - job creation
  - job status
  - safe provider reporting
  - no secret exposure
"""
from __future__ import annotations

import io
import json
import time
import zipfile

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from black_flag.web.app import create_app  # noqa: E402
from black_flag.web import services  # noqa: E402

_FAKE_SECRET = "WX-DO-NOT-LEAK-abcdef123456"


@pytest.fixture
def client():
    return TestClient(create_app())


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _wait_for_job(client, job_id: str, timeout: float = 40.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200
        last = r.json()
        if last["status"] in ("complete", "failed"):
            return last
        time.sleep(0.2)
    raise AssertionError(f"Job did not finish in time. Last state: {last}")


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["provider"] in ("deterministic", "watsonx", "ollama")


# ---------------------------------------------------------------------------
# /api/info — structure + safe provider reporting
# ---------------------------------------------------------------------------

def test_info_structure(client):
    r = client.get("/api/info")
    assert r.status_code == 200
    body = r.json()
    assert "provider" in body and "name" in body["provider"]
    assert "docker" in body and "available" in body["docker"]
    assert "ollama" in body and "available" in body["ollama"]
    assert isinstance(body["targets"], list) and len(body["targets"]) == 3
    ids = {t["id"] for t in body["targets"]}
    assert ids == {"ubuntu", "fedora", "arch"}


def test_info_never_leaks_watsonx_secret(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "watsonx")
    monkeypatch.setenv("WATSONX_API_KEY", _FAKE_SECRET)
    monkeypatch.setenv("WATSONX_PROJECT_ID", "proj-id")

    r = client.get("/api/info")
    assert r.status_code == 200
    raw = r.text
    assert _FAKE_SECRET not in raw
    assert "proj-id" not in raw
    body = r.json()
    assert body["provider"]["name"] == "watsonx"
    # Safe descriptor only — never the credential itself.
    assert body["provider"]["auth"] == "IAM access token"


def test_info_ollama_reporting_is_local(client, monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    body = client.get("/api/info").json()
    assert body["provider"]["name"] == "ollama"
    assert body["provider"]["auth"] == "local"
    assert body["provider"]["model"] == "granite4.2:3b"
    assert "localhost:11434" in body["provider"]["endpoint"]


# ---------------------------------------------------------------------------
# project selection + analysis route
# ---------------------------------------------------------------------------

def test_select_demo(client):
    r = client.post("/api/projects/select", json={"source": "demo"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "portable-demo"
    assert body["file_count"] > 0


def test_select_invalid_source(client):
    r = client.post("/api/projects/select", json={"source": "does-not-exist"})
    assert r.status_code == 400


def test_analyze_demo(client):
    r = client.post("/api/projects/analyze", json={"source": "demo"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "portable-demo"
    assert isinstance(body["score"], float)
    assert 0.0 <= body["score"] <= 1.0
    assert body["issue_count"] == len(body["issues"])
    assert body["issue_count"] > 0
    first = body["issues"][0]
    for key in ("category", "severity", "source_file", "line", "affected_targets", "explanation"):
        assert key in first


def test_analyze_invalid_source(client):
    r = client.post("/api/projects/analyze", json={"source": "nope"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# uploads — valid + path traversal rejection
# ---------------------------------------------------------------------------

def test_upload_and_analyze_zip(client):
    data = _make_zip({
        "setup.sh": "#!/bin/sh\napt-get install -y libssl-dev\n",
        "app.py": "import os\nprint(os.environ['DEBIAN_FRONTEND'])\n",
        "requirements.txt": "pyopenssl==23.0.0\n",
    })
    r = client.post("/api/projects/upload", files={"file": ("proj.zip", data, "application/zip")})
    assert r.status_code == 200
    body = r.json()
    assert body["upload_id"]
    assert body["file_count"] >= 3

    a = client.post("/api/projects/analyze", json={"source": body["upload_id"]})
    assert a.status_code == 200
    assert a.json()["issue_count"] > 0


def test_upload_rejects_path_traversal(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil.txt", "pwned")
    r = client.post(
        "/api/projects/upload",
        files={"file": ("evil.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 400


def test_upload_rejects_bad_extension(client):
    r = client.post("/api/projects/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# jobs — creation, status, report, package, 404s
# ---------------------------------------------------------------------------

def test_job_not_found(client):
    assert client.get("/api/jobs/deadbeef").status_code == 404
    assert client.get("/api/jobs/deadbeef/status").status_code == 404
    assert client.get("/api/jobs/deadbeef/report").status_code == 404
    assert client.get("/api/jobs/deadbeef/package").status_code == 404


def test_adapt_invalid_provider(client):
    r = client.post("/api/projects/adapt", json={"source": "demo", "provider": "skynet"})
    assert r.status_code == 400


def test_adapt_invalid_source(client):
    r = client.post("/api/projects/adapt", json={"source": "ghost"})
    assert r.status_code == 400


def test_adapt_dry_run_job_lifecycle(client):
    """Dry-run + deterministic => a full, offline job that produces a package."""
    r = client.post("/api/projects/adapt", json={
        "source": "demo",
        "targets": ["ubuntu", "fedora", "arch"],
        "provider": "deterministic",
        "dry_run": True,
        "max_iterations": 1,
    })
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    assert job_id

    # Status endpoint works immediately.
    s = client.get(f"/api/jobs/{job_id}/status")
    assert s.status_code == 200
    assert s.json()["job_id"] == job_id
    assert len(s.json()["stages"]) == 9

    job = _wait_for_job(client, job_id)
    assert job["status"] == "complete", job.get("error")

    # Build/test are skipped in dry-run; package stage completes.
    states = {st["id"]: st["state"] for st in job["stages"]}
    assert states["analyze"] == "complete"
    assert states["plan"] == "complete"
    assert states["adapt"] == "complete"
    assert states["build"] == "skipped"
    assert states["package"] == "complete"

    result = job["result"]
    assert result["ai_provider"] == "deterministic"
    assert isinstance(result["score_before"], float)
    assert isinstance(result["score_after"], float)
    assert result["adaptations_applied"] >= 0
    assert set(result["targets"].keys()) == {"ubuntu", "fedora", "arch"}
    # Dry-run cannot verify against Docker.
    assert all(t["status"] == "UNVERIFIED" for t in result["targets"].values())

    # Report endpoint returns the same result.
    rep = client.get(f"/api/jobs/{job_id}/report")
    assert rep.status_code == 200
    assert rep.json()["name"] == "portable-demo"

    # Package download works and yields a gzip tar.
    pkg = client.get(f"/api/jobs/{job_id}/package")
    assert pkg.status_code == 200
    assert pkg.content[:2] == b"\x1f\x8b"  # gzip magic
    assert "attachment" in pkg.headers.get("content-disposition", "")


def test_adapt_job_logs_are_safe(client):
    r = client.post("/api/projects/adapt", json={
        "source": "demo",
        "targets": ["ubuntu"],
        "provider": "deterministic",
        "dry_run": True,
        "max_iterations": 1,
    })
    job_id = r.json()["job_id"]
    job = _wait_for_job(client, job_id)
    blob = json.dumps(job["logs"])
    # No secrets, no IAM tokens, no authorization headers in the log stream.
    assert _FAKE_SECRET not in blob
    assert "authorization" not in blob.lower()
    assert "api_key" not in blob.lower()


# ---------------------------------------------------------------------------
# service-level unit checks (no HTTP)
# ---------------------------------------------------------------------------

def test_safe_member_name_rejects_traversal():
    assert services._is_safe_member_name("../x") is False
    assert services._is_safe_member_name("/abs/x") is False
    assert services._is_safe_member_name("C:\\Windows\\x") is False
    assert services._is_safe_member_name("src/app.py") is True


def test_resolve_source_demo():
    path = services.resolve_source("demo")
    assert path.is_dir()
    assert path.name == "portable-demo"
