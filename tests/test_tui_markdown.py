"""TUI Markdown → Rich Text 单次转换器测试

resize 卡死修复的核心前提：消息写入时把 Markdown 解析成带样式 Text，
布局期只做换行。这里锁定转换器的输出结构，防止回退到渲染期解析。
"""

from rich.style import Style
from rich.text import Text

from deeppulse.agent.tui.markdown_text import _display_width, _pad, markdown_to_text


def style_at(text: Text, idx: int) -> Style:
    """合并覆盖 idx 处的所有 span 样式"""
    style = Style()
    for span in text.spans:
        if span.start <= idx < span.end:
            s = span.style
            style = style + (s if isinstance(s, Style) else Style.parse(s))
    return style


def style_of(text: Text, needle: str) -> Style:
    return style_at(text, text.plain.index(needle))


class TestMarkdownToText:
    """块级/行内元素转换"""

    def test_empty_input(self):
        assert markdown_to_text("").plain == ""
        assert markdown_to_text(None).plain == ""

    def test_paragraph_softbreak(self):
        t = markdown_to_text("第一行\n第二行")
        assert "第一行\n第二行" in t.plain

    def test_heading_styled_and_spaced(self):
        t = markdown_to_text("## 分析结论\n\n正文段落")
        assert style_of(t, "分析结论").bold
        assert "分析结论\n\n" in t.plain  # 标题后空行

    def test_heading_levels_bold(self):
        t = markdown_to_text("# 一\n\n###### 六")
        assert style_of(t, "一").bold
        assert style_of(t, "六").bold

    def test_bold_italic_strike(self):
        t = markdown_to_text("**加粗** 与 *斜体* 与 ~~删除~~")
        assert style_of(t, "加粗").bold
        assert style_of(t, "斜体").italic
        assert style_of(t, "删除").strike

    def test_code_inline_has_bg(self):
        t = markdown_to_text("指标 `MA5 > MA10` 结束")
        assert style_of(t, "MA5 > MA10").bgcolor is not None

    def test_link_underlined(self):
        t = markdown_to_text("[文档](https://example.com)")
        assert style_of(t, "文档").underline

    def test_bullet_list_prefix(self):
        t = markdown_to_text("- 第一项\n- 第二项")
        assert "• 第一项\n• 第二项" in t.plain

    def test_ordered_list_numbers(self):
        t = markdown_to_text("1. 甲\n2. 乙")
        assert "1. 甲\n2. 乙" in t.plain

    def test_nested_list_indent(self):
        t = markdown_to_text("- 父项\n  - 子项")
        assert "• 父项" in t.plain
        assert "   • 子项" in t.plain

    def test_list_items_no_blank_between(self):
        # 列表项之间不应插入空行（此前会散架）
        assert "• 甲\n• 乙\n• 丙" in markdown_to_text("- 甲\n- 乙\n- 丙").plain

    def test_table_header_and_separator(self):
        md = "| 指标 | 数值 |\n| --- | --- |\n| RSI | 12 |\n| MACD | 3.5 |"
        t = markdown_to_text(md)
        lines = t.plain.splitlines()
        assert lines[0].startswith("指标")
        assert "数值" in lines[0]
        assert any("─" in ln for ln in lines)

    def test_table_numeric_column_right_aligned(self):
        md = "| 指标 | 数值 |\n| --- | --- |\n| RSI | 12 |\n| MACD | 3.5 |"
        t = markdown_to_text(md)
        rsiline = next(ln for ln in t.plain.splitlines() if ln.startswith("RSI"))
        macdline = next(ln for ln in t.plain.splitlines() if ln.startswith("MACD"))
        # 右对齐 ⇒ 行尾是数字本身（左对齐会在右侧补空格）
        assert rsiline.endswith("12")
        assert macdline.endswith("3.5")

    def test_table_mixed_column_left_aligned(self):
        # 数据行混入非数字 ⇒ 整列左对齐（行尾留补位空格）
        md = "| 名 | 值 |\n| --- | --- |\n| 茅台 | 12 |\n| 平安 | n/a |"
        t = markdown_to_text(md)
        line = next(ln for ln in t.plain.splitlines() if "12" in ln)
        assert line.endswith("12 ") or line.rstrip() != line

    def test_table_cjk_width_padding(self):
        # CJK 单元格按双宽对齐，表头与数据行显示宽度一致
        md = "| 指标 | 数值 |\n| --- | --- |\n| 量比 | 1.32 |"
        t = markdown_to_text(md)
        header = next(ln for ln in t.plain.splitlines() if ln.startswith("指标"))
        row = next(ln for ln in t.plain.splitlines() if ln.startswith("量比"))
        assert _display_width(header) == _display_width(row)

    def test_fence_code_block(self):
        t = markdown_to_text("```python\nx = 1\n```")
        assert "  x = 1" in t.plain
        assert style_of(t, "x = 1").bgcolor is not None

    def test_blockquote_bar(self):
        t = markdown_to_text("> 风险提示：注意仓位")
        assert "▎ " in t.plain
        assert "风险提示" in t.plain

    def test_hr(self):
        assert "─" in markdown_to_text("---").plain

    def test_long_document_structure(self):
        t = markdown_to_text("## 标题\n\n段落内容。\n\n- 条目\n")
        assert "标题\n\n段落内容。\n\n• 条目" in t.plain


class TestDisplayWidthHelpers:
    """CJK 混排宽度与补位"""

    def test_display_width(self):
        assert _display_width("abc") == 3
        assert _display_width("茅台") == 4
        assert _display_width("A股") == 3

    def test_pad_left_and_right(self):
        assert _pad("股", 4) == "股  "
        assert _pad("股", 4, right=True) == "  股"
        assert _pad("茅台", 4) == "茅台"
        assert _pad("茅台", 2) == "茅台"  # 不截断
