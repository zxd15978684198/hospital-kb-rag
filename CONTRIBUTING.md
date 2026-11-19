# 贡献指南

## 环境要求

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker
- Node.js 20+
- pnpm

安装后端依赖：

```bash
uv sync --extra dev --extra local-embed
```

安装前端依赖：

```bash
cd web
pnpm install
```

## 本地开发

启动完整开发栈：

```bash
uv run python scripts/dev_up.py
```

常用选项：

```bash
uv run python scripts/dev_up.py --check       # 只做健康检查
uv run python scripts/dev_up.py --no-web      # 只启动后端
uv run python scripts/dev_up.py --start-db    # 启动已停止的 PostgreSQL 容器
```

前端地址：

```text
http://127.0.0.1:3000/login
```

## 测试

### 后端

```bash
uv run pytest -q tests/unit
uv run pytest -q tests/contract
uv run pytest -q tests/api
uv run pytest -q tests/integration
```

`tests/api` 和 `tests/integration` 需要 PostgreSQL。不要对包含重要数据的库执行这两组测试，因为测试夹具可能会清空相关表。

### 前端

```bash
cd web
pnpm typecheck
pnpm test
pnpm build
```

## 静态检查

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

## 提交约定

- 保持改动聚焦，避免一次提交混合多个主题。
- 优先使用小而可评审的提交。
- 不要提交 `.env`、凭据、私有语料或评测输出。
- 行为或架构变化时，同步更新文档。
- 修复缺陷和新增行为时补充测试。
- 功能未完成时，在提交说明或 PR 描述中如实说明，不要包装成已完成。

## 分支命名示例

```text
feat/ingest-worker-retry
fix/cross-tenant-404
docs/evaluation-summary
chore/frontend-dependency-bump
```

## Pull Request 内容

请说明：

1. 改了什么。
2. 为什么需要这个改动。
3. 如何验证。
4. 已知限制或后续工作。
