# DeepSearch 企业级深度搜索研究平台

DeepSearch 是一个面向企业研究场景的深度搜索与证据生成平台。它从一个相对模糊的研究主题出发，自动完成研究大纲生成、多源检索、资料阅读、证据抽取、去重排序、交叉验证，并最终产出一份带引用、可追溯、可复核的 HTML 研究报告。

项目当前是可运行的工程骨架：LLM、搜索、知识库、存储等外部能力均提供了接口和桩实现，因此无需配置真实外部服务即可跑通主链路。后续可以逐步替换为真实搜索 API、内部知识库、向量数据库、生产级任务队列和真实大模型服务。

## 项目定位

普通搜索解决的是“找到资料”，RAG 解决的是“基于知识库回答问题”，DeepSearch 更关注“像研究员一样围绕复杂问题持续查证并形成报告”。核心流程如下：

```text
Plan -> Search -> Read -> Evidence -> Verify -> Report -> Evaluate
```

在实际业务中，它适合承接以下任务：

- 行业研究：围绕一个行业、技术方向或市场机会生成结构化调研报告。
- 战略分析：为公司战略、产品规划、投资判断提供证据链和结论依据。
- 竞品研究：持续收集公开资料与内部材料，沉淀可追溯的竞品分析。
- 企业知识检索：把公网搜索、内部知识库、数据库或业务工具的结果统一为证据。
- 研究流程自动化：把人工“拆问题、查资料、筛证据、写报告”的过程工程化。

## 核心能力

- 研究大纲生成：创建项目后自动生成初始研究大纲，支持人工确认或修改。
- 多源检索抽象：公网搜索、内部知识库、网页解析等工具统一输出为 `Evidence`。
- 证据管道：对证据进行标准化、去重、可信度打分、重排和来源绑定。
- 交叉验证：基于多个来源生成 `FactCard` 和 `InsightCard`，并保留置信度。
- 确定性报告生成：报告由结构化数据按模板渲染，不依赖“自由发挥式”的报告 Agent。
- Trace 可观测：每个任务会记录节点输入输出、耗时、状态与中间产物，便于排障和回归。
- 可替换工程接口：当前使用内存仓储和桩工具，生产环境可替换为真实基础设施。

## 工程架构

```text
app/
├── main.py                  # FastAPI 入口
├── config.py                # 模型、检索参数、质量阈值、打分权重配置
├── schemas/
│   ├── api.py               # REST 接口请求和响应模型
│   └── domain.py            # 领域模型，包含 Evidence / Fact / Insight
├── routers/                 # API 路由层
│   ├── health.py
│   ├── projects.py
│   ├── reports.py
│   └── tasks.py
├── repository.py            # 数据访问层，当前为内存实现
├── background.py            # 后台任务调度，当前基于 asyncio
├── agents.py                # 研究管理与信息检索智能体
├── tools/                   # 搜索、知识库、网页解析、重排等工具接口
├── pipeline/                # 证据处理与交叉验证
├── workflow/                # LangGraph 工作流，未安装时降级为本地执行器
└── report.py                # HTML 报告渲染与引用绑定

docs/
├── 01-概要设计与技术方案.md
├── 02-接口与子系统设计.md
└── 03-项目实现过程.md

scripts/
└── smoke.py                 # 端到端冒烟测试
```

整体分层可以理解为：

```text
FastAPI 接口层
  -> 后台任务与工作流编排层
  -> Agent 与工具能力层
  -> Evidence Pipeline 证据处理层
  -> Repository 数据层
  -> Report 确定性渲染层
```

## 快速开始

### 1. 安装依赖

建议使用 Python 3.10+。

```bash
pip install -r requirements.txt
```

当前必需依赖较少，主要包括：

- `fastapi`
- `uvicorn`
- `pydantic`

`langgraph`、`httpx`、`trafilatura`、`anthropic`、`psycopg`、`celery` 等生产依赖已在 `requirements.txt` 中以注释形式预留，可按实际接入需求开启。

### 2. 运行端到端冒烟测试

冒烟测试不启动 HTTP 服务，会直接驱动 repository 和 background 任务，验证“创建项目 -> 生成大纲 -> 确认大纲 -> 生成报告”的主链路。

```bash
python -m scripts.smoke
```

预期输出包括项目 ID、任务状态、报告标题、来源数、证据数、事实数、洞察数和 HTML 片段。

### 3. 运行阶段一自动化测试

```bash
python -m unittest discover -s tests
```

测试覆盖 Evidence Pipeline 的去重/排序，以及“创建项目 -> 生成大纲 -> 确认大纲 -> 生成报告”的阶段一主链路。

### 4. 启动 API 服务

```bash
uvicorn app.main:app --reload
```

启动后访问：

- API 文档：http://127.0.0.1:8000/docs
- 根路径：http://127.0.0.1:8000/
- 健康检查：http://127.0.0.1:8000/health

## 典型使用流程

### 1. 创建研究项目

```bash
curl -X POST http://127.0.0.1:8000/api/v1/research-projects \
  -H "Content-Type: application/json" \
  -d '{
    "topic": "具身智能行业未来三年的机会",
    "research_goal": "判断公司是否需要关注该行业",
    "target_audience": "公司战略团队",
    "region_scope": "china",
    "time_scope": {
      "type": "recent_years",
      "years": 3
    }
  }'
```

