"""Compatibility facade for repository access.

Implementation lives in app.repositories. Existing routers, background tasks,
and tests can keep importing from app.repository while the storage backends are
split by responsibility.
"""
from __future__ import annotations

from app.repositories import project_repo, reset_repositories_for_tests, task_repo

__all__ = ["project_repo", "task_repo", "reset_repositories_for_tests"]
