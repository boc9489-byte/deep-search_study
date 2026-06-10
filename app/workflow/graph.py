"""研究执行图装配。

优先用 LangGraph 构建 StateGraph；若环境未安装 langgraph，则用等价的本地执行器
(LinearResearchRunner) 跑同一批节点，保证骨架开箱即用、便于本地验证。
两者执行的节点完全一致。

设计方案对比：
  - 方案 A：普通函数顺序调用。优点是简单；缺点是节点状态、trace、断点恢复不显式。
  - 方案 B：LangGraph 状态图。优点是状态机清晰，适合复杂 Agent 工作流和 checkpoint；
    缺点是多一个依赖。
  - 方案 C：只用任务队列 chain/group。优点是分布式调度成熟；缺点是表达 Agent 状态
    和中间产物不如图自然。
  - 阶段一采用 B + A 的组合：有 LangGraph 就用图，没有就降级线性执行器。
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
    """assemble 节点：确定性渲染报告（不调用智能体）。

    步骤：
      1. 从 ResearchState 读取 brief、outline、facts、insights、evidences、sources；
      2. 根据 brief.topic 生成报告标题；
      3. 调用 `render_report` 做确定性 HTML 渲染和引用绑定；
      4. 生成 TraceEvent，记录输入规模、输出大小和耗时；
      5. 返回 state 增量：report + trace。
    """
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
    """返回编译后的 LangGraph；不可用时返回 None。

    步骤：
      1. 尝试导入 LangGraph；
      2. 导入失败说明本地未安装可选依赖，返回 None 交给降级执行器；
      3. 构造 ResearchNodes，注册每个节点函数；
      4. 明确 START -> ... -> END 的有向边；
      5. compile 后返回统一支持 `ainvoke(state)` 的执行器。
    """
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
        """按固定顺序执行研究节点。

        步骤：
          1. 复制初始 state，避免调用方传入对象被原地污染；
          2. 依次执行 plan / retrieve / insight / report 四个节点；
          3. 每个节点返回的是增量 update；
          4. 用 `_merge` 模拟 LangGraph reducer，把列表字段累加；
          5. 如果某个节点异常，把错误写入 errors 并终止后续节点。
        """
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
    """把节点增量合并回 ResearchState。

    步骤：
      1. 对 questions/evidences/facts/insights/sources/errors/trace 等列表字段做累加；
      2. 对 report、brief、outline 等普通字段做覆盖；
      3. 这样本地执行器与 LangGraph reducer 的行为保持一致。
    """
    for k, v in update.items():
        if k in _LIST_FIELDS and isinstance(v, list):
            state[k] = (state.get(k) or []) + v  # type: ignore[literal-required]
        else:
            state[k] = v  # type: ignore[literal-required]


def get_research_executor():
    """统一入口：优先 LangGraph，否则本地执行器。

    步骤：
      1. 先调用 `build_research_graph()` 尝试构建 LangGraph；
      2. 如果返回 compiled graph，直接使用生产路径；
      3. 如果返回 None，使用 LinearResearchRunner；
      4. 两者都暴露 `ainvoke(state)`，background 层无需关心具体实现。
    """
    compiled = build_research_graph()
    if compiled is not None:
        return compiled
    return LinearResearchRunner()
