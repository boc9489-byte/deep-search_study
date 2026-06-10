"""数据访问层（repository）。

隔离存储细节：路由/后台只依赖 repository 方法，不关心底层是内存还是 Postgres。
本期为线程/协程安全的内存实现，生产替换为 SQLAlchemy/SQLModel + PostgreSQL，
向量数据走 pgvector/Milvus，报告原文走对象存储。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.schemas.domain import (
    Evidence,
    FactCard,
    InsightCard,
    OutlineNode,
    ProjectStatus,
    Report,
    ResearchBrief,
    Source,
    TaskStatus,
    TaskType,
    TraceEvent,
    new_id,
    utcnow,
)


# --------------------------------------------------------------------------- #
# 内存实体
# --------------------------------------------------------------------------- #
@dataclass
class ProjectRecord:
    id: str
    topic: str
    research_goal: str
    target_audience: str
    region_scope: str
    time_scope: dict
    status: ProjectStatus
    created_at: object = field(default_factory=utcnow)
    brief: ResearchBrief | None = None
    outline: list[OutlineNode] = field(default_factory=list)
    # 研究产物
    sources: list[Source] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    facts: list[FactCard] = field(default_factory=list)
    insights: list[InsightCard] = field(default_factory=list)
    reports: list[Report] = field(default_factory=list)


@dataclass
class TaskRecord:
    id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus = TaskStatus.QUEUED
    message: str = ""
    created_at: object = field(default_factory=utcnow)
    updated_at: object = field(default_factory=utcnow)
    trace: list[TraceEvent] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 内存存储（单例）
# --------------------------------------------------------------------------- #
class _Store:
    def __init__(self) -> None:
        self.projects: dict[str, ProjectRecord] = {}
        self.tasks: dict[str, TaskRecord] = {}
        self.lock = asyncio.Lock()


store = _Store()


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
class ProjectRepository:
    async def create(self, req) -> ProjectRecord:
        async with store.lock:
            rec = ProjectRecord(
                id=new_id("proj"),
                topic=req.topic,
                research_goal=req.research_goal,
                target_audience=req.target_audience,
                region_scope=req.region_scope.value,
                time_scope=req.time_scope.model_dump(),
                status=ProjectStatus.BRIEF_GENERATING,
            )
            store.projects[rec.id] = rec
            return rec

    async def get(self, project_id: str) -> ProjectRecord | None:
        return store.projects.get(project_id)

    async def set_status(self, project_id: str, status: ProjectStatus) -> None:
        async with store.lock:
            if project_id in store.projects:
                store.projects[project_id].status = status

    async def save_brief_and_outline(
        self, project_id: str, brief: ResearchBrief, outline: list[OutlineNode]
    ) -> None:
        async with store.lock:
            rec = store.projects[project_id]
            rec.brief = brief
            rec.outline = outline
            rec.status = ProjectStatus.OUTLINE_READY

    async def save_outline(self, project_id: str, outline: list[OutlineNode]) -> None:
        async with store.lock:
            rec = store.projects[project_id]
            rec.outline = outline
            rec.status = ProjectStatus.OUTLINE_READY

    async def save_research_outputs(
        self,
        project_id: str,
        *,
        sources: list[Source],
        evidences: list[Evidence],
        facts: list[FactCard],
        insights: list[InsightCard],
        report: Report,
    ) -> None:
        async with store.lock:
            rec = store.projects[project_id]
            rec.sources = sources
            rec.evidences = evidences
            rec.facts = facts
            rec.insights = insights
            report.version = len(rec.reports) + 1
            rec.reports.append(report)
            rec.status = ProjectStatus.REPORT_READY

    async def latest_report(self, project_id: str) -> Report | None:
        rec = store.projects.get(project_id)
        if not rec or not rec.reports:
            return None
        return rec.reports[-1]


class TaskRepository:
    async def create(self, project_id: str, task_type: TaskType) -> TaskRecord:
        async with store.lock:
            rec = TaskRecord(id=new_id("task"), project_id=project_id, task_type=task_type)
            store.tasks[rec.id] = rec
            return rec

    async def get(self, task_id: str) -> TaskRecord | None:
        return store.tasks.get(task_id)

    async def update(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        message: str | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> None:
        async with store.lock:
            rec = store.tasks.get(task_id)
            if not rec:
                return
            if status is not None:
                rec.status = status
            if message is not None:
                rec.message = message
            if trace:
                rec.trace.extend(trace)
            rec.updated_at = utcnow()


project_repo = ProjectRepository()
task_repo = TaskRepository()


async def reset_repositories_for_tests() -> None:
    """清空内存仓储，仅供本地测试使用。

    生产环境替换为数据库后，测试层应改用事务回滚或临时 schema。
    """
    async with store.lock:
        store.projects.clear()
        store.tasks.clear()
