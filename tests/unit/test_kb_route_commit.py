"""知识库写路由必须在返回前提交。

**为什么单独测这一条**：`src/pyknora/api/deps.py` 的 `get_session` 是 yield 依赖，
它包的 `session_scope()` 在**响应发出之后**才 commit（FastAPI 0.106 起 yield 依赖的
退出代码晚于响应）。所以路由自己不 commit 的话，`POST /knowledge-bases` 会先回 201、
再落库——客户端拿着刚拿到的 `kb_id` 立刻上传文档，那个请求读不到这一行，返回
`404 knowledge base not found`。2026-11-25 CI 上就是这样红的（kb_rows=1 但
doc_rows=0，且本地 20 次复现不出来）。

**为什么不写成 API 级测试**：`httpx.ASGITransport` 会等整个请求生命周期跑完才返回，
依赖退出时的 commit 已经发生，断言恒真——那样的测试改坏了也不会红（试过，确实不红）。
所以这里直接调路由函数，用一个记录调用的假 session。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from pyknora.api.v1 import knowledge_bases as routes
from pyknora.core.scope import Scope
from pyknora.schemas.kb import EmbeddingProfile, KnowledgeBase


class _FakeSession:
    def __init__(self) -> None:
        self.committed = 0

    async def commit(self) -> None:
        self.committed += 1


class _FakeRepo:
    def __init__(self, kb: KnowledgeBase) -> None:
        self._kb = kb

    async def create(self, scope: Scope, *, name: str, profile: EmbeddingProfile) -> KnowledgeBase:
        return self._kb

    async def get(self, scope: Scope, kb_id: Any) -> KnowledgeBase | None:
        return self._kb

    async def rename(
        self, scope: Scope, kb_id: Any, *, name: str, expected_version: int
    ) -> KnowledgeBase:
        return self._kb.model_copy(update={"name": name, "row_version": expected_version + 1})

    async def soft_delete(
        self, scope: Scope, kb_id: Any, *, expected_version: int
    ) -> KnowledgeBase:
        return self._kb


def _kb(scope: Scope) -> KnowledgeBase:
    now = datetime.now(UTC)
    return KnowledgeBase(
        kb_id=uuid4(),
        workspace_id=scope.workspace_id,
        name="提交可见",
        profile=EmbeddingProfile(
            embedding_model_key="test/hashing",
            embedding_dim=512,
            chunk_size=512,
            chunk_overlap=64,
        ),
        row_version=1,
        generation=1,
        created_at=now,
        updated_at=now,
    )


def _scope() -> Scope:
    return Scope(workspace_id=uuid4(), principal_id=uuid4())


async def test_create_commits_before_returning() -> None:
    scope, session = _scope(), _FakeSession()
    repo = _FakeRepo(_kb(scope))
    body = routes.KnowledgeBaseCreate(
        name="提交可见",
        embedding_model_key="test/hashing",
        embedding_dim=512,
        chunk_size=512,
        chunk_overlap=64,
        normalize=True,
    )
    await routes.create(body=body, scope=scope, session=session, repo=repo)  # type: ignore[arg-type]
    assert session.committed == 1, "create 返回前必须已提交，否则 201 会早于落库"


async def test_patch_commits_before_returning() -> None:
    scope, session = _scope(), _FakeSession()
    repo = _FakeRepo(_kb(scope))
    await routes.patch(  # type: ignore[arg-type]
        kb_id=uuid4(),
        body=routes.KnowledgeBasePatch(name="新名称"),
        scope=scope,
        session=session,
        repo=repo,
        if_match='"1"',
    )
    assert session.committed == 1


async def test_delete_commits_before_returning(monkeypatch: pytest.MonkeyPatch) -> None:
    scope, session = _scope(), _FakeSession()
    kb = _kb(scope)
    repo = _FakeRepo(kb)

    class _Resp:
        headers: dict[str, str] = {}

    async def _fake_delete(session_: Any, repo_: Any, scope_: Scope, kb_id: Any, **kw: Any):
        return kb

    monkeypatch.setattr(routes, "delete_knowledge_base", _fake_delete)
    await routes.remove(  # type: ignore[arg-type]
        kb_id=kb.kb_id,
        response=_Resp(),  # type: ignore[arg-type]
        scope=scope,
        session=session,
        repo=repo,
        if_match='"1"',
    )
    assert session.committed == 1
