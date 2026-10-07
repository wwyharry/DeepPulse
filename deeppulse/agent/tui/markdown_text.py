"""Markdown → Rich Text 单次转换器

Rich 的 Markdown 渲染器每次测量/绘制都要重新解析整篇文档（含布局），
在窗口 resize 时是 O(消息数 × 文档长度) 的重渲染，直接把 UI 卡死。
这里在**消息写入时**把 Markdown 解析成带样式的 rich.text.Text，之后
布局只需做廉价的 Text 换行。支持常用子集：标题/段落/列表/表格/代码块/
引用/分割线/加粗斜体/行内代码/链接。
"""

from markdown_it import MarkdownIt
from rich.text import Text

from deeppulse.agent.tui import palette as P

_HEADING_STYLES = {
    1: f"bold {P.ACCENT_BRIGHT}",
    2: f"bold {P.TEXT}",
    3: f"bold {P.TEXT}",
    4: f"bold {P.TEXT_DIM}",
    5: f"bold {P.TEXT_DIM}",
    6: f"bold {P.TEXT_DIM}",
}


def _append_inline(out: Text, children: list, base: str = "") -> None:
    """把 inline token 流写入 Text（样式栈处理加粗/斜体/行内代码/链接）"""
    stack = [base]
    for tok in children:
        kind = tok.type
        if kind == "text":
            out.append(tok.content, style=stack[-1])
        elif kind == "strong_open":
            stack.append((stack[-1] + " bold").strip())
        elif kind == "em_open":
            stack.append((stack[-1] + " italic").strip())
        elif kind == "s_open":
            stack.append((stack[-1] + " strike").strip())
        elif kind == "code_inline":
            out.append(tok.content, style=(stack[-1] + f" on {P.CODE_BG} {P.CODE_FG}").strip())
        elif kind == "link_open":
            stack.append((stack[-1] + f" underline {P.ACCENT}").strip())
        elif kind in ("strong_close", "em_close", "s_close", "link_close"):
            if len(stack) > 1:
                stack.pop()
        elif kind in ("softbreak", "hardbreak"):
            out.append("\n")
        # 其余 token（image 等）忽略


def _plain_inline(children: list) -> str:
    """提取 inline token 流的纯文本（标题/表格单元格用）"""
    parts = []
    for tok in children or []:
        if tok.type in ("text", "code_inline"):
            parts.append(tok.content)
        elif tok.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts)


def _display_width(s: str) -> int:
    """CJK 与 ASCII 混排的显示宽度"""
    w = 0
    for ch in s:
        w += 2 if "⺀" <= ch <= "鿿" or "＀" <= ch <= "￯" or "　" <= ch <= "〿" else 1
    return w


def _pad(s: str, width: int, right: bool = False) -> str:
    gap = width - _display_width(s)
    if gap <= 0:
        return s
    return (" " * gap + s) if right else (s + " " * gap)


def _is_number(s: str) -> bool:
    t = s.replace(",", "").replace("%", "").replace("+", "").strip()
    if not t:
        return False
    try:
        float(t)
        return True
    except ValueError:
        return False


def _append_table(out: Text, rows: list[list[str]]) -> None:
    """对齐渲染表格；整列皆数字时右对齐"""
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    rows = [list(r) + [""] * (n_cols - len(r)) for r in rows]
    # 数字列判定只看数据行（表头是文字，不参与）
    body = rows[1:] or rows
    numeric = [all(_is_number(r[c]) or r[c] == "" for r in body) for c in range(n_cols)]
    widths = [max(_display_width(r[c]) for r in rows) for c in range(n_cols)]

    def emit_row(cells: list[str], style: str) -> None:
        for c, cell in enumerate(cells):
            out.append(_pad(cell, widths[c], right=numeric[c]), style=style)
            if c < n_cols - 1:
                out.append("  ", style=style)
        out.append("\n")

    emit_row(rows[0], f"bold {P.ACCENT}")
    out.append("  ".join("─" * widths[c] for c in range(n_cols)) + "\n", style=f"dim {P.TEXT_FAINT}")
    for row in rows[1:]:
        emit_row(row, "")


