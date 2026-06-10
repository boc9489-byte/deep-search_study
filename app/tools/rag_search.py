"""内部知识库检索工具（私域 RAG）。

桩实现：返回伪造的内部文档片段。
生产替换：dense(pgvector/Milvus) + BM25 召回 → RRF 融合 → 返回 chunk。
"""
from __future__ import annotations

from app.schemas.domain import Evidence, EvidenceScores, SourceType
from app.tools.base import BaseTool


class RagSearchTool(BaseTool):
    name = "rag_search"

    async def search(self, query: str, top_k: int = 8) -> list[Evidence]:
        # === 生产接入点 ===
        # dense = await vector_store.search(embed(query), k=top_k)
        # sparse = await bm25.search(query, k=top_k)
        # fused = rrf_fuse(dense, sparse)
        results: list[Evidence] = []
        for i in range(min(top_k, 2)):
            results.append(
                Evidence(
                    source_type=SourceType.INTERNAL_KB,
                    title=f"[内部] 知识库文档片段 {i + 1}",
                    url=f"kb://doc_{abs(hash(query)) % 999}/chunk_{i}",
                    content=(
                        f"内部资料：关于「{query}」，公司既有规划中已有相关场景描述，"
                        f"历史项目中沉淀过相应技术方案（示例片段 {i + 1}）。"
                    ),
                    published_at="2026-03-15",
                    # 知识库通常自带召回分，写入 rerank 位以参与融合
                    scores=EvidenceScores(rerank=0.7 - i * 0.1),
                )
            )
        return results
