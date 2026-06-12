"""API 功能测试：新增、修改、删除能力探测。

运行方式：
    uv run python -m unittest tests.test_api_functional -v
"""
from __future__ import annotations

import asyncio
import time
import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.repository import reset_repositories_for_tests
from app.schemas.domain import ProjectStatus, TaskStatus


def _wait_task(client: TestClient, task_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/tasks/{task_id}")
        resp.raise_for_status()
        body = resp.json()
        if body["status"] in {TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value}:
            return body
        time.sleep(0.05)
    raise TimeoutError(f"task {task_id} did not finish")


class ApiFunctionalTest(unittest.TestCase):
    def setUp(self) -> None:
        asyncio.run(reset_repositories_for_tests())

    def test_create_project_generates_outline(self) -> None:
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/research-projects",
                json={
                    "topic": "Enterprise RAG platform",
                    "research_goal": "Evaluate implementation plan",
                    "target_audience": "AI platform team",
                    "time_scope": {"type": "recent_years", "years": 3},
                },
            )

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["status"], ProjectStatus.BRIEF_GENERATING.value)
            self.assertTrue(body["project_id"].startswith("proj_"))
            self.assertTrue(body["initial_task_id"].startswith("task_"))

            task = _wait_task(client, body["initial_task_id"])
            self.assertEqual(task["status"], TaskStatus.SUCCEEDED.value)

            outline_resp = client.get(f"/api/v1/research-projects/{body['project_id']}/outline")
            self.assertEqual(outline_resp.status_code, 200)
            outline_body = outline_resp.json()
            self.assertEqual(outline_body["status"], ProjectStatus.OUTLINE_READY.value)
            self.assertGreater(len(outline_body["outline"]), 0)

    def test_revise_outline_and_create_report_task(self) -> None:
        with TestClient(app) as client:
            create_resp = client.post(
                "/api/v1/research-projects",
                json={
                    "topic": "AI agent evaluation",
                    "research_goal": "Design an evaluation framework",
                    "target_audience": "Engineering managers",
                },
            )
            create_resp.raise_for_status()
            project_id = create_resp.json()["project_id"]
            initial_task_id = create_resp.json()["initial_task_id"]
            self.assertEqual(_wait_task(client, initial_task_id)["status"], TaskStatus.SUCCEEDED.value)

            before = client.get(f"/api/v1/research-projects/{project_id}/outline").json()
            before_count = len(before["outline"])

            revise_resp = client.put(
                f"/api/v1/research-projects/{project_id}/outline",
                json={
                    "action": "revise",
                    "revision_instruction": "Add a section about risk controls.",
                },
            )
            self.assertEqual(revise_resp.status_code, 200)
            revise_body = revise_resp.json()
            self.assertEqual(revise_body["status"], ProjectStatus.OUTLINE_REVISING.value)
            self.assertTrue(revise_body["revision_task_id"].startswith("task_"))

            revise_task = _wait_task(client, revise_body["revision_task_id"])
            self.assertEqual(revise_task["status"], TaskStatus.SUCCEEDED.value)

            after = client.get(f"/api/v1/research-projects/{project_id}/outline").json()
            self.assertEqual(after["status"], ProjectStatus.OUTLINE_READY.value)
            self.assertGreater(len(after["outline"]), before_count)

            confirm_resp = client.put(
                f"/api/v1/research-projects/{project_id}/outline",
                json={"action": "confirm"},
            )
            self.assertEqual(confirm_resp.status_code, 200)
            self.assertEqual(confirm_resp.json()["status"], ProjectStatus.OUTLINE_CONFIRMED.value)

            report_resp = client.post(
                f"/api/v1/research-projects/{project_id}/report-tasks",
                json={"user_instruction": "Focus on metrics and acceptance criteria."},
            )
            self.assertEqual(report_resp.status_code, 200)
            report_body = report_resp.json()
            self.assertEqual(report_body["status"], TaskStatus.QUEUED.value)
            self.assertTrue(report_body["task_id"].startswith("task_"))

    def test_delete_project_is_not_implemented(self) -> None:
        with TestClient(app) as client:
            create_resp = client.post(
                "/api/v1/research-projects",
                json={"topic": "Delete capability probe"},
            )
            create_resp.raise_for_status()
            project_id = create_resp.json()["project_id"]

            delete_resp = client.delete(f"/api/v1/research-projects/{project_id}")

            self.assertIn(delete_resp.status_code, {404, 405})

