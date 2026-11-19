"""evaluation phase 评测语料落位与指纹校验（T0.2）。

三件事必须在动笔前说清楚，否则代码里的"生成 + 算哈希"看着像多余动作：

1) **为什么语料由本文件生成，而不是复制外部原件**：五份文档全部是虚构公司「澜川实业有限
   公司」的内部制度，正文就是下面那几个字符串常量。评测要的是"同一批字节、同一套 gold
   定位"，而语料一旦依赖仓库外部的文件（某一台机器上的原件、临时目录里的拷贝），路径漂移或
   被脚本重写时 30 条样本的 `gold_chunks` 会集体失效却没人发现。生成式语料没有这个缺口：
   代码在，语料就在，`--prepare` 随时能重建。

2) **为什么要 sha256**：语料是基线实验的自变量。同一份 `eval_set_v0.1.jsonl` 配两份不同的
   `员工手册v2.md`，跑出来的 Recall@5 不可比，但报告里两个数字长得一模一样。sha256 是
   "语料指纹"——把自变量钉死，换机器/换人重放时能立刻回答"还是不是那一份"。
   （`chunk_ordinal` + `content_sha1_8` 定位的是切片，sha256 定位的是切片之前的原始字节，
   两层指纹各管一段。）

3) **为什么用 `.md` 而不是 PDF/DOCX**：上传白名单是 16 个后缀（`services/ingest/parser.py`
   的 `SUPPORTED_SUFFIXES`，ADR 0008 D4），`.md` 在其中。DOCX 的 zip 头带时间戳，同样的内容
   每次生成 hash 都不同，第 2 条就废了；PDF 还多一层排版与字体，更不可控。`.md` 的字节完全
   由字符串常量决定，任何机器上生成同一串 sha256。PDF/DOCX/XLSX/RTF 等解析路径由
   `tests/unit/test_parse_*.py` 覆盖，评测语料不必重复承担这件事。

用法（默认行为就是 `--prepare`）：

    uv run python eval/corpus.py                    # 幂等落位 5 份文件 + 写清单
    uv run python eval/corpus.py --verify-only      # 按清单重算，不一致 exit 1
    uv run python eval/corpus.py --list             # 只打印清单

语料本身不入库（`.gitignore` 的 `.tmp_ref/` 已覆盖），入库的只有 `eval/corpus_manifest.json`。
换机器跑一次 `--prepare` 就能重新生成；`--verify-only` 失败就报错，绝不静默降级。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / ".tmp_ref" / "eval_corpus"
MANIFEST_PATH = REPO_ROOT / "eval" / "corpus_manifest.json"

# 语料全部由本文件生成，不依赖任何本机路径：clone 下来跑 `--prepare` 就能落盘，
# 换机器、换人、换场次都得到同一份字节（幂等约定见 _write_if_changed）。
# 公司、人名、金额、条款号一律虚构，与任何真实企业无关——既能保证评测可复现，
# 也不会把某家公司的内部制度带进公开仓库。
SOURCE_NOTE = (
    "5 份语料全部由本文件生成，指向虚构的「澜川实业有限公司」，不复制任何外部文档。"
    "target=main 进 workspace A 的评测 KB，target=forbidden 只上传到 workspace B，"
    "供 deny 样本证明「越权被拦」而非「库不存在」。语料不入库（.gitignore 的 .tmp_ref/），"
    "换机器重跑 --prepare 即可重新生成同名同内容的文件。"
)

# 越权语料：金额刻意与 A 区语料不重叠（A 区只有 800 万注册资本、2200/2600/3400 元工资、
# 80 元扣款、3000 元固定资产门槛，均无「万元」级数字），避免 gold 回填时把 B 区数字
# 错认成 A 的证据。
_BUDGET_MD = """# 财务内部预算（2026 年度）

> 本文件仅限财务负责人及以上查阅。未经财务负责人书面授权，不得转发、截图或对外引用。

## 一、总体口径

2026 年度预算按「部门申报 → 财务复核 → 总经理办公会审定」三步定稿。年中只保留一次调整窗口，
为每年 8 月 10 日至 8 月 20 日；窗口外不再受理部门提交的预算变更申请。

## 二、分部门预算（节选）

