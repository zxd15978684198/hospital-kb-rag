# Contributing

## Environment

Requirements:

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker
- Node.js 20+
- pnpm

Install backend dependencies:

```bash
uv sync --extra dev --extra local-embed
```

Install frontend dependencies:

```bash
cd web
pnpm install
```

## Local development

Start the development stack:

```bash
uv run python scripts/dev_up.py
```

Useful options:

```bash
uv run python scripts/dev_up.py --check       # health checks only
uv run python scripts/dev_up.py --no-web      # backend only
uv run python scripts/dev_up.py --start-db    # start stopped PostgreSQL container
```

The frontend is available at:

```text
http://127.0.0.1:3000/login
```

## Tests

### Backend

```bash
uv run pytest -q tests/unit
uv run pytest -q tests/contract
uv run pytest -q tests/api
uv run pytest -q tests/integration
```

`tests/api` and `tests/integration` require PostgreSQL. Do not run them against a database containing data you care about, because test fixtures may truncate tables.

### Frontend

```bash
cd web
pnpm typecheck
pnpm test
pnpm build
```

## Static checks

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

## Commit guidelines

- Keep changes focused.
- Prefer small, reviewable commits.
- Do not commit `.env`, credentials, private corpora or evaluation outputs.
- Update documentation when behaviour or architecture changes.
- Add tests for bug fixes and new behaviour.
- If a feature is incomplete, say so in the commit or pull request description instead of presenting it as complete.

## Branch naming

Examples:

```text
feat/ingest-worker-retry
fix/cross-tenant-404
docs/evaluation-summary
chore/frontend-dependency-bump
```

## Pull requests

Please include:

1. What changed.
2. Why the change is needed.
3. How it was tested.
4. Any known limitations or follow-up work.
