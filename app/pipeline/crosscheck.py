"""交叉验证（Cross-Check）。

深度搜索最怕"报告很完整但结论没有证据支撑"。本模块在事实卡片生成后做强校验：
- 每个结论是否有证据；
- 证据数是否达到下限；
- 是否存在冲突来源；
- 计算置信度，过滤掉低置信事实。

confidence 的计算是确定性的（基于支撑证据的数量与质量），LLM 只负责"由证据生成
候选事实陈述"（在 agents 里），不负责给自己打分。
"""
from __future__ import annotations

from app.config import settings
from app.schemas.domain import Evidence, FactCard


def _aggregate_confidence(supporting: list[Evidence]) -> float:
    """支撑证据越多、综合分越高、来源越多样 → 置信度越高。"""
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
    """填充置信度与冲突标记。"""
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
    """按置信度阈值切分：进入结论的 / 待验证的。"""
    threshold = settings.thresholds.fact_min_confidence
    accepted = [f for f in facts if f.confidence >= threshold and not f.conflicts]
    pending = [f for f in facts if f not in accepted]
    return accepted, pending
