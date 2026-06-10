"""报告与证据相关接口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.repository import project_repo
from app.schemas.api import ReportResponse
from app.schemas.domain import Source

router = APIRouter(prefix="/research-projects", tags=["reports"])


@router.get("/{project_id}/reports/latest", response_model=ReportResponse)
async def get_latest_report(project_id: str) -> ReportResponse:
    """获取项目最新报告。

    功能：
      返回最新报告版本的 HTML 正文和引用来源列表。

    输入输出：
      输入 project_id；输出 ReportResponse。

    实现说明：
      报告正文中的角标引用依赖 sources 顺序，接口按 report.source_ids 还原来源列表。
    """
    # Step 1: 校验项目存在。
    proj = await project_repo.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")

    # Step 2: 读取最新报告版本。阶段一只返回最新版本，生产可扩展版本列表。
    report = await project_repo.latest_report(project_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not ready")

    # Step 3: 根据 report.source_ids 按报告引用顺序还原 Source 对象。
    src_by_id: dict[str, Source] = {s.id: s for s in proj.sources}
    sources = [src_by_id[sid] for sid in report.source_ids if sid in src_by_id]

    # Step 4: 返回报告 HTML 和引用来源，前端可直接渲染或导出。
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
    """查询项目证据列表。

    功能：
      用于报告溯源、调试检索结果和分析某个大纲节点的证据质量。

    输入输出：
      输入 project_id，可选 node_id；输出证据数量和 Evidence JSON 列表。

    实现说明：
      node_id 过滤发生在内存列表上；生产环境应下推到数据库查询条件。
    """
    # Step 1: 校验项目存在。
    proj = await project_repo.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")

    # Step 2: 默认返回项目全部证据；如果传 node_id，则只看某个大纲节点证据。
    items = proj.evidences
    if node_id:
        items = [e for e in items if e.node_id == node_id]

    # Step 3: 用 JSON 模式序列化 Pydantic 对象，保证 datetime / enum 可输出。
    return {"project_id": project_id, "count": len(items),
            "evidence": [e.model_dump(mode="json") for e in items]}
