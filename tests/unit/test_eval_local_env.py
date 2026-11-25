"""门禁自带栈的环境必须自给自足。

2026-11-25 的教训：CI 里没有 `.env`，`PYKNORA_CHAT_API_KEY` 为空，被测服务对每条
样本都回 `503 chat model is not configured`，结果 `citations` 全空，门禁判成"检索
退化"——排查方向被带偏两轮。根因就是这里没有显式给 key，指望 `.env` 兜底。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import eval_local  # noqa: E402


def test_chat_api_key_is_injected_even_without_dotenv() -> None:
    env = eval_local.build_env("postgresql+asyncpg://u:p@h/db", "http://127.0.0.1:1/v1", base={})
    assert env["PYKNORA_CHAT_API_KEY"], "空 key 会让被测服务直接 503，而不是走假模型"


def test_parent_key_does_not_leak_into_gateway() -> None:
    # 父进程（本地 .env 或 shell）可能带着真 key；门禁必须指向 stub，不能拿真 key 打真网关。
    env = eval_local.build_env(
        "postgresql+asyncpg://u:p@h/db",
        "http://127.0.0.1:1/v1",
        base={"PYKNORA_CHAT_API_KEY": "real-secret"},
    )
    assert env["PYKNORA_CHAT_API_KEY"] == eval_local.STUB_CHAT_API_KEY
    assert env["PYKNORA_CHAT_BASE_URL"] == "http://127.0.0.1:1/v1"
    assert env["PYKNORA_CHAT_MODEL"] == "stub"


def test_worker_dsn_swaps_password_too() -> None:
    """只换用户名不换口令 = 必然认证失败。

    `migrations/0003` 建 pyknora_app 用 `pyknora_app_dev`，`migrations/0017` 建
    pyknora_worker 用 `pyknora_worker_dev`。早先的推导只把用户名换掉，于是 worker
    拿着 app 的口令去连，回 `password authentication failed for user "pyknora_worker"`；
    worker 是常驻循环，连不上只打 traceback 继续转，入库任务永远 pending，
    门禁在 `wait_job` 里对每篇文档等 600 秒——现象是"跑不完"，不是"报错"。
    """
    got = eval_local.derive_worker_dsn(
        "postgresql+asyncpg://pyknora_app:pyknora_app_dev@127.0.0.1:5435/pyknora"
    )
    assert got == "postgresql+asyncpg://pyknora_worker:pyknora_worker_dev@127.0.0.1:5435/pyknora"


def test_worker_dsn_leaves_other_roles_alone() -> None:
    # 不是 app 角色（比如 postgres 或自建角色）就原样返回：猜别人的口令比报错更糟。
    dsn = "postgresql+asyncpg://postgres:dev@127.0.0.1:5435/pyknora"
    assert eval_local.derive_worker_dsn(dsn) == dsn


def test_worker_dsn_keeps_no_port_form() -> None:
    got = eval_local.derive_worker_dsn(
        "postgresql+asyncpg://pyknora_app:pyknora_app_dev@db/pyknora"
    )
    assert got == "postgresql+asyncpg://pyknora_worker:pyknora_worker_dev@db/pyknora"
