# 排障：评测门禁

`eval` 这条链要同时满足两件事：**判定要严**（30 条固定集逐例比对基线，任何一条退化就红），
**环境要少**（确定性假模型 + `hashing` 嵌入，不需要真 key、不需要 torch）。

代价是它自己会以三种方式坏，而且三种**看起来都像「检索变差」**——门禁报出来的永远是
`citations 变空`，而故障分别在上传、模型调用、异步作业三处。这份文档按「现象 → 归因 → 确认」
的顺序写，先看症状表定位，再看对应小节。

| 现象 | 往哪个方向查 | 最快的一条确认 |
| --- | --- | --- |
| 22–30 条全是「citations 变空」，`doc_rows`/`chunk_rows` 都是 0 | 上传就没成功 | §1 |
| 逐例 `q=200 c=503`、`err=DEPENDENCY_UNAVAILABLE`、`chat_total_p95_ms` 比基线**低**一大截 | 模型没被调用 | §2 |
| 卡住十几分钟不结束，日志停在等作业，没有任何报错 | 异步作业没跑 | §3 |

---

## 1. 上传返回 404，库里一篇文档都没有

**现象**（CI annotations 里的 `eval db` 段）：

```
kb_rows    | 1
doc_rows   | 0
chunk_rows | 0 | 0        # 总数 / 带 embedding 的
```

日志里能看到 `POST /knowledge-bases` **201**，紧接着 `POST /knowledge-bases/{kb_id}/documents`
**404**，响应体 `RESOURCE_NOT_FOUND: knowledge base not found`。

**归因**：知识库确实建出来了（`kb_rows=1`），但上传请求读不到它。原因是写路由依赖
`get_session()` 这个 `yield` 依赖提交事务，而 FastAPI 从 0.106 起把 `yield` 依赖的退出代码放在
**响应发出之后**执行——所以 `POST /knowledge-bases` 回 201 时这一行还没提交。客户端拿着
`kb_id` 立刻发上传请求，上传事务里的 `SELECT knowledge_bases` 就看不见它。

**已修**（`0c25aa8`）：`src/pyknora/api/v1/knowledge_bases.py` 的 `create` / `patch` / `remove`
三条写路由在返回前显式 `await session.commit()`，把「201 意味着已经落库」变成真的。
同目录其余写路由（`auth.py` / `documents.py` / `jobs.py` / `workspaces.py`）本来就是这个写法。

**确认**：

```bash
docker compose exec -T db psql -U postgres -d pyknora -c \
  "select (select count(*) from knowledge_bases) kb, (select count(*) from documents) doc, (select count(*) from chunks) chunk;"
```

**判据**：如果同一条上传请求重试就成功了，那基本可以确定是「响应早于提交”的时序问题，
而不是权限问题——权限问题重试一万次也是 404。

---

## 2. 每条样本都 503，模型根本没被调用

**现象**：

```
status=UNKNOWN cites=0 results=5 err=DEPENDENCY_UNAVAILABLE q=200 c=503
cites_empty=30 of 30
abstain_accuracy  0.3636 → 0.0
chat_total_p95_ms 100.93 → 8.85
```

`q=200 c=503` 是关键：查询接口正常返回，聊天接口拒绝服务。`chat_total_p95_ms` 从 100 ms
掉到 8 ms 也说明模型调用那一步根本没发生。

**归因**：`src/pyknora/services/rag/answer.py` 在调模型前先判

```python
if not settings.chat_api_key:
    raise AppError(ErrorCode.DEPENDENCY_UNAVAILABLE, "chat model is not configured")
```

（这个顺序是对的：没配 key 是运维问题，不该被伪装成「资料不足，所以不作答」。）
本地因为有 `.env` 里填了真 key 所以一直是绿的；CI 不读 `.env`，`Eval gate` 步骤只显式给了
`PYKNORA_DATABASE_URL` / `PYKNORA_WORKER_DATABASE_URL` / `PYKNORA_EMBEDDING_BACKEND` 三项，
`PYKNORA_CHAT_API_KEY` 是空的。

**已修**（`b16362f`）：`scripts/eval_local.py` 的 `build_env()` 显式注入
`PYKNORA_CHAT_API_KEY = STUB_CHAT_API_KEY`，并且**覆盖**父进程里可能存在的真 key——
否则本地一个真 key 会漏进假模型网关，把「本地绿」变成假绿。
`.env.example` 与 `README.md` 也补了说明：这一项留空是正常的，门禁走假模型不读它。

**确认**：

```bash
# 本地：确认注入的确实是占位 key，不是 .env 里的真 key
uv run python -c "from scripts.eval_local import build_env; import json; print(json.dumps(build_env('postgresql+asyncpg://x', 'http://127.0.0.1:1/v1')['PYKNORA_CHAT_API_KEY']))"
```

