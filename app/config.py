"""集中配置：环境变量、模型、检索参数、阈值、证据打分权重。

生产环境用 pydantic-settings 从 .env / 环境变量加载；此处给出默认值，
保证 clone 后无需任何外部配置即可跑通主链路。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvidenceScoreWeights:
    """证据综合打分的因子权重。

    sum 不要求为 1，`EvidencePipeline.rank()` 会按总权重归一化。
    生产调参建议：
      - 行业研究偏公开权威来源：提高 credibility / freshness；
      - 内部知识问答偏语义匹配：提高 relevance / rerank；
      - 多来源交叉验证偏覆盖面：提高 diversity。
    """
    relevance: float = 0.35
    credibility: float = 0.20
    freshness: float = 0.15
    diversity: float = 0.10
    rerank: float = 0.20


@dataclass
class RetrievalConfig:
    """检索侧配置。

    阶段一使用桩工具，字段先固化为代码默认值；生产环境建议通过
    pydantic-settings 从环境变量、.env 或配置中心加载。
    """

    web_top_k: int = 8                # 单次公网检索返回条数；真实搜索 API 会影响成本
    kb_top_k: int = 8                 # 单次知识库检索返回条数；Hybrid Search 可拆 dense/sparse top_k
    per_question_keep: int = 6        # 每个原子问题最终保留的证据数，控制下游上下文规模
    dedup_simhash_distance: int = 3   # 预留：生产 SimHash 近似去重的汉明距离阈值
    enable_internal_kb: bool = True   # 默认查内部库，由智能体/规则判断是否进入报告


@dataclass
class QualityThresholds:
    """事实与洞察进入报告的质量门槛。"""

    fact_min_confidence: float = 0.5    # 低于此置信度的事实不进结论
    insight_min_confidence: float = 0.5
    min_sources_per_fact: int = 1       # 一个事实至少需要的来源数


@dataclass
class Settings:
    app_name: str = "deepsearch"
    api_prefix: str = "/api/v1"

    # 模型标识：阶段一仅作为 trace / 配置占位，生产替换为真实模型名和 endpoint。
    llm_model: str = "claude-sonnet-4-6"
    reranker_model: str = "rerank-cross-encoder-v1"

    # 并发：研究执行时同时处理的原子问题数。
    # 接入真实搜索/网页解析/LLM 后，要结合外部 API 限流和成本下调。
    max_concurrent_questions: int = 4

    weights: EvidenceScoreWeights = field(default_factory=EvidenceScoreWeights)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    thresholds: QualityThresholds = field(default_factory=QualityThresholds)

    # 来源可信度白名单（域名 -> 基础可信度）。
    # 生产建议按组织维护：政府/监管、论文、行业数据库、内部文档、低可信来源黑名单。
    credibility_whitelist: dict[str, float] = field(
        default_factory=lambda: {
            "gov.cn": 0.95, "stats.gov.cn": 0.95,
            "nature.com": 0.9, "arxiv.org": 0.8,
            "internal_kb": 0.85,  # 内部库基础可信度
        }
    )


settings = Settings()
