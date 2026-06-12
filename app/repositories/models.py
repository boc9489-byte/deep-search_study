"""Repository storage models.

These dataclasses are the storage-facing aggregate records used by both
memory and MongoDB repository implementations.
"""
from __future__ import annotations

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
    utcnow,
)


@dataclass
class ProjectRecord:
    """Research project aggregate persisted by repositories."""

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
    sources: list[Source] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    facts: list[FactCard] = field(default_factory=list)
    insights: list[InsightCard] = field(default_factory=list)
    reports: list[Report] = field(default_factory=list)


@dataclass
class TaskRecord:
    """Background task state persisted by repositories."""

    id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus = TaskStatus.QUEUED
    message: str = ""
    created_at: object = field(default_factory=utcnow)
    updated_at: object = field(default_factory=utcnow)
    trace: list[TraceEvent] = field(default_factory=list)
