"""Repository test helpers."""
from __future__ import annotations

from app.config import settings
from app.repositories.factory import project_repo, task_repo
from app.repositories.memory import reset_memory_store


async def reset_repositories_for_tests() -> None:
    """Clear repository data for tests."""
    if settings.storage_backend == "mongodb":
        await project_repo.projects.delete_many({})
        await task_repo.tasks.delete_many({})
        return

    await reset_memory_store()
