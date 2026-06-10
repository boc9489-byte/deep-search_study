"""研究执行图装配。

优先用 LangGraph 构建 StateGraph；若环境未安装 langgraph，则用等价的本地执行器
(LinearResearchRunner) 跑同一批节点，保证骨架开箱即用、便于本地验证。
两者执行的节点完全一致。
"""
from __future__ import annotations

import time

from app.agents import LLMClient, ResearchManagerAgent, RetrievalAgent
from app.pipeline.evidence_pipeline import EvidencePipeline
from app.report import render_report
from app.schemas.domain import Report, TraceEvent
from app.tools.rag_search import RagSearchTool
from app.tools.web_search import WebSearchTool
from app.workflow.nodes import ResearchNodes
from app.workflow.state import ResearchState


def _build_nodes() -> ResearchNodes:
    llm = LLMClient()
    manager = ResearchManagerAgent(llm=llm)
    retriever = RetrievalAgent(
        web_tool=WebSearchTool(),
        kb_tool=RagSearchTool(),
        pipeline=EvidencePipeline(),
        llm=llm,
    )
    return ResearchNodes(manager=manager, retriever=retriever)


async def _assemble_report(state: ResearchState) -> dict:
    """assemble 节点：确定性渲染报告（不调用智能体）。"""
    t0 = time.perf_counter()
    brief = state.get("brief")
    title = f"{brief.topic}研究报告" if brief else "研究报告"
    report: Report = render_report(
        project_id=state["project_id"],
        title=title,
        outline=state["outline"],
        facts=state.get("facts", []),
        insights=state.get("insights", []),
        evidences=state.get("evidences", []),
        sources=state.get("sources", []),
    )
    trace = TraceEvent(
        node="assemble_report",
        input_summary=f"{len(state.get('facts', []))} facts / {len(state.get('sources', []))} sources",
        output_summary=f"report v{report.version}, html {len(report.html)} chars",
        latency_ms=int((time.perf_counter() - t0) * 1000),
    )
    return {"report": report, "trace": [trace]}


# --------------------------------------------------------------------------- #
# LangGraph 构建（生产路径）
# --------------------------------------------------------------------------- #
def build_research_graph():
    """返回编译后的 LangGraph；不可用时返回 None。"""
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return None

    nodes = _build_nodes()
    g = StateGraph(ResearchState)
    g.add_node("plan_questions", nodes.plan_questions)
    g.add_node("retrieve_and_build_facts", nodes.retrieve_and_build_facts)
    g.add_node("build_insights", nodes.build_insights)
    g.add_node("assemble_report", _assemble_report)

    g.add_edge(START, "plan_questions")
    g.add_edge("plan_questions", "retrieve_and_build_facts")
    g.add_edge("retrieve_and_build_facts", "build_insights")
    g.add_edge("build_insights", "assemble_report")
    g.add_edge("assemble_report", END)
    # 生产可传入 checkpointer 以支持断点恢复：g.compile(checkpointer=...)
    return g.compile()


# --------------------------------------------------------------------------- #
# 本地降级执行器（开箱即用路径）
# --------------------------------------------------------------------------- #
class LinearResearchRunner:
    """按固定顺序执行节点，并应用 state reducer（列表累加）。"""

    def __init__(self) -> None:
        self.nodes = _build_nodes()
        self._steps = [
            ("plan_questions", self.nodes.plan_questions),
            ("retrieve_and_build_facts", self.nodes.retrieve_and_build_facts),
            ("build_insights", self.nodes.build_insights),
            ("assemble_report", _assemble_report),
        ]

    async def ainvoke(self, state: ResearchState) -> ResearchState:
        cur: ResearchState = dict(state)  # type: ignore[assignment]
        for name, fn in self._steps:
            try:
                update = await fn(cur)
            except Exception as exc:  # noqa: BLE001
                cur.setdefault("errors", []).append(f"{name}: {exc}")  # type: ignore[union-attr]
                break
            _merge(cur, update)
        return cur


_LIST_FIELDS = {"questions", "evidences", "facts", "insights", "sources", "errors", "trace"}


def _merge(state: ResearchState, update: dict) -> None:
    for k, v in update.items():
        if k in _LIST_FIELDS and isinstance(v, list):
            state[k] = (state.get(k) or []) + v  # type: ignore[literal-required]
        else:
            state[k] = v  # type: ignore[literal-required]


def get_research_executor():
    """统一入口：优先 LangGraph，否则本地执行器。两者都暴露 ainvoke。"""
    compiled = build_research_graph()
    if compiled is not None:
        return compiled
    return LinearResearchRunner()
