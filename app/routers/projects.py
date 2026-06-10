"""研究项目相关接口。路由只做：校验 → 调 repository/background → 组织响应。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import background as bg
from app.repository import project_repo, task_repo
from app.schemas.api import (
    ConfirmOutlineResponse,
    CreateProjectRequest,
    CreateProjectResponse,
    CreateReportTaskRequest,
    CreateReportTaskResponse,
    OutlineResponse,
    ReviseOutlineResponse,
    UpdateOutlineRequest,
)
from app.schemas.domain import ProjectStatus, TaskStatus, TaskType

router = APIRouter(prefix="/research-projects", tags=["projects"])


@router.post("", response_model=CreateProjectResponse)
async def create_project(req: CreateProjectRequest) -> CreateProjectResponse:
    proj = await project_repo.create(req)
    # 创建后自动启动大纲生成任务（前端无需单独调用）
    task = await task_repo.create(proj.id, TaskType.GENERATE_BRIEF)
    bg.spawn(bg.run_generate_brief(proj.id, task.id))
    return CreateProjectResponse(
        project_id=proj.id,
        initial_task_id=task.id,
        initial_task_type=TaskType.GENERATE_BRIEF,
        topic=proj.topic,
        status=ProjectStatus.BRIEF_GENERATING,
        next_step="wait_for_outline",
        created_at=proj.created_at,
    )


@router.get("/{project_id}/outline", response_model=OutlineResponse)
async def get_outline(project_id: str) -> OutlineResponse:
    proj = await project_repo.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    return OutlineResponse(project_id=proj.id, status=proj.status, outline=proj.outline)


@router.put("/{project_id}/outline")
async def update_outline(project_id: str, req: UpdateOutlineRequest):
    proj = await project_repo.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")

    if req.action == "confirm":
        await project_repo.set_status(project_id, ProjectStatus.OUTLINE_CONFIRMED)
        return ConfirmOutlineResponse(
            project_id=project_id,
            status=ProjectStatus.OUTLINE_CONFIRMED,
            next_step="generate_report",
        )

    if req.action == "revise":
        if not req.revision_instruction:
            raise HTTPException(status_code=422, detail="revision_instruction required")
        task = await task_repo.create(project_id, TaskType.REVISE_OUTLINE)
        await project_repo.set_status(project_id, ProjectStatus.OUTLINE_REVISING)
        bg.spawn(bg.run_revise_outline(project_id, task.id, req.revision_instruction))
        return ReviseOutlineResponse(
            project_id=project_id,
            revision_task_id=task.id,
            status=ProjectStatus.OUTLINE_REVISING,
            next_step="wait_for_outline",
        )

    raise HTTPException(status_code=422, detail="action must be confirm or revise")


@router.post("/{project_id}/report-tasks", response_model=CreateReportTaskResponse)
async def create_report_task(
    project_id: str, req: CreateReportTaskRequest
) -> CreateReportTaskResponse:
    proj = await project_repo.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    if proj.status not in (ProjectStatus.OUTLINE_CONFIRMED, ProjectStatus.REPORT_READY):
        raise HTTPException(status_code=409, detail=f"outline not confirmed (status={proj.status.value})")

    task = await task_repo.create(project_id, TaskType.GENERATE_REPORT)
    bg.spawn(bg.run_generate_report(project_id, task.id, req.user_instruction))
    return CreateReportTaskResponse(
        task_id=task.id,
        project_id=project_id,
        task_type=TaskType.GENERATE_REPORT,
        status=TaskStatus.QUEUED,
    )
