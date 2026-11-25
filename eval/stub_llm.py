#!/usr/bin/env python
"""eval/stub_llm.py · 确定性的"假模型"，只给 CI 的回归门禁用。

为什么不接真模型
----------------
真模型在 CI 里有两个致命问题：**要密钥**（fork 的 PR 拿不到 secret）、**不确定**
（同一份代码两次运行，答案措辞就变了，指标跟着抖）。回归门禁要比的是"这次改动有没有
把检索/门控/引用这条链路弄坏"，不是"模型今天心情如何"。所以 CI 用这个桩：同样输入
必然产出同一串字，任何指标下降都只能来自代码改动。

桩到底"会"什么
--------------
只做三件事，全部按 system prompt 里的资料块机械决定：

1. 上下文里有 ``<<<资料 N 开始>>>`` 块 → 逐块摘一句原文，每句后面挂 ``[资料 N]``。
   挂**全部**块而不是第一块：引用校验（``invalid_cited_indices``）要能真的被判到，
   只挂第一块会让"引用越界"这条分支在 CI 里永远不被触发。
2. 上下文里没有任何资料块 → 回「资料不足，无法回答。」
3. 任何情况下都不产出资料以外的数字——它压根不生成数字。

因此：``citation_accuracy`` 在桩下的含义是「**召回里有几成被正确标了号**」，
``abstain_accuracy`` 只反映门控（没有证据时服务端就走不到这里）。这两个口径必须
写进基线说明，否则下一个人会以为这是模型质量分。

用法：
    uv run python eval/stub_llm.py --port 8899          # 前台跑
    uv run python eval/stub_llm.py --port 8899 --quiet  # 只报启动/退出
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 与 `services/rag/answer.py` 的分隔符保持逐字一致——这里是"假模型"，不是第二套协议。
_BLOCK = re.compile(
    r"<<<资料\s*(\d+)\s*开始>>>\s*(.*?)\s*<<<资料\s*\1\s*结束>>>",
    re.DOTALL,
)

# 摘要长度：只取原文一句，避免把整段资料复述一遍（复述会让"答案"看起来像抄袭，
# 也会把 forbidden 字面（如金额）意外带出来）。
_SNIPPET_CHARS = 60

ABSTAIN_TEXT = "资料不足，无法回答。"


def _blocks(system_content: str) -> list[tuple[int, str]]:
    """从 system prompt 里抽出 (序号, 正文) 列表。

    正文去掉首行的 `[资料 N] document_id=…; page=…`：那是给出处用的把手，
    不是资料内容，混进摘句会变成"引用里套引用"。
    """
    out: list[tuple[int, str]] = []
    for raw_index, body in _BLOCK.findall(system_content):
        lines = [ln for ln in body.splitlines() if ln.strip()]
        if lines and lines[0].lstrip().startswith("[资料"):
            lines = lines[1:]
        text = " ".join(" ".join(lines).split())
        if text:
            out.append((int(raw_index), text[:_SNIPPET_CHARS]))
    return out


def compose_answer(system_content: str) -> str:
    """桩的"推理"：有资料就逐块摘一句并挂号，没资料就拒答。"""
    blocks = _blocks(system_content)
    if not blocks:
        return ABSTAIN_TEXT
    # 只引用前 3 块：citation 指标的真实语义是"金标切片被标到号"，全挂等于把
    # precision 变成常数 1，反而看不出检索退化。
    picked = blocks[:3]
    parts = [f"{snippet} [资料 {index}]" for index, snippet in picked]
    return "；".join(parts) + "。"


def _chunk(delta: dict[str, object], finish: str | None = None) -> bytes:
    payload = {
        "id": "stub-chatcmpl",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "stub-deterministic",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    quiet = True

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        if not self.quiet:
            sys.stderr.write(f"{self.address_string()} - {fmt % args}\n")

    def _fail(self, code: int, message: str) -> None:
        body = json.dumps({"error": {"message": message}}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._fail(404, f"stub 只实现 /chat/completions，收到 {self.path}")
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            self._fail(400, f"请求体不是 JSON：{exc}")
            return

        system = ""
        for message in payload.get("messages") or []:
            if isinstance(message, dict) and message.get("role") == "system":
                system += str(message.get("content") or "")
        answer = compose_answer(system)

        # 一次性吐完再 [DONE]：桩不需要模拟逐字延迟，首 token 时间在这里没有意义
        # （指标里 ttft 那一栏在桩基线下同样是常数）。
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(_chunk({"role": "assistant", "content": ""}))
            self.wfile.write(_chunk({"content": answer}))
            self.wfile.write(_chunk({}, finish="stop"))
            usage = {
                "id": "stub-chatcmpl",
                "object": "chat.completion.chunk",
                "choices": [],
                "usage": {
                    "prompt_tokens": max(1, len(system) // 4),
                    "completion_tokens": max(1, len(answer) // 4),
                    "total_tokens": max(2, (len(system) + len(answer)) // 4),
                },
            }
            self.wfile.write(f"data: {json.dumps(usage, ensure_ascii=False)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            # 客户端提前断开（门控拒答、评测超时）不是错误，别让它污染 CI 日志。
            pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in ("/health", "/healthz", ""):
            body = b'{"status":"ok","stub":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._fail(404, f"stub 没有 {self.path}")


def serve(port: int, quiet: bool) -> ThreadingHTTPServer:
    Handler.quiet = quiet
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="确定性的假模型，供 CI 评测门禁使用")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    Handler.quiet = args.quiet
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[stub] 假模型就绪 http://127.0.0.1:{args.port}/v1/chat/completions", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[stub] 收到 Ctrl-C，退出", flush=True)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
