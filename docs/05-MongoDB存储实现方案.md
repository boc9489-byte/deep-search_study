# MongoDB 存储实现方案

## 1. 一句话定位

MongoDB 在当前项目中承担研究项目和后台任务的持久化存储，目标是在不改变 Router、Background、Workflow 调用方式的前提下，把阶段一的进程内内存数据升级为可重启、可查询、可扩展的工程存储。

## 2. 背景与目标

### 2.1 背景

项目早期使用内存仓储跑通主链路：

```text
FastAPI Router
  -> app.repository
  -> memory dict
```

这种方式适合教学、单元测试和本地演示，但存在明显边界：

- API 进程重启后，项目、任务和报告都会丢失。
- 多进程或多实例部署时，每个进程看到的数据不同。
- 后台任务失败后缺少可恢复的任务状态。
- 无法承接后续 Celery worker 独立进程读写同一份数据。

因此需要引入 MongoDB，同时保留内存实现，让测试和本地快速启动仍然零外部依赖。

### 2.2 改造目标

- 路由层不直接依赖 MongoDB。
- 后台任务、Agent、Workflow 不感知底层存储类型。
- 默认 `memory`，通过环境变量切换 `mongodb`。
- 项目和任务状态可持久化，可被 API 进程和 worker 进程共同访问。
- 仓储接口稳定，为后续 Celery 化后台任务提供数据基础。

### 2.3 非目标

- 本阶段不拆复杂 DAO / Service / Repository 三层。
- 本阶段不引入多集合强事务。
- 本阶段不把报告 HTML 拆到对象存储，当前代码仍将 `Report.html` 保存在项目文档的 `reports` 数组中。
- 本阶段不实现用户、租户、权限和审计日志。

## 3. 当前真实实现

当前代码已经不是单文件内存仓储，而是采用：

```text
Repository Protocol + Memory Repository + MongoDB Repository + Factory + Facade
```

目录结构如下：

```text
app/
├── repository.py
└── repositories/
    ├── __init__.py
    ├── base.py
    ├── factory.py
    ├── memory.py
    ├── models.py
    ├── mongodb.py
    └── testing.py
```

职责边界：

| 文件 | 职责 |
|---|---|
| `app/repository.py` | 兼容旧 import 的 facade，只导出 `project_repo`、`task_repo`、`reset_repositories_for_tests` |
| `repositories/base.py` | 定义 `ProjectRepositoryProtocol` 和 `TaskRepositoryProtocol` |
| `repositories/models.py` | 定义持久化聚合对象 `ProjectRecord`、`TaskRecord` |
| `repositories/memory.py` | 进程内内存实现，用于默认开发和单元测试 |
| `repositories/mongodb.py` | MongoDB 实现，负责模型和文档之间的转换 |
| `repositories/factory.py` | 根据 `settings.storage_backend` 装配仓储 |
| `repositories/testing.py` | 测试清理入口 |

业务层统一这样使用：

```python
from app.repository import project_repo, task_repo
```

这意味着 Router 和 Background 不需要知道当前用的是内存还是 MongoDB。

## 4. 企业工程方案对比

| 方案 | 说明 | 优点 | 缺点 | 本项目结论 |
|---|---|---|---|---|
| 单文件直接改 MongoDB | 把 `app/repository.py` 全部改成 MongoDB 操作 | 改动少 | 测试依赖 MongoDB，回滚困难，职责集中 | 不推荐 |
| 双实现 + Factory | Memory 和 MongoDB 分开，通过配置选择 | 可测试、可回滚、边界清晰 | 文件数量略多 | 当前采用 |
| Protocol + 双实现 + Factory | 在双实现基础上增加接口契约 | 类型边界更清楚，便于替换 PostgreSQL、Redis、对象存储 | 多一点样板 | 当前采用 |
| Service + DAO + Repository | 进一步拆业务服务和数据访问 | 大型团队协作更规范 | 当前阶段过度设计 | 后续再做 |
| 事件溯源 / CQRS | 每个状态变化以事件保存 | 审计和回放能力强 | 复杂度高 | 企业增强项 |

当前选择是偏保守的企业工程折中：既不把系统做重，也不把数据访问写死在业务代码里。

## 5. 配置设计

