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
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="eval 门禁：自带 stub 栈跑一遍")
    parser.add_argument("--baseline", default="eval/baselines/stub-hashing")
    parser.add_argument("--python", default=sys.executable, help="跑子命令用的解释器")
    args = parser.parse_args(argv)

    api_dsn = os.environ.get("PYKNORA_DATABASE_URL") or DEFAULT_DSN
    # worker 必须用能跨租户认领任务的角色：面向用户的 pyknora_app 受 RLS 限制，
    # 用它认领 `ingestion_jobs` 恒为 0 条，入库会永远停在 pending（且不报错）。
    worker_dsn = os.environ.get("PYKNORA_WORKER_DATABASE_URL") or api_dsn.replace(
        "pyknora_app:", "pyknora_worker:"
    ).replace("pyknora_app%40", "pyknora_worker%40")
    stub_port, api_port = free_port(), free_port()
    stub_url = f"http://127.0.0.1:{stub_port}/v1"
    api_root = f"http://127.0.0.1:{api_port}"
    env = build_env(api_dsn, stub_url)

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
