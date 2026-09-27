"""
web/routes.py — FastAPI router exposing the Black Flag engine over HTTP.

All endpoints live under the /api prefix. The router is intentionally thin:
it validates input, delegates to web/services.py (which drives the existing
core engine), and shapes the response. No portability logic lives here.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import schemas, services

router = APIRouter(prefix="/api", tags=["black-flag"])


# ---------------------------------------------------------------------------
# Health & info
# ---------------------------------------------------------------------------

@router.get("/health", response_model=schemas.HealthResponse)
def get_health() -> dict:
    """Liveness probe. Returns status, version, and the active provider name."""
    return services.health()


@router.get("/info")
def get_info() -> dict:
    """Provider / Docker / Ollama / target status. Contains NO credentials."""
    try:
        return services.environment_info()
    except Exception as exc:  # pragma: no cover — defensive
        raise HTTPException(status_code=500, detail="Unable to collect environment info.") from exc


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@router.post("/projects/select", response_model=schemas.ProjectMeta)
def select_project(req: schemas.SelectRequest) -> dict:
    """Resolve lightweight metadata for a demo/uploaded project."""
    try:
        return services.project_metadata(req.source)
    except services.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/upload", response_model=schemas.UploadResponse)
async def upload_project(file: UploadFile = File(...)) -> dict:
    """Upload a .zip / .tar.gz project archive into an isolated workspace."""
    data = await file.read()
    try:
        return services.save_upload(file.filename or "upload.zip", data)
    except services.UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/analyze", response_model=schemas.AnalyzeResponse)
def analyze_project(req: schemas.AnalyzeRequest) -> dict:
    """Run static portability analysis (fast; no Docker)."""
    try:
        return services.analyze_source(req.source)
    except services.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/projects/adapt")
def adapt_project(req: schemas.AdaptRequest) -> dict:
    """Start a background adaptation job. Returns immediately with a job_id."""
    try:
        job_id = services.start_adapt_job(
            source=req.source,
            targets=req.targets,
            provider_name=req.provider,
            max_iterations=req.max_iterations,
            dry_run=req.dry_run,
        )
    except services.ServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_id": job_id, "status": "pending"}


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def _require_job(job_id: str) -> services.Job:
    job = services.job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.get("/jobs/{job_id}", response_model=schemas.JobResponse)
def get_job(job_id: str) -> dict:
    """Full job state: stages, logs, result, and error (if any)."""
    job = _require_job(job_id)
    return services.job_store.to_full_dict(job)


@router.get("/jobs/{job_id}/status", response_model=schemas.JobStatusResponse)
def get_job_status(job_id: str) -> dict:
    """Lightweight polling endpoint: status + stage states only."""
    job = _require_job(job_id)
    return services.job_store.to_status_dict(job)


@router.get("/jobs/{job_id}/report")
def get_job_report(job_id: str) -> dict:
    """Compatibility report (scores, targets, manifest). 409 until complete."""
    job = _require_job(job_id)
    if job.status == "failed":
        raise HTTPException(status_code=409, detail=job.error or "Job failed.")
    if not job.result:
        raise HTTPException(status_code=409, detail="Report is not ready yet.")
    return job.result


@router.get("/jobs/{job_id}/package")
def download_package(job_id: str) -> FileResponse:
    """Download the generated .bfpack archive."""
    job = _require_job(job_id)
    if not job.package_path:
        raise HTTPException(status_code=404, detail="Package is not available for this job.")
    path = Path(job.package_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Package file no longer exists on disk.")
    return FileResponse(
        path=str(path),
        media_type="application/gzip",
        filename=path.name,
    )
