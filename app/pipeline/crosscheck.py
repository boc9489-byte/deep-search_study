"""交叉验证（Cross-Check）。

深度搜索最怕"报告很完整但结论没有证据支撑"。本模块在事实卡片生成后做强校验：
- 每个结论是否有证据；
- 证据数是否达到下限；
- 是否存在冲突来源；
- 计算置信度，过滤掉低置信事实。

confidence 的计算是确定性的（基于支撑证据的数量与质量），LLM 只负责"由证据生成
候选事实陈述"（在 agents 里），不负责给自己打分。

设计方案对比：
  - 方案 A：让 LLM 同时生成事实、判断事实可信度。优点是实现快；缺点是模型容易
    自我背书，分数不可复算，也不利于回归评测。
  - 方案 B：LLM 只生成候选事实，置信度由证据数量、来源多样性、证据分数确定性计算。
    优点是可解释、可调参、可测试；缺点是早期规则会比较粗糙。
  - 阶段一选择方案 B，后续可以在确定性规则外叠加 NLI/LLM Judge 做冲突检测。
"""
from __future__ import annotations

from app.config import settings
from app.schemas.domain import Evidence, FactCard


def _aggregate_confidence(supporting: list[Evidence]) -> float:
    """支撑证据越多、综合分越高、来源越多样 → 置信度越高。

    设计方案对比：
      - 简单平均 Evidence.final：容易被单一来源多条重复内容抬高；
      - 加入数量和来源类型加成：更符合交叉验证直觉；
      - 复杂贝叶斯/学习排序模型：更准但需要标注数据。

    阶段一采用“平均质量 + 数量加成 + 来源多样性”的可解释公式。
    """
    if not supporting:
        return 0.0
    avg_final = sum(e.scores.final for e in supporting) / len(supporting)
    # 多来源加成：到 3 条封顶
    count_boost = min(len(supporting), 3) / 3
    distinct_sources = len({e.source_type for e in supporting})
    diversity_boost = min(distinct_sources, 2) / 2
    conf = 0.6 * avg_final + 0.25 * count_boost + 0.15 * diversity_boost
    return round(min(1.0, conf), 4)


def verify_fact(fact: FactCard, evidence_index: dict[str, Evidence]) -> FactCard:
    """填充置信度与冲突标记。

    设计方案对比：
      - 在事实生成时直接过滤：流程短，但丢失低置信事实，不方便展示风险；
      - 先保留 FactCard，再打 confidence/conflicts：报告可把低置信内容放到“风险与限制”。

    本项目选择后者，保证证据不足不是静默丢失，而是可观测、可解释。
    """
    supporting = [
        evidence_index[eid] for eid in fact.supporting_evidence if eid in evidence_index
    ]
    fact.confidence = _aggregate_confidence(supporting)

    # 冲突检测占位：生产可用 NLI 判断证据间是否矛盾
    # if has_contradiction(supporting): fact.conflicts.append("来源间存在分歧")
    if len(supporting) < settings.thresholds.min_sources_per_fact:
        fact.conflicts.append("支撑证据不足")
    return fact


def filter_facts(facts: list[FactCard]) -> tuple[list[FactCard], list[FactCard]]:
    """按置信度阈值切分：进入结论的 / 待验证的。

    设计方案对比：
      - 把所有事实都写入正文：信息多，但容易混入弱证据结论；
      - 只写高置信事实，低置信事实放“风险与限制”：更适合企业报告审阅。

    阶段一选择第二种，阈值由 `settings.thresholds.fact_min_confidence` 控制。
    """
    threshold = settings.thresholds.fact_min_confidence
    accepted = [f for f in facts if f.confidence >= threshold and not f.conflicts]
    pending = [f for f in facts if f not in accepted]
    return accepted, pending
