"""领域核心模型（domain models）。

这是系统的"内部契约"。其中 Evidence 是整个深度搜索的 universal schema：
去重、排序、引用绑定、交叉验证、报告生成、评测、trace 全部依赖它。
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #
class ProjectStatus(str, Enum):
    BRIEF_GENERATING = "brief_generating"
    OUTLINE_READY = "outline_ready"
    OUTLINE_REVISING = "outline_revising"
    OUTLINE_CONFIRMED = "outline_confirmed"
    RESEARCHING = "researching"
    REPORT_READY = "report_ready"
    FAILED = "failed"


class TaskType(str, Enum):
    GENERATE_BRIEF = "generate_research_brief"
    REVISE_OUTLINE = "revise_outline"
    GENERATE_REPORT = "generate_report"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SourceType(str, Enum):
    PUBLIC_WEB = "public_web"
    INTERNAL_KB = "internal_kb"
    DATABASE = "database"
    FILE = "file"


class RegionScope(str, Enum):
    CHINA = "china"
    OVERSEAS = "overseas"
    GLOBAL = "global"


# --------------------------------------------------------------------------- #
# 工具：ID 与时间
# --------------------------------------------------------------------------- #
def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# 研究设定 / 任务书 / 大纲
# --------------------------------------------------------------------------- #
class TimeScope(BaseModel):
    type: str = "recent_years"          # recent_years | unlimited
    years: int | None = 3


class ResearchBrief(BaseModel):
    """研究任务书：智能体对任务的结构化理解。"""
    topic: str
    objective: str = ""                 # 研究目标
    scope: str = ""                     # 研究范围描述
    default_assumptions: list[str] = Field(default_factory=list)


class OutlineNode(BaseModel):
    """大纲节点，支持多级嵌套。"""
    node_id: str
    title: str
    question: str = ""                  # 该章节要回答的核心问题
    description: str = ""               # 写作说明
    children: list["OutlineNode"] = Field(default_factory=list)

    def leaves(self) -> list["OutlineNode"]:
        """返回所有叶子节点（实际承载研究的最小单元）。"""
        if not self.children:
            return [self]
        out: list[OutlineNode] = []
        for c in self.children:
            out.extend(c.leaves())
        return out


OutlineNode.model_rebuild()


class AtomicQuestion(BaseModel):
    """由大纲叶子节点拆出的原子检索问题。"""
    question_id: str
    node_id: str
    text: str


# --------------------------------------------------------------------------- #
# 来源 / 证据 / 事实 / 洞察
# --------------------------------------------------------------------------- #
class Source(BaseModel):
    id: str = Field(default_factory=lambda: new_id("src"))
    title: str
    url: str | None = None
    published_at: str | None = None
    source_type: SourceType = SourceType.PUBLIC_WEB


class EvidenceScores(BaseModel):
    relevance: float = 0.0
    credibility: float = 0.0
    freshness: float = 0.0
    diversity: float = 0.0
    rerank: float = 0.0
    final: float = 0.0


class Evidence(BaseModel):
    """证据：平台的 universal schema。所有来源都收敛成它。"""
    evidence_id: str = Field(default_factory=lambda: new_id("ev"))
    source_type: SourceType
    source_id: str | None = None
    title: str = ""
    url: str | None = None
    content: str = ""                   # 清洗后的正文片段
    quote: str = ""                     # 支撑结论的关键摘录（短）
    published_at: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    scores: EvidenceScores = Field(default_factory=EvidenceScores)
    question_id: str | None = None
    node_id: str | None = None


class FactCard(BaseModel):
    """事实卡片：被证据支撑的事实陈述。"""
    id: str = Field(default_factory=lambda: new_id("fact"))
    node_id: str
    claim: str
    supporting_evidence: list[str] = Field(default_factory=list)  # evidence_id 列表
    confidence: float = 0.0
    conflicts: list[str] = Field(default_factory=list)  # 冲突说明


class InsightCard(BaseModel):
    """洞察卡片：基于多个事实的综合判断。"""
    id: str = Field(default_factory=lambda: new_id("ins"))
    node_id: str
    insight: str
    based_on_facts: list[str] = Field(default_factory=list)  # fact id 列表
    implication: str = ""               # 对研究目标的含义
    confidence: float = 0.0


# --------------------------------------------------------------------------- #
# 报告 / trace
# --------------------------------------------------------------------------- #
class Report(BaseModel):
    id: str = Field(default_factory=lambda: new_id("rep"))
    project_id: str
    version: int = 1
    title: str = ""
    html: str = ""
    source_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)


class TraceEvent(BaseModel):
    """链路观测事件：每个节点 / 工具调用记录一条。"""
    node: str
    input_summary: str = ""
    output_summary: str = ""
    latency_ms: int = 0
    token_usage: int = 0
    ts: float = Field(default_factory=time.time)
    error: str | None = None
