"""智能体层（agents）—— 系统的"大脑"，只关心"在哪里用 LLM、怎么用"。

每个决策点都是一个 `输入 → 结构化输出` 的纯函数，便于单测与替换模型。
LLMClient 在此处给出桩实现（返回确定性结构化结果），生产替换为真实模型调用，
建议用"结构化输出/工具调用"约束模型只返回 JSON。

设计方案对比：
  - 方案 A：一个“大 Agent”同时负责计划、搜索、阅读、写报告。优点是开发快；
    缺点是黑盒、难测试、难定位质量问题。
  - 方案 B：双智能体 + 工具 + pipeline。ResearchManager 负责计划/合成，
    RetrievalAgent 负责检索执行，EvidencePipeline 负责质量控制。优点是边界清楚；
    缺点是模块更多。
  - 阶段一选择方案 B，因为企业研究系统更看重可追溯、可测试和可替换。
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
    """生产替换为真实 Anthropic/OpenAI 调用；此处返回确定性结果以便跑通主链路。

    设计方案对比：
      - 直接在每个 Agent 方法里调用 SDK：接入快，但模型、超时、重试、日志会散落；
      - 统一 LLMClient：便于集中处理模型选择、结构化输出、重试、token 统计和审计。

    本项目保留 LLMClient 抽象，阶段一不实际调用外部模型。
    """

    def __init__(self, model: str = settings.llm_model) -> None:
        """初始化 LLM 客户端。

        功能：
          保存模型标识，供后续真实模型调用、trace 和审计使用。

        输入输出：
          输入模型名；无返回值。

        实现说明：
          阶段一不创建真实 SDK client，避免本地运行依赖 API key。
        """
        self.model = model

    async def structured(self, prompt: str, schema_hint: str = "") -> dict:
        """结构化模型调用接口。

        功能：
          约定生产环境中所有 LLM 决策都应返回结构化 JSON，而不是自由文本。

        输入输出：
          输入 prompt 和 schema_hint；输出 dict。

        实现说明：
          阶段一由各 agent 方法直接构造确定性结果，因此这里显式抛出未实现。
        """
        # === 生产接入点 ===
        # resp = await client.messages.create(model=self.model, ...,
        #     tools=[{"name": "emit", "input_schema": ...}])
        # return parse_tool_use(resp)
        raise NotImplementedError("由各 agent 的桩方法直接构造结果")


# --------------------------------------------------------------------------- #
# 研究管理智能体
# --------------------------------------------------------------------------- #
class ResearchManagerAgent:
    """编排者：理解任务、生成大纲、拆子问题、合成事实与洞察。

    设计方案对比：
      - 把计划逻辑放在 workflow 节点里：文件少，但节点会变成复杂业务代码；
      - 把语义决策放在 Agent，workflow 只负责串联：职责清楚，方便替换 LLM 策略。

    本项目选择后者。
    """

    def __init__(self, llm: LLMClient | None = None) -> None:
        """初始化研究管理智能体。

        功能：
          注入 LLMClient，供任务理解、大纲生成、事实/洞察合成使用。

        实现说明：
          默认使用阶段一 LLMClient 桩；测试或生产可以传入替代实现。
        """
        self.llm = llm or LLMClient()

    async def understand_brief(self, topic: str, goal: str, audience: str) -> ResearchBrief:
        """把模糊问题结构化为研究任务书（LLM 决策点）。

        步骤：
          1. 接收用户输入的 topic / research_goal / target_audience；
          2. 补齐缺省研究目标和受众描述；
          3. 固化默认假设，避免后续节点反复猜测边界；
          4. 返回 ResearchBrief，作为大纲生成的唯一输入。
        """
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
        """基于任务书生成大纲（LLM 决策点）。

        步骤：
          1. 读取 ResearchBrief 中的 topic / objective / scope；
          2. 生成一级章节，覆盖定义、市场、竞争、风险建议；
          3. 对需要细化的章节补充 children，形成可递归遍历的大纲树；
          4. 每个节点都提供 question，后续用它拆原子检索问题。

        阶段一为确定性桩：给出通用研究骨架。
        """
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
        """按自然语言指令修改大纲（LLM 决策点）。

        步骤：
          1. 接收当前大纲和用户修改意见；
          2. 基于旧大纲生成新版大纲，而不是原地修改旧对象；
          3. 保持 OutlineNode 结构不变，保证前端和后续 workflow 不需要适配；
          4. 返回新版大纲，后台任务负责保存并更新状态。

        阶段一为确定性桩：追加一章示意。
        """
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
        """把一个大纲叶子拆成原子检索问题（LLM 决策点）。

        步骤：
          1. 取叶子节点的 question，若为空则退回 title；
          2. 生成一个“直接回答章节问题”的检索问题；
          3. 再生成一个“关键数据与案例”的补充检索问题；
          4. 写入 question_id 和 node_id，保证证据能回挂到大纲节点。
        """
        base = node.question or node.title
        texts = [base, f"{node.title} 的关键数据与案例"]
        return [
            AtomicQuestion(question_id=f"q_{node.node_id}_{i}", node_id=node.node_id, text=t)
            for i, t in enumerate(texts)
        ]

    async def synthesize_facts(
        self, node_id: str, question: AtomicQuestion, evidences: list[Evidence]
    ) -> list[FactCard]:
        """由证据合成候选事实陈述（LLM 决策点），再交 crosscheck 打分。

        步骤：
          1. 如果没有证据，直接返回空事实，避免凭空生成结论；
          2. 取排序后的 Top Evidence 作为候选事实支撑；
          3. 生成 FactCard，并挂载 supporting_evidence；
          4. 调用 `verify_fact` 基于证据数量、来源和分数计算 confidence；
          5. 返回已验证事实，后续报告只引用这些结构化事实。
        """
        if not evidences:
            return []
        # 阶段一桩：取 top 证据构造一个事实，挂载其证据 id。
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
        """由多个事实合成洞察（LLM 决策点）。

        步骤：
          1. 如果当前节点没有事实，不生成洞察；
          2. 汇总同一 node_id 下的事实置信度；
          3. 生成 InsightCard，并记录 based_on_facts；
          4. 将事实平均置信度传递给 insight，供报告摘要筛选。
        """
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
    """执行者：决定检索路由 → 多源检索 → 证据管道 → 返回有序 Evidence。

    设计方案对比：
      - 普通 RAG：一个 query 直接向量检索 TopK。实现简单，但对复杂研究问题覆盖不足；
      - DeepSearch：先按大纲拆原子问题，再对每个问题多源检索。成本更高，但覆盖面、
        可追踪性和报告结构更好。

    本项目选择 DeepSearch 路线，RetrievalAgent 负责把每个原子问题变成证据集合。
    """

    def __init__(
        self,
        web_tool: BaseTool,
        kb_tool: BaseTool,
        pipeline: EvidencePipeline | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        """初始化信息检索智能体。

        功能：
          注入公网搜索工具、内部知识库工具、EvidencePipeline 和 LLMClient。

        输入输出：
          输入工具和可选 pipeline/llm；无返回值。

        实现说明：
          RetrievalAgent 只依赖 BaseTool 协议，生产替换搜索 API 或知识库实现时
          不需要改 Agent 逻辑。
        """
        self.web_tool = web_tool
        self.kb_tool = kb_tool
        self.pipeline = pipeline or EvidencePipeline()
        self.llm = llm or LLMClient()

    async def decide_route(self, question: AtomicQuestion) -> list[SourceType]:
        """判断该子问题去哪里查（LLM 决策点，可降级为规则）。

        步骤：
          1. 默认加入公网搜索，保证能拿到最新公开资料；
          2. 如果配置允许内部知识库，则加入 INTERNAL_KB；
          3. 返回 SourceType 列表，后续 retrieve 按路由调用工具。
        """
        routes = [SourceType.PUBLIC_WEB]
        if settings.retrieval.enable_internal_kb:
            routes.append(SourceType.INTERNAL_KB)
        return routes

    async def retrieve(self, question: AtomicQuestion) -> list[Evidence]:
        """执行一次原子问题检索。

        步骤：
          1. 先调用 `decide_route` 得到需要使用的来源类型；
          2. 按路由分别调用 WebSearchTool / RagSearchTool；
          3. 给所有原始证据打上 question_id 和 node_id；
          4. 交给 EvidencePipeline 做标准化、去重、打分和截断；
          5. 返回有序 Evidence，供事实合成节点使用。
        """
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
