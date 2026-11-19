# PyKnora Web

PyKnora 知识库系统的 Next.js 前端。

## 技术栈

- Next.js App Router
- React 19
- TypeScript
- TanStack Query
- Tailwind CSS
- Zod
- Vitest
- Playwright

## 快速开始

```bash
pnpm install
pnpm dev
```

打开：

```text
http://127.0.0.1:3000
```

前端会把 `/api/*` 代理到后端 `http://127.0.0.1:8010`。如果需要覆盖后端地址：

```bash
$env:API_PROXY_TARGET="http://127.0.0.1:8000"; pnpm dev
```

## 常用脚本

| 命令 | 作用 |
| --- | --- |
| `pnpm dev` | 启动开发服务器 |
| `pnpm build` | 生产构建 |
| `pnpm start` | 启动生产构建 |
| `pnpm typecheck` | TypeScript 类型检查 |
| `pnpm test` | Vitest 测试 |
| `pnpm test:e2e` | Playwright 端到端测试 |
| `pnpm gen:contracts` | 重新生成 OpenAPI 类型 |
| `pnpm check` | 类型检查加测试 |

## 目录结构

```text
web/
├── app/                # Next.js 路由
│   ├── (auth)/         # 登录页
│   └── (app)/          # 已登录知识工作台
├── components/         # 通用 UI 组件
├── features/           # 按业务域拆分的 API、hooks 和组件
├── lib/                # API client、契约和鉴权工具
├── contracts/          # OpenAPI 快照与生成类型
└── tests/              # 单元、契约、MSW 和 Playwright 测试
```

## API 契约

后端 OpenAPI 快照保存在 `contracts/openapi.json`。生成的 TypeScript 类型在 `lib/contracts/openapi.d.ts`。

后端接口变化后：

1. 更新 OpenAPI 快照。
2. 执行 `pnpm gen:contracts`。
3. 必要时更新契约测试。
4. 执行 `pnpm check`。
