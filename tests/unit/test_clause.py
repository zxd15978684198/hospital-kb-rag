"""条款号抽取（`services/ingest/clause.py`）与它在 `parse()` 出口的接线。

为什么这些用例看着"太小心"：条款号一旦抽错，前端会把引用指到别的条款上，
而错的引用比没有引用更糟——用户点过去看到一段相关文字，会默认那就是依据。
所以下面一半的用例是**反例**：这些情况下必须返回 `None`。
"""

from __future__ import annotations

import pytest

from pyknora.schemas.locator import PageLocator, ParaLocator, TableLocator
from pyknora.services.ingest.clause import extract_clause
from pyknora.services.ingest.parser import parse


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 中文「第X条」
        ("第十二条 报销标准如下", "第十二条"),
        ("第三条　适用范围", "第三条"),
        # 中文数字排版里常夹空格
        ("第 12 条 报销上限", "第12条"),
        # 「章 + 条」取条：引用要落到条款，章太粗
        ("第三章 第十二条 报销上限 500 元", "第十二条"),
        # 数字层级编号
        ("3.2 住宿标准", "3.2"),
        ("5.1.4、验收方式", "5.1.4"),
        # markdown 标题的井号要先剥掉
        ("## 3.2.1 桑拿与娱乐", "3.2.1"),
        ("### 第八条 附则", "第八条"),
        # 段前空行、装饰符不该影响判定
        ("\n\n> 3.3 交通费", "3.3"),
    ],
)
def test_extract_clause_positive(text: str, expected: str) -> None:
    assert extract_clause(text) == expected


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("报销标准如下", "没有编号：没有可指认的位置"),
        ("", "空文本"),
        ("见第十二条规定的流程", "『见』开头＝指向别处，不是本块的位置"),
        ("根据 3.2 执行", "行中出现的引用，不是本块条款号"),
        ("（三）其他事项", "列表编号不是条款号"),
        ("(3) 住宿", "同上，阿拉伯括号编号"),
        ("1. 报销标准", "有序列表序号，不构成条款"),
        ("3.2.1.4 太深的层级", "四级以上宁可判成抽不出来"),
        ("第三段内容", "『第三段』不是『第三条』"),
    ],
)
def test_extract_clause_negative(text: str, why: str) -> None:
    assert extract_clause(text) is None, why


def test_locator_carries_clause_and_round_trips() -> None:
    """`clause` 要能落进 jsonb 再读回来，否则前端拿不到。"""
    loc = PageLocator(page=12, clause="第十二条")
    dumped = loc.model_dump(mode="json")
    assert dumped["clause"] == "第十二条"
    assert PageLocator.model_validate(dumped) == loc
    # 不传就保持 None：不是"没查"，是"没有"
    assert PageLocator(page=1).clause is None


def test_parse_fills_clause_from_markdown_heading() -> None:
    """`parse()` 出口统一补 clause，六个解析器不用各写一份正则。"""
    md = (
        "# 报销管理制度\n\n"
        "## 3.2 住宿标准\n\n"
        "住宿上限 500 元，超额部分自理。\n\n"
        "## 3.3 交通费\n\n"
        "按实际票据报销。\n"
    ).encode()
    parsed = parse(md, "制度.md")
    by_kind = [b.locator for b in parsed.blocks if b.locator is not None]
    clauses = [getattr(loc, "clause", None) for loc in by_kind]
    assert "3.2" in clauses
    assert "3.3" in clauses


def test_parse_leaves_clause_none_when_text_has_no_number() -> None:
    """抽不到就不许编：`clause` 必须是 `None`，而不是拿段落序号顶上。"""
    parsed = parse("这是一段没有编号的说明文字。\n".encode(), "说明.md")
    for block in parsed.blocks:
        if isinstance(block.locator, ParaLocator):
            assert block.locator.clause is None


def test_table_locator_accepts_clause() -> None:
    """表格块也要能挂条款号：表格常挂在『3.2 报销标准』这种小节下面。"""
    assert TableLocator(index=1, clause="3.2").clause == "3.2"