| 部门 | 2026 年度预算总额 | 其中差旅预算 |
| --- | --- | --- |
| 研发中心 | 1200 万元 | 60 万元 |
| 市场部 | 260 万元 | 45 万元 |

研发中心 2026 年度预算总额 1200 万元，含设备采购与云资源两部分，其中云资源按季度预付。

## 三、专项预算

机房扩容专项预算 78 万元，须在 2026 年 9 月 30 日前执行完毕，逾期未执行部分收回总盘子。

## 四、审批口径

单笔超 50 万元的预算调整，除部门负责人签批外，还须财务负责人审批后方可执行；跨部门调剂
由财务负责人汇总后统一报总经理办公会。
"""

# 干扰文档：只写年会与团建本身。刻意不出现"报销/差旅/年假/预算"等与被测问题同主题的词，
# 否则它就不是"无关内容"，而是给检索送分的近邻；同样不写任何金额，避免与 fact 类 gold 撞数字。
_NOTICE_MD = """# 关于 2027 年迎新年会与春季团建的通知

各位同事：

为感谢大家一年来的付出，公司定于 2027 年 1 月 16 日（周六）举办迎新年会，现将有关事项通知如下。

## 一、时间与地点

- 年会时间：2027 年 1 月 16 日 17:30 入场，18:00 正式开始。
- 年会地点：园区 3 号楼 1 楼多功能厅。
- 交通：园区东门进来步行约 8 分钟；地下车库当晚免费。

## 二、报名

- 报名截止时间：2026 年 12 月 31 日 17:00，逾期不再补录。
- 报名方式：各部门行政对接人汇总名单后发至行政部邮箱，名单需注明是否携带家属。
- 座位按报名先后编排，同一部门尽量相邻。

## 三、节目征集

- 征集截止时间：2026 年 12 月 20 日 18:00。
- 形式不限：歌舞、小品、乐器、魔术、脱口秀均可，单个节目时长不超过 6 分钟。
- 报名时请附节目名称、参演人数与所需道具，行政部统一安排彩排。

## 四、春季团建

春季团建安排在 2027 年 4 月中旬，以部门为单位自行组织，具体日期由各部门与行政部协商后确定。
团建当天的用车与场地由行政部统一预订，部门只需在活动前一周提交人数。

请各部门于本周内确定对接人并回复本邮件。

行政部
2026 年 12 月 1 日
"""

# ── 虚构公司「澜川实业有限公司」的制度语料 ──────────────────────────────────
# 三份文档的条款号、金额、期限互相咬合：fact 类答案在单一段内，exact_term 类的固定说法
# 一字不改，version 类靠 v1/v2 同名重传制造「旧口径已作废」。全部虚构。
_POLICY_MD = """# 澜川实业有限公司 内部财务管理制度

## 第一章 总则

第一条 为规范公司财务行为，加强资金与预算管理，依据国家有关法律法规并结合公司实际，制定本制度。

第二条 公司实行“集中决策、分级授权”的财务管理体制。重大财务事项由总经理办公会集中决策，
日常资金支付由各分公司、各部门在授权额度内分级执行。

第三条 财务部是财务管理的归口部门，负责预算编制、资金调度、会计核算与内部稽核。

## 第二章 预算管理

第十条 年度全面预算按照自下而上、分级编制、逐级平衡的程序进行：各部门提出预算建议，
分公司汇总初审，财务部复核平衡，报总经理办公会审定后下达执行。

第十一条 年度预算在年中只保留一次调整窗口，为每年 8 月 10 日至 8 月 20 日；
窗口期外不再受理部门提交的预算变更申请。

## 第三章 会计交接

第二十条 会计人员调动工作或离职，必须办清交接手续。移交清册应当填制一式四份，
交接双方各执一份，财务部与档案室各存档一份。

第二十一条 一般会计人员交接，由财务主管和稽核会计负责监交；财务主管、稽核会计交接，
由总经理和财务总监监交。

## 第四章 票据与资产

第三十条 财务部每季度至少对库存票据抽点一次，抽点结果形成书面记录并留存三年。

第三十一条 单项价值在 3000 元以上的设备、器具、办公家具计入固定资产，按类别建立台账。

## 第五章 对外融资

第四十条 各分公司原则上不得单独向银行和外单位融资，包括利用融资租赁工具开展销售业务；
确需筹资或利用融资租赁工具开展销售业务的，必须向财务部提出申请，经总经理办公会批准。

