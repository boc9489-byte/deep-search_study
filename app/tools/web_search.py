"""公网搜索工具。

桩实现：返回与 query 相关的伪造结果，使主链路无需外部依赖即可跑通。
生产替换：调用 Bing/Google/Serper/Tavily 等搜索 API，把返回适配为 Evidence。
"""
from __future__ import annotations

from app.schemas.domain import Evidence, SourceType
from app.tools.base import BaseTool


class WebSearchTool(BaseTool):
    name = "web_search"

    async def search(self, query: str, top_k: int = 8) -> list[Evidence]:
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
