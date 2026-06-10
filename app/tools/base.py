"""工具层统一协议。

Agent 只面向 BaseTool 编程，不关心底层是哪家搜索 API / 知识库。
每个工具负责把自己的原生结果"适配"为 Evidence（标准化的第一步在工具内完成
来源相关字段，跨源的清洗/打分由 pipeline 完成）。

设计方案对比：
  - 方案 A：Agent 里直接写 Serper/Bing/Milvus/httpx 调用。优点是直观；缺点是
    Agent 代码会被供应商 SDK、异常处理和字段适配污染，后续替换工具代价高。
  - 方案 B：每个外部能力实现 BaseTool，统一输出 Evidence。优点是 Agent 只管
    路由和编排，工具只管接入和适配；缺点是需要维护一个工具协议。
  - 阶段一选择方案 B，因为 DeepSearch 的核心不是某一家 API，而是多源证据统一。
"""
from __future__ import annotations

import abc
import logging

from app.schemas.domain import Evidence

logger = logging.getLogger(__name__)


class ToolError(Exception):
    pass


class BaseTool(abc.ABC):
    """检索类工具协议：输入查询，输出 Evidence 列表。

    设计方案对比：
      - 返回各工具原生 JSON：保留细节多，但 pipeline/report 需要识别多种结构；
      - 返回统一 Evidence：会丢弃少量供应商特有字段，但让去重、打分、引用绑定统一。

    本项目选择统一 Evidence；供应商特有字段如需保留，可后续加 metadata 字段。
    """

    name: str = "base_tool"

    @abc.abstractmethod
    async def search(self, query: str, top_k: int = 8) -> list[Evidence]:
        """执行检索，返回未经跨源清洗的 Evidence（content 可能是原始 snippet）。"""
        raise NotImplementedError

    async def safe_search(self, query: str, top_k: int = 8) -> list[Evidence]:
        """带 fallback 的检索：单个工具失败不应中断整条研究链路。

        设计方案对比：
          - 工具失败直接抛错：问题暴露明显，但公网搜索偶发失败会导致整份报告失败；
          - 工具失败返回空列表并记录日志：降低单点故障影响，但需要 trace/日志排查质量下降。

        阶段一选择容错返回空列表；生产建议再增加重试、超时、错误码和降级告警。
        """
        try:
            return await self.search(query, top_k=top_k)
        except Exception as exc:  # noqa: BLE001  工具层兜底
            logger.warning("tool %s failed for query=%r: %s", self.name, query, exc)
            return []
