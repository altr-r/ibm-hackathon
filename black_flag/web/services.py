"""
web/services.py — Orchestration layer for the Black Flag web application.

This module is the single place where the web API touches the core engine.
It reuses the EXISTING analyzer, adaptation engine, Docker build matrix, and
packager — it never re-implements portability logic. Both the CLI and the web
API therefore drive the same underlying functions.

Responsibilities:
  * Resolve a project source (built-in demo or a safely-extracted upload).
  * Run static analysis and serialize issues + score.
  * Report safe environment info (provider / Docker / Ollama) with NO secrets.
  * Run the full adapt pipeline as a background job, streaming pipeline stage
    states and console-style log lines that reflect REAL backend progress.
  * Maintain an in-process, thread-safe job registry (no Redis/Celery).
  * Accept uploaded .zip / .tar.gz archives with path-traversal protection,
    a size limit, and an isolated temporary workspace.

Uploaded code is NEVER executed on the host — it only enters the static
analyzer, and any build/test happens inside Docker via the existing matrix.
"""
from __future__ import annotations

import re
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

# black_flag/web/services.py -> parents[2] == repository root
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_PROJECT = _REPO_ROOT / "examples" / "portable-demo"

_VERSION = "0.1.0"

# Upload safeguards
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
_ALLOWED_ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz")

# The nine pipeline stages visualized in the UI (id, label).
PIPELINE_STAGES: list[tuple[str, str]] = [
    ("analyze", "Analyze"),
    ("plan", "AI Plan"),
    ("adapt", "Adapt"),
    ("build", "Build"),
    ("test", "Test"),
    ("diagnose", "Diagnose"),
    ("repair", "Repair"),
    ("verify", "Verify"),
    ("package", "Package"),
]

_MAX_LOG_LINES = 600


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ServiceError(Exception):
    """A user-facing, human-readable error (safe to return to the browser)."""


class UploadError(ServiceError):
    """Raised when an uploaded archive is invalid, unsafe, or too large."""


# ---------------------------------------------------------------------------
# Workspace helpers (persistent for the server lifetime)
# ---------------------------------------------------------------------------

def _workspace_root() -> Path:
    root = Path(tempfile.gettempdir()) / "blackflag-web"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Job registry (in-process, thread-safe)
# ---------------------------------------------------------------------------

@dataclass
class _StageState:
    id: str
    label: str
    state: str = "pending"  # pending | running | complete | failed | skipped


@dataclass
class Job:
    id: str
    kind: str
    status: str = "pending"  # pending | running | complete | failed
    stage: Optional[str] = None
    stages: list[_StageState] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)
    result: Optional[dict] = None
    error: Optional[str] = None
    package_path: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""


