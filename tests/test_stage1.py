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

        out = await EvidencePipeline().run(raw, "具身智能市场增长")

        self.assertEqual(len(out), 2)
        self.assertGreaterEqual(out[0].scores.final, out[1].scores.final)
        self.assertTrue(all(item.quote for item in out))
        self.assertTrue(all(item.scores.final > 0 for item in out))


class StageOneWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await reset_repositories_for_tests()

    async def test_stage_one_main_workflow_generates_traceable_report(self) -> None:
        req = CreateProjectRequest(
            topic="具身智能行业未来三年的机会",
            research_goal="判断公司是否需要关注该行业",
            target_audience="公司战略团队",
            time_scope=TimeScope(type="recent_years", years=3),
        )

        proj = await project_repo.create(req)
        brief_task = await task_repo.create(proj.id, TaskType.GENERATE_BRIEF)
        bg.spawn(bg.run_generate_brief(proj.id, brief_task.id))

        self.assertEqual(await _wait_task(brief_task.id), TaskStatus.SUCCEEDED)
        proj = await project_repo.get(proj.id)
        self.assertIsNotNone(proj)
        self.assertEqual(proj.status, ProjectStatus.OUTLINE_READY)
        self.assertGreater(len(proj.outline), 0)

        await project_repo.set_status(proj.id, ProjectStatus.OUTLINE_CONFIRMED)
        report_task = await task_repo.create(proj.id, TaskType.GENERATE_REPORT)
        bg.spawn(bg.run_generate_report(proj.id, report_task.id, "结论要明确，引用要完整"))

        self.assertEqual(await _wait_task(report_task.id), TaskStatus.SUCCEEDED)
        task = await task_repo.get(report_task.id)
        report = await project_repo.latest_report(proj.id)
        proj = await project_repo.get(proj.id)

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