## 第六章 费用报销

第五十条 费用报销应当及时，严禁分拆；属于同一项目、同一时间或同一性质的费用，
应当按单笔费用报销。
"""

_HANDBOOK_V1_MD = """# 员工手册（2019 年版）

## 一、公司简介

澜川实业有限公司成立于 2009 年，注册资本八百万元人民币，主营工业自动化设备的研发、
制造与技术服务。

## 二、假期

员工连续工作满一年后，可以享受探亲假与年休假。

与配偶分居两地的员工，每两年可以享受一次为期四十天的探亲假；未婚员工与父母分居两地的，
每四年可以享受一次为期二十天的探亲假。

春节休假从每年腊月二十八到正月初七止。

## 三、考勤

员工迟到或早退三十分钟以上、两小时以下的，每次扣除薪金 80 元。

## 四、资产

单项价值在 2000 元以上的设备、器具、办公家具计入固定资产。
"""

_HANDBOOK_V2_MD = """# 员工手册（2026 年修订版）

## 一、公司简介

澜川实业有限公司成立于 2009 年，注册资本八百万元人民币，主营工业自动化设备的研发、
制造与技术服务。

## 二、薪酬

公司付薪日期为每月的 10 日，遇法定节假日提前至最近一个工作日发放。

应届毕业生试用期工资按月发放：大专学历 2200 元，本科学历 2600 元，硕士学历 3400 元。

## 三、假期

员工转正后可享受婚假、丧假、产假及护理假；连续工作满一年后，可以按规定享受探亲假与年休假。

与配偶分居两地的员工，每年可以享受一次为期 25 天的探亲假；未婚员工与父母均在外地居住的，
每四年可以享受一次为期 20 天的探亲假。

春节休假从每年腊月二十六到正月初八止。

## 四、考勤

员工迟到或早退 20 分钟以上、两小时以下的，每次扣除薪金 80 元。

## 五、资产与档案

单项价值在 3000 元以上的设备、器具、办公家具计入固定资产。

档案编号格式：LC-CW-年份-流水号，由综合管理部统一编排。

## 六、附则

