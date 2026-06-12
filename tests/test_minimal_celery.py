"""最小 Celery 后台任务示例。

运行方式：
    python -m unittest tests.test_minimal_celery

说明：
  1. 使用 memory broker + eager 模式，不依赖 Redis/RabbitMQ；
  2. Celery 未安装时跳过测试，避免影响默认开发环境；
  3. 复用 app.background 里的异步业务函数，展示生产 Celery worker 的最小封装形态。
"""
from __future__ import annotations

import asyncio
import unittest

try:
    from celery import Celery
except ModuleNotFoundError:  # pragma: no cover - depends on optional prod extra.
    Celery = None

from app import background as bg
from app.repository import project_repo, reset_repositories_for_tests, task_repo
from app.schemas.api import CreateProjectRequest
from app.schemas.domain import ProjectStatus, TaskStatus, TaskType, TimeScope


def _run_async(coro):
    """在 Celery 的同步 task 入口中执行项目里的 async 业务协程。"""
    return asyncio.run(coro)


if Celery is not None:
    celery_app = Celery(
        "deepsearch_minimal_test",
        broker="memory://",
        backend="cache+memory://",
    )
    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
    )

    @celery_app.task(name="deepsearch.test.generate_brief")
    def generate_brief_task(project_id: str, task_id: str) -> None:
        _run_async(bg.run_generate_brief(project_id, task_id))

    @celery_app.task(name="deepsearch.test.generate_report")
    def generate_report_task(project_id: str, task_id: str, user_instruction: str) -> None:
        _run_async(bg.run_generate_report(project_id, task_id, user_instruction))


@unittest.skipIf(Celery is None, "celery optional dependency is not installed")
class MinimalCeleryTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        _run_async(reset_repositories_for_tests())

    def test_celery_task_wraps_generate_brief_coroutine(self) -> None:
        req = CreateProjectRequest(
            topic="具身智能行业未来三年的机会",
            research_goal="判断公司是否需要关注该行业",
            target_audience="公司战略团队",
            time_scope=TimeScope(type="recent_years", years=3),
        )
        proj = _run_async(project_repo.create(req))
        task = _run_async(task_repo.create(proj.id, TaskType.GENERATE_BRIEF))

        result = generate_brief_task.delay(proj.id, task.id)

        self.assertTrue(result.successful())
        saved_task = _run_async(task_repo.get(task.id))
        saved_project = _run_async(project_repo.get(proj.id))
        self.assertIsNotNone(saved_task)
        self.assertIsNotNone(saved_project)
        self.assertEqual(saved_task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(saved_project.status, ProjectStatus.OUTLINE_READY)
        self.assertGreater(len(saved_project.outline), 0)

    def test_celery_task_wraps_generate_report_coroutine(self) -> None:
        req = CreateProjectRequest(
            topic="企业知识库 RAG 系统建设",
            research_goal="形成可落地的技术方案",
            target_audience="AI 平台研发团队",
            time_scope=TimeScope(type="recent_years", years=3),
        )
        proj = _run_async(project_repo.create(req))
        brief_task = _run_async(task_repo.create(proj.id, TaskType.GENERATE_BRIEF))
        generate_brief_task.delay(proj.id, brief_task.id)
        _run_async(project_repo.set_status(proj.id, ProjectStatus.OUTLINE_CONFIRMED))

        report_task = _run_async(task_repo.create(proj.id, TaskType.GENERATE_REPORT))
        result = generate_report_task.delay(proj.id, report_task.id, "突出工程风险和验收指标")

        self.assertTrue(result.successful())
        saved_task = _run_async(task_repo.get(report_task.id))
        saved_project = _run_async(project_repo.get(proj.id))
        report = _run_async(project_repo.latest_report(proj.id))
        self.assertIsNotNone(saved_task)
        self.assertIsNotNone(saved_project)
        self.assertIsNotNone(report)
        self.assertEqual(saved_task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(saved_project.status, ProjectStatus.REPORT_READY)
        self.assertIn("<article", report.html.lower())

