# PyKnora

多租户 RAG 知识库系统：文档摄取、混合检索、引用问答、异步索引与工作区权限隔离。

当前落地的业务场景是企业内部制度问答：财务管理制度、人事手册、通知类文档从共享盘进来，员工用自然语言提问，回答必须附出处，检索不到支撑证据时明确拒答。制度类问答的难点不在「找相似段落」，而在「判断这段文字里到底有没有问句要的那个事实」——问「差旅报销上限是多少」，一段语气接近但没有那个数字的文字不能算支撑。

PyKnora 解决的是内部文档检索的两个现实问题：一是资料散落在共享盘、邮件与协作工具里，找人问比找文档快；二是通用模型直接作答会编造，答案没有出处就无法在业务里采信。系统把「文档进来」到「答案给出」做成一条可核验的链路，答不了的时候明确说答不了。

## 系统能力

| 能力 | 说明 |
| --- | --- |
| 文档摄取 | 按后缀路由到 PDF、DOCX、XLSX、PPTX 解析器，切片后写入向量与全文索引 |
| 索引发布 | 旧索引退休、新索引激活、文档指针切换在同一事务内完成，检索侧读不到中间态 |
| 混合检索 | 向量召回与 PostgreSQL 全文检索并行，RRF 融合；任一分支失败在响应中写明降级原因 |
| 引用问答 | SSE 流式输出，回答附带可核验引用；答前判定证据是否支撑问题，不支撑直接拒答 |
| 权限隔离 | JWT 作用域、工作区成员关系、PostgreSQL 行级安全策略三层叠加，跨租户统一返回 404 |
| 异步任务 | 任务落 PostgreSQL 队列，Worker 通过 `FOR UPDATE SKIP LOCKED` 并发消费，带租约与重试上限 |
| 版本与血缘 | 文档版本化，保留活动版本指针与贡献者链路，可追溯到具体版本 |
| 管理前端 | 知识库、文档、作业、检索调试与问答界面 |

## 架构

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

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 接口层 | `src/pyknora/api/` | 路由、请求校验、依赖注入、统一错误信封 |
| 服务层 | `src/pyknora/services/` | 摄取、检索、问答、队列、权限与迁移 |
| 数据层 | `migrations/`、`src/pyknora/db/` | PostgreSQL 16 + pgvector，读路径固定走 `v_active_chunks` 视图 |
| 异步层 | `src/pyknora/workers/` | 摄取作业消费、租约续期、失败重试 |
| 前端 | `web/` | Next.js App Router + React + TanStack Query + Tailwind CSS |

模块边界与工程不变量见 [`ARCHITECTURE.md`](./ARCHITECTURE.md)。

## 关键设计

以下几处是有意为之，不是默认选项。

**发布是一个事务。** 解析、切片、向量化都完成之后，旧索引退休、新索引激活、文档三处指针切换、作业状态落定在同一个事务里提交。检索侧要么看到旧版本，要么看到新版本，不存在「一半文档已经更新」的窗口。代价是发布期间持有更长的事务，换来的是不需要在查询侧做版本对齐。

**降级要可见，不能静默。** 检索走两路，任何一路失败时响应体里的 `degraded` 字段会写明是哪一路、错误码是什么。这条设计来自一个具体教训：全文检索分支曾经静默恒空，表现为「检索总是只有向量结果」，从指标上看不出来，只有把降级状态透出给调用方，才可能在联调阶段发现。

**判定发生在调模型之前。** 问答链路先跑证据门控，三态结论 `SUPPORTED` / `INSUFFICIENT` / `CONFLICTING`。只有 `SUPPORTED` 才调模型；`INSUFFICIENT` 直接拒答并说明缺什么；`CONFLICTING` 把冲突的两处片段与取值列出来，不替用户裁决。门控不接收相似度阈值——阈值回答的是「这段文本像不像」，而这里要判的是「这段文本里有没有问句要的那个事实」，两者不是一回事。

**跨租户返回 404，不返回 403。** 403 等于告诉对方「这个资源存在，但你没权限」，本身就是信息泄露。隔离靠三层叠加，数据库层的行级安全策略是最后一道，即使应用层出错也不会越界。

**不引入中间件。** 队列用同库表加 `SKIP LOCKED`，任务状态与业务数据同库同事务；迁移用带数字编号的手写 SQL，只追加不改历史，每条可评审可回退。取舍是吞吐受单库限制，换来的是部署依赖少、故障域小。

## 部署

前置：Docker、Docker Compose。

```bash
cp .env.example .env        # 配置数据库口令、JWT 密钥、嵌入与对话模型
docker compose up -d --build --wait
docker compose ps -a        # migrate 应为 Exited (0)
```

