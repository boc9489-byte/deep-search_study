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
    """构造节点 TraceEvent。

    功能：
      统一记录工作流节点的输入摘要、输出摘要、耗时和错误。

    输入输出：
      输入节点名、起始时间、输入摘要、输出摘要和可选错误；输出 TraceEvent。

    实现说明：
      输入/输出摘要截断到 200 字符，避免 trace 过大或写入敏感长文本。
    """
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
        """初始化研究节点集合。

        功能：
          注入 ResearchManagerAgent 和 RetrievalAgent，供节点函数复用。

        实现说明：
          workflow 层不直接 new 具体工具，依赖由 `graph._build_nodes()` 装配，
          这样后续测试或生产可以替换 agent/tool 实现。
        """
        self.manager = manager
        self.retriever = retriever

    # ---- 1. 计划：按大纲叶子拆原子问题 ---- #
    async def plan_questions(self, state: ResearchState) -> dict:
        """计划节点：把已确认大纲拆成原子检索问题。

        步骤：
          1. 从 state 读取 outline；
          2. 递归遍历每个根节点的 leaves，只对叶子节点生成问题；
          3. 调用 ResearchManagerAgent.plan_questions；
          4. 汇总所有 AtomicQuestion；
          5. 返回 questions 和本节点 trace。
        """
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
        """检索与事实节点：并发检索证据，并合成事实卡片。

        步骤：
          1. 从 state 读取 questions；
          2. 用 Semaphore 控制并发，避免真实搜索/LLM 接入后打爆外部服务；
          3. 每个问题调用 RetrievalAgent.retrieve，得到排序后的 Evidence；
          4. 调用 ResearchManagerAgent.synthesize_facts，把 Evidence 转为 FactCard；
          5. 汇总所有 Evidence 和 FactCard；
          6. 根据 Evidence 去重生成 Source，并把 source_id 回写到 Evidence；
          7. 返回 evidences / facts / sources / trace。
        """
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
        """洞察节点：按大纲节点聚合事实，并生成洞察。

        步骤：
          1. 从 state 读取所有 FactCard；
          2. 按 node_id 分组，保证洞察仍挂在对应大纲章节下；
          3. 每组调用 ResearchManagerAgent.synthesize_insights；
          4. 汇总 InsightCard；
          5. 返回 insights 和 trace。
        """
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
    """从 Evidence 列表生成去重 Source 列表，并回写 source_id。

    步骤：
      1. 以 url 优先、title 兜底作为来源去重 key；
      2. 如果已见过该来源，复用已有 Source.id；
      3. 如果是新来源，创建 Source；
      4. 把 Source.id 写回 Evidence.source_id，供报告引用绑定使用；
      5. 返回按首次出现顺序排列的来源列表。
    """
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
