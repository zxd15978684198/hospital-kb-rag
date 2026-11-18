# AGENTS.md · PyKnora 仓库协作规范

> 适用于在本仓库工作的所有贡献者与 AI 编码代理。
> 本文件管**流程与纪律**；`docs/PRD_PyKnora.md`、`docs/tdd/`、`docs/adr/` 管**技术口径**。

## 1. 项目事实

| 项 | 口径 |
| --- | --- |
| 语言与包管理 | Python 3.12，`uv`（依赖只写 `pyproject.toml`，锁文件 `uv.lock` 勿手改） |
| Web | FastAPI + pydantic v2；应用工厂 `pyknora.api.main:create_app` |
| 数据 | PostgreSQL 16 + `pgvector`；SQLAlchemy 2 async + `asyncpg` |
| 明确不用 | Redis、Celery、alembic；禁止直接 `import lxml` 做 HTML/XML 解析（OOXML 库依赖除外） |
| 库结构 | `migrations/0001–0016`：17 张表 + `v_active_chunks` 视图；ORM 16 个 Row（outbox 走原生 SQL） |
| 错误码 | `src/pyknora/core/errors.py` 的 19 个 `ErrorCode`；响应体统一 `{"error":{...},"request_id"}` |
| 测试 | unit / contract / api / integration 共 1158 条（`uv run pytest --collect-only` 为准） |

## 2. 代码红线

1. 活动 chunk 读取必须走 `v_active_chunks`，不许绕开视图写 SQL；
2. `Scope` 只从 JWT 来；请求体/查询串里的身份字段一律不读；
3. 发布是一个事务（旧 build 退休 + 新 build 激活 + documents 三指针 + job 完成 + outbox）；
4. 迁移只追加：已合并号段不改历史文件，新改动新建 `000N_*.sql`；
5. 切片 `content` 禁止内部空白折叠，`char_start`/`char_end` 必须能拼回原文（表格块为块内偏移，见 ADR-0008 D3）；
6. 向量维度不匹配必须抛错，绝不截断；
7. 错误一律走 `AppError`；流式端点开始输出后不许抛 `AppError`（降级成 SSE error 事件再补 done）；
8. 404 不泄漏"存在但无权"。

## 3. 命令

```bash
uv sync --extra dev
docker compose up -d --wait      # 一键起栈（db → migrate → api/worker → web）
uv run ruff check src tests scripts
uv run pytest -q                 # 全量；跑测试前先停本地 API 与 Worker
uv run python scripts/ci.py --stage all
```

## 4. 提交纪律

- 提交前必须过本地门禁（pre-commit / pre-push 自动执行）；
- `git status --short` 只含本轮文件；不 `commit --amend` 他人提交、不改写历史；
- 证据先于结论：没跑过的命令不写"通过"，没做的验证写"未验证"。

## 5. 已知约束（摘要）

- 视图 `v_active_chunks` 不校验向量维度，维度校验在 `SqlChunkSearchRepo.search_similar`；
- `eval/` 目录下的结果产物随 `eval/results/` gitignore，`eval/baselines/` 为冻结基线只增不改；
- `docs/` 下的 ADR 全部以"提议→已决"流程演进，修订需在 ADR 内记录裁决依据。
