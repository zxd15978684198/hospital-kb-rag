"""从块的正文开头抽「条款号」（ADR 0008 D2 的补充）。

为什么需要它：`page` / `para` 两种 locator 只能把引用指到「第 12 页」「第 7 段」，
而制度类文档真正被引用的是条款——「第十二条」「3.2」「5.1.4」。前端拿到页码还要人
自己翻，等于没指到位。条款号是可机械判定的文本特征，抽出来落进 `chunks.locator`，
引用就能一路带到 `Citation.locator`。

**宁可不给，也不许编**（本模块唯一硬规则）：
- 只在块的**第一行开头**找，句子中间的「见第十二条」不算这一块的条款号；
- 抽到之后要求后面紧跟分隔符或直接结束，`3.2.1.4` 这种超长编号也只认前三级；
- 抽不出来返回 `None`，**不要**退回到标题、不要用段落序号凑、不要标 `inferred=True`
  假装读到了——`inferred` 的语义是"位置是回填的"，不是"我猜了个条款号"。

`第X章` 与 `第X条` 同时出现时取 `条`：引用要落到条款，章太粗。
"""

from __future__ import annotations

import re

# 中文数字 + 阿拉伯数字都认：「第十二条」「第 12 条」「第三条」。
_CN_NUM = r"[一二三四五六七八九十百零〇\d]+"
# 「第X条」必须在行首。允许中间有空格（排版常把「第 12 条」拆开）。
_ARTICLE = re.compile(rf"^第\s*({_CN_NUM})\s*条")
# 「第X章 第Y条」：只取第 Y 条。
_CHAPTER_ARTICLE = re.compile(rf"^第\s*{_CN_NUM}\s*章\s*第\s*({_CN_NUM})\s*条")
# 数字层级编号：「3.2」「3.2.1」。最多三级（`{1,2}` 即最多再跟两段）。
# 末尾的 `(?!\.\d)` 是给 `3.2.1.4` 这种更深编号用的：允许"3.2."这种中文排版尾点，
# 但不许把 `3.2.1.4` 截成 `3.2` 或 `3.2.1` 充数——半截编号和错编号一样有害。
_NUMERIC = re.compile(
    r"^(\d+(?:\.\d+){1,2})(?=$|[\s\u3000、.．:：,，)）])(?!\.\d)",
)
# 行首的 markdown 标题井号与常见装饰符，抽之前先剥掉。
# 这里**故意不剥「1.」「2、」这类列表序号**：剥了会把 `3.2` 的 `3.` 当成列表符号吃掉，
# `3.2` 就再也抽不出来了；而列表序号本身也不是条款号，留着它只是让它匹配不上。
_DECORATION = re.compile(r"^[\s\u3000#>*\-—=·•]*")


def _first_line(text: str) -> str:
    """取第一条非空行。空行开头在 docx 里很常见（段前空段）。"""
    for raw in text.splitlines():
        line = raw.strip()
        if line:
            return line
    return ""


def extract_clause(text: str) -> str | None:
    """从块正文里抽条款号；抽不出来返回 ``None``（**不许编**）。

    只认三种形态，且都必须出现在块的第一行开头：

    - ``第十二条`` / ``第 12 条`` → ``第十二条``（原样保留中文数字，前端照着显示）
    - ``第三章 第十二条`` → ``第十二条``（章太粗，取条）
    - ``3.2`` / ``3.2.1``（后接空格、顿号、冒号、闭合括号或行尾）→ ``3.2`` / ``3.2.1``

    刻意**不认**的：

    - ``（三）`` ``(3)`` 这类列表项——它们是排版编号，不是可被引用的条款；
    - 行中间的 ``见第十二条``——那是指向别处，不是这块自己的位置；
    - 纯标题行 ``报销标准``——没有编号就没有可指认的位置，此时 ``clause`` 留 ``None``。
    """
    line = _DECORATION.sub("", _first_line(text or ""), count=1)
    if not line:
        return None
    # 先试「章 + 条」，命中就不要再让 `_ARTICLE` 抢先匹配走 "第X章"
    if (m := _CHAPTER_ARTICLE.match(line)) is not None:
        return f"第{m.group(1)}条"
    if (m := _ARTICLE.match(line)) is not None:
        return f"第{m.group(1)}条"
    if (m := _NUMERIC.match(line)) is not None:
        return m.group(1)
    return None
