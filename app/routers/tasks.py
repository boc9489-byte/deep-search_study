"""任务相关接口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.repository import task_repo
from app.schemas.api import TaskStatusResponse

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskStatusResponse)
async def get_task(task_id: str) -> TaskStatusResponse:
    """查询后台任务状态。

    功能：
      给前端轮询 generate_brief / revise_outline / generate_report 的执行进度。

    输入输出：
      输入 task_id；输出任务类型、状态、进度文案和时间戳。

    实现说明：
      该接口只返回任务摘要；节点级详情通过 `/tasks/{task_id}/trace` 获取。
    """
    # Step 1: 根据 task_id 读取后台任务记录。
    rec = await task_repo.get(task_id)
    if not rec:
        raise HTTPException(status_code=404, detail="task not found")

    # Step 2: 返回任务状态快照。前端通常轮询该接口，直到 succeeded / failed。
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
    """查询后台任务节点 Trace。

    功能：
      查看工作流每个节点的输入摘要、输出摘要、耗时和错误信息。

    输入输出：
      输入 task_id；输出 trace 列表。

    实现说明：
      阶段一 trace 存在 TaskRecord 内存对象里；生产应落库并支持按 trace_id 查询。
    """
    # Step 1: 读取任务记录。
    rec = await task_repo.get(task_id)
    if not rec:
        raise HTTPException(status_code=404, detail="task not found")

    # Step 2: 返回节点 trace。这里用 mode=json 的上层调用方可直接序列化。
    return {"task_id": task_id, "trace": [t.model_dump() for t in rec.trace]}