本手册自发布之日起施行，2019 年版同时废止。
"""


@dataclass(frozen=True)
class FileSpec:
    """一份语料的落位方式。source 与 text 二选一：前者复制，后者现造。"""

    name: str
    kind: str  # 源文件后缀名（当前全是 md；pdf/docx 等由解析器单测覆盖）
    target: str  # main | forbidden
    description: str
    source: Path | None = None
    text: str | None = None


def _build_specs() -> tuple[FileSpec, ...]:
    """A 区 4 份 + B 区 1 份，全部由本文件生成，不依赖任何外部原件。"""
    return (
        FileSpec(
            name="内部财务管理制度.md",
            kind="md",
            target="main",
            description="制度主文档；条款号、固定说法与审批流程是 exact_term 类样本的出处",
            text=_POLICY_MD,
        ),
        FileSpec(
            name="员工手册v1.md",
            kind="md",
            target="main",
            description=(
                "手册旧版；随后上传 v2 作为同一逻辑文档的新版本，把 active_index_version 顶到 2"
            ),
            text=_HANDBOOK_V1_MD,
        ),
        FileSpec(
            name="员工手册v2.md",
            kind="md",
            target="main",
            description="手册现行版；重传后 v1 的切片应被 retire，只有 v2 现行",
            text=_HANDBOOK_V2_MD,
        ),
        FileSpec(
            name="团建与年会通知.md",
            kind="md",
            target="main",
            description="无关干扰文档；制造 no_evidence 与 top5 噪声，不得命中被测问题",
            text=_NOTICE_MD,
        ),
        FileSpec(
            name="财务内部预算.md",
            kind="md",
            target="forbidden",
            description="越权语料，仅上传到 workspace B；A 的令牌问它必须 404 且无片段",
            text=_BUDGET_MD,
        ),
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_if_changed(path: Path, data: bytes) -> bool:
    """内容相同就不动文件。

    不是为了省 IO，而是为了让 `--prepare` 真正幂等：Windows 上重写会让 mtime 变，
    文件监视类工具和"看修改时间判断有没有跑过"的习惯都会被假更新误导。返回是否写了。
    """
    if path.is_file() and path.read_bytes() == data:
        return False
    path.write_bytes(data)
    return True


def _materialize(spec: FileSpec) -> tuple[str, str]:
    """落位一份语料，返回 (动作, 失败原因)。动作只有 复制/生成/跳过/沿用 四种。"""
    dest = CORPUS_DIR / spec.name
    if spec.text is not None:
        # newline="" 等价于"不转换换行"：写死 \n，否则 Windows 会写成 CRLF，
        # 同一份代码在两台机器上算出两个 sha256，指纹就失效了。
        data = spec.text.encode("utf-8")
        return ("生成" if _write_if_changed(dest, data) else "跳过", "")

    assert spec.source is not None, "只有带 source 的规格才走复制分支；生成式语料必带 text"
    if spec.source.is_file():
        if dest.is_file() and dest.read_bytes() == spec.source.read_bytes():
            return ("跳过", "")
        shutil.copyfile(spec.source, dest)
        return ("复制", "")

    if dest.is_file():
        # 原件不在了但拷贝还在：评测的可复现锚是清单里的 sha256，不是原件路径，
        # 所以沿用已有拷贝是安全降级；但要说出来，不能让人以为刚复制过。
        return ("沿用", f"原件不存在，沿用已有拷贝：{spec.source}")

    return ("缺失", f"原件与目标都不存在：{spec.source}")


def _display_width(text: str) -> int:
    """中文 Windows 控制台把 CJK 按 2 列宽渲染，用 len() 对齐会整体右移。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _print_table(rows: list[tuple[str, str, str, str, str]]) -> None:
    """中文表头 + ASCII 竖线：制表符（│─┌）与 emoji 在 cp936 控制台会直接抛
    UnicodeEncodeError，而 `|` 和 `-` 在任何编码下都能落地。"""
    headers = ("文件名", "字节", "sha256 前 12 位", "kind", "target")
    table = [headers, *rows]
    widths = [max(_display_width(row[i]) for row in table) for i in range(len(headers))]
    print("  ".join(_pad(cell, widths[i]) for i, cell in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        print("  ".join(_pad(cell, widths[i]) for i, cell in enumerate(row)))


def _table_row(
    name: str, size: int | None, digest: str, kind: str, target: str
) -> tuple[str, str, str, str, str]:
    return (
        name,
        "-" if size is None else str(size),
        digest[:12] if digest else "-",
        kind,
        target,
    )


def _build_manifest(specs: tuple[FileSpec, ...]) -> dict:
    files = []
    for spec in specs:
        data = (CORPUS_DIR / spec.name).read_bytes()
        files.append(
            {
                "name": spec.name,
                # 相对仓库根的 posix 路径：清单要能被别人 checkout 后直接读，不能带 E:\\ 前缀。
                "path": (CORPUS_DIR / spec.name).relative_to(REPO_ROOT).as_posix(),
                "bytes": len(data),
                "sha256": _sha256(data),
                "kind": spec.kind,
                "target": spec.target,
                "description": spec.description,
            }
        )
    return {"generated_at": "", "source_note": SOURCE_NOTE, "files": files}


def _write_manifest(payload: dict) -> str:
    """内容没变就不重写 generated_at。

    否则每次 `--prepare` 都在 `git status` 里制造一行无意义的 diff，评审时要多花时间确认
    "清单变了"其实什么都没变。返回给人看的动作说明。
    """
    if MANIFEST_PATH.is_file():
        try:
            old = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old = None
        if isinstance(old, dict) and old.get("files") == payload["files"]:
            if old.get("source_note") == payload["source_note"]:
                return f"清单无变化，保留 generated_at={old.get('generated_at')}"
            payload["generated_at"] = old.get("generated_at") or ""
        else:
            payload["generated_at"] = ""
    if not payload["generated_at"]:
        payload["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    MANIFEST_PATH.write_text(text, encoding="utf-8", newline="\n")
    return "已重写清单"


def prepare() -> int:
    specs = _build_specs()
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)

    actions: dict[str, str] = {}
    warnings: list[str] = []
    for spec in specs:
        action, note = _materialize(spec)
        actions[spec.name] = action
        if note:
            warnings.append(f"{spec.name}：{note}")
        if action == "缺失":
            print(f"[失败] {spec.name}：{note}", file=sys.stderr)
            print("先放好原件（或用环境变量指定），再重跑 --prepare。", file=sys.stderr)
            return 1

    payload = _build_manifest(specs)
    rows = [
        _table_row(item["name"], item["bytes"], item["sha256"], item["kind"], item["target"])
        for item in payload["files"]
    ]
    print("语料目录：" + CORPUS_DIR.relative_to(REPO_ROOT).as_posix())
    _print_table(rows)
    print()
    for item in payload["files"]:
        print(f"  {actions[item['name']]}  {item['name']}")
    for line in warnings:
        print(f"  [注意] {line}")
    print("  " + _write_manifest(payload))
    print("  清单：" + MANIFEST_PATH.relative_to(REPO_ROOT).as_posix())
    return 0


_REQUIRED_KEYS = ("name", "path", "bytes", "sha256", "kind", "target", "description")


def verify() -> int:
    if not MANIFEST_PATH.is_file():
        print(f"[失败] 找不到清单 {MANIFEST_PATH}，先跑一次 --prepare。", file=sys.stderr)
        return 1
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[失败] 清单不是合法 JSON：{exc}", file=sys.stderr)
        return 1

    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not files:
        print("[失败] 清单缺少 files 数组或为空。", file=sys.stderr)
        return 1

    rows: list[tuple[str, str, str, str, str]] = []
    problems: list[str] = []
    for item in files:
        missing_keys = [key for key in _REQUIRED_KEYS if key not in item]
        if missing_keys:
            problems.append(f"{item.get('name', '<无名>')}：清单缺字段 {missing_keys}")
            continue
        path = REPO_ROOT / item["path"]
        if not path.is_file():
            rows.append(_table_row(item["name"], None, "", item["kind"], item["target"]))
            problems.append(f"{item['name']}：文件不存在（{item['path']}）")
            continue
        data = path.read_bytes()
        digest = _sha256(data)
        rows.append(_table_row(item["name"], len(data), digest, item["kind"], item["target"]))
        if digest != item["sha256"]:
            problems.append(
                f"{item['name']}：sha256 不一致（清单 {item['sha256'][:12]} / 实算 {digest[:12]}）"
            )
        elif len(data) != item["bytes"]:
            problems.append(
                f"{item['name']}：字节数不一致（清单 {item['bytes']} / 实算 {len(data)}）"
            )

    print("语料目录：" + CORPUS_DIR.relative_to(REPO_ROOT).as_posix())
    print("清单生成时间：" + str(manifest.get("generated_at")))
    print()
    _print_table(rows)

    listed = {item.get("name") for item in files if isinstance(item, dict)}
    if CORPUS_DIR.is_dir():
        extras = sorted(
            path.name for path in CORPUS_DIR.iterdir() if path.is_file() and path.name not in listed
        )
        if extras:
            print()
            print(f"[注意] 目录里有清单之外的文件（不判失败）：{extras}")

    expected = {spec.name for spec in _build_specs()}
    if expected != listed:
        print()
        print(
            "[注意] 清单与代码里的语料清单不是同一套："
            f"代码有而清单无 {sorted(expected - listed)}；"
            f"清单有而代码无 {sorted(listed - expected)}"
        )

    print()
    if problems:
        print(f"[失败] 语料与清单不一致：{len(problems)} 处", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        print("确认是不是有人动了语料；要接受新语料就重跑 --prepare 并同步 gold。", file=sys.stderr)
        return 1

    print(f"[通过] {len(files)}/{len(files)} 份语料与清单一致。")
    return 0


def list_manifest() -> int:
    if not MANIFEST_PATH.is_file():
        print(f"[失败] 找不到清单 {MANIFEST_PATH}，先跑一次 --prepare。", file=sys.stderr)
        return 1
    print(MANIFEST_PATH.read_text(encoding="utf-8"), end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="evaluation phase 评测语料落位与指纹校验（不带参数时等价于 --prepare）"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prepare", action="store_true", help="幂等落位 5 份语料并写清单")
    mode.add_argument("--verify-only", action="store_true", help="按清单重算 sha256，不一致 exit 1")
    mode.add_argument("--list", dest="list_only", action="store_true", help="只打印清单")
    args = parser.parse_args(argv)

    if args.verify_only:
        return verify()
    if args.list_only:
        return list_manifest()
    return prepare()


if __name__ == "__main__":
    raise SystemExit(main())
