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
    """研究项目生命周期状态。

    功能：
      描述一个项目从创建、大纲准备、研究执行到报告完成的业务状态。

    实现说明：
      路由层用它限制用户下一步动作；前端用它展示流程状态。
    """

    BRIEF_GENERATING = "brief_generating"
    OUTLINE_READY = "outline_ready"
    OUTLINE_REVISING = "outline_revising"
    OUTLINE_CONFIRMED = "outline_confirmed"
    RESEARCHING = "researching"
    REPORT_READY = "report_ready"
    FAILED = "failed"


class TaskType(str, Enum):
    """后台任务类型。

    功能：
      区分大纲生成、修改大纲、生成报告三类异步作业。
    """

    GENERATE_BRIEF = "generate_research_brief"
    REVISE_OUTLINE = "revise_outline"
    GENERATE_REPORT = "generate_report"


class TaskStatus(str, Enum):
    """后台任务执行状态。

    功能：
      给前端轮询和测试断言使用。
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SourceType(str, Enum):
    """证据来源类型。

    功能：
      标记 Evidence 来自公网、内部知识库、数据库或文件。

    实现说明：
      来源类型参与可信度、交叉验证和报告引用展示。
    """

    PUBLIC_WEB = "public_web"
    INTERNAL_KB = "internal_kb"
    DATABASE = "database"
    FILE = "file"


class RegionScope(str, Enum):
    """研究地域范围。

    功能：
      表达研究面向中国、海外或全球市场。
    """

    CHINA = "china"
    OVERSEAS = "overseas"
    GLOBAL = "global"


# --------------------------------------------------------------------------- #
# 工具：ID 与时间
# --------------------------------------------------------------------------- #
def new_id(prefix: str) -> str:
    """生成带前缀的短 ID。

    输入：业务前缀，例如 proj/task/ev/fact。
    输出：形如 `proj_xxx` 的字符串。
    实现：使用 uuid4 的前 12 位，阶段一足够稳定；生产可替换为雪花 ID 或数据库 ID。
    """
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    """返回 UTC 当前时间。

    功能：
      统一项目、任务、证据、报告的时间字段时区。

    实现说明：
      使用 timezone-aware datetime，避免本地时区和部署时区混用。
    """
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# 研究设定 / 任务书 / 大纲
# --------------------------------------------------------------------------- #
class TimeScope(BaseModel):
    """研究时间范围。

    功能：
      表示只看近 N 年，或不限制时间。
    """

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
    """由大纲叶子节点拆出的原子检索问题。

    功能：
      把一个章节问题拆成更小的检索单元。

    实现说明：
      question_id 用于 trace 和 evidence 归因；node_id 用于把证据回挂到大纲节点。
    """
    question_id: str
    node_id: str
    text: str


# --------------------------------------------------------------------------- #
# 来源 / 证据 / 事实 / 洞察
# --------------------------------------------------------------------------- #
class Source(BaseModel):
    """报告引用来源。

    功能：
      表示一篇网页、一个知识库文档片段来源、数据库记录或文件来源。

    实现说明：
      Source 是报告引用列表的落点；Evidence.source_id 会指向 Source.id。
    """

    id: str = Field(default_factory=lambda: new_id("src"))
    title: str
    url: str | None = None
    published_at: str | None = None
    source_type: SourceType = SourceType.PUBLIC_WEB


class EvidenceScores(BaseModel):
    """证据评分。

    功能：
      保存 Evidence Pipeline 中各维度评分和最终融合分。

    实现说明：
      final 用于排序，分项分数用于可解释调参和问题排查。
    """

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
    """研究报告版本。

    功能：
      保存一次报告生成的 HTML、版本号和引用来源 ID。

    实现说明：
      报告正文不直接嵌入 Source 对象，而保存 source_ids，便于来源单独管理和复用。
    """

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
