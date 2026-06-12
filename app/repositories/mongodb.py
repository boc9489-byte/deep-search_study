"""MongoDB repository implementation."""
from __future__ import annotations

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


def _dump_model(value):
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def project_to_doc(rec: ProjectRecord) -> dict:
    return {
        "id": rec.id,
        "topic": rec.topic,
        "research_goal": rec.research_goal,
        "target_audience": rec.target_audience,
        "region_scope": rec.region_scope,
        "time_scope": rec.time_scope,
        "status": rec.status.value,
        "created_at": rec.created_at,
        "brief": _dump_model(rec.brief),
        "outline": [_dump_model(item) for item in rec.outline],
        "sources": [_dump_model(item) for item in rec.sources],
        "evidences": [_dump_model(item) for item in rec.evidences],
        "facts": [_dump_model(item) for item in rec.facts],
        "insights": [_dump_model(item) for item in rec.insights],
        "reports": [_dump_model(item) for item in rec.reports],
    }


def project_from_doc(doc: dict) -> ProjectRecord:
    return ProjectRecord(
        id=doc["id"],
        topic=doc["topic"],
        research_goal=doc.get("research_goal", ""),
        target_audience=doc.get("target_audience", ""),
        region_scope=doc.get("region_scope", "china"),
        time_scope=doc.get("time_scope", {}),
        status=ProjectStatus(doc["status"]),
        created_at=doc.get("created_at", utcnow()),
        brief=ResearchBrief(**doc["brief"]) if doc.get("brief") else None,
        outline=[OutlineNode(**item) for item in doc.get("outline", [])],
        sources=[Source(**item) for item in doc.get("sources", [])],
        evidences=[Evidence(**item) for item in doc.get("evidences", [])],
        facts=[FactCard(**item) for item in doc.get("facts", [])],
        insights=[InsightCard(**item) for item in doc.get("insights", [])],
        reports=[Report(**item) for item in doc.get("reports", [])],
    )


def task_to_doc(rec: TaskRecord) -> dict:
    return {
        "id": rec.id,
        "project_id": rec.project_id,
        "task_type": rec.task_type.value,
        "status": rec.status.value,
        "message": rec.message,
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "trace": [_dump_model(item) for item in rec.trace],
    }


def task_from_doc(doc: dict) -> TaskRecord:
    return TaskRecord(
        id=doc["id"],
        project_id=doc["project_id"],
        task_type=TaskType(doc["task_type"]),
        status=TaskStatus(doc.get("status", TaskStatus.QUEUED.value)),
        message=doc.get("message", ""),
        created_at=doc.get("created_at", utcnow()),
        updated_at=doc.get("updated_at", utcnow()),
        trace=[TraceEvent(**item) for item in doc.get("trace", [])],
    )


class MongoProjectRepository:
    """Project repository backed by MongoDB."""

    def __init__(self, db, collection_name: str = "projects") -> None:
        self.projects = db[collection_name]

    async def ensure_indexes(self) -> None:
        await self.projects.create_index("id", unique=True)
        await self.projects.create_index("status")

    async def create(self, req) -> ProjectRecord:
        rec = ProjectRecord(
            id=new_id("proj"),
            topic=req.topic,
            research_goal=req.research_goal,
            target_audience=req.target_audience,
            region_scope=req.region_scope.value,
            time_scope=req.time_scope.model_dump(),
            status=ProjectStatus.BRIEF_GENERATING,
        )
        await self.projects.insert_one(project_to_doc(rec))
        return rec

    async def get(self, project_id: str) -> ProjectRecord | None:
        doc = await self.projects.find_one({"id": project_id}, {"_id": 0})
        return project_from_doc(doc) if doc else None

    async def set_status(self, project_id: str, status: ProjectStatus) -> None:
        await self.projects.update_one(
            {"id": project_id},
            {"$set": {"status": status.value}},
        )

    async def save_brief_and_outline(
        self, project_id: str, brief: ResearchBrief, outline: list[OutlineNode]
    ) -> None:
        await self.projects.update_one(
            {"id": project_id},
            {
                "$set": {
                    "brief": brief.model_dump(mode="json"),
                    "outline": [item.model_dump(mode="json") for item in outline],
                    "status": ProjectStatus.OUTLINE_READY.value,
                }
            },
        )

    async def save_outline(self, project_id: str, outline: list[OutlineNode]) -> None:
        await self.projects.update_one(
            {"id": project_id},
            {
                "$set": {
                    "outline": [item.model_dump(mode="json") for item in outline],
                    "status": ProjectStatus.OUTLINE_READY.value,
                }
            },
        )

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
        rec = await self.get(project_id)
        if rec is None:
            raise KeyError(project_id)

        report.version = len(rec.reports) + 1
        rec.sources = sources
        rec.evidences = evidences
        rec.facts = facts
        rec.insights = insights
        rec.reports.append(report)
        rec.status = ProjectStatus.REPORT_READY

        await self.projects.replace_one({"id": project_id}, project_to_doc(rec))

    async def latest_report(self, project_id: str) -> Report | None:
        rec = await self.get(project_id)
        if not rec or not rec.reports:
            return None
        return rec.reports[-1]


class MongoTaskRepository:
    """Task repository backed by MongoDB."""

    def __init__(self, db, collection_name: str = "tasks") -> None:
        self.tasks = db[collection_name]

    async def ensure_indexes(self) -> None:
        await self.tasks.create_index("id", unique=True)
        await self.tasks.create_index("project_id")
        await self.tasks.create_index("status")
        await self.tasks.create_index("created_at")

    async def create(self, project_id: str, task_type: TaskType) -> TaskRecord:
        rec = TaskRecord(id=new_id("task"), project_id=project_id, task_type=task_type)
        await self.tasks.insert_one(task_to_doc(rec))
        return rec

    async def get(self, task_id: str) -> TaskRecord | None:
        doc = await self.tasks.find_one({"id": task_id}, {"_id": 0})
        return task_from_doc(doc) if doc else None

    async def update(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        message: str | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> None:
        set_values = {"updated_at": utcnow()}
        if status is not None:
            set_values["status"] = status.value
        if message is not None:
            set_values["message"] = message

        update_doc: dict = {"$set": set_values}
        if trace:
            update_doc["$push"] = {
                "trace": {"$each": [item.model_dump(mode="json") for item in trace]}
            }

        await self.tasks.update_one({"id": task_id}, update_doc)
