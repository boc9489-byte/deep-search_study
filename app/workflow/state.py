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
    """LangGraph reducer：累加列表（多个节点/并发分支向同一字段追加）。"""
    return (left or []) + (right or [])


class ResearchState(TypedDict, total=False):
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
