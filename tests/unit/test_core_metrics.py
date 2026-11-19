"""N1 指标底座（`src/pyknora/core/metrics.py`）的契约测试。

钉住四件事：渲染格式是 Prometheus text v0.0.4（`# HELP` / `# TYPE` / `名{标签} 值`）、
标签白名单真的会拒绝未声明的名字与取值、并发自增不丢写、以及"埋点不能打断主路径"
——`safe_inc` 遇到非法标签必须吞掉异常并把失败记到 `pyknora_metrics_errors_total`。

另外钉住两件容易被顺手改坏的事：没测过的序列**不出现在输出里**（否则 0 与"没测到"
会被读成同一个意思），以及计数器不能减少。
"""

from __future__ import annotations

import threading

import pytest

from pyknora.core.metrics import (
    BUILD_INFO,
    GATE_VERDICT,
    INTERNAL_ERRORS,
    QUEUE_JOBS,
    Counter,
    Gauge,
    configure_metrics,
    degraded_code_bucket,
    render,
    reset_for_tests,
    safe_inc,
    safe_set,
)

# 每个用例都从空序列起步：注册表是模块级单例，不清空会让断言互相污染。
# 声明本身不清理（重复声明必须一致，清理反而会掩盖两处定义同一个名字的问题）。


@pytest.fixture(autouse=True)
def _clean_series() -> None:
    reset_for_tests()


def test_render_emits_help_type_and_sorted_series() -> None:
    counter = Counter(
        "test_metric_render_total",
        "渲染样例",
        {"branch": ("vector", "keyword")},
    )
    counter.inc(branch="vector")
    counter.inc(branch="vector")
    counter.inc(branch="keyword")

    text = render()

    assert "# HELP test_metric_render_total 渲染样例" in text
    assert "# TYPE test_metric_render_total counter" in text
    # 标签按名字排序、序列按标签元组排序：同一份状态每次渲染出同样的字节。
    assert 'test_metric_render_total{branch="keyword"} 1' in text
    assert 'test_metric_render_total{branch="vector"} 2' in text
    assert text.index('branch="keyword"') < text.index('branch="vector"')
    assert text.endswith("\n")


def test_unmeasured_series_do_not_appear() -> None:
    """没测到 ≠ 0：没有序列时整页为空，而不是出现一堆 0。"""
    assert render() == ""

    Counter("test_metric_partial_total", "只测一个分支", {"branch": ("vector", "keyword")})
    assert render() == ""


def test_same_labels_accumulate_and_different_labels_separate() -> None:
    counter = Counter("test_metric_accum_total", "累加", {"outcome": ("ok", "error")})
    counter.inc(amount=2.0, outcome="ok")
    counter.inc(amount=3.0, outcome="ok")
    counter.inc(outcome="error")

    assert counter.value(outcome="ok") == 5.0
    assert counter.value(outcome="error") == 1.0
    assert counter.value(outcome="error") == 1.0


def test_counter_rejects_negative_amount() -> None:
    counter = Counter("test_metric_negative_total", "不许减少", {"outcome": ("ok",)})
    with pytest.raises(ValueError, match="不能减少"):
        counter.inc(amount=-1.0, outcome="ok")


@pytest.mark.parametrize(
    ("labels", "match"),
    [
        ({"outcome": "ok", "extra": "x"}, "标签必须恰好是"),
        ({"outcome": "not-an-enum"}, "不在允许取值"),
        ({"outcome": 1}, "必须是字符串"),
    ],
)
def test_undeclared_labels_rejected(labels: dict[str, object], match: str) -> None:
    counter = Counter("test_metric_strict_total", "白名单", {"outcome": ("ok", "error")})
    with pytest.raises(ValueError, match=match):
        counter.inc(**labels)  # type: ignore[arg-type]


def test_open_label_is_format_checked() -> None:
    gauge = Gauge("test_metric_open_info", "开放取值", {"env": None})
    gauge.set(1.0, env="dev")
    assert gauge.value(env="dev") == 1.0

    with pytest.raises(ValueError, match="不符合开放取值格式"):
        gauge.set(1.0, env='bad"value')


def test_gauge_can_go_down_and_unset_returns_none() -> None:
    gauge = Gauge("test_metric_queue", "量表", {"status": ("queued", "running")})
    assert gauge.value(status="queued") is None

    gauge.set(5, status="queued")
    gauge.set(2, status="queued")
    assert gauge.value(status="queued") == 2.0


def test_concurrent_increments_do_not_lose_writes() -> None:
    """8 线程各加 500 次，合计必须正好 4000——丢了就说明锁没起作用。"""
    counter = Counter("test_metric_concurrent_total", "并发", {"outcome": ("ok",)})
    threads = [
        threading.Thread(target=lambda: [counter.inc(outcome="ok") for _ in range(500)])
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert counter.value(outcome="ok") == 4000.0


def test_configure_metrics_writes_build_info() -> None:
    configure_metrics(env="dev", version="0.1.0")
    assert BUILD_INFO.value(env="dev", version="0.1.0") == 1.0
    assert 'pyknora_build_info{env="dev",version="0.1.0"} 1' in render()


def test_configure_metrics_never_raises() -> None:
    """版本号里出现非法字符时，启动不能失败，只记一笔内部错误。"""
    configure_metrics(env="dev", version="not a legal version")
    assert INTERNAL_ERRORS.value(op="configure") == 1.0


def test_safe_inc_swallows_bad_labels() -> None:
    safe_inc(GATE_VERDICT, verdict="NOT_A_VERDICT")
    assert INTERNAL_ERRORS.value(op="inc") == 1.0

    safe_inc(GATE_VERDICT, verdict="SUPPORTED")
    assert GATE_VERDICT.value(verdict="SUPPORTED") == 1.0


def test_safe_set_swallows_bad_labels() -> None:
    safe_set(QUEUE_JOBS, 3, status="nowhere")
    assert INTERNAL_ERRORS.value(op="inc") == 1.0


@pytest.mark.parametrize(
    ("code", "bucket"),
    [
        ("PARSE_TIMEOUT", "timeout"),
        ("TimeoutError", "timeout"),
        ("EmbeddingDimensionError", "embedding_dimension"),
        ("OperationalError", "connection"),
        ("DEPENDENCY_UNAVAILABLE", "connection"),
        ("INTERNAL_ERROR", "app_error"),
        ("WeirdThing", "other"),
    ],
)
def test_degraded_code_bucket(code: str, bucket: str) -> None:
    assert degraded_code_bucket(code) == bucket


def test_reset_for_tests_clears_values() -> None:
    counter = Counter("test_metric_reset_total", "重置", {"outcome": ("ok",)})
    counter.inc(outcome="ok")
    assert render() != ""

    reset_for_tests()

    assert render() == ""
    assert counter.value(outcome="ok") == 0.0
