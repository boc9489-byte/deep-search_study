"""报告渲染（确定性流程）。

关键设计决策：报告生成**不使用独立智能体**。从结构化数据按固定模板渲染 HTML，
保证结构稳定、引用准确、可复算、可 diff。引用绑定是确定性映射：
fact.supporting_evidence → evidence.source_id → sources 下标 → 可点击角标 [n]。

设计方案对比：
  - 方案 A：让 LLM 一次性生成整篇报告。优点是文字自然；缺点是结构不稳定，
    引用容易编造，回归测试困难。
  - 方案 B：结构化事实/洞察 + 确定性模板渲染。优点是引用可追溯、报告可 diff、
    失败点可定位；缺点是文字风格早期较模板化。
  - 方案 C：模板渲染骨架，局部段落再由 LLM 润色。兼顾可控和可读性，但需要
    对润色输出做引用约束和回填校验。
  - 阶段一选择方案 B；生产可在不改变引用链的前提下演进到方案 C。
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
    """渲染研究报告。

    步骤：
      1. 建立 evidence_id -> Evidence 索引，用于事实找证据；
      2. 建立 source_id -> 引用序号索引，用于生成 [n] 角标；
      3. 按 node_id 聚合 facts 和 insights，保证报告按大纲组织；
      4. 调用 filter_facts，把已验证事实和待验证事实分开；
      5. 渲染执行摘要，只展示置信度达标的洞察；
      6. 递归渲染大纲节点、事实、洞察和引用角标；
      7. 若存在待验证事实，渲染“风险与限制”；
      8. 渲染引用来源列表；
      9. 返回 Report 对象，repository 负责写入版本号和保存。
    """
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
    """递归渲染一个大纲节点。

    步骤：
      1. 根据当前层级选择 h3-h6 标题标签；
      2. 渲染当前 node_id 下的事实段落；
      3. 每条事实通过 `_citation_marks` 绑定引用角标；
      4. 渲染当前 node_id 下的洞察段落；
      5. 递归渲染 children；
      6. 合并为 HTML 片段返回。
    """
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
    """把事实的支撑证据映射为可点击的引用角标 [n]。

    步骤：
      1. 遍历 FactCard.supporting_evidence 中的 evidence_id；
      2. 从 ev_index 找到 Evidence；
      3. 从 Evidence.source_id 找到来源序号；
      4. 去重并排序引用序号；
      5. 生成 `<sup><a href="#src-n">[n]</a></sup>`。

    这是确定性绑定，不让 LLM 编造引用。
    """
    nums: list[int] = []
    for eid in fact.supporting_evidence:
        ev = ev_index.get(eid)
        if ev and ev.source_id in src_order:
            nums.append(src_order[ev.source_id])
    nums = sorted(set(nums))
    return "".join(f"<sup><a href='#src-{n}'>[{n}]</a></sup>" for n in nums)
