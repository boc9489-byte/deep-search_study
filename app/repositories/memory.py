"""In-memory repository implementation."""
from __future__ import annotations

import asyncio

from app.repositories.models import ProjectRecord, TaskRecord
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


class MemoryStore:
    """Process-local store guarded by an asyncio lock."""

    def __init__(self) -> None:
        self.projects: dict[str, ProjectRecord] = {}
        self.tasks: dict[str, TaskRecord] = {}
        self.lock = asyncio.Lock()


store = MemoryStore()


class MemoryProjectRepository:
    """Project repository backed by process memory."""

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


class MemoryTaskRepository:
    """Task repository backed by process memory."""

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


async def reset_memory_store() -> None:
    async with store.lock:
        store.projects.clear()
        store.tasks.clear()
