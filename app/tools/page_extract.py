"""网页正文解析工具（Read 阶段）。

搜索 API 通常只给 title/snippet/url，深度搜索需要真正"打开网页读正文"。
桩实现：基于已有 content 模拟"抓取并清洗正文"。
生产替换：httpx 抓取 → trafilatura/readability 抽正文 → 去广告/导航/脚本
         → 识别发布时间/作者 → 过长内容摘要压缩。
"""
from __future__ import annotations

from app.schemas.domain import Evidence, SourceType


class PageExtractTool:
    name = "page_extract"

    async def enrich(self, evidence: Evidence) -> Evidence:
        """把仅含 snippet 的证据升级为含清洗正文的证据。

        步骤：
          1. 判断证据类型：内部库片段已经是正文，直接返回；
          2. 对公网证据，阶段一在已有 content 后追加“已清洗”标记；
          3. 生产环境这里会真实请求 URL、抽正文、识别发布时间；
          4. 返回仍然是同一个 Evidence 对象，保持下游 schema 稳定。
        """
        # Step 1: 内部库片段已是正文，无需抓取。
        if evidence.source_type != SourceType.PUBLIC_WEB or not evidence.url:
            return evidence

        # === 生产接入点 ===
        # html = await httpx_get(evidence.url)
        # main_text = trafilatura.extract(html)
        # published = detect_published_date(html) or evidence.published_at
        main_text = (
            evidence.content
            + "（正文已清洗：移除广告/导航/脚本，保留主要论述。）"
        )
        evidence.content = main_text
        return evidence
