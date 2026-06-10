"""内部知识库检索工具（私域 RAG）。

桩实现：返回伪造的内部文档片段。
生产替换：dense(pgvector/Milvus) + BM25 召回 → RRF 融合 → 返回 chunk。

设计方案对比：
  - 只用向量检索：语义召回好，但编号、专有名词、表格字段命中不稳定；
  - 只用 BM25：关键词精确，但语义泛化弱；
  - Hybrid Search + RRF：兼顾语义和关键词，适合企业知识库；
  - Hybrid + Rerank：质量更好，但增加延迟和模型成本。

阶段一用桩实现；生产建议从 Hybrid + RRF 开始，再按质量需要增加 rerank。
"""
from __future__ import annotations

from app.schemas.domain import Evidence, EvidenceScores, SourceType
from app.tools.base import BaseTool


class RagSearchTool(BaseTool):
    name = "rag_search"

    async def search(self, query: str, top_k: int = 8) -> list[Evidence]:
        """执行内部知识库检索。

        步骤：
          1. 接收原子检索问题 query；
          2. 阶段一生成最多 2 条内部文档片段；
          3. 每个片段适配为 Evidence，source_type 固定为 INTERNAL_KB；
          4. 写入一个模拟 rerank 分，代表知识库召回自带的相关性信号；
          5. 返回原始 Evidence，后续进入统一 EvidencePipeline。

        生产替换时，建议 dense + BM25 + RRF 后再适配为 Evidence。
        """
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
