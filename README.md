# PyKnora

多租户可信 RAG 知识库系统：文档摄取、混合检索、引用问答、异步索引与工作区权限隔离。

本仓库是一个面向作品集展示的公开快照，来源于多人协作项目；公开版重点保留架构、核心链路、测试与评测方法论，不包含真实语料、内部进度表或私有评测结果。

## 核心能力

- 文档上传 → 解析 → 切片 → Embedding → 索引发布。
- 向量检索 + PostgreSQL 全文检索，RRF 融合排序。
- SSE 流式问答，返回可核验引用；证据不足时拒答。
- workspace / JWT / PostgreSQL RLS 的多层权限隔离。
- PostgreSQL 表 + `SKIP LOCKED` 异步任务队列，独立 Worker 消费。
- 版本化文档与原子发布，避免旧版本内容继续被检索。
- Next.js 前端覆盖知识库、文档、作业、检索调试与问答。

## 架构概览

```text
Browser / Next.js
        │  same-origin /api
        ▼
FastAPI  ──► PostgreSQL + pgvector
   │             ▲
   │             │
   └──► PG queue ──► Worker
                        │
                        └──► parse → chunk → embed → publish
```

- **API 层**：`src/pyknora/api/`，负责请求校验、错误信封、路由与依赖注入。
- **服务层**：`src/pyknora/services/`，承载摄取、检索、问答、队列、权限与迁移逻辑。
- **数据层**：PostgreSQL 16 + pgvector，读路径使用 `v_active_chunks` 视图保证活动版本一致性。
- **异步层**：任务落 PG 队列，Worker 通过 `FOR UPDATE SKIP LOCKED` 并发消费。
- **前端**：`web/`，Next.js App Router + React 19 + TanStack Query + Tailwind CSS。

更多模块与不变量见 [`ARCHITECTURE.md`](./ARCHITECTURE.md)。

## 快速开始

前置：Python 3.12、[uv](https://docs.astral.sh/uv/)、Docker、Node.js 20+ 与 pnpm。

### 本地开发

```bash
cp .env.example .env
uv sync --extra dev --extra local-embed

docker compose up -d --build --wait
uv run python scripts/migrate.py
uv run python scripts/dev_up.py
```

打开：

```text
http://127.0.0.1:3000/login
```

`scripts/dev_up.py` 会依次探活数据库、启动 API、Worker 与前端，并自动处理常见的本机端口错位和离线模型加载问题。

### 后端测试

```bash
uv run pytest -q tests/unit
uv run pytest -q tests/contract
uv run pytest -q tests/api        # 需要 PostgreSQL
uv run pytest -q tests/integration # 需要 PostgreSQL
```

### 前端检查

```bash
cd web
pnpm install
pnpm typecheck
pnpm test
pnpm build
```

## 文档

| 文档 | 内容 |
| --- | --- |
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | 模块边界、核心链路与工程不变量 |
| [`EVALUATION.md`](./EVALUATION.md) | RAG 评测口径、指标与结果解读 |
| [`SECURITY.md`](./SECURITY.md) | 身份、租户隔离、数据处理与安全边界 |
| [`CONTRIBUTING.md`](./CONTRIBUTING.md) | 本地开发、测试与提交约定 |
| [`docs/PRD.md`](./docs/PRD.md) | 产品范围与演进方向 |
| [`docs/adr/`](./docs/adr/) | 架构决策记录 |
| [`docs/tdd/`](./docs/tdd/) | 技术设计说明 |
| [`docs/OPERATIONS.md`](./docs/OPERATIONS.md) | 部署、健康检查与回滚 |

## 已知边界

- 这是一个工程化原型，不是已上线的企业生产系统。
- 没有做高并发压测、完整企业治理、审计平台或多集群部署。
- 公开仓库不包含真实语料、私有评测结果或生产凭据。
- RAG 评测指标来自内部离线评测，样本量有限，不作为性能承诺。