服务与端口（宿主）：

| 服务 | 端口 | 说明 |
| --- | --- | --- |
| web | 3000 | Next.js 前端 |
| api | 8010 | FastAPI，含 `/health` 健康检查 |
| db | 5435 | PostgreSQL 16 + pgvector |

迁移由一次性容器执行，按编号顺序喂 SQL，任一文件失败即中断。部署细节、健康检查与版本回滚见 [`docs/OPERATIONS.md`](./docs/OPERATIONS.md)。

### 本地开发

```bash
uv sync --extra dev --extra local-embed
uv run python scripts/dev_up.py
```

`scripts/dev_up.py` 依次探活数据库、启动 API、Worker 与前端，并处理本机端口错位与离线模型加载。只做装配与探活，不改配置、不含业务逻辑。

## 接口

统一前缀 `/api/v1`，错误响应统一为：

```json
{"error": {"code": "...", "message": "...", "retryable": false, "details": {}}, "request_id": "..."}
```

错误码集中定义在 `src/pyknora/core/errors.py`，服务端不抛裸异常、不返回框架默认错误体。接口契约快照在 `web/contracts/openapi.json`，前端类型由快照生成，避免手写类型与服务端漂移。

`/health` 与 `/metrics` 是运维端点，不进契约快照（`/metrics` 标了 `include_in_schema=False`），前端不消费。

## 质量保障

测试分四层，越往下越依赖真实环境：

```bash
uv run pytest -q tests/unit          # 纯函数与门控判定
uv run pytest -q tests/contract      # 接口契约
uv run pytest -q tests/api           # ASGI 打真实 PostgreSQL
uv run pytest -q tests/integration   # 真实 PostgreSQL，逐例清理
uv run ruff check src tests scripts
```

证据门控单独有 47 条用例，覆盖数值锚点覆盖、适用对象不匹配、版本冲突三类判定；`eval/` 下另有一套离线评测工具链，按 8 类行为组织样本（事实、固定表述、同义改写、版本、无证据、指令注入、污染、越权），通过 HTTP 调用运行中的服务，评测路径与真实调用路径一致。

指标口径、样本构成与结果解读见 [`EVALUATION.md`](./EVALUATION.md)。

## 安全

身份与会话、租户隔离、数据处理与提示注入防护见 [`SECURITY.md`](./SECURITY.md)。

## 当前版本边界

以下是本版本明确未覆盖的部分，写在前面以免误判能力范围：

- 单机 Docker Compose 部署形态，未提供多环境（开发、预发、生产）编排与灰度发布
- 未接入企业 SSO、统一审计平台与配额治理
- 未做容量规划与并发压测，性能数字仅作回归基线
- 冲突检测覆盖带单位的数值事实，非数值冲突（同一问题的两种相反结论）不进入 `CONFLICTING`
- 评测尚未包含答案忠实度指标，拒答判定仍存在可测量的漏判
- 观测面只到事件日志与进程内计数器：`/metrics` 只覆盖 api 容器，worker 是另一个进程，未接采集端、未配告警与看板

## 本仓库的定位

这是项目级的实现说明，不是个人作品集。作者的投入集中在检索与证据判定两处，也是这套离线评测主要服务的地方：

- `src/pyknora/services/retrieval/` —— 全文与向量双路召回、RRF 融合、单路故障时的降级上报
- `src/pyknora/services/rag/evidence.py` —— 调模型之前的三态门控（`SUPPORTED` / `INSUFFICIENT` / `CONFLICTING`）与拒答判定
- `eval/` —— 支撑上面两块迭代的离线评测：30 条固定样本、8 类行为，作为回归门禁

摄取解析、异步队列、权限隔离与前端是同一系统内的配套实现，为了系统能真正跑起来而一并放在这个仓库里。

## 文档

| 文档 | 内容 |
| --- | --- |
| [`ARCHITECTURE.md`](./ARCHITECTURE.md) | 模块边界、核心链路与工程不变量 |
| [`EVALUATION.md`](./EVALUATION.md) | 评测口径、指标定义与结果解读 |
| [`SECURITY.md`](./SECURITY.md) | 身份、租户隔离、数据处理与安全边界 |
| [`docs/OPERATIONS.md`](./docs/OPERATIONS.md) | 部署、健康检查与回滚 |
| [`CONTRIBUTING.md`](./CONTRIBUTING.md) | 本地开发、测试与提交约定 |

## 技术栈

Python 3.12、FastAPI、pydantic v2、SQLAlchemy 2（async）、asyncpg、PostgreSQL 16 + pgvector、Next.js App Router、React、TanStack Query、Tailwind CSS、Docker Compose。
