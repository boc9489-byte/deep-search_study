"""任务相关接口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.repository import task_repo
from app.schemas.api import TaskStatusResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: str) -> TaskStatusResponse:
    rec = await task_repo.get(task_id)
    if not rec:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskStatusResponse(
        task_id=rec.id,
        project_id=rec.project_id,
        task_type=rec.task_type,
        status=rec.status,
        message=rec.message,
        created_at=rec.created_at,
        updated_at=rec.updated_at,
    )


@router.get("/{task_id}/trace")
async def get_trace(task_id: str):
    rec = await task_repo.get(task_id)
    if not rec:
        raise HTTPException(status_code=404, detail="task not found")
    return {"task_id": task_id, "trace": [t.model_dump() for t in rec.trace]}
