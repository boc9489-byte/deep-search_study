"""阶段一验收测试。

运行：
    python -m unittest discover -s tests

覆盖范围：
  1. Evidence Pipeline 的标准化、去重、排序与截断；
  2. 创建项目 -> 生成大纲 -> 确认大纲 -> 生成报告的 MVP 主链路。
"""
from __future__ import annotations

import asyncio
import unittest

from app import background as bg
from app.pipeline.evidence_pipeline import EvidencePipeline
from app.repository import project_repo, reset_repositories_for_tests, task_repo
from app.schemas.api import CreateProjectRequest
from app.schemas.domain import (
    Evidence,
    ProjectStatus,
    SourceType,
    TaskStatus,
    TaskType,
    TimeScope,
)


async def _wait_task(task_id: str, timeout: float = 10.0) -> TaskStatus:
    """轮询等待后台任务结束。

    步骤：
      1. 按 task_id 从内存仓储读取任务；
      2. 如果任务进入 succeeded / failed，立即返回最终状态；
      3. 否则 sleep 50ms 后继续轮询；
      4. 超过 timeout 后抛 TimeoutError，避免测试无限等待。
    """
    waited = 0.0
    while waited < timeout:
        rec = await task_repo.get(task_id)
        if rec and rec.status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED):
            return rec.status
        await asyncio.sleep(0.05)
        waited += 0.05
    raise TimeoutError(f"task {task_id} did not finish")


class EvidencePipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_dedup_rank_and_keep_top_evidence(self) -> None:
        # Step 1: 构造三条原始证据，其中前两条 URL/正文重复。
        raw = [
            Evidence(
                source_type=SourceType.PUBLIC_WEB,
                title="权威来源",
                url="https://stats.gov.cn/report",
                content="具身智能 机器人 市场 增长 政策 支持",
                published_at="2026-01-01",
            ),
            Evidence(
                source_type=SourceType.PUBLIC_WEB,
                title="重复来源",
                url="https://stats.gov.cn/report/",
                content="具身智能 机器人 市场 增长 政策 支持",
                published_at="2026-01-01",
            ),
            Evidence(
                source_type=SourceType.INTERNAL_KB,
                title="内部资料",
                content="内部调研显示具身智能试点项目增长",
                published_at="2025-10-01",
            ),
        ]

        # Step 2: 运行完整 Evidence Pipeline：normalize -> dedup -> rank -> keep。
        out = await EvidencePipeline().run(raw, "具身智能市场增长")

        # Step 3: 验证重复证据被去掉。
        self.assertEqual(len(out), 2)

        # Step 4: 验证排序、quote 和 final score 都已生成。
        self.assertGreaterEqual(out[0].scores.final, out[1].scores.final)
        self.assertTrue(all(item.quote for item in out))
        self.assertTrue(all(item.scores.final > 0 for item in out))


class StageOneWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # Step 0: 每个测试前清空内存仓储，避免测试之间互相污染。
        await reset_repositories_for_tests()

    async def test_stage_one_main_workflow_generates_traceable_report(self) -> None:
        # Step 1: 构造创建研究项目的请求。
        req = CreateProjectRequest(
            topic="具身智能行业未来三年的机会",
            research_goal="判断公司是否需要关注该行业",
            target_audience="公司战略团队",
            time_scope=TimeScope(type="recent_years", years=3),
        )

        # Step 2: 创建项目和大纲生成任务，模拟 POST /research-projects 的核心行为。
        proj = await project_repo.create(req)
        brief_task = await task_repo.create(proj.id, TaskType.GENERATE_BRIEF)
        bg.spawn(bg.run_generate_brief(proj.id, brief_task.id))

        # Step 3: 等待大纲任务完成，并验证项目进入 outline_ready。
        self.assertEqual(await _wait_task(brief_task.id), TaskStatus.SUCCEEDED)
        proj = await project_repo.get(proj.id)
        self.assertIsNotNone(proj)
        self.assertEqual(proj.status, ProjectStatus.OUTLINE_READY)
        self.assertGreater(len(proj.outline), 0)

        # Step 4: 模拟用户确认大纲，然后投递报告任务。
        await project_repo.set_status(proj.id, ProjectStatus.OUTLINE_CONFIRMED)
        report_task = await task_repo.create(proj.id, TaskType.GENERATE_REPORT)
        bg.spawn(bg.run_generate_report(proj.id, report_task.id, "结论要明确，引用要完整"))

        # Step 5: 等待研究工作流完成，并读取任务、报告和项目产物。
        self.assertEqual(await _wait_task(report_task.id), TaskStatus.SUCCEEDED)
        task = await task_repo.get(report_task.id)
        report = await project_repo.latest_report(proj.id)
        proj = await project_repo.get(proj.id)

        # Step 6: 验证状态推进、报告 HTML、证据链和 trace 都已生成。
        self.assertIsNotNone(task)
        self.assertIsNotNone(report)
        self.assertEqual(proj.status, ProjectStatus.REPORT_READY)
        self.assertIn("研究报告", report.title)
        self.assertIn("<article", report.html.lower())
        self.assertIn("引用来源", report.html)
        self.assertGreater(len(proj.sources), 0)
        self.assertGreater(len(proj.evidences), 0)
        self.assertGreater(len(proj.facts), 0)
        self.assertGreater(len(proj.insights), 0)
        self.assertGreaterEqual(len(task.trace), 4)
