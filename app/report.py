"""报告渲染（确定性流程）。

关键设计决策：报告生成**不使用独立智能体**。从结构化数据按固定模板渲染 HTML，
保证结构稳定、引用准确、可复算、可 diff。引用绑定是确定性映射：
fact.supporting_evidence → evidence.source_id → sources 下标 → 可点击角标 [n]。
"""
from __future__ import annotations

import html as _html

from app.pipeline.crosscheck import filter_facts
from app.schemas.domain import (
    Evidence,
    FactCard,
    InsightCard,
    OutlineNode,
    Report,
    Source,
    new_id,
)


def _esc(text: str) -> str:
    return _html.escape(text or "")


def render_report(
    *,
    project_id: str,
    title: str,
    outline: list[OutlineNode],
    facts: list[FactCard],
    insights: list[InsightCard],
    evidences: list[Evidence],
    sources: list[Source],
    version: int = 1,
) -> Report:
    ev_index = {e.evidence_id: e for e in evidences}
    src_order = {s.id: i + 1 for i, s in enumerate(sources)}  # 来源 → 角标号

    facts_by_node: dict[str, list[FactCard]] = {}
    for f in facts:
        facts_by_node.setdefault(f.node_id, []).append(f)
    insights_by_node: dict[str, list[InsightCard]] = {}
    for ins in insights:
        insights_by_node.setdefault(ins.node_id, []).append(ins)

    accepted, pending = filter_facts(facts)

    parts: list[str] = [
        "<article class='research-report'>",
        f"<h1>{_esc(title)}</h1>",
    ]

    # 1. 执行摘要（仅基于已验证洞察）
    parts.append("<section><h2>1. 执行摘要</h2><ul>")
    for ins in insights:
        if ins.confidence >= 0.5:
            parts.append(f"<li>{_esc(ins.insight)}（{_esc(ins.implication)}）</li>")
    parts.append("</ul></section>")

    # 2. 详细分析（按大纲组织，每节点挂事实 + 引用角标）
    parts.append("<section><h2>2. 详细分析</h2>")
    for root in outline:
        parts.append(_render_node(root, facts_by_node, insights_by_node,
                                  ev_index, src_order, level=3))
    parts.append("</section>")

    # 3. 风险与限制
    if pending:
        parts.append("<section><h2>3. 风险与限制</h2><ul>")
        for f in pending:
            note = "；".join(f.conflicts) or "证据不足，结论待验证"
            parts.append(f"<li>{_esc(f.claim)} —— {_esc(note)}</li>")
        parts.append("</ul></section>")

    # 4. 引用来源
    parts.append("<section><h2>引用来源</h2><ol>")
    for s in sources:
        link = f"<a href='{_esc(s.url)}'>{_esc(s.url)}</a>" if s.url else ""
        parts.append(
            f"<li id='src-{src_order[s.id]}'>{_esc(s.title)} "
            f"[{_esc(s.source_type.value)}] {link} "
            f"{_esc(s.published_at or '')}</li>"
        )
    parts.append("</ol></section>")

    parts.append("</article>")

    return Report(
        id=new_id("rep"),
        project_id=project_id,
        version=version,
        title=title,
        html="\n".join(parts),
        source_ids=[s.id for s in sources],
    )


def _render_node(
    node: OutlineNode,
    facts_by_node: dict[str, list[FactCard]],
    insights_by_node: dict[str, list[InsightCard]],
    ev_index: dict[str, Evidence],
    src_order: dict[str, int],
    level: int,
) -> str:
    tag = f"h{min(level, 6)}"
    out = [f"<{tag}>{_esc(node.node_id)} {_esc(node.title)}</{tag}>"]

    for f in facts_by_node.get(node.node_id, []):
        cites = _citation_marks(f, ev_index, src_order)
        out.append(f"<p>{_esc(f.claim)} {cites}</p>")
    for ins in insights_by_node.get(node.node_id, []):
        out.append(f"<p><em>洞察：</em>{_esc(ins.insight)}</p>")

    for child in node.children:
        out.append(_render_node(child, facts_by_node, insights_by_node,
                                ev_index, src_order, level + 1))
    return "\n".join(out)


def _citation_marks(
    fact: FactCard, ev_index: dict[str, Evidence], src_order: dict[str, int]
) -> str:
    """把事实的支撑证据映射为可点击的引用角标 [n]。确定性绑定。"""
    nums: list[int] = []
    for eid in fact.supporting_evidence:
        ev = ev_index.get(eid)
        if ev and ev.source_id in src_order:
            nums.append(src_order[ev.source_id])
    nums = sorted(set(nums))
    return "".join(f"<sup><a href='#src-{n}'>[{n}]</a></sup>" for n in nums)
