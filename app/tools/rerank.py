"""重排工具（Reranker）。

桩实现：用 query 与 content 的词重叠近似语义相关度。
生产替换：Cross-Encoder（bge-reranker / cohere-rerank 等），输入 (query, doc) 对
         输出语义相关分，比 dense 召回分更准。
"""
from __future__ import annotations

import re


def _tokenize(text: str) -> set[str]:
    """阶段一简化 token 化。

    步骤：
      1. 用正则提取英文/数字/中文连续片段；
      2. 全部转小写；
      3. 返回 set，后续用交集近似相关性。
    """
    return set(re.findall(r"[\w\u4e00-\u9fff]+", text.lower()))


class RerankerTool:
    name = "reranker"

    async def score(self, query: str, content: str) -> float:
        """计算 query 与 content 的相关性分。

        步骤：
          1. 分别 token 化 query 和 content；
          2. 任一为空时返回 0；
          3. 计算 query token 被 content 命中的比例；
          4. 加一个 0.3 基线，避免阶段一桩数据全部为 0；
          5. 截断到 1.0，并保留 4 位小数。

        生产环境用 Cross-Encoder / Rerank API 替换该逻辑。
        """
        # === 生产接入点 ===
        # return await cross_encoder.predict([(query, content)])[0]
        q, c = _tokenize(query), _tokenize(content)
        if not q or not c:
            return 0.0
        overlap = len(q & c) / len(q)
        return round(min(1.0, 0.3 + overlap), 4)  # 给个非零基线，避免全 0
