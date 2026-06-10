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
    """创建研究项目并自动投递大纲生成任务。

    功能：
      接收用户研究主题，创建项目记录，并启动 `generate_research_brief` 后台任务。

    输入输出：
      输入 CreateProjectRequest；输出 project_id、initial_task_id 和下一步提示。

    实现说明：
      HTTP 请求不等待大纲生成完成；前端用 initial_task_id 轮询任务状态。
    """
    # Step 1: 保存研究项目基础信息，初始状态为 brief_generating。
    proj = await project_repo.create(req)

    # Step 2: 创建大纲生成任务。创建项目与生成大纲拆成两个对象，
    # 这样前端可以通过 task_id 轮询长任务状态。
    task = await task_repo.create(proj.id, TaskType.GENERATE_BRIEF)

    # Step 3: 投递后台任务。HTTP 请求立即返回，不同步等待大纲生成完成。
    bg.spawn(bg.run_generate_brief(proj.id, task.id))

    # Step 4: 返回 project_id + initial_task_id，告诉前端下一步 wait_for_outline。
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
    """获取项目当前研究大纲。

    功能：
      供前端展示、编辑或确认大纲。

    输入输出：
      输入 project_id；输出项目状态和 OutlineNode 列表。

    实现说明：
      大纲可能为空或正在修改，调用方需要结合 status 判断 UI 状态。
    """
    # Step 1: 根据 project_id 读取项目。
    proj = await project_repo.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")

    # Step 2: 返回当前大纲和项目状态。状态可能是 outline_ready / outline_revising 等。
    return OutlineResponse(project_id=proj.id, status=proj.status, outline=proj.outline)


@router.put("/{project_id}/outline")
async def update_outline(project_id: str, req: UpdateOutlineRequest):
    """确认或修改研究大纲。

    功能：
      支持 `confirm` 直接确认大纲，或 `revise` 按自然语言异步修改大纲。

    输入输出：
      输入 project_id 和 UpdateOutlineRequest；输出确认响应或修改任务响应。

    实现说明：
      confirm 是同步状态更新；revise 会创建后台任务并进入 outline_revising。
    """
    # Step 1: 校验项目存在。
    proj = await project_repo.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")

    # Step 2A: 用户确认大纲。确认后才允许提交报告生成任务。
    if req.action == "confirm":
        await project_repo.set_status(project_id, ProjectStatus.OUTLINE_CONFIRMED)
        return ConfirmOutlineResponse(
            project_id=project_id,
            status=ProjectStatus.OUTLINE_CONFIRMED,
            next_step="generate_report",
        )

    # Step 2B: 用户要求修改大纲。创建 revision task，由后台异步执行。
    if req.action == "revise":
        if not req.revision_instruction:
            raise HTTPException(status_code=422, detail="revision_instruction required")
        task = await task_repo.create(project_id, TaskType.REVISE_OUTLINE)

        # Step 3B: 项目进入 outline_revising，前端等待任务完成后重新 GET outline。
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
    """创建研究报告生成任务。

    功能：
      在大纲确认后投递长耗时研究工作流。

    输入输出：
      输入 project_id 和用户补充说明；输出 report task_id。

    实现说明：
      只允许 `outline_confirmed` 或已有报告的项目再次生成报告，避免未确认大纲进入研究。
    """
    # Step 1: 校验项目存在。
    proj = await project_repo.get(project_id)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")

    # Step 2: 校验状态。只有大纲确认后，研究任务才有稳定输入。
    if proj.status not in (ProjectStatus.OUTLINE_CONFIRMED, ProjectStatus.REPORT_READY):
        raise HTTPException(status_code=409, detail=f"outline not confirmed (status={proj.status.value})")

    # Step 3: 创建报告任务，并投递后台研究工作流。
    task = await task_repo.create(project_id, TaskType.GENERATE_REPORT)
    bg.spawn(bg.run_generate_report(project_id, task.id, req.user_instruction))

    # Step 4: 返回 task_id，前端通过 /tasks/{task_id} 轮询。
    return CreateReportTaskResponse(
        task_id=task.id,
        project_id=project_id,
        task_type=TaskType.GENERATE_REPORT,
        status=TaskStatus.QUEUED,
    )
