"""公网搜索工具。

桩实现：返回与 query 相关的伪造结果，使主链路无需外部依赖即可跑通。
生产替换：调用 Bing/Google/Serper/Tavily 等搜索 API，把返回适配为 Evidence。

设计方案对比：
  - 直接爬搜索结果页：成本低，但容易被反爬，稳定性和合规性差；
  - 使用搜索 API：稳定、结构化、可控，但有成本；
  - 自建垂直爬虫：可定制，但建设和维护成本高。

阶段一用桩实现；生产优先搜索 API，重点不在爬虫，而在证据管道和引用追踪。
"""
from __future__ import annotations

from app.schemas.domain import Evidence, SourceType
from app.tools.base import BaseTool


class WebSearchTool(BaseTool):
    name = "web_search"

    async def search(self, query: str, top_k: int = 8) -> list[Evidence]:
        """执行公网搜索。

        步骤：
          1. 接收原子检索问题 query；
          2. 阶段一生成最多 4 条伪造公网结果，避免依赖外部搜索 API；
          3. 每条结果适配为 Evidence，设置 source_type/title/url/content/published_at；
          4. 返回原始 Evidence，后续由 EvidencePipeline 统一清洗和排序。

        生产替换时，只需要保持返回 list[Evidence]，下游链路不需要改。
        """
        # === 生产接入点 ===
        # resp = await serper_client.search(query, num=top_k)
        # return [self._adapt(item) for item in resp["organic"]]
        results: list[Evidence] = []
        for i in range(min(top_k, 4)):
            results.append(
                Evidence(
                    source_type=SourceType.PUBLIC_WEB,
                    title=f"[公网] 关于「{query}」的资料 {i + 1}",
                    url=f"https://example.com/{abs(hash(query)) % 9999}/{i}",
                    content=(
                        f"针对「{query}」，公开资料显示该方向近年增长明显，"
                        f"主要驱动包括技术成熟度提升与企业需求增加（示例正文 {i + 1}）。"
                    ),
                    published_at="2026-05-01",
                )
            )
        return results