配置集中在 `app/config.py`，通过 `DEEPSEARCH_` 前缀环境变量覆盖。

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEARCH_STORAGE_BACKEND` | `memory` | 存储后端，可选 `memory` / `mongodb` |
| `DEEPSEARCH_MONGODB_URI` | `mongodb://127.0.0.1:27017` | MongoDB 连接地址 |
| `DEEPSEARCH_MONGODB_DB` | `deepsearch` | 数据库名 |
| `DEEPSEARCH_MONGODB_PROJECTS_COLLECTION` | `projects` | 项目集合 |
| `DEEPSEARCH_MONGODB_TASKS_COLLECTION` | `tasks` | 任务集合 |

本地切换到 MongoDB：

```bash
DEEPSEARCH_STORAGE_BACKEND=mongodb uvicorn app.main:app --reload
```

企业部署建议：

```text
DEEPSEARCH_STORAGE_BACKEND=mongodb
DEEPSEARCH_MONGODB_URI=mongodb://user:password@mongo-1:27017,mongo-2:27017/deepsearch?replicaSet=rs0
DEEPSEARCH_MONGODB_DB=deepsearch_prod
```

## 6. 集合设计

当前实现使用两个集合：

```text
deepsearch.projects
deepsearch.tasks
```

### 6.1 projects 文档

`ProjectRecord` 对应 MongoDB 中的一条项目文档。

```json
{
  "_id": "proj_xxx",
  "topic": "具身智能行业未来三年的机会",
  "research_goal": "判断公司是否需要关注该行业",
  "target_audience": "公司战略团队",
  "region_scope": "china",
  "time_scope": {
    "type": "recent_years",
    "years": 3
  },
  "status": "outline_ready",
  "created_at": "2026-06-12T10:00:00Z",
  "brief": {
    "topic": "具身智能行业未来三年的机会",
    "objective": "判断公司是否需要关注该行业",
    "scope": "china",
    "default_assumptions": []
  },
  "outline": [],
  "sources": [],
  "evidences": [],
  "facts": [],
  "insights": [],
  "reports": []
}
```

当前代码没有单独的 `research_result`、`confirmed_outline`、`report_versions` 集合；报告版本以内嵌数组方式保存在 `reports` 字段中。

MongoDB 层使用 `_id` 作为唯一主键；Repository 层读取文档时会把 `_id` 映射回 `ProjectRecord.id`，因此 API 和业务代码仍然使用 `project_id` / `record.id`，不会暴露 MongoDB 字段名。

### 6.2 tasks 文档

`TaskRecord` 对应 MongoDB 中的一条后台任务文档。

```json
{
  "_id": "task_xxx",
  "project_id": "proj_xxx",
  "task_type": "generate_report",
  "status": "running",
  "message": "正在检索与分析资料",
  "created_at": "2026-06-12T10:00:00Z",
  "updated_at": "2026-06-12T10:01:00Z",
  "trace": []
}
```

任务状态和项目状态分开保存：

| 对象 | 状态字段 | 作用 |
|---|---|---|
| Project | `status` | 表示业务流程走到哪里，如 `outline_ready`、`researching`、`report_ready` |
| Task | `status` | 表示某个后台作业执行状态，如 `queued`、`running`、`succeeded`、`failed` |

## 7. 索引设计

当前 MongoDB 仓储提供基础索引：

```text
projects._id
projects.status
tasks._id
tasks.project_id
tasks.status
tasks.created_at
```

`_id` 是 MongoDB 天然唯一索引，不需要额外创建 unique index。当前代码只显式创建业务查询需要的二级索引，例如 `status`、`project_id` 和 `created_at`。

企业增强建议：

| 集合 | 索引 | 用途 |
|---|---|---|
| `projects` | `created_at` | 项目列表按时间排序 |
| `projects` | `status, created_at` | 后台巡检和状态筛选 |
| `tasks` | `project_id, created_at` | 查看某项目任务历史 |
| `tasks` | `status, updated_at` | 发现长时间 running 的任务 |
| `tasks` | `task_type, status` | 按任务类型统计积压 |

如果后续引入用户和租户，需要增加：

```text
projects.tenant_id, created_at
tasks.tenant_id, status, created_at
```

## 8. 数据流

### 8.1 仓储装配流程

```mermaid
flowchart TD
    A["FastAPI / Worker 进程启动"] --> B["读取 app.config.settings"]
    B --> C{"settings.storage_backend"}
    C -->|"memory"| D["MemoryProjectRepository / MemoryTaskRepository"]
    C -->|"mongodb"| E["MongoProjectRepository / MongoTaskRepository"]
    D --> F["app.repository facade"]
    E --> F
    F --> G["Router / Background / Tests"]
```

