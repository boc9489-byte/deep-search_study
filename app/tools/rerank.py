"""重排工具（Reranker）。

桩实现：用 query 与 content 的词重叠近似语义相关度。
生产替换：Cross-Encoder（bge-reranker / cohere-rerank 等），输入 (query, doc) 对
         输出语义相关分，比 dense 召回分更准。
"""
from __future__ import annotations

import re


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[\w\u4e00-\u9fff]+", text.lower()))


class RerankerTool:
    name = "reranker"

    async def score(self, query: str, content: str) -> float:
        # === 生产接入点 ===
        # return await cross_encoder.predict([(query, content)])[0]
        q, c = _tokenize(query), _tokenize(content)
        if not q or not c:
            return 0.0
        overlap = len(q & c) / len(q)
        return round(min(1.0, 0.3 + overlap), 4)  # 给个非零基线，避免全 0
