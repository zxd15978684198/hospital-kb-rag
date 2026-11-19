"""指标底座：进程内的计数器与量表，按 Prometheus text exposition 渲染。

## 为什么需要这个模块

日志回答"刚才这一次请求发生了什么"，指标回答"过去一天一共发生了多少次"。
两者不能互相替代：`retrieval_completed outcome=error` 这种事件每天上万条，
采集器能存下来，却没人会去数——所以"降级了几次""拒答率是多少"必须有个
带标签的累计值，否则只能靠人肉 grep 日志。

改造前 `src/` 里没有任何计数器（`grep -E 'Counter|Histogram|metrics'` 零命中），
`degraded` 只作为一次请求的响应字段透给前端；运维问"昨天向量那一路挂了几次"
是答不出来的。这个模块补的正是这一层。

## 为什么不用 prometheus_client

它是标准答案，但对这个仓库是净负担：新增一个第三方依赖、给离线环境多一道
安装门槛、`multiprocess` 模式还要额外配置；而本项目只需要 10 个指标、
一个 `/metrics` 端点、单机 compose。标准库足够，且渲染格式有公开规范可依
（Prometheus text format v0.0.4），不构成私有协议。

如果将来真的要接更多指标或直方图，替换点是 `render()` 与 `Counter`/`Gauge`
两个类——调用点只认 `inc()` / `set()`，不认实现。

## 标签白名单（这条是硬约束）

所有标签名与取值都在本模块**声明**，未声明的名字或取值抛 `ValueError`。
理由是指标基数：Prometheus 按标签组合存时间序列，把用户问题、文档 ID、
异常原文塞进标签，一张表能涨到千万级序列，抓取端先垮，服务端也跟着慢。

因此：

- 标签值只允许来自代码内枚举（`retrieval_strategy`、`Verdict` 这类）；
- 需要"错误码"的地方用**桶**（`DEGRADED_CODE_BUCKETS`），精确码留在日志里——
  日志没有基数上限，指标有；
- `build_info` 的 env/version 是开放取值，只做格式校验（短、无引号无换行）。

## 与日志的分工

| 想知道 | 看哪里 |
| --- | --- |
| 这一次为什么降级 / 降级码是什么 | 日志 `retrieval_branch_failed` |
| 降级累计多少次、按哪个分支 | `pyknora_retrieval_branch_degraded_total` |
| 拒答的理由是什么 | 日志 `gate_refused` + `assessment.reason` |
| 拒答占比 | `pyknora_gate_verdict_total` / `pyknora_gate_refusals_total` |

## 边界

- **进程内计数**：`docker-compose.yml` 里 `api` 与 `worker` 是两个进程，注册表不
  共享——worker 自增的值不会出现在 api 容器的 `/metrics` 上。跨进程只能看日志
  事件；需要真共享得换 Redis/多进程聚合器，那属于另一套基础设施。
- **不是安全边界**：`/metrics` 无鉴权。这里只有聚合计数，没有租户 ID、文档内容
  或问答原文；上公网需在外层反代上加访问控制。
- **不做直方图**：延迟已由 `chat_finished` 事件的 stage 字典记录，聚合口径归
  离线评测（`eval/`）。三档分位数远不够定位，硬塞进指标只会让标签爆炸。
- import 无副作用：只做声明，不读环境变量、不建连接。
"""

from __future__ import annotations

import re
import threading
from typing import Final, Literal

MetricKind = Literal["counter", "gauge"]

# 开放取值标签（env / version）的格式约束：短、可打印、无需转义。
_OPEN_LABEL = re.compile(r"^[A-Za-z0-9._:+-]{1,32}$")

# 内部错误的操作名，只在本模块内使用。
_METRICS_OPS: Final = ("inc", "scrape", "render", "configure")

_LOCK = threading.Lock()
# 指标名 -> (help, kind, 标签名 -> 允许取值（None 表示开放取值）)
_DECLARED: dict[str, tuple[str, MetricKind, dict[str, tuple[str, ...] | None]]] = {}
# 指标名 -> {(标签名, 取值) 的排序元组 -> 当前值}
_SERIES: dict[str, dict[tuple[tuple[str, str], ...], float]] = {}


def _declare(
    name: str,
    help_text: str,
    kind: MetricKind,
    labels: dict[str, tuple[str, ...] | None],
) -> None:
    """登记一个指标。重复登记必须完全一致，否则说明两处定义了同一个名字。"""
    spec = (help_text, kind, dict(labels))
    with _LOCK:
        existing = _DECLARED.get(name)
        if existing is None:
            _DECLARED[name] = spec
            _SERIES.setdefault(name, {})
            return
        if existing != spec:
            raise ValueError(f"指标 {name} 被以不同定义重复声明，两处必须保持一致")


