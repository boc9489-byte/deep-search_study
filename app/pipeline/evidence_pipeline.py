"""证据管道（Evidence Pipeline）—— 平台的核心工程资产。

四步：标准化 Normalize → 去重 Dedup → 打分 Rank → 截断保留。
没有这一层，深度搜索就只是"搜索 + 总结"。

设计要点：去重、打分、排序都是**确定性**的（不依赖 LLM），只有重排分来自模型。
"""
from __future__ import annotations

import re
from datetime import date
from urllib.parse import urlparse

from app.config import settings
from app.schemas.domain import Evidence, SourceType
from app.tools.page_extract import PageExtractTool
from app.tools.rerank import RerankerTool


# --------------------------------------------------------------------------- #
# 1) 标准化
# --------------------------------------------------------------------------- #
def _extract_quote(content: str, query: str, max_len: int = 120) -> str:
    """从正文中抽取与问题最相关的一句作为关键摘录 quote。"""
    sentences = re.split(r"[。.!?！？\n]", content)
    q_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query.lower()))
    best, best_score = "", -1
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        s_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", s.lower()))
        score = len(q_tokens & s_tokens)
        if score > best_score:
            best, best_score = s, score
    return best[:max_len]


async def normalize(
    evidences: list[Evidence], query: str, extractor: PageExtractTool
) -> list[Evidence]:
    """清洗正文、抽 quote、补全字段。"""
    out: list[Evidence] = []
    for ev in evidences:
        ev = await extractor.enrich(ev)          # 抓正文/清洗
        if not ev.quote:
            ev.quote = _extract_quote(ev.content, query)
        out.append(ev)
    return out


# --------------------------------------------------------------------------- #
# 2) 去重（确定性：URL 归一 + 内容指纹）
# --------------------------------------------------------------------------- #
def _normalize_url(url: str | None) -> str:
    if not url:
        return ""
    p = urlparse(url)
    return f"{p.netloc}{p.path}".rstrip("/").lower()


def _content_fingerprint(content: str) -> int:
    """简化版指纹（生产用 SimHash/MinHash 做近似去重）。"""
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", content.lower())
    return hash(" ".join(sorted(set(tokens))[:50]))


def dedup(evidences: list[Evidence]) -> list[Evidence]:
    seen_url: set[str] = set()
    seen_fp: set[int] = set()
    out: list[Evidence] = []
    for ev in evidences:
        url_key = _normalize_url(ev.url)
        fp = _content_fingerprint(ev.content)
        if url_key and url_key in seen_url:
            continue
        if fp in seen_fp:
            continue
        if url_key:
            seen_url.add(url_key)
        seen_fp.add(fp)
        out.append(ev)
    return out


# --------------------------------------------------------------------------- #
# 3) 打分（多因子融合）
# --------------------------------------------------------------------------- #
def _credibility(ev: Evidence) -> float:
    if ev.source_type == SourceType.INTERNAL_KB:
        return settings.credibility_whitelist.get("internal_kb", 0.85)
    domain = _normalize_url(ev.url).split("/")[0]
    for known, score in settings.credibility_whitelist.items():
        if known in domain:
            return score
    return 0.55  # 未知来源基础可信度


def _freshness(ev: Evidence) -> float:
    """按发布时间衰减；越新越高。"""
    if not ev.published_at:
        return 0.5
    try:
        pub = date.fromisoformat(ev.published_at[:10])
    except ValueError:
        return 0.5
    days = max(0, (date.today() - pub).days)
    # 一年内 ~1.0，三年外快速衰减
    return round(max(0.1, 1.0 - days / 1095), 4)


async def rank(
    evidences: list[Evidence], query: str, reranker: RerankerTool
) -> list[Evidence]:
    """为每条证据打分并按 final 降序；含来源多样性降权（MMR 思想）。"""
    w = settings.weights
    total_w = w.relevance + w.credibility + w.freshness + w.diversity + w.rerank

    domain_seen: dict[str, int] = {}
    for ev in evidences:
        rerank_score = ev.scores.rerank or await reranker.score(query, ev.content)
        relevance = rerank_score                      # 桩中以重排分近似相关性
        credibility = _credibility(ev)
        freshness = _freshness(ev)

        domain = _normalize_url(ev.url).split("/")[0] or ev.source_type.value
        repeat = domain_seen.get(domain, 0)
        diversity = round(1.0 / (1 + repeat), 4)      # 同源重复降权
        domain_seen[domain] = repeat + 1

        final = (
            w.relevance * relevance
            + w.credibility * credibility
            + w.freshness * freshness
            + w.diversity * diversity
            + w.rerank * rerank_score
        ) / total_w

        ev.scores.relevance = round(relevance, 4)
        ev.scores.credibility = round(credibility, 4)
        ev.scores.freshness = freshness
        ev.scores.diversity = diversity
        ev.scores.rerank = round(rerank_score, 4)
        ev.scores.final = round(final, 4)

    return sorted(evidences, key=lambda e: e.scores.final, reverse=True)


# --------------------------------------------------------------------------- #
# 编排：完整管道
# --------------------------------------------------------------------------- #
class EvidencePipeline:
    def __init__(
        self,
        extractor: PageExtractTool | None = None,
        reranker: RerankerTool | None = None,
    ) -> None:
        self.extractor = extractor or PageExtractTool()
        self.reranker = reranker or RerankerTool()

    async def run(self, raw: list[Evidence], query: str) -> list[Evidence]:
        evidences = await normalize(raw, query, self.extractor)
        evidences = dedup(evidences)
        evidences = await rank(evidences, query, self.reranker)
        keep = settings.retrieval.per_question_keep
        return evidences[:keep]
