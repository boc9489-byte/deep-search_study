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
    """创建研究项目请求。

    功能：
      承接用户输入的研究主题、目标、受众和研究范围。

    实现说明：
      第一版不暴露 knowledge_mode，内部知识库是否进入报告由 RetrievalAgent 和
      EvidencePipeline 判断，避免把检索策略过早暴露给前端。
    """

    topic: str
    research_goal: str = ""
    target_audience: str = ""
    region_scope: RegionScope = RegionScope.CHINA
    time_scope: TimeScope = Field(default_factory=TimeScope)
    # 第一版不暴露 knowledge_mode：默认查内部库，由智能体判断是否进报告


class CreateProjectResponse(BaseModel):
    """创建研究项目响应。

    功能：
      返回项目 ID 和初始大纲生成任务 ID。

    实现说明：
      `next_step` 是给前端的流程提示，创建后通常为 `wait_for_outline`。
    """

    project_id: str
    initial_task_id: str
    initial_task_type: TaskType
    topic: str
    status: ProjectStatus
    next_step: str
    created_at: datetime


# --------- 获取大纲 --------- #
class OutlineResponse(BaseModel):
    """获取大纲响应。

    功能：
      返回当前项目状态和大纲树。

    实现说明：
      前端可根据 status 判断是展示加载态、可编辑大纲，还是允许确认。
    """

    project_id: str
    status: ProjectStatus
    outline: list[OutlineNode]


# --------- 确认 / 修改大纲 --------- #
class UpdateOutlineRequest(BaseModel):
    """确认或修改大纲请求。

    功能：
      用 `action` 表达用户动作：confirm 或 revise。

    实现说明：
      当 action=revise 时，必须提供 revision_instruction，由后台任务异步修改大纲。
    """

    action: str                          # confirm | revise
    revision_instruction: str | None = None


class ConfirmOutlineResponse(BaseModel):
    """确认大纲响应。

    功能：
      告诉前端大纲已确认，下一步可以提交报告任务。
    """

    project_id: str
    status: ProjectStatus
    next_step: str


class ReviseOutlineResponse(BaseModel):
    """修改大纲响应。

    功能：
      返回 revision_task_id，前端据此轮询修改任务。
    """

    project_id: str
    revision_task_id: str
    status: ProjectStatus
    next_step: str


# --------- 报告任务 --------- #
class CreateReportTaskRequest(BaseModel):
    """创建报告任务请求。

    功能：
      接收用户对报告风格、重点、输出偏好的补充说明。

    实现说明：
      阶段一将 user_instruction 放入 ResearchState；后续可参与 prompt 或报告模板选择。
    """

    user_instruction: str = ""


class CreateReportTaskResponse(BaseModel):
    """创建报告任务响应。

    功能：
      返回报告任务 ID 和初始任务状态。
    """

    task_id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus


# --------- 任务状态 --------- #
class TaskStatusResponse(BaseModel):
    """任务状态响应。

    功能：
      给前端轮询后台任务进度。

    实现说明：
      `message` 是面向用户的短进度文案；详细节点信息通过 `/trace` 获取。
    """

    task_id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus
    message: str = ""
    created_at: datetime
    updated_at: datetime


# --------- 报告 --------- #
class ReportResponse(BaseModel):
    """报告响应。

    功能：
      返回最新报告 HTML 与报告引用来源。

    实现说明：
      报告正文和 sources 分开返回，方便前端做引用悬浮、来源面板或导出。
    """

    project_id: str
    report_id: str
    version: int
    title: str
    html: str
    sources: list[Source]
    created_at: datetime
