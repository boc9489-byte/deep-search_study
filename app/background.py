"""后台异步任务调度（background）。

本期用 asyncio.create_task 驱动；生产替换为 Celery/RQ 独立 worker（支持横向扩容/重试）。
三类作业：
  - generate_brief：理解任务书 + 生成大纲（创建项目后自动触发）
  - revise_outline：按自然语言修改大纲
  - generate_report：执行研究（驱动 LangGraph）+ 确定性渲染报告

设计方案对比：
  - 方案 A：HTTP 请求内同步执行研究。优点是链路直观；缺点是研究任务耗时长，
    容易超时，前端体验差，也不利于重试。
  - 方案 B：`asyncio.create_task` 异步执行。优点是零外部依赖，适合阶段一；
    缺点是进程重启会丢任务，不支持分布式 worker。
  - 方案 C：Celery/RQ/Arq。优点是持久队列、重试、扩容、任务隔离；缺点是
    需要 Redis/RabbitMQ 和更多部署配置。
  - 阶段一选择方案 B，生产建议切到方案 C。
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
    """投递后台任务（持有引用避免被 GC）。

    步骤：
      1. 用 `asyncio.create_task` 把协程交给当前事件循环执行；
      2. 把 task 放入 `_BG_TASKS`，避免任务对象没有强引用而被回收；
      3. 任务结束后通过 callback 自动从集合移除，避免集合无限增长。
    """
    task = asyncio.create_task(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)


_BG_TASKS: set[asyncio.Task] = set()


# --------------------------------------------------------------------------- #
# 作业 1：生成研究任务书 + 大纲
# --------------------------------------------------------------------------- #
async def run_generate_brief(project_id: str, task_id: str) -> None:
    # Step 1: 任务进入 running，前端轮询任务状态时能看到当前进度。
    await task_repo.update(task_id, status=TaskStatus.RUNNING, message="正在理解研究主题")
    try:
        # Step 2: 读取项目原始输入，包括 topic、research_goal、target_audience。
        proj = await project_repo.get(project_id)

        # Step 3: 构造研究管理智能体。阶段一 LLMClient 是桩实现，
        # 后续接真实模型时只替换 LLMClient/Agent 内部逻辑。
        manager = ResearchManagerAgent(LLMClient())

        # Step 4: 把用户的模糊研究主题结构化成 ResearchBrief。
        brief = await manager.understand_brief(
            proj.topic, proj.research_goal, proj.target_audience
        )

        # Step 5: 基于 ResearchBrief 生成可确认/可修改的研究大纲。
        outline = await manager.generate_outline(brief)

        # Step 6: 保存 brief + outline，并把项目状态推进到 outline_ready。
        await project_repo.save_brief_and_outline(project_id, brief, outline)

        # Step 7: 任务成功，前端下一步可以 GET outline。
        await task_repo.update(task_id, status=TaskStatus.SUCCEEDED, message="大纲已生成")
    except Exception as exc:  # noqa: BLE001
        # Step 8: 任意异常都落到任务状态，避免后台任务静默失败。
        logger.exception("generate_brief failed")
        await project_repo.set_status(project_id, ProjectStatus.FAILED)
        await task_repo.update(task_id, status=TaskStatus.FAILED, message=str(exc))


# --------------------------------------------------------------------------- #
# 作业 2：修改大纲
# --------------------------------------------------------------------------- #
async def run_revise_outline(project_id: str, task_id: str, instruction: str) -> None:
    # Step 1: 标记修改任务正在执行，前端继续轮询 revision_task_id。
    await task_repo.update(task_id, status=TaskStatus.RUNNING, message="正在修改大纲")
    try:
        # Step 2: 读取当前大纲，作为 LLM/规则修改的输入。
        proj = await project_repo.get(project_id)
        manager = ResearchManagerAgent(LLMClient())

        # Step 3: 根据自然语言修改意见生成新版大纲。
        revised = await manager.revise_outline(proj.outline, instruction)

        # Step 4: 保存新版大纲，并把项目状态回到 outline_ready，等待用户确认。
        await project_repo.save_outline(project_id, revised)
        await task_repo.update(task_id, status=TaskStatus.SUCCEEDED, message="大纲已更新")
    except Exception as exc:  # noqa: BLE001
        # Step 5: 修改失败只失败该任务，不清空旧大纲，用户仍可基于旧大纲继续操作。
        logger.exception("revise_outline failed")
        await task_repo.update(task_id, status=TaskStatus.FAILED, message=str(exc))


# --------------------------------------------------------------------------- #
# 作业 3：执行研究 + 生成报告
# --------------------------------------------------------------------------- #
async def run_generate_report(project_id: str, task_id: str, user_instruction: str) -> None:
    # Step 1: 项目进入 researching，任务进入 running。
    await project_repo.set_status(project_id, ProjectStatus.RESEARCHING)
    await task_repo.update(task_id, status=TaskStatus.RUNNING, message="正在检索与分析资料")
    try:
        # Step 2: 读取已确认的大纲、任务书等研究上下文。
        proj = await project_repo.get(project_id)

        # Step 3: 获取统一执行器。安装 LangGraph 时是 StateGraph，否则是本地线性执行器。
        executor = get_research_executor()  # LangGraph 或本地执行器

        # Step 4: 初始化 ResearchState。后续每个工作流节点只读写这个状态对象。
        init: ResearchState = {
            "project_id": project_id,
            "task_id": task_id,
            "brief": proj.brief,
            "outline": proj.outline,
            "user_instruction": user_instruction,
        }

        # Step 5: 执行研究图：plan_questions -> retrieve_and_build_facts
        # -> build_insights -> assemble_report。
        final: ResearchState = await executor.ainvoke(init)

        # Step 6: 校验关键产物。没有 report 说明图执行中断或节点失败。
        report = final.get("report")
        if report is None:
            raise RuntimeError("报告未生成：" + "; ".join(final.get("errors", [])))

        # Step 7: 持久化阶段一研究产物。生产环境这里会拆成事务写多张表。
        await project_repo.save_research_outputs(
            project_id,
            sources=final.get("sources", []),
            evidences=final.get("evidences", []),
            facts=final.get("facts", []),
            insights=final.get("insights", []),
            report=report,
        )

        # Step 8: 任务成功，并把节点 trace 写入 task，方便排障和回归。
        await task_repo.update(
            task_id,
            status=TaskStatus.SUCCEEDED,
            message="报告已生成",
            trace=final.get("trace", []),
        )
    except Exception as exc:  # noqa: BLE001
        # Step 9: 报告生成失败时回到 outline_confirmed，允许用户重新提交报告任务。
        logger.exception("generate_report failed")
        await project_repo.set_status(project_id, ProjectStatus.OUTLINE_CONFIRMED)
        await task_repo.update(task_id, status=TaskStatus.FAILED, message=str(exc))
