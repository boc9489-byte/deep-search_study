"""智能体层（agents）—— 系统的"大脑"，只关心"在哪里用 LLM、怎么用"。

每个决策点都是一个 `输入 → 结构化输出` 的纯函数，便于单测与替换模型。
LLMClient 在此处给出桩实现（返回确定性结构化结果），生产替换为真实模型调用，
建议用"结构化输出/工具调用"约束模型只返回 JSON。
"""
from __future__ import annotations

from app.config import settings
from app.pipeline.crosscheck import verify_fact
from app.pipeline.evidence_pipeline import EvidencePipeline
from app.schemas.domain import (
    AtomicQuestion,
    Evidence,
    FactCard,
    InsightCard,
    OutlineNode,
    ResearchBrief,
    SourceType,
    new_id,
)
from app.tools.base import BaseTool


# --------------------------------------------------------------------------- #
# LLM 客户端（抽象 + 桩）
# --------------------------------------------------------------------------- #
class LLMClient:
    """生产替换为真实 Anthropic/OpenAI 调用；此处返回确定性结果以便跑通主链路。"""

    def __init__(self, model: str = settings.llm_model) -> None:
        self.model = model

    async def structured(self, prompt: str, schema_hint: str = "") -> dict:
        # === 生产接入点 ===
        # resp = await client.messages.create(model=self.model, ...,
        #     tools=[{"name": "emit", "input_schema": ...}])
        # return parse_tool_use(resp)
        raise NotImplementedError("由各 agent 的桩方法直接构造结果")


# --------------------------------------------------------------------------- #
# 研究管理智能体
# --------------------------------------------------------------------------- #
class ResearchManagerAgent:
    """编排者：理解任务、生成大纲、拆子问题、合成事实与洞察。"""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    async def understand_brief(self, topic: str, goal: str, audience: str) -> ResearchBrief:
        """把模糊问题结构化为研究任务书（LLM 决策点）。"""
        return ResearchBrief(
            topic=topic,
            objective=goal or f"围绕「{topic}」形成可支撑决策的研究结论",
            scope=f"面向{audience or '决策团队'}，聚焦核心机会与风险",
            default_assumptions=[
                "若用户未指定，默认覆盖近三年信息",
                "内部知识库结果需与研究问题相关才进入报告",
            ],
        )

    async def generate_outline(self, brief: ResearchBrief) -> list[OutlineNode]:
        """基于任务书生成大纲（LLM 决策点）。桩：给出通用研究骨架。"""
        return [
            OutlineNode(
                node_id="1", title="行业定义与研究边界",
                question=f"本报告讨论的「{brief.topic}」具体指什么？",
                description="明确定义、研究对象与不覆盖的边界",
                children=[
                    OutlineNode(node_id="1.1", title="核心定义",
                                question="与相邻概念的区别是什么？",
                                description="说明核心概念与技术边界"),
                    OutlineNode(node_id="1.2", title="研究范围",
                                question="覆盖哪些地区、时间与场景？",
                                description="说明地域/时间/场景边界"),
                ],
            ),
            OutlineNode(node_id="2", title="市场规模与增长驱动",
                        question="未来是否存在足够大的增长空间？",
                        description="分析规模、增速、驱动因素与不确定性"),
            OutlineNode(node_id="3", title="竞争格局与关键玩家",
                        question="主要参与者是谁、差异在哪？",
                        description="梳理头部玩家与产品对比"),
            OutlineNode(node_id="4", title="风险与建议",
                        question="落地风险是什么、是否建议推进？",
                        description="给出风险判断与行动建议"),
        ]

    async def revise_outline(
        self, outline: list[OutlineNode], instruction: str
    ) -> list[OutlineNode]:
        """按自然语言指令修改大纲（LLM 决策点）。桩：追加一章示意。"""
        revised = list(outline)
        revised.append(
            OutlineNode(
                node_id=str(len(outline) + 1),
                title="（按用户意见新增）专题分析",
                question=instruction,
                description=f"根据修改意见调整：{instruction}",
            )
        )
        return revised

    async def plan_questions(self, node: OutlineNode) -> list[AtomicQuestion]:
        """把一个大纲叶子拆成原子检索问题（LLM 决策点）。"""
        base = node.question or node.title
        texts = [base, f"{node.title} 的关键数据与案例"]
        return [
            AtomicQuestion(question_id=f"q_{node.node_id}_{i}", node_id=node.node_id, text=t)
            for i, t in enumerate(texts)
        ]

    async def synthesize_facts(
        self, node_id: str, question: AtomicQuestion, evidences: list[Evidence]
    ) -> list[FactCard]:
        """由证据合成候选事实陈述（LLM 决策点），再交 crosscheck 打分。"""
        if not evidences:
            return []
        # 桩：取 top 证据构造一个事实，挂载其证据 id
        top = evidences[: min(3, len(evidences))]
        fact = FactCard(
            node_id=node_id,
            claim=f"围绕「{question.text}」，多源资料显示存在明确的趋势与支撑。",
            supporting_evidence=[e.evidence_id for e in top],
        )
        index = {e.evidence_id: e for e in evidences}
        return [verify_fact(fact, index)]

    async def synthesize_insights(
        self, node_id: str, facts: list[FactCard]
    ) -> list[InsightCard]:
        """由多个事实合成洞察（LLM 决策点）。"""
        if not facts:
            return []
        avg_conf = sum(f.confidence for f in facts) / len(facts)
        return [
            InsightCard(
                node_id=node_id,
                insight="综合已验证事实，该方向具备值得关注的机会，但需关注落地条件。",
                based_on_facts=[f.id for f in facts],
                implication="建议纳入下一阶段评估范围。",
                confidence=round(avg_conf, 4),
            )
        ]


# --------------------------------------------------------------------------- #
# 信息检索智能体
# --------------------------------------------------------------------------- #
class RetrievalAgent:
    """执行者：决定检索路由 → 多源检索 → 证据管道 → 返回有序 Evidence。"""

    def __init__(
        self,
        web_tool: BaseTool,
        kb_tool: BaseTool,
        pipeline: EvidencePipeline | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self.web_tool = web_tool
        self.kb_tool = kb_tool
        self.pipeline = pipeline or EvidencePipeline()
        self.llm = llm or LLMClient()

    async def decide_route(self, question: AtomicQuestion) -> list[SourceType]:
        """判断该子问题去哪里查（LLM 决策点，可降级为规则）。"""
        routes = [SourceType.PUBLIC_WEB]
        if settings.retrieval.enable_internal_kb:
            routes.append(SourceType.INTERNAL_KB)
        return routes

    async def retrieve(self, question: AtomicQuestion) -> list[Evidence]:
        routes = await self.decide_route(question)
        raw: list[Evidence] = []
        if SourceType.PUBLIC_WEB in routes:
            raw += await self.web_tool.safe_search(
                question.text, top_k=settings.retrieval.web_top_k
            )
        if SourceType.INTERNAL_KB in routes:
            raw += await self.kb_tool.safe_search(
                question.text, top_k=settings.retrieval.kb_top_k
            )
        # 打标：归属问题与节点
        for ev in raw:
            ev.question_id = question.question_id
            ev.node_id = question.node_id
        # 走证据管道：标准化 → 去重 → 打分 → 截断
        return await self.pipeline.run(raw, question.text)
