"""数据访问层（repository）。

隔离存储细节：路由/后台只依赖 repository 方法，不关心底层是内存还是 Postgres。
本期为线程/协程安全的内存实现，生产替换为 SQLAlchemy/SQLModel + PostgreSQL，
向量数据走 pgvector/Milvus，报告原文走对象存储。

设计方案对比：
  - 方案 A：路由层直接操作数据库。优点是代码少；缺点是接口、业务状态和 SQL 耦合，
    后续从内存换 PostgreSQL 时要大面积改路由，不适合演进。
  - 方案 B：引入 Repository 抽象。优点是路由/后台只依赖方法契约，底层可从内存
    平滑替换为 PostgreSQL、对象存储或测试替身；缺点是多一层样板代码。
  - 阶段一选择方案 B，并用内存实现降低运行门槛；生产保留 Repository 边界，
    只替换具体实现。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.schemas.domain import (
    Evidence,
    FactCard,
    InsightCard,
    OutlineNode,
    ProjectStatus,
    Report,
    ResearchBrief,
    Source,
    TaskStatus,
    TaskType,
    TraceEvent,
    new_id,
    utcnow,
)


# --------------------------------------------------------------------------- #
# 内存实体
# --------------------------------------------------------------------------- #
@dataclass
class ProjectRecord:
    """研究项目的内存聚合对象。

    功能：
      保存一个研究项目从创建到报告生成的全部阶段一数据。

    实现说明：
      阶段一把 brief、outline、sources、evidences、facts、insights、reports 都聚合
      在 ProjectRecord 内，便于本地调试和端到端测试。生产落库时通常拆成多张表，
      但仍可以把 ProjectRecord 视作读取后的聚合视图。
    """

    id: str
    topic: str
    research_goal: str
    target_audience: str
    region_scope: str
    time_scope: dict
    status: ProjectStatus
    created_at: object = field(default_factory=utcnow)
    brief: ResearchBrief | None = None
    outline: list[OutlineNode] = field(default_factory=list)
    # 研究产物
    sources: list[Source] = field(default_factory=list)
    evidences: list[Evidence] = field(default_factory=list)
    facts: list[FactCard] = field(default_factory=list)
    insights: list[InsightCard] = field(default_factory=list)
    reports: list[Report] = field(default_factory=list)


@dataclass
class TaskRecord:
    """后台任务的内存状态对象。

    功能：
      表示一次异步作业，例如生成大纲、修改大纲、生成报告。

    实现说明：
      `trace` 保存节点级观测事件；`message` 给前端展示当前阶段；`status` 用于
      前端轮询和测试断言。
    """

    id: str
    project_id: str
    task_type: TaskType
    status: TaskStatus = TaskStatus.QUEUED
    message: str = ""
    created_at: object = field(default_factory=utcnow)
    updated_at: object = field(default_factory=utcnow)
    trace: list[TraceEvent] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 内存存储（单例）
# --------------------------------------------------------------------------- #
class _Store:
    """阶段一内存 Store。

    设计方案对比：
      - 直接使用模块级 dict：更简单，但多个 repository 方法并发写入时容易产生竞态；
      - dict + asyncio.Lock：仍然轻量，同时保证协程并发下状态更新原子；
      - 真实数据库：支持持久化和事务，但需要迁移、连接池和部署依赖。

    本阶段选择 dict + asyncio.Lock，用最小复杂度模拟生产中的事务边界。
    """

    def __init__(self) -> None:
        self.projects: dict[str, ProjectRecord] = {}
        self.tasks: dict[str, TaskRecord] = {}
        self.lock = asyncio.Lock()


store = _Store()


# --------------------------------------------------------------------------- #
# Repository
# --------------------------------------------------------------------------- #
class ProjectRepository:
    """项目仓储。

    设计方案对比：
      - 把 Project、Evidence、Report 拆成多个仓储：更贴近生产表结构，但阶段一文件多；
      - 先聚合到 ProjectRepository：主链路更容易读，适合 MVP；
      - 生产建议按聚合根拆分为 ProjectRepository、EvidenceRepository、ReportRepository，
        但对上层保持方法语义一致。
    """

    async def create(self, req) -> ProjectRecord:
        """创建研究项目。

        输入：
          `CreateProjectRequest`，包含 topic、research_goal、target_audience 等。

        输出：
          新建的 ProjectRecord，初始状态为 `brief_generating`。

        实现：
          在锁内生成项目 ID，复制请求字段到 ProjectRecord，并写入内存 store。
        """
        async with store.lock:
            rec = ProjectRecord(
                id=new_id("proj"),
                topic=req.topic,
                research_goal=req.research_goal,
                target_audience=req.target_audience,
                region_scope=req.region_scope.value,
                time_scope=req.time_scope.model_dump(),
                status=ProjectStatus.BRIEF_GENERATING,
            )
            store.projects[rec.id] = rec
            return rec

    async def get(self, project_id: str) -> ProjectRecord | None:
        """按项目 ID 读取项目。

        输入：project_id。
        输出：存在时返回 ProjectRecord，否则返回 None。
        实现：阶段一直接从内存 dict 读取；生产实现可替换为数据库查询。
        """
        return store.projects.get(project_id)

    async def set_status(self, project_id: str, status: ProjectStatus) -> None:
        """更新项目状态。

        输入：project_id 与目标 ProjectStatus。
        输出：无返回值；项目不存在时静默忽略。
        实现：用锁保护写操作，避免后台任务并发更新状态时产生竞态。
        """
        async with store.lock:
            if project_id in store.projects:
                store.projects[project_id].status = status

    async def save_brief_and_outline(
        self, project_id: str, brief: ResearchBrief, outline: list[OutlineNode]
    ) -> None:
        """保存研究任务书和初始大纲。

        输入：
          project_id、ResearchBrief、OutlineNode 列表。

        功能：
          大纲生成任务成功后的落库动作。

        实现：
          写入 brief 和 outline，并把项目状态推进到 `outline_ready`。
        """
        async with store.lock:
            rec = store.projects[project_id]
            rec.brief = brief
            rec.outline = outline
            rec.status = ProjectStatus.OUTLINE_READY

    async def save_outline(self, project_id: str, outline: list[OutlineNode]) -> None:
        """保存修改后的大纲。

        输入：project_id 与新版 outline。
        功能：大纲修改任务完成后覆盖旧大纲。
        实现：写入 outline，并把状态回到 `outline_ready`，等待用户再次确认。
        """
        async with store.lock:
            rec = store.projects[project_id]
            rec.outline = outline
            rec.status = ProjectStatus.OUTLINE_READY

    async def save_research_outputs(
        self,
        project_id: str,
        *,
        sources: list[Source],
        evidences: list[Evidence],
        facts: list[FactCard],
        insights: list[InsightCard],
        report: Report,
    ) -> None:
        """保存研究执行产物。

        输入：
          sources、evidences、facts、insights 和最终 report。

        功能：
          把一次报告任务产出的完整证据链保存到项目下。

        实现：
          阶段一覆盖 sources/evidences/facts/insights，report 以版本方式 append；
          report.version 根据已有报告数量自增；最后项目状态变为 `report_ready`。
        """
        async with store.lock:
            rec = store.projects[project_id]
            rec.sources = sources
            rec.evidences = evidences
            rec.facts = facts
            rec.insights = insights
            report.version = len(rec.reports) + 1
            rec.reports.append(report)
            rec.status = ProjectStatus.REPORT_READY

    async def latest_report(self, project_id: str) -> Report | None:
        """读取项目最新报告。

        输入：project_id。
        输出：最新 Report；项目不存在或尚无报告时返回 None。
        实现：阶段一从 ProjectRecord.reports 取最后一个版本。
        """
        rec = store.projects.get(project_id)
        if not rec or not rec.reports:
            return None
        return rec.reports[-1]


class TaskRepository:
    """任务仓储。

    设计方案对比：
      - 任务状态直接放在 ProjectRecord：实现简单，但无法表达一个项目多次后台作业；
      - 独立 TaskRecord：支持大纲生成、修改大纲、报告生成等多任务并存，
        前端也能按 task_id 轮询。

    本项目选择独立任务模型，为生产队列、重试、取消和 trace 查询预留空间。
    """

    async def create(self, project_id: str, task_type: TaskType) -> TaskRecord:
        """创建后台任务。

        输入：project_id 和任务类型。
        输出：TaskRecord，初始状态为 `queued`。
        实现：生成 task ID 后写入内存任务表，供后台任务更新和前端轮询。
        """
        async with store.lock:
            rec = TaskRecord(id=new_id("task"), project_id=project_id, task_type=task_type)
            store.tasks[rec.id] = rec
            return rec

    async def get(self, task_id: str) -> TaskRecord | None:
        """按任务 ID 读取任务。

        输入：task_id。
        输出：TaskRecord 或 None。
        实现：阶段一直接从内存 dict 读取。
        """
        return store.tasks.get(task_id)

    async def update(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        message: str | None = None,
        trace: list[TraceEvent] | None = None,
    ) -> None:
        """局部更新任务状态。

        输入：
          task_id，以及可选 status、message、trace。

        功能：
          后台任务在不同阶段更新状态、进度文案和 trace。

        实现：
          只更新非 None 字段；trace 使用 extend 追加；每次更新刷新 updated_at。
        """
        async with store.lock:
            rec = store.tasks.get(task_id)
            if not rec:
                return
            if status is not None:
                rec.status = status
            if message is not None:
                rec.message = message
            if trace:
                rec.trace.extend(trace)
            rec.updated_at = utcnow()


project_repo = ProjectRepository()
task_repo = TaskRepository()


async def reset_repositories_for_tests() -> None:
    """清空内存仓储，仅供本地测试使用。

    生产环境替换为数据库后，测试层应改用事务回滚或临时 schema。
    """
    async with store.lock:
        store.projects.clear()
        store.tasks.clear()
