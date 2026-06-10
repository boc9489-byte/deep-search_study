"""报告与证据相关接口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.repository import project_repo
from app.schemas.api import ReportResponse
from app.schemas.domain import Source

router = APIRouter(prefix="/research-projects", tags=["reports"])


@router.get("/{project_id}/reports/latest", response_model=ReportResponse)
async def get_latest_report(project_id: str) -> ReportResponse:
    proj = await project_repo.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    report = await project_repo.latest_report(project_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not ready")

    src_by_id: dict[str, Source] = {s.id: s for s in proj.sources}
    sources = [src_by_id[sid] for sid in report.source_ids if sid in src_by_id]
    return ReportResponse(
        project_id=project_id,
        report_id=report.id,
        version=report.version,
        title=report.title,
        html=report.html,
        sources=sources,
        created_at=report.created_at,
    )


@router.get("/{project_id}/evidence")
async def list_evidence(project_id: str, node_id: str | None = None):
    proj = await project_repo.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    items = proj.evidences
    if node_id:
        items = [e for e in items if e.node_id == node_id]
    return {"project_id": project_id, "count": len(items),
            "evidence": [e.model_dump(mode="json") for e in items]}
