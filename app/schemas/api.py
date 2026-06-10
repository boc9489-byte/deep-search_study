"""接口出入参（API contract）。对应 docs §5 接口设计。"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.domain import (
    OutlineNode,
    ProjectStatus,
    RegionScope,
    Source,
    TaskStatus,
    TaskType,
    TimeScope,
)


# --------- 创建研究项目 --------- #
class CreateProjectRequest(BaseModel):
    topic: str
    research_goal: str = ""
    target_audience: str = ""
    region_scope: RegionScope = RegionScope.CHINA
    time_scope: TimeScope = Field(default_factory=TimeScope)
    # 第一版不暴露 knowledge_mode：默认查内部库，由智能体判断是否进报告


class CreateProjectResponse(BaseModel):
    project_id: str
    initial_task_id: str
    initial_task_type: TaskType
    topic: str
    status: ProjectStatus
    next_step: str
    created_at: datetime


# --------- 获取大纲 --------- #
class OutlineResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    outline: list[OutlineNode]


# --------- 确认 / 修改大纲 --------- #
class UpdateOutlineRequest(BaseModel):
    action: str                          # confirm | revise
    revision_instruction: str | None = None


class ConfirmOutlineResponse(BaseModel):
    project_id: str
    status: ProjectStatus
    next_step: str


class ReviseOutlineResponse(BaseModel):
    project_id: str
    revision_task_id: str
    status: ProjectStatus
    next_step: str


# --------- 报告任务 --------- #
class CreateReportTaskRequest(BaseModel):
    user_instruction: str = ""


class CreateReportTaskResponse(BaseModel):
    task_id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus


# --------- 任务状态 --------- #
class TaskStatusResponse(BaseModel):
    task_id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus
    message: str = ""
    created_at: datetime
    updated_at: datetime


# --------- 报告 --------- #
class ReportResponse(BaseModel):
    project_id: str
    report_id: str
    version: int
    title: str
    html: str
    sources: list[Source]
    created_at: datetime