---

## 3. 跑不完：不报错，就是不动

**现象**：`eval_local.py` 起了栈，前几步都正常，然后一直等。没有任何异常，也没有超时——
因为超时是 600 秒一次，5 篇文档就是 50 分钟。

**归因**：worker 连不上库。`eval_local.py` 早先用「只改用户名」的方式从 api 的 DSN 推导 worker
的 DSN：

```
postgresql+asyncpg://pyknora_worker:pyknora_app_dev@...   # 错：口令是 api 角色的
```

而 `migrations/0017_worker_role.sql` 建这个角色时用的口令是 `pyknora_worker_dev`。
于是 worker 每一轮心跳都抛 `asyncpg.exceptions.InvalidPasswordError`。**worker 是常驻循环，
认证失败只打 traceback 继续转**，所以进程看起来活着，入库作业永远停在 pending，
`wait_job()` 老老实实等满 600 秒。

**已修**（`54d478a`）：`derive_worker_dsn()` 换角色时**连口令一起换**（用户名不是
`pyknora_app` 时原样返回，不猜口令）；起栈前用 `probe_dsn()` 对 api 与 worker 各做一次
`SELECT 1`，连不上立刻 `return 2` 并打人话：

```
[eval-local] worker 连不上库：DBAPIError: ... password authentication failed for user "pyknora_worker"
```

**确认**：

```bash
docker compose logs worker --tail=50 | grep -i "password authentication"
```

---

## 4. 在本地复现 CI 的红

CI 的 `eval` job 和本地跑的是同一条命令，只是环境不同。复现走这三步：

```bash
# 1) 独立的库（别用演示库：集成测试与门禁都会清表）
#    迁移全部应用后才有 RLS 策略与角色
uv run python - <<'PY'
print("MIGRATIONS: 0001..0017 全量应用")
PY

# 2) 用应用角色，不是超级用户（超级用户天然 BYPASSRLS，会让 RLS 静默失效）
export PYKNORA_DATABASE_URL="postgresql+asyncpg://pyknora_app:pyknora_app_dev@127.0.0.1:5435/pyknora_ci"

# 3) 跑门禁
uv run python scripts/eval_local.py --baseline eval/baselines/stub-hashing
```

要点：

- **端口别猜**。CI 里 db 在 `127.0.0.1:5433`，本地 `docker compose` 发布的是 `5435`；用错端口
  报的是 `ConnectionRefusedError`，不是「库不存在」。
- **`.env` 不参与**。`scripts/eval_local.py` 起的是子进程，子进程不读 `.env`，凡是必须有的值都在
  `build_env()` 里显式写死。
- **`app_commit` 不一致只是提示**，不是失败：基线是在某个 commit 上生成的，候选永远比它新。

---

## 5. 三条设计判据

这三类故障能同时存在，是因为它们都满足「默认路径把正确性推给调用者」。下次加功能时按这三条自查：

1. **默认路径必须自证正确**。判据：*别人 clone 下来直接跑，这件事还成立吗？* 不成立就必须由代码
   保证，或者 3 秒内报错。`chat_api_key` 默认空、worker 口令靠推导、提交靠依赖退出——三条都不满足。
2. **静默失败比报错贵一个数量级**。worker 认证失败继续转，表现是「卡 50 分钟」；启动时
   `SELECT 1` 一下，就变成 1 秒报错。常驻进程（worker、调度器、监听器）启动时必须先探一次依赖。
3. **报错信息要区分上游和下游**。「citations 变空」同时可能意味着「数据没进来」「模型没被调用」
   「检索没命中」。门禁在报指标退化之前，先断言前置条件（`doc_rows > 0`、`chat_http_status == 200`），
   否则读日志的人会被指到错误的方向。

---

## 相关文件

| 文件 | 作用 |
| --- | --- |
| `scripts/eval_local.py` | 起自带栈（stub + worker + api）跑门禁；`build_env` / `derive_worker_dsn` / `probe_dsn` 都在这里 |
| `scripts/eval_ci.py` | 单条命令跑完整评测：校验语料 sha256 → 上传 → 建集 → 跑一轮 → 过门禁 |
| `eval/gate.py` | 门禁判定与退出码：`2` 三件套不全 / `3` 安全泄漏 / `4` null_ratio 超阈 / `1` 逐例退化 |
| `eval/baselines/stub-hashing/` | 公开仓的基线：`hashing` 嵌入 + 假模型，阈值写在 `summary.json` |
| `.github/workflows/ci.yml` | `eval` job；失败时 `Surface eval failure` 把日志与库计数打成 annotations |
