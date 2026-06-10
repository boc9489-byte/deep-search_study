"""端到端冒烟测试：不起 HTTP，直接驱动 repository + background，验证主链路。

运行： python -m scripts.smoke
预期：依次打印各阶段状态，最后输出报告标题、来源数与 HTML 片段。
"""
from __future__ import annotations

import asyncio

from app import background as bg
from app.repository import project_repo, task_repo
from app.schemas.api import CreateProjectRequest
from app.schemas.domain import ProjectStatus, TaskStatus, TaskType, TimeScope


async def _wait_task(task_id: str, timeout: float = 10.0) -> TaskStatus:
    waited = 0.0
    while waited < timeout:
        rec = await task_repo.get(task_id)
        if rec and rec.status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED):
            return rec.status
        await asyncio.sleep(0.05)
        waited += 0.05
    raise TimeoutError("task did not finish")


async def main() -> None:
    # 1. 创建项目（自动触发大纲生成）
    req = CreateProjectRequest(
        topic="具身智能行业未来三年的机会",
        research_goal="判断公司是否需要关注该行业",
        target_audience="公司战略团队",
        time_scope=TimeScope(type="recent_years", years=3),
    )
    proj = await project_repo.create(req)
    brief_task = await task_repo.create(proj.id, TaskType.GENERATE_BRIEF)
    bg.spawn(bg.run_generate_brief(proj.id, brief_task.id))
    print(f"[1] 创建项目 {proj.id}，大纲任务 {brief_task.id}")

    # 2. 等待大纲生成
    assert await _wait_task(brief_task.id) == TaskStatus.SUCCEEDED
    proj = await project_repo.get(proj.id)
    print(f"[2] 大纲就绪 status={proj.status.value}，章节数={len(proj.outline)}")
    assert proj.status == ProjectStatus.OUTLINE_READY

    # 3. 确认大纲
    await project_repo.set_status(proj.id, ProjectStatus.OUTLINE_CONFIRMED)
    print("[3] 大纲已确认")

    # 4. 提交研究/报告任务
    report_task = await task_repo.create(proj.id, TaskType.GENERATE_REPORT)
    bg.spawn(bg.run_generate_report(proj.id, report_task.id, "结论要明确"))
    print(f"[4] 报告任务 {report_task.id} 已投递")

    # 5. 等待报告生成
    status = await _wait_task(report_task.id)
    rec = await task_repo.get(report_task.id)
    print(f"[5] 报告任务 status={status.value} message={rec.message}")
    assert status == TaskStatus.SUCCEEDED, rec.message

    # 6. 取最新报告
    report = await project_repo.latest_report(proj.id)
    proj = await project_repo.get(proj.id)
    print(f"[6] 报告标题：{report.title}")
    print(f"    版本：v{report.version}  来源数：{len(report.source_ids)}  "
          f"证据数：{len(proj.evidences)}  事实数：{len(proj.facts)}  洞察数：{len(proj.insights)}")
    print(f"    trace 事件数：{len(rec.trace)}")
    print("    —— HTML 片段 ——")
    print("    " + report.html[:400].replace("\n", "\n    "))

    print("\n✅ 主链路冒烟测试通过")


if __name__ == "__main__":
    asyncio.run(main())
