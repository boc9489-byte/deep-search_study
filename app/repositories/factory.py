"""Repository factory."""
from __future__ import annotations

from app.config import settings
from app.repositories.base import ProjectRepositoryProtocol, TaskRepositoryProtocol
from app.repositories.memory import MemoryProjectRepository, MemoryTaskRepository


def create_repositories() -> tuple[ProjectRepositoryProtocol, TaskRepositoryProtocol]:
    if settings.storage_backend == "mongodb":
        from motor.motor_asyncio import AsyncIOMotorClient

        from app.repositories.mongodb import MongoProjectRepository, MongoTaskRepository

        client = AsyncIOMotorClient(settings.mongodb_uri)
        db = client[settings.mongodb_db]
        return (
            MongoProjectRepository(db, settings.mongodb_projects_collection),
            MongoTaskRepository(db, settings.mongodb_tasks_collection),
        )

    return MemoryProjectRepository(), MemoryTaskRepository()


project_repo, task_repo = create_repositories()
