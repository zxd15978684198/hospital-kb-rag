"""库角色分工：app 角色不许绕过 RLS，worker 角色必须绕过。

这条测试守的是一个**静默失败**：worker 按 `FOR UPDATE SKIP LOCKED` 跨租户认领
`ingestion_jobs`，而 `ws_isolation` 策略要求 `app.workspace_id` 有值——若 worker 用
`pyknora_app` 连库，认领恒为 0 条，任务永远 pending，日志里一个错误都没有。
第一次踩到时，现象是"上传成功、索引永远建不完、/jobs 永不终态"。

反过来，app 角色一旦被授予 BYPASSRLS，面向用户的查询路径就再没有第二道网——
所以两边都断言，缺一半都等于没守住。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def _role_attrs(session, role: str) -> tuple[bool, bool] | None:
    from sqlalchemy import text

    row = (
        await session.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = :name"),
            {"name": role},
        )
    ).first()
    if row is None:
        return None
    return bool(row[0]), bool(row[1])


async def test_app_role_does_not_bypass_rls(session) -> None:
    attrs = await _role_attrs(session, "pyknora_app")
    assert attrs is not None, "pyknora_app 角色不存在，0003 迁移没跑？"
    is_super, bypasses = attrs
    assert not is_super, "pyknora_app 不该是超级用户"
    assert not bypasses, (
        "pyknora_app 被授予了 BYPASSRLS——面向用户的查询路径就再没有 RLS 兜底了。"
        "需要跨租户扫描的后台作业应该用 pyknora_worker（0017）。"
    )


async def test_worker_role_bypasses_rls(session) -> None:
    attrs = await _role_attrs(session, "pyknora_worker")
    assert attrs is not None, "pyknora_worker 角色不存在，0017 迁移没跑？"
    is_super, bypasses = attrs
    assert not is_super, "worker 不该是超级用户，BYPASSRLS 就够了"
    assert bypasses, (
        "pyknora_worker 没有 BYPASSRLS：它按 FOR UPDATE SKIP LOCKED 跨租户认领任务，"
        "ws_isolation 会让它一条都认领不到，且不会有任何报错。"
    )
