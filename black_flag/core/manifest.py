"""
core/manifest.py — CompatibilityManifest builder and JSON serialization.

Provides:
  - build_manifest(): construct a CompatibilityManifest from analysis + build results
  - manifest_to_dict(): serialize to a JSON-serializable dict
  - manifest_from_dict(): deserialize from a dict
  - write_manifest(): write JSON to a file path
  - read_manifest(): read JSON from a file path
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from black_flag.core.types import (
    AppliedDiff,
    CompatibilityManifest,
    PortabilityIssue,
    TargetResult,
)
from black_flag.analyzer.runner import compute_score


def build_manifest(
    *,
    name: str,
    version: str,
    description: str,
    issues_before: list[PortabilityIssue],
    issues_after: list[PortabilityIssue],
    applied_diffs: list[AppliedDiff],
    target_results: dict[str, TargetResult],
    ai_summary: str,
    ai_provider: str,
    cache_key: str,
    verified: bool,
) -> CompatibilityManifest:
    """Construct a CompatibilityManifest from the outputs of the adaptation loop."""
    return CompatibilityManifest(
        name=name,
        version=version,
        description=description,
        portability_score=compute_score(issues_after),
        portability_score_before=compute_score(issues_before),
        verified=verified,
        targets=target_results,
        applied_adaptations=applied_diffs,
        issues=issues_after,
        ai_summary=ai_summary,
        ai_provider=ai_provider,
        compatibility_cache_key=cache_key,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _issue_to_dict(issue: PortabilityIssue) -> dict:
    return {
        "category": issue.category,
        "severity": issue.severity,
        "source_file": issue.source_file,
        "line": issue.line,
        "affected_targets": issue.affected_targets,
        "explanation": issue.explanation,
        "suggested_primitive": issue.suggested_primitive,
        "verification_method": issue.verification_method,
    }


def _diff_to_dict(diff: AppliedDiff) -> dict:
    return {
        "sequence": diff.sequence,
        "primitive_id": diff.primitive_id,
        "params": diff.params,
        "target_file": diff.target_file,
        "diff_text": diff.diff_text,
        "reason": diff.reason,
        "affected_targets": diff.affected_targets,
        "verification_method": diff.verification_method,
        "iteration": diff.iteration,
    }


def _target_result_to_dict(tr: TargetResult) -> dict:
    return {
        "status": tr.status,
        "prepare": tr.prepare,
        "build": tr.build,
        "test": tr.test,
    }


def manifest_to_dict(m: CompatibilityManifest) -> dict:
    """Convert a CompatibilityManifest to a JSON-serializable dict."""
    return {
        "name": m.name,
        "version": m.version,
        "description": m.description,
        "portability_score": m.portability_score,
        "portability_score_before": m.portability_score_before,
        "verified": m.verified,
        "targets": {k: _target_result_to_dict(v) for k, v in m.targets.items()},
        "applied_adaptations": [_diff_to_dict(d) for d in m.applied_adaptations],
        "issues": [_issue_to_dict(i) for i in m.issues],
        "ai_summary": m.ai_summary,
        "ai_provider": m.ai_provider,
        "compatibility_cache_key": m.compatibility_cache_key,
        "created_at": m.created_at,
    }


def manifest_from_dict(d: dict) -> CompatibilityManifest:
    """Reconstruct a CompatibilityManifest from a previously serialized dict."""
    from black_flag.core.types import AppliedDiff, PortabilityIssue, TargetResult

    issues = [
        PortabilityIssue(
            category=i["category"],
            severity=i["severity"],
            source_file=i["source_file"],
            line=i["line"],
            affected_targets=i["affected_targets"],
            explanation=i["explanation"],
            suggested_primitive=i.get("suggested_primitive"),
            verification_method=i["verification_method"],
        )
        for i in d.get("issues", [])
    ]

    diffs = [
        AppliedDiff(
            sequence=ad["sequence"],
            primitive_id=ad["primitive_id"],
            params=ad["params"],
            target_file=ad["target_file"],
            diff_text=ad["diff_text"],
            reason=ad["reason"],
            affected_targets=ad["affected_targets"],
            verification_method=ad["verification_method"],
            iteration=ad["iteration"],
        )
        for ad in d.get("applied_adaptations", [])
    ]

    targets = {
        k: TargetResult(
            status=v["status"],
            prepare=v.get("prepare"),
            build=v.get("build"),
            test=v.get("test"),
        )
        for k, v in d.get("targets", {}).items()
    }

    return CompatibilityManifest(
        name=d["name"],
        version=d["version"],
        description=d.get("description", ""),
        portability_score=d.get("portability_score", 0.0),
        portability_score_before=d.get("portability_score_before", 0.0),
        verified=d.get("verified", False),
        targets=targets,
        applied_adaptations=diffs,
        issues=issues,
        ai_summary=d.get("ai_summary", ""),
        ai_provider=d.get("ai_provider", "deterministic"),
        compatibility_cache_key=d.get("compatibility_cache_key", ""),
        created_at=d.get("created_at", ""),
    )


def write_manifest(manifest: CompatibilityManifest, path: Path) -> None:
    """Write manifest as pretty-printed JSON to *path*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = manifest_to_dict(manifest)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_manifest(path: Path) -> CompatibilityManifest:
    """Read a manifest JSON file and return a CompatibilityManifest."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return manifest_from_dict(data)
