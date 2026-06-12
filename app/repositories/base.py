"""Repository contracts.

Routers and background tasks depend on these method shapes, not on a
specific storage backend.
"""
from __future__ import annotations

from typing import Protocol

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
)


class ProjectRepositoryProtocol(Protocol):
    async def create(self, req) -> ProjectRecord: ...

    async def get(self, project_id: str) -> ProjectRecord | None: ...

    async def set_status(self, project_id: str, status: ProjectStatus) -> None: ...

    async def save_brief_and_outline(
        self, project_id: str, brief: ResearchBrief, outline: list[OutlineNode]
    ) -> None: ...

    async def save_outline(self, project_id: str, outline: list[OutlineNode]) -> None: ...

    async def save_research_outputs(
        self,
        project_id: str,
        *,
        sources: list[Source],
        evidences: list[Evidence],
        facts: list[FactCard],
        insights: list[InsightCard],
        report: Report,
    ) -> None: ...

    async def latest_report(self, project_id: str) -> Report | None: ...


class TaskRepositoryProtocol(Protocol):
    async def create(self, project_id: str, task_type: TaskType) -> TaskRecord: ...

    async def get(self, task_id: str) -> TaskRecord | None: ...

    async def update(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        message: str | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> None: ...