创建成功后会返回 `project_id` 和初始大纲生成任务 `initial_task_id`。

### 2. 查询任务状态

```bash
curl http://127.0.0.1:8000/api/v1/tasks/{task_id}
```

任务状态包括：

```text
queued -> running -> succeeded | failed
```

### 3. 查看并确认大纲

```bash
curl http://127.0.0.1:8000/api/v1/research-projects/{project_id}/outline
```

确认大纲：

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/research-projects/{project_id}/outline \
  -H "Content-Type: application/json" \
  -d '{"action": "confirm"}'
```

如需修改大纲：

```bash
curl -X PUT http://127.0.0.1:8000/api/v1/research-projects/{project_id}/outline \
  -H "Content-Type: application/json" \
  -d '{
    "action": "revise",
    "revision_instruction": "增加商业化落地和产业链玩家分析"
  }'
```

### 4. 提交报告任务

```bash
curl -X POST http://127.0.0.1:8000/api/v1/research-projects/{project_id}/report-tasks \
  -H "Content-Type: application/json" \
  -d '{"user_instruction": "结论要明确，突出机会和风险"}'
```

### 5. 获取报告和证据

```bash
curl http://127.0.0.1:8000/api/v1/research-projects/{project_id}/reports/latest
```

查看项目证据：

```bash
curl http://127.0.0.1:8000/api/v1/research-projects/{project_id}/evidence
```

查看任务 trace：

```bash
curl http://127.0.0.1:8000/api/v1/tasks/{task_id}/trace
```

## 主要接口

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/health` | GET | 健康检查 |
| `/api/v1/research-projects` | POST | 创建研究项目，自动触发大纲生成 |
| `/api/v1/research-projects/{project_id}/outline` | GET | 获取研究大纲 |
| `/api/v1/research-projects/{project_id}/outline` | PUT | 确认或修改研究大纲 |
| `/api/v1/research-projects/{project_id}/report-tasks` | POST | 提交研究报告生成任务 |
| `/api/v1/tasks/{task_id}` | GET | 查询任务状态 |
| `/api/v1/tasks/{task_id}/trace` | GET | 查看任务执行 trace |
| `/api/v1/research-projects/{project_id}/reports/latest` | GET | 获取最新报告 |
| `/api/v1/research-projects/{project_id}/evidence` | GET | 查看项目证据，可按 `node_id` 过滤 |

## 生产工程使用方式

当前项目最适合作为企业深度研究 Agent 的后端基础骨架。真实落地时，可以按以下顺序逐步替换：

| 当前模块 | 当前实现 | 生产建议 |
| --- | --- | --- |
| `app/tools/web_search.py` | 桩搜索结果 | 接入 Serper、Tavily、Bing Search 或自研搜索服务 |
| `app/tools/rag_search.py` | 桩内部知识库 | 接入 Milvus、pgvector、Elasticsearch、BM25、RRF |
| `app/tools/page_extract.py` | 桩网页解析 | 使用 `httpx` 抓取，`trafilatura` / readability 抽正文 |
| `app/tools/rerank.py` | 桩重排 | 接入 BGE reranker、Cohere rerank 或 Cross-Encoder |
| `app/agents.py` | 桩 LLM 客户端 | 接入 OpenAI、Anthropic 或企业私有模型服务 |
| `app/repository.py` | 内存存储 | 替换为 PostgreSQL、对象存储和审计表 |
| `app/background.py` | `asyncio.create_task` | 替换为 Celery、RQ、Arq 或工作流平台 |
| `app/workflow/graph.py` | LangGraph 可选 | 启用 checkpointer，实现断点恢复和状态持久化 |
| `app/report.py` | HTML 模板渲染 | 扩展为 PDF、Markdown、Docx 或前端富文本报告 |

工程落地时建议优先保证三件事：

1. 证据 schema 稳定：所有搜索、知识库、工具结果都先转换为 `Evidence`。
2. 引用链路完整：报告结论必须能追溯到 source、evidence、fact 和 trace。
3. 评测闭环可用：对召回率、引用准确率、事实一致性和报告质量进行回归评测。

## 设计文档

更完整的系统设计见：

- `docs/01-概要设计与技术方案.md`
- `docs/02-接口与子系统设计.md`
- `docs/03-项目实现过程.md`
- `docs/04-阶段一实现与企业工程建议.md`

这些文档包含业务状态机、模块职责、REST 契约、Evidence Schema、工作流节点、评测体系、可观测性和部署扩展建议。

## 当前状态

本仓库当前重点是展示深度搜索研究平台的工程主链路和可扩展架构，已具备：

- 可运行的 FastAPI 服务。
- 可执行的端到端冒烟测试。
- 研究项目、大纲、任务、证据、报告的领域模型。
- 证据处理、交叉验证和确定性报告生成的核心骨架。
- 面向生产替换的模块边界。

它不是一个已经接入真实公网搜索和真实 LLM 的成品 SaaS，而是一个便于二次开发、面试展示、技术方案验证和企业内部 PoC 的深度搜索 Agent 工程模板。
