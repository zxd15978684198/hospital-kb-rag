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
