"""默认连库角色必须是 `pyknora_app`，不是超级用户。

为什么要一条测试来钉住这件事：`migrations/0011_index_migrations_rls.sql` 建的
`ws_isolation` 策略在超级用户面前等于不存在——`postgres` 天然 BYPASSRLS，用它连库时
RLS 只是"写在库里"，并不会真拦数据（见 `tests/integration/test_rls.py` 的对照）。
这是**静默失效**：不报错、不告警、跨工作区查询照样返回别人的行，所以在默认值上退回去
一次，就等于把第二道网悄悄拆掉。

`api/main.py` 启动时会打 `db_role_bypasses_rls` 告警，但那只是运行期提醒；这里从
**配置默认值**上钉死，让"改回 postgres"必须先改这条测试。

注意断言的是 `Settings` 的**字段默认值**，不是 `settings`（运行期实例）——后者会被
`.env` 或环境变量覆盖（本地/CI 各自不同），拿它断言等于在测部署环境而不是测代码。
"""

from __future__ import annotations

from sqlalchemy.engine import make_url

from pyknora.core.settings import Settings

SUPERUSER_NAMES = frozenset({"postgres"})


def _default_database_url() -> str:
    field = Settings.model_fields["database_url"]
    default = field.default
    assert isinstance(default, str), "database_url 必须有字符串默认值"
    return default


def test_default_database_url_uses_app_role() -> None:
    url = make_url(_default_database_url())
    assert url.username == "pyknora_app", (
        f"默认连库角色是 {url.username!r}，应为 'pyknora_app'。"
        "用超级用户连库会让 0011 的 ws_isolation 策略静默失效。"
    )


def test_default_database_url_is_not_superuser() -> None:
    url = make_url(_default_database_url())
    assert url.username not in SUPERUSER_NAMES

    # 迁移服务（compose 的 `migrate`）走的是 psql -U postgres，那是建表需要 owner 权限；
    # 运行期默认值不该跟着它一起用超级用户。
    assert "postgres:" not in _default_database_url()
