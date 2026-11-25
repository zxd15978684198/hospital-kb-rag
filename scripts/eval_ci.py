#!/usr/bin/env python
"""scripts/eval_ci.py · 把「上传语料 → 建评测集 → 跑一轮 → 过门禁」串成一条可重复的流水线。

为什么要有这个脚本
------------------
`eval/` 下每个工具都只做一件事，且都假设"语料已经上传好、评测集已经建好"。这个假设在
本机靠 `_tmp_ref` 里的临时脚本凑合过，但在 CI 里没人会手动补那三步——于是"CI 有评测
门禁"这件事就永远停在文档上。这里把缺的链条补上：**同一个入口、同一套参数、可以连跑**。

为什么语料要进仓库
------------------
`eval/corpus.py` 的 manifest 记着 5 份语料的 sha256，其中 `员工手册v1/v2.docx` 与
`基本财务管理制度.pdf` 的原件在仓库外（桌面 / `.tmp_ref/`）。CI 的 checkout 里没有
这两处，`--prepare` 会直接失败，评测就变成"只有本机跑得动"。所以 `eval/fixtures/corpus/`
里放一份字节完全相同的拷贝（728 KB，可入库），本脚本从这里落位并用 manifest 校验指纹：
语料一旦被换过，sha256 立刻对不上，而不是安静地跑出一个不可比的分数。

为什么 A/B 两个用户都自己注册
-----------------------------
deny 样本要证明的是「越权被拦」而不是「库不存在」：B 区的库必须真实存在、真实有内容。
所以脚本注册 A（跑评测）与 B（被越权的另一租户）两个账号，各建一个库。已存在就登录，
脚本可以反复跑。

用法：
    # 本机（API 已在 8010，真模型或假模型都行）
    uv run --frozen python scripts/eval_ci.py --out eval/results/ci-local

    # 带门禁比对（拿本轮和已入库的基线比）
    uv run --frozen python scripts/eval_ci.py \
        --out eval/results/ci-$GITHUB_SHA \
        --gate-baseline eval/baselines/stub-day03

    # 只重跑而不重传语料（改评测集/改参数时）
    uv run --frozen python scripts/eval_ci.py --skip-ingest --out eval/results/repeat
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "eval" / "fixtures" / "corpus"
CORPUS_DIR = ROOT / ".tmp_ref" / "eval_corpus"
MANIFEST = ROOT / "eval" / "corpus_manifest.json"
SETUP = ROOT / ".tmp_ref" / "eval_setup.json"

USER_A = "eval-a"
USER_B = "eval-b"
PASSWORD = "eval-pass-2026"

# A 区（被测租户）与 B 区（被越权的另一租户）的库名。名字固定，便于人工在库里认出来。
KB_A_NAME = "CI 评测库"
KB_B_NAME = "CI 越权库"


def log(message: str) -> None:
    print(f"[eval-ci] {message}", flush=True)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_corpus() -> list[Path]:
    """把语料落位到 `.tmp_ref/eval_corpus/`，并用 manifest 的 sha256 校验。

    两种来源，按仓库里实际有的东西挑：
    - `eval/fixtures/corpus/` 存在 → 从那里复制（语料是仓库外拿不到的原件时用它）；
    - 不存在 → 调 `eval/corpus.py --prepare` 现场生成（语料能由脚本确定性重建时用它，
      这样仓库里不必存二进制文档）。
    两条路都必须在最后过 sha256 校验：语料换过而 manifest 没换就该立刻红，
    而不是安静跑出一个不可比的分数。
    """
    if FIXTURES.is_dir():
        CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        generated = False
    else:
        log("未发现 eval/fixtures/corpus/，改用 eval/corpus.py 生成语料")
        subprocess.run(
            [sys.executable, str(ROOT / "eval" / "corpus.py"), "--prepare"],
            cwd=ROOT,
            check=True,
        )
        generated = True

    expected: dict[str, str] = {}
    if MANIFEST.is_file():
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        expected = {f["name"]: f["sha256"] for f in manifest.get("files", [])}

    placed: list[Path] = []
    source_root = CORPUS_DIR if generated else FIXTURES
    for source in sorted(source_root.iterdir()):
        if not source.is_file():
            continue
        target = CORPUS_DIR / source.name
        if not generated:
            shutil.copyfile(source, target)
        placed.append(target)
        want = expected.get(source.name)
        if want and sha256_of(target) != want:
            raise SystemExit(
                f"语料指纹不匹配：{source.name}\n"
                f"  manifest={want}\n  实际    ={sha256_of(target)}\n"
                "  换过语料就必须同步更新 eval/corpus_manifest.json，否则本轮指标与基线不可比。"
            )
    if len(placed) != 5:
        raise SystemExit(f"语料应有 5 份，实际落位 {len(placed)} 份：{[p.name for p in placed]}")
    log(f"语料落位 {len(placed)} 份 → {CORPUS_DIR}")
    return placed


def _client(base_url: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, timeout=120.0)


def login_or_register(client: httpx.Client, username: str) -> str:
    """注册或登录，返回 Bearer 令牌。

    注册会下发会话 Cookie；带 Cookie 再发 POST 会被 `deps.require_csrf` 拦（它只在
    **没有** Cookie 时豁免）。命令行一律走纯 Bearer，所以每次都清 Cookie。
    """
    payload = {
        "username": username,
        "email": f"{username}@example.invalid",
        "password": PASSWORD,
    }
    response = client.post("/auth/register", json=payload)
    if response.status_code == 201:
        token = response.json()["access_token"]
        log(f"注册 {username}")
    elif response.status_code == 409:
        response = client.post("/auth/login", json={"username": username, "password": PASSWORD})
        response.raise_for_status()
        token = response.json()["access_token"]
        log(f"登录 {username}")
    else:
        raise SystemExit(f"{username} 注册失败 {response.status_code}: {response.text[:300]}")
    client.cookies.clear()
    return token


def pick_kb(client: httpx.Client, token: str, name: str) -> tuple[str, str]:
    """取（或建）一个库，返回 (workspace_id, kb_id)。"""
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers)
    me.raise_for_status()
    workspace_id = me.json()["workspace_id"]

    body = client.get("/knowledge-bases", headers=headers)
    body.raise_for_status()
    payload = body.json()
    items = payload.get("items") if isinstance(payload, dict) else payload
    for item in items or []:
        if item.get("name") == name:
            return workspace_id, item["kb_id"]

    # KB 的 embedding 配方必须与**当前部署真正在用的后端**一致，否则上传口
    # （`documents.py` 的 `check_profile_compat`）会以 422 拒收。这里从 `/health`
    # 读部署自报的身份，而不是写死某个模型名——CI 跑 hashing 后端、本机跑 local
    # 后端时，同一份脚本都能用；写死的话换后端就是一条"永远误报"的假红。
    # `/health` 挂在根路径，不在 `/api/v1` 下：用 base_url 拼绝对地址，别用相对路径
    health = client.get(client.base_url.join("/health"))
    health.raise_for_status()
    model_key = health.json().get("embedder") or "BAAI/bge-small-zh-v1.5"
    dim = 512
    created = client.post(
        "/knowledge-bases",
        headers=headers,
        json={
            "name": name,
            "embedding_model_key": model_key,
            "embedding_dim": dim,
            "chunk_size": 512,
            "chunk_overlap": 64,
            "normalize": True,
        },
    )
    created.raise_for_status()
    return workspace_id, created.json()["kb_id"]


def upload(client: httpx.Client, token: str, kb_id: str, path: Path) -> dict:
    """上传一篇文档，返回接口原始响应（含 job_id / document_id）。"""
    response = client.post(
        f"/knowledge-bases/{kb_id}/documents",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": (path.name, path.read_bytes(), "application/octet-stream")},
    )
    if response.status_code not in (200, 201, 202):
        raise SystemExit(f"上传 {path.name} -> {response.status_code} {response.text[:300]}")
    return response.json()


def wait_job(client: httpx.Client, token: str, job_id: str, timeout: float = 600.0) -> str:
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.time() + timeout
    last = "?"
    while time.time() < deadline:
        response = client.get(f"/jobs/{job_id}", headers=headers)
        response.raise_for_status()
        body = response.json()
        last = str(body.get("status"))
        if body.get("terminal"):
            if last.upper() not in ("SUCCEEDED", "COMPLETED"):
                raise SystemExit(f"入库任务 {job_id} 终态={last} err={body.get('error_code')}")
            return last
        time.sleep(2.0)
    raise SystemExit(f"入库任务 {job_id} 超时，最后状态={last}")


def snapshot(client: httpx.Client, token: str, uploaded: dict[str, str]) -> list[dict]:
    """按「文件名 → document_id」把每篇文档的活动切片指针读回来。

    文件名用调用方上传时记下的那张表，**不**从接口反查：`documents` 表没有
    `original_filename`，反查只能靠 object_key 末段猜，重传/改名时会静默错位，
    而 gold 的定位恰恰按文件名对账。
    """
    headers = {"Authorization": f"Bearer {token}"}
    docs: list[dict] = []
    for name, document_id in uploaded.items():
        detail = client.get(f"/documents/{document_id}", headers=headers)
        detail.raise_for_status()
        body = detail.json()
        # 活动版本/构建只在详情里；version 类样本断言的就是「命中当前活动 build」，
        # 所以这两个字段必须现读，不能从旧快照里拿。
        #
        # 切片数**不在** DocumentDetail 里（它只有指针字段 + searchable），
        # 别去 `.get("chunk_count")`——那个键不存在，会安静返回 0，
        # 于是"5 篇 / 0 切片"看着像语料没入库，其实只是读错了地方。
        chunk_total = 0
        if body.get("active_build_id"):
            page = client.get(
                f"/documents/{document_id}/chunks",
                headers=headers,
                params={"limit": 1},
            )
            page.raise_for_status()
            chunk_total = int(page.json().get("total") or 0)
        docs.append(
            {
                "corpus": name,
                "document_id": document_id,
                "active_document_version_id": body.get("active_document_version_id"),
                "active_build_id": body.get("active_build_id"),
                "active_index_version": body.get("active_index_version") or 0,
                "chunks": chunk_total,
            }
        )
    return docs


def run(cmd: list[str], *, cwd: Path = ROOT) -> int:
    log("$ " + " ".join(str(part) for part in cmd))
    return subprocess.call([str(part) for part in cmd], cwd=cwd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评测流水线：上传语料 → 建集 → 跑一轮 → 过门禁")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("PYKNORA_EVAL_BASE_URL", "http://127.0.0.1:8010/api/v1"),
        help="默认取 PYKNORA_EVAL_BASE_URL，缺省 8010（docker compose 的 api 容器）",
    )
    parser.add_argument("--out", type=Path, help="本轮产物目录（缺省 eval/results/ci-<时间戳>）")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--skip-ingest", action="store_true", help="复用 .tmp_ref/eval_setup.json")
    parser.add_argument(
        "--gate-baseline",
        type=Path,
        help="已入库的基线产物目录；给了就在跑完后执行 eval/gate.py，并以其退出码为准",
    )
    parser.add_argument("--python", default=sys.executable, help="跑子命令用的解释器")
    args = parser.parse_args(argv)

    out_dir = args.out or (ROOT / "eval" / "results" / f"ci-{int(time.time())}")
    out_dir = out_dir if out_dir.is_absolute() else (ROOT / out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_ingest:
        corpus = prepare_corpus()
        with _client(args.base_url) as client:
            token_a = login_or_register(client, USER_A)
            workspace_a, kb_a = pick_kb(client, token_a, KB_A_NAME)
            log(f"A 区 workspace={workspace_a} kb={kb_a}")
            uploaded: dict[str, str] = {}
            for path in corpus:
                body = upload(client, token_a, kb_a, path)
                # 必须等终态：`build_eval_set` 要读活动 build 的切片指针，
                # 索引还没建完时读回来是空的，gold 会静默退化成 0 命中。
                # 重复上传同一份内容时服务端按内容哈希去重，不建新任务（job_id 为空）。
                # 这不是错误：语义是"这篇已经在库里了"，等价于上一次已经等过终态。
                job_id = body.get("job_id")
                if job_id:
                    status = wait_job(client, token_a, job_id)
                    log(f"上传 {path.name} job={job_id} → {status}")
                else:
                    log(f"上传 {path.name} → 已存在（内容哈希去重），跳过等待")
                uploaded[path.name] = body["document_id"]
            docs = snapshot(client, token_a, uploaded)

            token_b = login_or_register(client, USER_B)
            workspace_b, kb_b = pick_kb(client, token_b, KB_B_NAME)
            log(f"B 区 workspace={workspace_b} kb={kb_b}")
            # B 区必须有内容，否则 404 的可能是"库是空的"而不是"你没权限"。
            forbidden_doc = next((p for p in corpus if p.suffix == ".md"), corpus[0])
            upload(client, token_b, kb_b, forbidden_doc)

        total = sum(int(doc["chunks"]) for doc in docs)
        SETUP.write_text(
            json.dumps(
                {
                    "workspace_id": workspace_a,
                    "kb_id": kb_a,
                    "documents": docs,
                    "forbidden": {"workspace_id": workspace_b, "kb_id": kb_b},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        log(f"写入 {SETUP}（{len(docs)} 篇 / {total} 切片）")

    code = run([args.python, "eval/make_configs.py"])
    if code != 0:
        return code
    code = run(
        [
            args.python,
            "eval/build_eval_set.py",
            "--build",
            # 必须显式传：`build_eval_set.py` 默认打 8010（docker compose 的 api），
            # 在自带栈里那是**另一个数据库**，登录不上会给出莫名其妙的 403。
            "--base-url",
            str(args.base_url).split("/api/v1")[0],
            "--username",
            USER_A,
            "--password",
            PASSWORD,
        ]
    )
    if code != 0:
        return code
    code = run(
        [
            args.python,
            "eval/run_eval.py",
            "--config",
            "eval/baseline_config.yaml",
            "--repeats",
            str(args.repeats),
            "--out",
            str(out_dir),
            "--base-url",
            args.base_url.replace("/api/v1", ""),
            "--username",
            USER_A,
            "--password",
            PASSWORD,
        ]
    )
    if code != 0:
        return code

    if args.gate_baseline:
        baseline = args.gate_baseline
        baseline = baseline if baseline.is_absolute() else (ROOT / baseline)
        log(f"门禁比对：baseline={baseline} candidate={out_dir}")
        return run(
            [
                args.python,
                "eval/gate.py",
                "--baseline",
                str(baseline),
                "--candidate",
                str(out_dir),
            ]
        )
    log(f"产物：{out_dir}（未给 --gate-baseline，本轮不判定）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
