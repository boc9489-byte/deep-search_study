"""研究执行图的节点实现。

每个节点遵循 LangGraph 约定：`async def node(state) -> dict`（返回对 state 的增量更新）。
节点是"编排胶水"，把 agents（决策）+ tools（能力）+ pipeline（质检）串起来，并写 trace。
"""
from __future__ import annotations

import asyncio
import time

from app.agents import ResearchManagerAgent, RetrievalAgent
from app.config import settings
from app.schemas.domain import (
    AtomicQuestion,
    Evidence,
    FactCard,
    InsightCard,
    Source,
    TraceEvent,
)
from app.workflow.state import ResearchState


def _trace(node: str, t0: float, inp: str, out: str, err: str | None = None) -> TraceEvent:
    return TraceEvent(
        node=node,
        input_summary=inp[:200],
        output_summary=out[:200],
        latency_ms=int((time.perf_counter() - t0) * 1000),
        error=err,
    )


class ResearchNodes:
    """持有依赖（agents），暴露各节点函数。"""

    def __init__(self, manager: ResearchManagerAgent, retriever: RetrievalAgent) -> None:
        self.manager = manager
        self.retriever = retriever

    # ---- 1. 计划：按大纲叶子拆原子问题 ---- #
    async def plan_questions(self, state: ResearchState) -> dict:
        t0 = time.perf_counter()
        outline = state["outline"]
        questions: list[AtomicQuestion] = []
        for root in outline:
            for leaf in root.leaves():
                questions.extend(await self.manager.plan_questions(leaf))
        return {
            "questions": questions,
            "trace": [_trace("plan_questions", t0,
                             f"{len(outline)} roots",
                             f"{len(questions)} questions")],
        }

    # ---- 2. 检索 + 建事实（按问题并发）---- #
    async def retrieve_and_build_facts(self, state: ResearchState) -> dict:
        t0 = time.perf_counter()
        questions = state["questions"]
        sem = asyncio.Semaphore(settings.max_concurrent_questions)

        async def handle(q: AtomicQuestion) -> tuple[list[Evidence], list[FactCard]]:
            async with sem:
                evs = await self.retriever.retrieve(q)            # 路由+多源+管道
                facts = await self.manager.synthesize_facts(q.node_id, q, evs)  # 含 crosscheck
                return evs, facts

        results = await asyncio.gather(*(handle(q) for q in questions))

        all_ev: list[Evidence] = []
        all_facts: list[FactCard] = []
        for evs, facts in results:
            all_ev.extend(evs)
            all_facts.extend(facts)

        # 由证据汇总来源（去重）
        sources = _sources_from_evidence(all_ev)

        return {
            "evidences": all_ev,
            "facts": all_facts,
            "sources": sources,
            "trace": [_trace("retrieve_and_build_facts", t0,
                             f"{len(questions)} questions",
                             f"{len(all_ev)} evidences / {len(all_facts)} facts")],
        }

    # ---- 3. 建洞察（按节点聚合事实）---- #
    async def build_insights(self, state: ResearchState) -> dict:
        t0 = time.perf_counter()
        facts = state.get("facts", [])
        by_node: dict[str, list[FactCard]] = {}
        for f in facts:
            by_node.setdefault(f.node_id, []).append(f)

        insights: list[InsightCard] = []
        for node_id, node_facts in by_node.items():
            insights.extend(await self.manager.synthesize_insights(node_id, node_facts))
        return {
            "insights": insights,
            "trace": [_trace("build_insights", t0,
                             f"{len(facts)} facts",
                             f"{len(insights)} insights")],
        }


def _sources_from_evidence(evidences: list[Evidence]) -> list[Source]:
    seen: dict[str, Source] = {}
    for ev in evidences:
        key = ev.url or ev.title
        if key in seen:
            ev.source_id = seen[key].id
            continue
        src = Source(
            title=ev.title or "未命名来源",
            url=ev.url,
            published_at=ev.published_at,
            source_type=ev.source_type,
        )
        ev.source_id = src.id
        seen[key] = src
    return list(seen.values())
