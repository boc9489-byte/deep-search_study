"""证据管道（Evidence Pipeline）—— 平台的核心工程资产。

四步：标准化 Normalize → 去重 Dedup → 打分 Rank → 截断保留。
没有这一层，深度搜索就只是"搜索 + 总结"。

设计要点：去重、打分、排序都是**确定性**的（不依赖 LLM），只有重排分来自模型。

设计方案对比：
  - 方案 A：把搜索结果直接塞给 LLM 总结。优点是实现最快；缺点是重复、低质、
    过期、低可信来源都会进入上下文，引用也难以复核。
  - 方案 B：先统一 Evidence Schema，再做标准化、去重、打分、截断。优点是
    质量可控、可观测、可评估；缺点是需要维护 pipeline。
  - 方案 C：完全依赖 LLM Judge 逐条筛选证据。优点是语义判断强；缺点是成本高、
    延迟高、结果稳定性差。
  - 阶段一选择方案 B，并预留 reranker / LLM Judge 作为增强项。
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
    """从正文中抽取与问题最相关的一句作为关键摘录 quote。

    步骤：
      1. 按中文/英文句号、问号、感叹号和换行切句；
      2. 对 query 和每个句子做简单 token 化；
      3. 计算 query token 与句子 token 的重叠数；
      4. 选择重叠最多的句子；
      5. 截断到 max_len，避免报告引用过长。
    """
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
    """清洗正文、抽 quote、补全字段。

    步骤：
      1. 逐条调用 PageExtractTool.enrich，模拟或执行网页正文抽取；
      2. 如果证据没有 quote，就从 content 中抽取最相关句子；
      3. 保留原 Evidence schema，不改变下游字段契约；
      4. 返回标准化后的 Evidence 列表。
    """
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
    """把 URL 归一为去重 key。

    步骤：
      1. 空 URL 返回空字符串；
      2. 解析 netloc 与 path，忽略 query/fragment；
      3. 去掉尾部 `/` 并小写；
      4. 返回稳定 key，用于同 URL 去重。
    """
    if not url:
        return ""
    p = urlparse(url)
    return f"{p.netloc}{p.path}".rstrip("/").lower()


def _content_fingerprint(content: str) -> int:
    """简化版内容指纹。

    步骤：
      1. 对正文做 token 化；
      2. 去重并排序，降低原文顺序差异带来的影响；
      3. 取前 50 个 token 控制指纹长度；
      4. 用 hash 生成阶段一指纹。

    生产用 SimHash/MinHash 做近似去重。
    """
    tokens = re.findall(r"[\w\u4e00-\u9fff]+", content.lower())
    return hash(" ".join(sorted(set(tokens))[:50]))


def dedup(evidences: list[Evidence]) -> list[Evidence]:
    """证据去重。

    步骤：
      1. 为每条 Evidence 生成 URL key 和正文指纹；
      2. 如果 URL 已出现，判定为重复来源；
      3. 如果正文指纹已出现，判定为重复内容；
      4. 非重复证据按原顺序保留；
      5. 返回去重后的 Evidence 列表。
    """
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
    """计算来源可信度分。

    步骤：
      1. 内部知识库走 internal_kb 的基础可信度；
      2. 公网来源提取域名；
      3. 命中可信域名白名单则返回对应分数；
      4. 未知来源返回保守基础分。
    """
    if ev.source_type == SourceType.INTERNAL_KB:
        return settings.credibility_whitelist.get("internal_kb", 0.85)
    domain = _normalize_url(ev.url).split("/")[0]
    for known, score in settings.credibility_whitelist.items():
        if known in domain:
            return score
    return 0.55  # 未知来源基础可信度


def _freshness(ev: Evidence) -> float:
    """按发布时间衰减；越新越高。

    步骤：
      1. 没有 published_at 时返回中性分 0.5；
      2. 尝试解析 ISO 日期；
      3. 计算距离今天的天数；
      4. 按三年窗口线性衰减，最低保留 0.1。
    """
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
    """为每条证据打分并按 final 降序；含来源多样性降权（MMR 思想）。

    步骤：
      1. 读取配置中的证据评分权重；
      2. 对每条 Evidence 获取 rerank_score；
      3. 计算 relevance、credibility、freshness、diversity；
      4. 按加权平均得到 final；
      5. 把所有分数写回 Evidence.scores；
      6. 按 final 从高到低排序。
    """
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
    """证据处理管道。

    功能：
      把多个工具返回的原始 Evidence 处理成可排序、可引用、可进入事实合成的证据集。

    实现说明：
      Pipeline 本身不关心证据来自公网还是内部库，只依赖统一 Evidence Schema。
      extractor 和 reranker 可注入，便于测试替身和生产服务替换。
    """

    def __init__(
        self,
        extractor: PageExtractTool | None = None,
        reranker: RerankerTool | None = None,
    ) -> None:
        """初始化证据管道依赖。

        功能：
          装配网页正文抽取工具和重排工具。

        输入输出：
          输入可选 extractor/reranker；无返回值。

        实现说明：
          默认使用阶段一桩工具；生产环境可以传入真实 Page Extract 服务和 Reranker。
        """
        self.extractor = extractor or PageExtractTool()
        self.reranker = reranker or RerankerTool()

    async def run(self, raw: list[Evidence], query: str) -> list[Evidence]:
        """运行完整 Evidence Pipeline。

        步骤：
          1. normalize：正文清洗和 quote 抽取；
          2. dedup：URL 与内容指纹去重；
          3. rank：多因子打分和排序；
          4. keep：按 `per_question_keep` 截断，控制下游上下文规模。
        """
        evidences = await normalize(raw, query, self.extractor)
        evidences = dedup(evidences)
        evidences = await rank(evidences, query, self.reranker)
        keep = settings.retrieval.per_question_keep
        return evidences[:keep]