def _check_labels(name: str, labels: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """校验标签名/取值是否在声明内，返回可作键的排序元组。"""
    spec = _DECLARED[name][2]
    if set(labels) != set(spec):
        raise ValueError(f"指标 {name} 的标签必须恰好是 {sorted(spec)}，收到 {sorted(labels)}")
    for key, value in labels.items():
        if not isinstance(value, str):
            raise ValueError(f"指标 {name} 的标签 {key} 必须是字符串，收到 {type(value).__name__}")
        allowed = spec[key]
        if allowed is None:
            if not _OPEN_LABEL.match(value):
                raise ValueError(f"指标 {name} 的标签 {key}={value!r} 不符合开放取值格式")
        elif value not in allowed:
            raise ValueError(f"指标 {name} 的标签 {key}={value!r} 不在允许取值 {allowed} 内")
    return tuple(sorted(labels.items()))


class Counter:
    """只增不减的累计值。"""

    __slots__ = ("_name",)

    def __init__(
        self,
        name: str,
        help_text: str,
        labels: dict[str, tuple[str, ...] | None] | None = None,
    ) -> None:
        _declare(name, help_text, "counter", labels or {})
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError(f"计数器 {self._name} 不能减少，收到 amount={amount}")
        key = _check_labels(self._name, labels)
        with _LOCK:
            series = _SERIES[self._name]
            series[key] = series.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        """读当前值；未出现过返回 0.0（只给测试与断言用）。"""
        key = _check_labels(self._name, labels)
        with _LOCK:
            return _SERIES[self._name].get(key, 0.0)


class Gauge:
    """可升可降的瞬时值。"""

    __slots__ = ("_name",)

    def __init__(
        self,
        name: str,
        help_text: str,
        labels: dict[str, tuple[str, ...] | None] | None = None,
    ) -> None:
        _declare(name, help_text, "gauge", labels or {})
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def set(self, value: float, **labels: str) -> None:
        key = _check_labels(self._name, labels)
        with _LOCK:
            _SERIES[self._name][key] = float(value)

    def value(self, **labels: str) -> float | None:
        key = _check_labels(self._name, labels)
        with _LOCK:
            return _SERIES[self._name].get(key)


# --------------------------------------------------------------------------- 指标定义
#
# 取值集合一律写在这里，而不是在调用点传字符串字面量；调用点拼错了会当场抛
# ValueError，而不是悄悄长出一条无人查询的时间序列。

RETRIEVAL_STRATEGIES: Final = ("baseline", "candidate")
RETRIEVAL_OUTCOMES: Final = ("ok", "empty", "error")
RETRIEVAL_BRANCHES: Final = ("vector", "keyword")
# 降级码是桶不是原文：异常类名与 ErrorCode 都可能新增，精确值放日志。
DEGRADED_CODE_BUCKETS: Final = (
    "timeout",
    "connection",
    "embedding_dimension",
    "app_error",
    "other",
)
VERDICTS: Final = ("SUPPORTED", "INSUFFICIENT", "CONFLICTING", "SKIPPED")
REFUSAL_REASONS: Final = ("no_evidence", "conflict")
# 与 `pyknora/schemas/chat.py` 的 `RunStatus` 同序同值：终态改动时这里会跟着报错，
# 而不是悄悄长出一条没人查的序列。
CHAT_STATUSES: Final = ("COMPLETED", "ABSTAINED", "TIMEOUT", "BUDGET_EXCEEDED", "FAILED")
EMBEDDING_PROVIDERS: Final = ("local", "openai", "hashing")
# 与 `services/queue/jobs.py` 的 `STATUS_*` 一致；`superseded` 也在库里出现，
# 漏掉它会让换代中的作业在下一次抓取时凭空消失。
QUEUE_STATUSES: Final = (
    "pending",
    "processing",
    "succeeded",
    "failed",
    "cancelled",
    "superseded",
)
SCRAPE_COLLECTORS: Final = ("queue",)

BUILD_INFO: Final = Gauge(
    "pyknora_build_info",
    "构建信息，恒为 1；用于把版本对到时间轴上",
    {"env": None, "version": None},
)
RETRIEVAL_REQUESTS: Final = Counter(
    "pyknora_retrieval_requests_total",
    "检索请求次数，按策略与结果分",
    {"strategy": RETRIEVAL_STRATEGIES, "outcome": RETRIEVAL_OUTCOMES},
)
RETRIEVAL_BRANCH_DEGRADED: Final = Counter(
    "pyknora_retrieval_branch_degraded_total",
    "单路检索失败降级的次数，按分支与错误桶分",
    {"branch": RETRIEVAL_BRANCHES, "code": DEGRADED_CODE_BUCKETS},
)
GATE_VERDICT: Final = Counter(
    "pyknora_gate_verdict_total",
    "证据门控判定次数；SKIPPED 表示这次请求没进门控（未配模型等）",
    {"verdict": VERDICTS},
)
GATE_REFUSALS: Final = Counter(
    "pyknora_gate_refusals_total",
    "门控拒答次数；这些请求没有产生任何模型 token",
    {"reason": REFUSAL_REASONS},
)
CHAT_RUNS: Final = Counter(
    "pyknora_chat_runs_total",
    "问答轮次终态",
    {"status": CHAT_STATUSES},
)
EMBEDDING_DIMENSION_ERRORS: Final = Counter(
    "pyknora_embedding_dimension_errors_total",
    "向量维度与库内不一致的次数（绝不截断，直接报错）",
    {"provider": EMBEDDING_PROVIDERS},
)
QUEUE_JOBS: Final = Gauge(
    "pyknora_queue_jobs",
    "摄取任务按状态堆积量；抓取时查库得到",
    {"status": QUEUE_STATUSES},
)
SCRAPE_ERRORS: Final = Counter(
    "pyknora_metrics_scrape_errors_total",
    "抓取时收集阶段失败的次数；失败只丢该收集器，不影响其它序列",
    {"collector": SCRAPE_COLLECTORS},
)
INTERNAL_ERRORS: Final = Counter(
    "pyknora_metrics_errors_total",
    "埋点自身的失败次数；不记这个，埋点坏了就是静默失效",
    {"op": _METRICS_OPS},
)


def degraded_code_bucket(code: str) -> str:
    """把降级码折进固定桶。

    精确码（``PARSE_TIMEOUT`` / ``OperationalError`` / …）继续进日志，这里只留
    可枚举的粗分类，避免标签基数随异常类型增长。
    """
    lowered = code.lower().replace("_", "")
    if "timeout" in lowered or "timedout" in lowered:
        return "timeout"
    if "embedding" in lowered and "dimension" in lowered:
        return "embedding_dimension"
    if any(word in lowered for word in ("connection", "operational", "unavailable", "oserror")):
        return "connection"
    if code.isupper() or "error" in lowered:
        return "app_error"
    return "other"


def configure_metrics(*, env: str, version: str) -> None:
    """写 ``build_info``。由应用启动时调用一次；失败不影响启动。"""
    try:
        BUILD_INFO.set(1.0, env=env or "unknown", version=version or "unknown")
    except Exception:  # noqa: BLE001 - 指标不能拖垮启动
        _record_internal_error("configure")


def safe_inc(counter: Counter, /, **labels: str) -> None:
    """埋点专用的计数：任何异常都吞掉并计入 ``pyknora_metrics_errors_total``。

    调用点必须在请求主路径上（检索、门控、聊天流），那里一次未捕获的异常会
    把用户可见的回答打断——指标不值得用这个代价换。标签拼错属于编码错误，
    由单测直接调 ``Counter.inc`` 覆盖。
    """
    try:
        counter.inc(**labels)
    except Exception:  # noqa: BLE001 - 见 docstring
        _record_internal_error("inc")


def safe_set(gauge: Gauge, value: float, /, **labels: str) -> None:
    """``Gauge`` 版本的 ``safe_inc``。"""
    try:
        gauge.set(value, **labels)
    except Exception:  # noqa: BLE001 - 见 safe_inc
        _record_internal_error("inc")


def _record_internal_error(op: str) -> None:
    """内部错误计数：直接写注册表，不再走校验，避免递归。"""
    try:
        with _LOCK:
            series = _SERIES[INTERNAL_ERRORS.name]
            key = (("op", op),)
            series[key] = series.get(key, 0.0) + 1.0
    except Exception:  # noqa: BLE001 - 最后一道，绝不再抛
        return


def _format_value(value: float) -> str:
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(key: tuple[tuple[str, str], ...]) -> str:
    if not key:
        return ""
    inner = ",".join(f'{name}="{_escape(value)}"' for name, value in key)
    return "{" + inner + "}"


def render() -> str:
    """按 Prometheus text format v0.0.4 渲染全部序列；无序列时返回空串。"""
    try:
        with _LOCK:
            declared = dict(_DECLARED)
            snapshot = {name: dict(series) for name, series in _SERIES.items()}
    except Exception:  # noqa: BLE001 - 渲染失败也只丢这一页
        _record_internal_error("render")
        return ""

    lines: list[str] = []
    for name in sorted(snapshot):
        series = snapshot[name]
        if not series:
            continue
        help_text, kind, _ = declared[name]
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")
        for key in sorted(series):
            lines.append(f"{name}{_format_labels(key)} {_format_value(series[key])}")
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def reset_for_tests() -> None:
    """清空所有序列值，保留声明。只给测试用。"""
    with _LOCK:
        for series in _SERIES.values():
            series.clear()
