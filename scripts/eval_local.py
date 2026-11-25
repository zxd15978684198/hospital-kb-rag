#!/usr/bin/env python3
"""跑 eval 门禁，自带一套临时栈：确定性假模型 + API。

为什么要有这个脚本：`eval/baselines/stub-hashing` 是**假模型 + 确定性 hashing 嵌入**
跑出来的基线。
如果拿一个接真模型的 API 去比，会得到一堆 `COMPLETED→FAILED` 的"退化"——
那不是回归，那是把两套东西当成了同一套。所以门禁的输入必须自证：
本脚本自己拉起 `eval/stub_llm.py` 与一个只指向它的 uvicorn，跑完即拆。

CI 的 `eval` job 在 workflow 里手写了同样的三段（起 stub → 起 api → 跑 eval_ci）。
这里再写一遍不是为了重复，而是让 `python scripts/ci.py --stage eval` 在本地
等价于 CI 的那条 job：本地绿、CI 红，才说明是真差异而不是栈不一样。

前置：PostgreSQL（pgvector）已起、迁移已跑。数据库地址取 `PYKNORA_DATABASE_URL`，
缺省 `postgresql+asyncpg://pyknora_app:pyknora_app_dev@127.0.0.1:5433/pyknora`。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DSN = "postgresql+asyncpg://pyknora_app:pyknora_app_dev@127.0.0.1:5433/pyknora"

# 假模型不校验鉴权，但**被测服务**在 `chat_api_key` 为空时会直接回
# `503 DEPENDENCY_UNAVAILABLE chat model is not configured`（answer.py:348，
# 这是对的：没配 key 是运维问题，不该先判"资料不足"）。所以这里必须给一个占位 key。
#
# 为什么踩过：本地 `.env` 里有真的 `PYKNORA_CHAT_API_KEY`，所以本地一直绿；
# CI 里没有 `.env`，key 为空 —— 于是 30 条样本全是 503、citations 全空，
# 门禁报的却是"检索退化"，排查方向被带偏了整整两轮。
STUB_CHAT_API_KEY = "stub-not-a-secret"


def build_env(
    api_dsn: str,
    stub_url: str,
    *,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    """被测服务与 stub 共用的环境。

    数据源是 `base`（默认 `os.environ`）：本地有 `.env` 时它会被 pydantic-settings
    读进 Settings，但**子进程不继承 .env**，只继承这里给的东西 —— 所以凡是
    "被测服务必须有值"的项都得在这显式写，不能指望 `.env` 兜底。
    """
    return {
        **(base if base is not None else os.environ),
        "PYKNORA_DATABASE_URL": api_dsn,
        "PYKNORA_CHAT_BASE_URL": stub_url,
        "PYKNORA_CHAT_MODEL": "stub",
        # 见 STUB_CHAT_API_KEY 的注释：空 key 会让被测服务直接 503。
        "PYKNORA_CHAT_API_KEY": STUB_CHAT_API_KEY,
        # 嵌入后端固定走 hashing：CI runner 上没有 bge 模型缓存，且 hashing 是确定性的。
        # 代价是向量路召回没有语义能力，所以这条门禁只验接线与安全，不产质量数字。
        "PYKNORA_EMBEDDING_BACKEND": os.environ.get("PYKNORA_EMBEDDING_BACKEND", "hashing"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "PYTHONUNBUFFERED": "1",
    }


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_health(url: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - 本机回环
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - 端口还没起来时任何异常都算"再等等"
            time.sleep(1.0)
    return False


def stop(proc: subprocess.Popen | None) -> None:
    """拆树：Windows 上 uvicorn 会派生 reloader/子进程，只杀父进程会留孤儿占端口。"""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            check=False,
            capture_output=True,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()


# 两个默认角色的口令来自 `migrations/0003`（app）与 `migrations/0017`（worker）。
# 换 DSN 时**用户名和口令必须一起换**：只换用户名的话，worker 会拿 app 的口令去连，
# 回 `password authentication failed for user "pyknora_worker"`，而 worker 是常驻循环、
# 连不上只会每轮打一条 traceback 继续转，于是入库任务永远 pending，
# 门禁在 `wait_job` 里对每篇文档干等 600 秒 —— 表现为"跑不完"，不是"报错"。
APP_ROLE, APP_PASSWORD = "pyknora_app", "pyknora_app_dev"
WORKER_ROLE, WORKER_PASSWORD = "pyknora_worker", "pyknora_worker_dev"


def derive_worker_dsn(api_dsn: str) -> str:
    """把面向用户的 app DSN 换成能跨租户认领任务的 worker DSN。

    为什么要换角色：`pyknora_app` 受 RLS 约束，用它认领 `ingestion_jobs` 恒为 0 条，
    入库永远停在 pending 且**不报错**（这正是 `migrations/0017` 存在的理由）。
    为什么口令也要换：见上面的常量注释——只换用户名会得到一个必然认证失败的 DSN。
    显式传 `PYKNORA_WORKER_DATABASE_URL` 时仍然完全以它为准（生产口令各不相同）。
    """
    parts = urlsplit(api_dsn)
    if parts.username != APP_ROLE:
        # 已经是别的角色（postgres、自定义角色），原样返回，不去猜它的口令。
        return api_dsn
    netloc = f"{WORKER_ROLE}:{WORKER_PASSWORD}@{parts.hostname}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit(parts._replace(netloc=netloc))


def probe_dsn(dsn: str, role: str) -> str | None:
    """连得上返回 None，连不上返回一句面向人的原因。

    为什么要在起栈之前查：worker 是常驻循环，DSN 错了它只会每轮打一条 traceback 接着转；
    入库任务因此永远停在 pending，而门禁在 `wait_job` 里对每篇文档等 600 秒。
    结果是"跑不完"而不是"报错"——排查成本极高（本次就是这么被误导的）。
    这里花一秒把口令/角色错误摆到台面上。
    """

    async def _try() -> None:
        engine = create_async_engine(dsn, pool_pre_ping=False, pool_size=1)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_try())
    except Exception as exc:  # noqa: BLE001 —— 这里要的就是把各种连接错误统一说人话
        return f"{role} 连不上库：{type(exc).__name__}: {exc}".replace(dsn, "<dsn>")
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="eval 门禁：自带 stub 栈跑一遍")
    parser.add_argument("--baseline", default="eval/baselines/stub-hashing")
    parser.add_argument("--python", default=sys.executable, help="跑子命令用的解释器")
    args = parser.parse_args(argv)

    api_dsn = os.environ.get("PYKNORA_DATABASE_URL") or DEFAULT_DSN
    worker_dsn = os.environ.get("PYKNORA_WORKER_DATABASE_URL") or derive_worker_dsn(api_dsn)
    stub_port, api_port = free_port(), free_port()
    stub_url = f"http://127.0.0.1:{stub_port}/v1"
    api_root = f"http://127.0.0.1:{api_port}"
    env = build_env(api_dsn, stub_url)

    for dsn, role in ((api_dsn, "api"), (worker_dsn, "worker")):
        problem = probe_dsn(dsn, role)
        if problem is not None:
            print(f"[eval-local] {problem}", file=sys.stderr)
            return 2

    stub: subprocess.Popen | None = None
    worker: subprocess.Popen | None = None
    api: subprocess.Popen | None = None
    try:
        print(f"[eval-local] stub -> {stub_url}", flush=True)
        stub = subprocess.Popen(
            [args.python, "eval/stub_llm.py", "--port", str(stub_port), "--quiet"],
            cwd=ROOT,
            env=env,
        )
        if not wait_health(f"http://127.0.0.1:{stub_port}/health", 30):
            print("[eval-local] stub 没起来", file=sys.stderr)
            return 2

        print("[eval-local] worker（入库）", flush=True)
        worker = subprocess.Popen(
            [args.python, "scripts/run_worker.py"],
            cwd=ROOT,
            env={**env, "PYKNORA_DATABASE_URL": worker_dsn},
        )

        print(f"[eval-local] api  -> {api_root}", flush=True)
        api = subprocess.Popen(
            [
                args.python,
                "-m",
                "uvicorn",
                "pyknora.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            cwd=ROOT,
            env=env,
        )
        if not wait_health(f"{api_root}/health", 180):
            print("[eval-local] api 没起来（数据库起没起？迁移跑了吗？）", file=sys.stderr)
            return 2

        print(f"[eval-local] gate vs {args.baseline}", flush=True)
        completed = subprocess.run(
            [
                args.python,
                "scripts/eval_ci.py",
                "--base-url",
                f"{api_root}/api/v1",
                "--gate-baseline",
                args.baseline,
            ],
            cwd=ROOT,
            env=env,
            check=False,
        )
        return completed.returncode
    finally:
        stop(api)
        stop(worker)
        stop(stub)


if __name__ == "__main__":
    sys.exit(main())