def markdown_to_text(src: str) -> Text:
    """Markdown 源码 → 可重排的 rich.text.Text（一次解析，布局期只做换行）"""
    out = Text()
    tokens = MarkdownIt("gfm-like").parse(src or "")

    list_stack: list[dict] = []
    quote_depth = 0
    item_prefix = ""  # 当前列表项的行首前缀（单行简化）

    def emit_paragraph(inline_children: list) -> None:
        nonlocal item_prefix
        if quote_depth:
            out.append("▎ " * quote_depth, style=f"dim {P.TEXT_FAINT}")
        if item_prefix:
            out.append(item_prefix, style=f"dim {P.TEXT_DIM}")
        _append_inline(out, inline_children)
        out.append("\n")
        item_prefix = ""

    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        kind = tok.type

        if kind == "heading_open":
            level = int(tok.tag[1])
            inline = tokens[i + 1]
            if out.plain and not out.plain.endswith("\n\n"):
                out.append("\n")
            out.append(_plain_inline(inline.children or []), style=_HEADING_STYLES[level])
            out.append("\n\n")
            i += 3  # heading_open / inline / heading_close
            continue

        if kind == "paragraph_open":
            emit_paragraph(tokens[i + 1].children or [])
            if not list_stack:
                out.append("\n")
            i += 3
            continue

        if kind == "bullet_list_open":
            list_stack.append({"ordered": False})
            i += 1
            continue
        if kind == "ordered_list_open":
            list_stack.append({"ordered": True, "n": 0})
            i += 1
            continue
        if kind in ("bullet_list_close", "ordered_list_close"):
            if list_stack:
                list_stack.pop()
            out.append("\n")
            i += 1
            continue

        if kind == "list_item_open":
            depth = max(len(list_stack) - 1, 0)
            indent = "   " * depth
            if list_stack and list_stack[-1]["ordered"]:
                list_stack[-1]["n"] = int(tok.info or list_stack[-1]["n"] + 1)
                item_prefix = f"{indent}{list_stack[-1]['n']}. "
            else:
                item_prefix = f"{indent}• "
            i += 1
            continue
        if kind == "list_item_close":
            item_prefix = ""
            i += 1
            continue

        if kind == "table_open":
            rows: list[list[str]] = []
            row: list[str] | None = None
            i += 1
            while i < n and tokens[i].type != "table_close":
                t = tokens[i]
                if t.type == "tr_open":
                    row = []
                elif t.type in ("th_open", "td_open"):
                    inline = tokens[i + 1]
                    cell = _plain_inline(inline.children or [])
                    if row is not None:
                        row.append(cell)
                    i += 2  # 跳过 inline，落在 *_close 上
                    continue
                elif t.type == "tr_close" and row is not None:
                    rows.append(row)
                    row = None
                i += 1
            _append_table(out, rows)
            out.append("\n")
            i += 1
            continue

        if kind in ("fence", "code_block"):
            for line in (tok.content or "").rstrip("\n").split("\n"):
                if quote_depth:
                    out.append("▎ " * quote_depth, style=f"dim {P.TEXT_FAINT}")
                out.append("  " + line, style=f"on {P.CODE_BG} {P.CODE_FG}")
                out.append("\n")
            out.append("\n")
            i += 1
            continue

        if kind == "hr":
            out.append("─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n\n", style=f"dim {P.TEXT_FAINT}")
            i += 1
            continue

        if kind == "blockquote_open":
            quote_depth += 1
            i += 1
            continue
        if kind == "blockquote_close":
            quote_depth = max(0, quote_depth - 1)
            out.append("\n")
            i += 1
            continue

        if kind == "inline":
            emit_paragraph(tok.children or [])
            i += 1
            continue

        i += 1

    return out
