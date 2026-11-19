"""`GET /metrics` 的契约测试。

只钉三件事，且都不碰数据库：端点回 200 且 Content-Type 是 Prometheus text v0.0.4、
`create_app()` 已经把 `build_info` 写进注册表、以及它**不出现在 OpenAPI 里**
（`include_in_schema=False`，否则 `web/contracts/openapi.json` 会无谓漂移）。

这里不走 `tests/api/conftest.py` 的 `client` 夹具：那个夹具会 seed 身份数据、
需要真库；`/metrics` 是纯旁路端点，不该为了一个静态页面拉一套 PG 进来。
"""

from __future__ import annotations

import httpx

from pyknora.api.main import create_app


async def test_metrics_endpoint_serves_prometheus_text() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    # `configure_metrics` 在 create_app 里就跑过了，所以这一行一定在。
    assert "pyknora_build_info{" in response.text
    assert "# TYPE pyknora_build_info gauge" in response.text


async def test_metrics_is_not_part_of_the_openapi_contract() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/openapi.json")

    paths = response.json()["paths"]
    assert "/health" in paths
    assert "/metrics" not in paths