class JobStore:
    """A minimal in-process job registry guarded by a re-entrant lock."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    # -- lifecycle ---------------------------------------------------------
    def create(self, kind: str) -> Job:
        job_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        job = Job(
            id=job_id,
            kind=kind,
            stages=[_StageState(id=sid, label=label) for sid, label in PIPELINE_STAGES],
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def _touch(self, job: Job) -> None:
        job.updated_at = _now_iso()

    # -- mutations ---------------------------------------------------------
    def set_status(self, job_id: str, status: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = status
                self._touch(job)

    def set_current_stage(self, job_id: str, stage_id: Optional[str]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.stage = stage_id
                self._touch(job)

    def set_stage(self, job_id: str, stage_id: str, state: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for st in job.stages:
                if st.id == stage_id:
                    st.state = state
            self._touch(job)

    def add_log(self, job_id: str, message: str, level: str = "info") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.logs.append({"ts": _now_clock(), "level": level, "message": message})
            if len(job.logs) > _MAX_LOG_LINES:
                del job.logs[: len(job.logs) - _MAX_LOG_LINES]
            self._touch(job)

    def set_result(self, job_id: str, result: dict) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.result = result
                self._touch(job)

    def set_error(self, job_id: str, error: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.error = error
                self._touch(job)

    def set_package(self, job_id: str, path: Path) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.package_path = str(path)
                self._touch(job)

    # -- serialization -----------------------------------------------------
    def to_status_dict(self, job: Job) -> dict:
        with self._lock:
            return {
                "job_id": job.id,
                "kind": job.kind,
                "status": job.status,
                "stage": job.stage,
                "stages": [{"id": s.id, "label": s.label, "state": s.state} for s in job.stages],
                "created_at": job.created_at,
                "updated_at": job.updated_at,
            }

    def to_full_dict(self, job: Job) -> dict:
        base = self.to_status_dict(job)
        with self._lock:
            base["logs"] = list(job.logs)
            base["result"] = job.result
            base["error"] = job.error
        return base


job_store = JobStore()


# ---------------------------------------------------------------------------
# Upload registry
# ---------------------------------------------------------------------------

class UploadStore:
    def __init__(self) -> None:
        self._items: dict[str, dict] = {}
        self._lock = threading.RLock()

    def put(self, upload_id: str, record: dict) -> None:
        with self._lock:
            self._items[upload_id] = record

    def get(self, upload_id: str) -> Optional[dict]:
        with self._lock:
            return self._items.get(upload_id)


upload_store = UploadStore()


# ---------------------------------------------------------------------------
# Project resolution & metadata
# ---------------------------------------------------------------------------

def resolve_source(source: str) -> Path:
    """Map a source token ('demo' or an upload_id) to a project directory."""
    if not source or source.strip().lower() == "demo":
        if not DEMO_PROJECT.is_dir():
            raise ServiceError("Built-in demo project not found.")
        return DEMO_PROJECT
    record = upload_store.get(source.strip())
    if not record:
        raise ServiceError(f"Unknown project source: {source!r}. Upload it first.")
    path = Path(record["path"])
    if not path.is_dir():
        raise ServiceError("Uploaded project is no longer available on disk.")
    return path


_SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".pytest_cache", "node_modules",
    ".tox", ".venv", "venv", "env", "site-packages", "bf_scripts", "diffs",
}


def _iter_files(directory: Path):
    for path in directory.rglob("*"):
        if any(skip in path.parts for skip in _SKIP_DIR_NAMES):
            continue
        if path.is_file():
            yield path


def count_files(directory: Path) -> int:
    return sum(1 for _ in _iter_files(directory))


def detect_project_type(directory: Path) -> Optional[str]:
    """Best-effort project type detection using only file presence."""
    names = {p.name.lower() for p in _iter_files(directory)}
    suffixes = {p.suffix.lower() for p in _iter_files(directory)}
    if {"requirements.txt", "pyproject.toml", "setup.py"} & names:
        return "python"
    if {"makefile", "cmakelists.txt"} & names or (suffixes & {".c", ".cpp", ".h"}):
        return "c/c++"
    if suffixes & {".sh", ".bash"}:
        return "shell"
    return "generic"


def project_metadata(source: str) -> dict:
    src = resolve_source(source)
    return {
        "name": src.name,
        "file_count": count_files(src),
        "project_type": detect_project_type(src),
    }


def _issue_to_dict(issue) -> dict:
    return {
        "category": issue.category,
        "severity": issue.severity,
        "source_file": issue.source_file,
        "line": issue.line,
        "affected_targets": list(issue.affected_targets),
        "explanation": issue.explanation,
        "suggested_primitive": issue.suggested_primitive,
        "verification_method": issue.verification_method,
    }


def analyze_source(source: str) -> dict:
    """Run the existing static analyzer and return serialized results."""
    from black_flag.analyzer.runner import run_analysis, compute_score

    src = resolve_source(source)
    issues = run_analysis(src)
    score = compute_score(issues)
    meta = project_metadata(source)
    return {
        **meta,
        "score": score,
        "issue_count": len(issues),
        "issues": [_issue_to_dict(i) for i in issues],
    }


# ---------------------------------------------------------------------------
# Environment info (safe — never exposes secrets)
# ---------------------------------------------------------------------------

def _docker_version() -> Optional[str]:
    import subprocess

    docker = shutil.which("docker")
    if docker is None:
        return None
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def environment_info() -> dict:
    """Return provider / Docker / Ollama status. Contains NO credentials."""
    from black_flag.ai.factory import get_provider
    from black_flag.build.container import is_docker_available
    from black_flag.runtime.detector import get_all_adapters, detect_distro_id, detect_adapter

    provider = get_provider()
    model = getattr(provider, "model_id", "") or ""
    provider_info: dict[str, Any] = {
        "name": provider.name,
        "model": model,
        "fallback": "deterministic",
    }
    if provider.name == "ollama":
        provider_info["endpoint"] = f"{getattr(provider, 'base_url', '')}/api/chat"
        provider_info["auth"] = "local"
        provider_info["available"] = bool(provider.is_available())
    elif provider.name == "watsonx":
        provider_info["endpoint"] = f"{getattr(provider, 'base_url', '')}/text/chat"
        provider_info["auth"] = "IAM access token"
        provider_info["available"] = bool(provider.is_configured())
    else:
        provider_info["endpoint"] = ""
        provider_info["auth"] = "none"
        provider_info["available"] = True

    # Always probe Ollama so the UI can show its status independently.
    from black_flag.ai.ollama import OllamaProvider

    _ollama = OllamaProvider()
    ollama_info = {
        "base_url": _ollama.base_url,
        "model": _ollama.model_id,
        "available": bool(_ollama.is_available()),
    }

    docker_version = _docker_version()
    targets = [
        {
            "id": a.distro_id,
            "display_name": a.display_name,
            "docker_image": a.docker_image,
            "package_manager": a.package_manager(),
        }
        for a in get_all_adapters()
    ]

    active = detect_adapter()
    return {
        "version": _VERSION,
        "provider": provider_info,
        "docker": {"available": is_docker_available(), "version": docker_version},
        "ollama": ollama_info,
        "targets": targets,
        "host_distro": {
            "id": detect_distro_id(),
            "display_name": active.display_name if active else None,
            "supported": active is not None,
        },
    }


def health() -> dict:
    from black_flag.ai.factory import get_provider

    return {
        "status": "ok",
        "version": _VERSION,
        "provider": get_provider().name,
    }


# ---------------------------------------------------------------------------
# Safe archive extraction & upload handling
# ---------------------------------------------------------------------------

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _is_safe_member_name(name: str) -> bool:
    if not name:
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return False
    if _WINDOWS_DRIVE_RE.match(normalized):
        return False
    parts = normalized.split("/")
    if any(part == ".." for part in parts):
        return False
    return True


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _extract_zip(archive_path: Path, dest: Path) -> None:
    import zipfile

    if not zipfile.is_zipfile(archive_path):
        raise UploadError("File is not a valid .zip archive.")
    with zipfile.ZipFile(archive_path) as zf:
        for info in zf.infolist():
            if not _is_safe_member_name(info.filename):
                raise UploadError(f"Unsafe path in archive: {info.filename!r}")
            if not _is_within(dest, dest / info.filename):
                raise UploadError("Path traversal detected in archive.")
        zf.extractall(dest)


def _extract_tar(archive_path: Path, dest: Path) -> None:
    import tarfile

    try:
        tf = tarfile.open(archive_path, "r:*")
    except (tarfile.TarError, OSError):
        raise UploadError("File is not a valid .tar.gz archive.")
    with tf:
        members = tf.getmembers()
        for m in members:
            if not _is_safe_member_name(m.name):
                raise UploadError(f"Unsafe path in archive: {m.name!r}")
            if m.issym() or m.islnk():
                raise UploadError("Symbolic/hard links are not allowed in uploads.")
            if not _is_within(dest, dest / m.name):
                raise UploadError("Path traversal detected in archive.")
        try:
            tf.extractall(dest, members=members, filter="data")  # Python 3.12+
        except TypeError:
            tf.extractall(dest, members=members)  # Python 3.11 fallback


def _detect_project_root(extract_dir: Path) -> Path:
    """If the archive unpacked into a single top-level folder, descend into it."""
    entries = [p for p in extract_dir.iterdir()]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return extract_dir


def save_upload(filename: str, data: bytes) -> dict:
    """Validate + extract an uploaded archive into an isolated workspace."""
    if data is None:
        raise UploadError("Empty upload.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadError(
            f"Upload exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
        )

    lower = (filename or "").lower()
    if not lower.endswith(_ALLOWED_ARCHIVE_SUFFIXES):
        raise UploadError("Only .zip and .tar.gz archives are supported.")

    upload_id = uuid.uuid4().hex[:12]
    root = _workspace_root() / "uploads" / upload_id
    extract_dir = root / "src"
    extract_dir.mkdir(parents=True, exist_ok=True)

    archive_path = root / Path(filename).name
    archive_path.write_bytes(data)

    try:
        if lower.endswith(".zip"):
            _extract_zip(archive_path, extract_dir)
        else:
            _extract_tar(archive_path, extract_dir)
        project_dir = _detect_project_root(extract_dir)
        file_count = count_files(project_dir)
        if file_count == 0:
            raise UploadError("Archive contained no usable files.")
    except UploadError:
        shutil.rmtree(root, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise UploadError("Archive could not be extracted (it may be corrupted).")

    record = {
        "path": str(project_dir),
        "root": str(root),
        "name": project_dir.name,
    }
    upload_store.put(upload_id, record)

    return {
        "upload_id": upload_id,
        "name": project_dir.name,
        "file_count": file_count,
        "project_type": detect_project_type(project_dir),
    }


# ---------------------------------------------------------------------------
# Adapt pipeline (background job)
# ---------------------------------------------------------------------------

def _log_ai_call(provider, mode_label: str, log: Callable[[str, str], None]) -> None:
    """Emit a safe SUCCESS/FAILED line for the last AI call (no secrets)."""
    lc = getattr(provider, "last_call", None)
    if not isinstance(lc, dict):
        return
    status = lc.get("status")
    detail = lc.get("detail") or ""
    label = str(getattr(provider, "name", "ai")).capitalize()
    if status == "success":
        log(f"{label} request: SUCCESS ({mode_label})", "info")
    elif status in ("failed", "skipped"):
        suffix = f" ({detail})" if detail else ""
        log(f"{label} request: FAILED{suffix} — fallback: deterministic ({mode_label})", "warn")


def _run_adapt_pipeline(
    job_id: str,
    src: Path,
    targets: list[str],
    provider_name: Optional[str],
    max_iterations: int,
    dry_run: bool,
) -> None:
    """Execute the full adapt loop in a background thread, updating the job."""
    from black_flag.analyzer.runner import run_analysis, compute_score
    from black_flag.adaptation_engine.planner import validate_plan
    from black_flag.adaptation_engine.patcher import apply_plan, apply_primitive, _find_matching_file
    from black_flag.adaptation_engine.rollback import create_working_tree, reapply_diffs
    from black_flag.ai.factory import get_provider
    from black_flag.build.container import is_docker_available
    from black_flag.build.script_gen import generate_scripts
    from black_flag.build.matrix import run_matrix, matrix_to_target_results
    from black_flag.core.cache import compute_source_hash, cache_set
    from black_flag.core.manifest import build_manifest, manifest_to_dict
    from black_flag.core.types import TargetResult
    from black_flag.packager.packer import create_bfpack
    from black_flag.primitives.catalog import list_primitive_ids, get_primitive
    from black_flag.primitives.base import PrimitiveNotApplicable

    store = job_store

    def log(message: str, level: str = "info") -> None:
        store.add_log(job_id, message, level)

    def set_stage(stage_id: str, state: str) -> None:
        store.set_stage(job_id, stage_id, state)

    def enter(stage_id: str) -> None:
        store.set_current_stage(job_id, stage_id)
        set_stage(stage_id, "running")

    working_tree: Optional[Path] = None
    tmp_parent: Optional[Path] = None
    try:
        store.set_status(job_id, "running")

        provider = get_provider(provider=provider_name)
        catalog = list_primitive_ids()
        model = getattr(provider, "model_id", "")
        log(f"AI provider: {provider.name}" + (f" (model={model})" if model else ""))

        docker_ok = is_docker_available()
        log(f"Docker: {'connected' if docker_ok else 'unavailable'}")
        effective_dry = dry_run or not docker_ok
        if not docker_ok:
            log("Docker unavailable — build/test skipped; results will be UNVERIFIED.", "warn")
        log(f"Targets: {', '.join(targets)}")

        # --- Stage 1: Analyze -------------------------------------------
        enter("analyze")
        issues_before = run_analysis(src)
        score_before = compute_score(issues_before)
        log(f"Analyze started")
        log(f"{len(issues_before)} portability issues detected")
        log(f"Initial portability score: {score_before:.2f}")
        set_stage("analyze", "complete")

        # --- Stage 2: AI Plan -------------------------------------------
        enter("plan")
        raw_plan = provider.plan_adaptations(issues_before, catalog, targets)
        _log_ai_call(provider, "Mode A: planning", log)
        plan, plan_warnings = validate_plan(raw_plan)
        for w in plan_warnings:
            log(w, "warn")
        log(f"Adaptation plan validated — {len(plan.applications)} primitive(s) selected")
        set_stage("plan", "complete")

        # --- Working tree (temp copy) ------------------------------------
        working_tree = create_working_tree(src)
        tmp_parent = working_tree.parent

        # --- Stage 3: Adapt ---------------------------------------------
        enter("adapt")
        applied_diffs, patch_warnings = apply_plan(
            plan_applications=plan.applications,
            working_tree=working_tree,
            start_sequence=1,
            iteration=0,
            issues=issues_before,
        )
        for w in patch_warnings:
            log(w, "warn")
        log("Applying bounded primitives")
        for d in applied_diffs:
            log(f"  ✓ {d.sequence:03d}-{d.primitive_id} → {d.target_file}")
        log(f"{len(applied_diffs)} adaptation(s) applied")
        set_stage("adapt", "complete")

        best_matrix = None
        unsupported: set[str] = set()
        ran_diagnose = False
        ran_repair = False

        if effective_dry:
            for sid in ("build", "test", "diagnose", "repair", "verify"):
                set_stage(sid, "skipped")
            target_results = {t: TargetResult(status="UNVERIFIED") for t in targets}
        else:
            # --- Stages 4/5/6/7: Build / Test / Diagnose / Repair loop ---
            for iteration in range(max_iterations):
                enter("build")
                set_stage("test", "running")
                log(f"Running build matrix (iteration {iteration + 1}/{max_iterations})")
                generate_scripts(working_tree)

                active_targets = [t for t in targets if t not in unsupported]

                def _on_result(r) -> None:
                    if r.exit_code == 0:
                        log(f"{r.distro} {r.stage}: PASS ({r.elapsed_s:.1f}s)", "info")
                    elif r.exit_code == -1:
                        log(f"{r.distro} {r.stage}: skipped", "warn")
                    else:
                        log(f"{r.distro} {r.stage}: FAIL ({r.elapsed_s:.1f}s)", "error")

                matrix = run_matrix(
                    working_tree=working_tree,
                    targets=active_targets,
                    iteration=iteration,
                    progress_cb=_on_result,
                )

                if matrix.all_passed():
                    set_stage("build", "complete")
                    set_stage("test", "complete")
                    best_matrix = matrix
                    log("All targets compatible!", "info")
                    break

                # Track best-so-far
                if best_matrix is None or sum(
                    1 for r in matrix.results if r.exit_code == 0
                ) >= sum(1 for r in best_matrix.results if r.exit_code == 0):
                    best_matrix = matrix

                if iteration < max_iterations - 1:
                    failures = [
                        fr for fr in matrix.failed_results()
                        if fr.distro not in unsupported and fr.exit_code != -1
                    ]
                    if failures:
                        enter("diagnose")
                        ran_diagnose = True
                        log("Diagnosing failures", "info")
                        repaired_any = False
                        for failed_r in failures:
                            repair = provider.diagnose_failure(failed_r, applied_diffs, catalog)
                            _log_ai_call(
                                provider,
                                f"Mode B: diagnose {failed_r.distro}/{failed_r.stage}",
                                log,
                            )
                            if repair.action == "give_up":
                                log(
                                    f"Give up {failed_r.distro}/{failed_r.stage}: "
                                    f"{repair.rationale[:80]}",
                                    "warn",
                                )
                                unsupported.add(failed_r.distro)
                                continue
                            enter("repair")
                            ran_repair = True
                            prim = get_primitive(repair.primitive_id)
                            target_file = repair.params.get("file") or _find_matching_file(prim, working_tree)
                            if not target_file:
                                log(f"No target file for {repair.primitive_id}", "warn")
                                continue
                            try:
                                new_diff = apply_primitive(
                                    primitive=prim,
                                    params=repair.params,
                                    target_file_rel=target_file,
                                    working_tree=working_tree,
                                    sequence=len(applied_diffs) + 1,
                                    rationale=repair.rationale,
                                    affected_targets=[failed_r.distro],
                                    iteration=iteration + 1,
                                )
                                applied_diffs.append(new_diff)
                                log(f"  ✓ repair {new_diff.sequence:03d}-{new_diff.primitive_id} → {new_diff.target_file}")
                                repaired_any = True
                            except (PrimitiveNotApplicable, FileNotFoundError) as e:
                                log(f"Repair skipped: {e}", "warn")
                        if not repaired_any:
                            log("No repairs applied — stopping repair loop.", "warn")
                            break
                    else:
                        break
                # else: last iteration, loop ends

            # Finalize build/test stage states from the best matrix
            if best_matrix is not None:
                build_ok = all(
                    best_matrix.passed(t) or t in unsupported for t in targets
                )
                set_stage("build", "complete" if build_ok else "failed")
                set_stage("test", "complete" if build_ok else "failed")
            else:
                set_stage("build", "failed")
                set_stage("test", "failed")
            if not ran_diagnose:
                set_stage("diagnose", "skipped")
            else:
                set_stage("diagnose", "complete")
            if not ran_repair:
                set_stage("repair", "skipped")
            else:
                set_stage("repair", "complete")

            enter("verify")
            target_results = matrix_to_target_results(best_matrix, targets) if best_matrix else {
                t: TargetResult(status="UNVERIFIED") for t in targets
            }
            for t in unsupported:
                target_results[t] = TargetResult(status="UNSUPPORTED")

        # --- Stage 8: Verify (final scoring) -----------------------------
        if not effective_dry:
            pass  # verify stage already entered above
        else:
            enter("verify")
        issues_after = run_analysis(working_tree)
        score_after = compute_score(issues_after)
        for t in targets:
            tr = target_results.get(t)
            log(f"{t}: {tr.status if tr else 'UNVERIFIED'}", "info")
        log(f"Final portability score: {score_after:.2f}")

        cache_key = compute_source_hash(src)
        passed = [t for t in targets if target_results.get(t) and target_results[t].status == "COMPATIBLE"]
        failed = [t for t in targets if target_results.get(t) and target_results[t].status in ("FAILED", "UNSUPPORTED")]
        ai_summary = provider.summarize(
            issues=issues_after,
            applied_diffs=applied_diffs,
            passed_targets=passed,
            failed_targets=failed,
        )
        _log_ai_call(provider, "summarize", log)

        manifest = build_manifest(
            name=src.name,
            version=_VERSION,
            description="Portable package produced by the Black Flag web pipeline",
            issues_before=issues_before,
            issues_after=issues_after,
            applied_diffs=applied_diffs,
            target_results=target_results,
            ai_summary=ai_summary,
            ai_provider=provider.name,
            cache_key=cache_key,
            verified=not effective_dry,
        )
        set_stage("verify", "complete")

        # --- Stage 9: Package -------------------------------------------
        enter("package")
        generate_scripts(working_tree)
        out_dir = _workspace_root() / "jobs" / job_id
        out_dir.mkdir(parents=True, exist_ok=True)
        archive = create_bfpack(working_tree=working_tree, manifest=manifest, output_dir=out_dir)

        try:
            cache_set(cache_key, {
                "name": src.name,
                "score": score_after,
                "passed_targets": passed,
                "archive": str(archive),
            })
        except Exception:
            pass

        size_kb = archive.stat().st_size // 1024
        log(f"Package created: {archive.name} ({size_kb} KB)")
        set_stage("package", "complete")
        store.set_package(job_id, archive)

        store.set_result(job_id, {
            "name": manifest.name,
            "version": manifest.version,
            "score_before": manifest.portability_score_before,
            "score_after": manifest.portability_score,
            "adaptations_applied": len(manifest.applied_adaptations),
            "issues_before_count": len(issues_before),
            "issues_after_count": len(issues_after),
            "targets": {
                t: {
                    "status": (target_results.get(t).status if target_results.get(t) else "UNVERIFIED"),
                    "prepare": (target_results.get(t).prepare if target_results.get(t) else None),
                    "build": (target_results.get(t).build if target_results.get(t) else None),
                    "test": (target_results.get(t).test if target_results.get(t) else None),
                }
                for t in targets
            },
            "ai_provider": manifest.ai_provider,
            "ai_summary": manifest.ai_summary,
            "verified": manifest.verified,
            "package": {
                "name": archive.name,
                "size_kb": size_kb,
                "download_url": f"/api/jobs/{job_id}/package",
            },
            "manifest": manifest_to_dict(manifest),
        })

        store.set_current_stage(job_id, None)
        store.set_status(job_id, "complete")
        log("Job complete", "info")

    except Exception as exc:  # noqa: BLE001 — surface a safe message, keep traceback in server log
        import traceback

        # Keep the detailed traceback server-side only; never send it to the browser.
        traceback.print_exc()
        safe_msg = str(exc) or exc.__class__.__name__
        # Redact anything that looks like a filesystem temp path noise; keep it short.
        safe_msg = safe_msg.splitlines()[0][:300] if safe_msg else "Unexpected error"
        log(f"Job failed: {safe_msg}", "error")
        store.set_error(job_id, safe_msg)
        # Mark the currently-running stage as failed.
        job = store.get(job_id)
        if job:
            for st in job.stages:
                if st.state == "running":
                    st.state = "failed"
        store.set_current_stage(job_id, None)
        store.set_status(job_id, "failed")
    finally:
        if tmp_parent is not None:
            shutil.rmtree(tmp_parent, ignore_errors=True)


def start_adapt_job(
    source: str,
    targets: Optional[list[str]] = None,
    provider_name: Optional[str] = None,
    max_iterations: int = 3,
    dry_run: bool = False,
) -> str:
    """Validate input, register a job, and launch the pipeline in a thread."""
    from .schemas import VALID_TARGETS

    src = resolve_source(source)  # raises ServiceError for unknown source

    if targets:
        cleaned: list[str] = []
        for t in targets:
            tl = str(t).strip().lower()
            if tl in VALID_TARGETS and tl not in cleaned:
                cleaned.append(tl)
        targets = cleaned
    else:
        targets = list(VALID_TARGETS)
    if not targets:
        raise ServiceError("At least one valid target distro is required.")

    if provider_name is not None:
        pn = str(provider_name).strip().lower()
        if pn and pn not in ("deterministic", "watsonx", "ollama"):
            raise ServiceError(f"Unknown AI provider: {provider_name!r}")
        provider_name = pn or None

    job = job_store.create(kind="adapt")
    job_store.add_log(job.id, f"Job created for project '{src.name}'")

    thread = threading.Thread(
        target=_run_adapt_pipeline,
        args=(job.id, src, targets, provider_name, int(max_iterations), bool(dry_run)),
        name=f"bf-adapt-{job.id}",
        daemon=True,
    )
    thread.start()
    return job.id