### 8.2 创建项目和初始任务

```mermaid
sequenceDiagram
    participant U as User
    participant R as Projects Router
    participant P as ProjectRepository
    participant T as TaskRepository
    participant B as Background
    participant M as MongoDB

    U->>R: POST /api/v1/research-projects
    R->>P: create(req)
    P->>M: insert projects
    R->>T: create(project_id, generate_research_brief)
    T->>M: insert tasks(status=queued)
    R->>B: spawn/run_generate_brief
    R-->>U: project_id + initial_task_id
```

### 8.3 报告任务执行

```mermaid
sequenceDiagram
    participant B as Background
    participant P as ProjectRepository
    participant T as TaskRepository
    participant W as Research Workflow
    participant M as MongoDB

    B->>P: set_status(project_id, researching)
    B->>T: update(task_id, running)
    T->>M: update tasks
    B->>P: get(project_id)
    P->>M: find projects
    B->>W: ainvoke(ResearchState)
    W-->>B: sources/evidences/facts/insights/report/trace
    B->>P: save_research_outputs(...)
    P->>M: replace projects
    B->>T: update(task_id, succeeded, trace)
    T->>M: update tasks
```

## 9. 当前实现边界

当前 MongoDB 实现已经解决“数据不随进程重启丢失”的问题，但距离严格企业生产还有差距：

| 能力 | 当前实现 | 企业工程建议 |
|---|---|---|
| 事务 | 项目和任务分别更新 | 对关键状态流转使用 MongoDB transaction 或补偿任务 |
| 乐观锁 | 暂无版本字段 | 增加 `version` / `updated_at` 条件更新 |
| 幂等 | 依赖前端避免重复提交 | 增加 `idempotency_key` 和唯一索引 |
| 任务恢复 | 只保存状态，不自动恢复 | Celery worker 重试，巡检 stuck running 任务 |
| 报告存储 | HTML 内嵌在项目文档 | 大报告迁移到 MinIO/S3，MongoDB 只存元数据 |
| Trace | trace 内嵌在 task 文档 | 大规模 trace 独立集合，支持分页和检索 |
| 审计 | 暂无 | 增加 project_events / task_events |

## 10. 与 Celery 改造的关系

MongoDB 不是 Celery 的 broker。Celery 仍建议使用 Redis 或 RabbitMQ 作为 broker，MongoDB 负责业务状态和结果持久化。

引入 Celery 后，数据流会变成：

```text
Router
  -> task_repo.create(status=queued)
  -> Celery broker.enqueue(task_id, project_id)
  -> Worker
  -> project_repo / task_repo 读写 MongoDB
  -> Frontend 继续 GET /tasks/{task_id}
```

因此 MongoDB 改造是 Celery 改造的前置基础：API 进程和 worker 进程必须能读写同一份项目、任务和报告数据。

## 11. 测试与验证

默认测试仍走内存：

```bash
python -m unittest discover -s tests
```

MongoDB 模式冒烟：

```bash
DEEPSEARCH_STORAGE_BACKEND=mongodb \
DEEPSEARCH_MONGODB_DB=deepsearch_test \
python -m scripts.smoke
```

建议验证点：

- 创建项目后，`projects` 集合有项目文档。
- 创建项目后，`tasks` 集合有 `generate_research_brief` 任务。
- 大纲生成成功后，项目状态变为 `outline_ready`。
- 报告生成成功后，项目状态变为 `report_ready`，`reports` 数组新增版本。
- `/api/v1/tasks/{task_id}` 能查询到最终任务状态。

回滚到内存模式：

```bash
DEEPSEARCH_STORAGE_BACKEND=memory
```

## 12. 后续演进

推荐按以下顺序演进：

1. 保持现有 `ProjectRepositoryProtocol` / `TaskRepositoryProtocol` 不变。
2. 增加任务幂等字段和唯一索引，避免重复投递。
3. 引入 Celery，把 `background.spawn()` 替换为队列投递。
4. 增加任务超时、重试次数、错误类型、worker 信息。
5. 将大体积报告 HTML 和 trace 从项目文档中拆出。
6. 增加审计事件集合，记录关键状态流转。

这样 MongoDB 文档模型既能支撑当前 MVP，又能自然过渡到企业级后台任务和可观测体系。
