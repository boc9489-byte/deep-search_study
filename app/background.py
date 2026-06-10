"""后台异步任务调度（background）。

本期用 asyncio.create_task 驱动；生产替换为 Celery/RQ 独立 worker（支持横向扩容/重试）。
三类作业：
  - generate_brief：理解任务书 + 生成大纲（创建项目后自动触发）
  - revise_outline：按自然语言修改大纲
  - generate_report：执行研究（驱动 LangGraph）+ 确定性渲染报告
"""
from __future__ import annotations

import asyncio
import logging

from app.agents import LLMClient, ResearchManagerAgent
from app.repository import project_repo, task_repo
from app.schemas.domain import ProjectStatus, TaskStatus
from app.workflow.graph import get_research_executor
from app.workflow.state import ResearchState

logger = logging.getLogger(__name__)


def spawn(coro) -> None:
    """投递后台任务（持有引用避免被 GC）。"""
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


_BG_TASKS: set[asyncio.Task] = set()


# --------------------------------------------------------------------------- #
# 作业 1：生成研究任务书 + 大纲
# --------------------------------------------------------------------------- #
async def run_generate_brief(project_id: str, task_id: str) -> None:
    await task_repo.update(task_id, status=TaskStatus.RUNNING, message="正在理解研究主题")
    try:
        proj = await project_repo.get(project_id)
        manager = ResearchManagerAgent(LLMClient())
        brief = await manager.understand_brief(
            proj.topic, proj.research_goal, proj.target_audience
        )
        outline = await manager.generate_outline(brief)
        await project_repo.save_brief_and_outline(project_id, brief, outline)
        await task_repo.update(task_id, status=TaskStatus.SUCCEEDED, message="大纲已生成")
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_brief failed")
        await project_repo.set_status(project_id, ProjectStatus.FAILED)
        await task_repo.update(task_id, status=TaskStatus.FAILED, message=str(exc))


# --------------------------------------------------------------------------- #
# 作业 2：修改大纲
# --------------------------------------------------------------------------- #
async def run_revise_outline(project_id: str, task_id: str, instruction: str) -> None:
    await task_repo.update(task_id, status=TaskStatus.RUNNING, message="正在修改大纲")
    try:
        proj = await project_repo.get(project_id)
        manager = ResearchManagerAgent(LLMClient())
        revised = await manager.revise_outline(proj.outline, instruction)
        await project_repo.save_outline(project_id, revised)
        await task_repo.update(task_id, status=TaskStatus.SUCCEEDED, message="大纲已更新")
    except Exception as exc:  # noqa: BLE001
        logger.exception("revise_outline failed")
        await task_repo.update(task_id, status=TaskStatus.FAILED, message=str(exc))


# --------------------------------------------------------------------------- #
# 作业 3：执行研究 + 生成报告
# --------------------------------------------------------------------------- #
async def run_generate_report(project_id: str, task_id: str, user_instruction: str) -> None:
    await project_repo.set_status(project_id, ProjectStatus.RESEARCHING)
    await task_repo.update(task_id, status=TaskStatus.RUNNING, message="正在检索与分析资料")
    try:
        proj = await project_repo.get(project_id)
        executor = get_research_executor()  # LangGraph 或本地执行器

        init: ResearchState = {
            "project_id": project_id,
            "task_id": task_id,
            "brief": proj.brief,
            "outline": proj.outline,
            "user_instruction": user_instruction,
        }
        final: ResearchState = await executor.ainvoke(init)

        report = final.get("report")
        if report is None:
            raise RuntimeError("报告未生成：" + "; ".join(final.get("errors", [])))

        await project_repo.save_research_outputs(
            project_id,
            sources=final.get("sources", []),
            evidences=final.get("evidences", []),
            facts=final.get("facts", []),
            insights=final.get("insights", []),
            report=report,
        )
        await task_repo.update(
            task_id,
            status=TaskStatus.SUCCEEDED,
            message="报告已生成",
            trace=final.get("trace", []),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_report failed")
        await project_repo.set_status(project_id, ProjectStatus.OUTLINE_CONFIRMED)
        await task_repo.update(task_id, status=TaskStatus.FAILED, message=str(exc))
