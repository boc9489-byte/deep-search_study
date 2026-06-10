"""LangGraph 工作流状态。

所有中间产物都显式放在 state 里，这样工作流可中断、可恢复、可观测：
失败后能从最近成功节点重启，无需从头重跑检索。
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from app.schemas.domain import (
    AtomicQuestion,
    Evidence,
    FactCard,
    InsightCard,
    OutlineNode,
    Report,
    ResearchBrief,
    Source,
    TraceEvent,
)


def _extend(left: list, right: list) -> list:
    """LangGraph reducer：累加列表。

    功能：
      当多个节点或并发分支向同一列表字段写入时，把新结果追加到旧结果后面。

    输入输出：
      输入 left/right 两个列表；输出合并后的列表。

    实现说明：
      使用 `(left or []) + (right or [])`，兼容字段首次写入时 left 为空的情况。
    """
    return (left or []) + (right or [])


class ResearchState(TypedDict, total=False):
    """研究工作流共享状态。

    功能：
      承载一次报告任务的所有输入、中间产物、输出和观测信息。

    实现说明：
      LangGraph 节点返回 state 的增量更新；列表字段通过 `_extend` reducer 追加，
      普通字段如 report 则覆盖。`total=False` 允许不同阶段只携带部分字段。
    """

    # —— 标识 ——
    project_id: str
    task_id: str

    # —— 输入 ——
    brief: ResearchBrief
    outline: list[OutlineNode]
    user_instruction: str

    # —— 工作区（中间产物，带 reducer 以支持并发追加）——
    questions: list[AtomicQuestion]
    evidences: Annotated[list[Evidence], _extend]
    facts: Annotated[list[FactCard], _extend]
    insights: Annotated[list[InsightCard], _extend]
    sources: Annotated[list[Source], _extend]

    # —— 输出 ——
    report: Report | None

    # —— 控制 / 观测 ——
    errors: Annotated[list[str], _extend]
    trace: Annotated[list[TraceEvent], _extend]
