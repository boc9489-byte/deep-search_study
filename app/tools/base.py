"""工具层统一协议。

Agent 只面向 BaseTool 编程，不关心底层是哪家搜索 API / 知识库。
每个工具负责把自己的原生结果"适配"为 Evidence（标准化的第一步在工具内完成
来源相关字段，跨源的清洗/打分由 pipeline 完成）。
"""
from __future__ import annotations

import abc
import logging

from app.schemas.domain import Evidence

logger = logging.getLogger(__name__)


class ToolError(Exception):
    pass


class BaseTool(abc.ABC):
    """检索类工具协议：输入查询，输出 Evidence 列表。"""

    name: str = "base_tool"

    @abc.abstractmethod
    async def search(self, query: str, top_k: int = 8) -> list[Evidence]:
        """执行检索，返回未经跨源清洗的 Evidence（content 可能是原始 snippet）。"""
        raise NotImplementedError

    async def safe_search(self, query: str, top_k: int = 8) -> list[Evidence]:
        """带 fallback 的检索：单个工具失败不应中断整条研究链路。"""
        try:
            return await self.search(query, top_k=top_k)
        except Exception as exc:  # noqa: BLE001  工具层兜底
            logger.warning("tool %s failed for query=%r: %s", self.name, query, exc)
            return []
