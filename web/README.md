# PyKnora Web

Next.js client for the PyKnora knowledge base.

## Stack

- Next.js App Router
- React 19
- TypeScript
- TanStack Query
- Tailwind CSS
- Zod
- Vitest
- Playwright

## Quick start

```bash
pnpm install
pnpm dev
```

Open:

```text
http://127.0.0.1:3000
```

The frontend proxies `/api/*` to the backend at `http://127.0.0.1:8010` by default. To override the backend address:

```bash
$env:API_PROXY_TARGET="http://127.0.0.1:8000"; pnpm dev
```

## Scripts

| Command | Purpose |
| --- | --- |
| `pnpm dev` | Start the development server |
| `pnpm build` | Create a production build |
| `pnpm start` | Start the production build |
| `pnpm typecheck` | Run TypeScript checks |
| `pnpm test` | Run Vitest suites |
| `pnpm test:e2e` | Run Playwright end-to-end tests |
| `pnpm gen:contracts` | Regenerate OpenAPI types |
| `pnpm check` | Run typecheck and tests |

## Project layout

```text
web/
├── app/               # Next.js routes and layouts
├── components/        # Shared UI primitives
├── features/          # Domain-specific API, hooks and components
├── lib/               # API client, contracts and auth helpers
├── contracts/         # OpenAPI snapshot and generated types
└── tests/             # Unit, contract, MSW and end-to-end tests
```

## API contract

The backend contract snapshot lives in `contracts/openapi.json`. Generated TypeScript types are in `lib/contracts/openapi.d.ts`.

After changing the backend API:

1. Refresh the OpenAPI snapshot.
2. Run `pnpm gen:contracts`.
3. Update contract tests if needed.
4. Run `pnpm check`.
