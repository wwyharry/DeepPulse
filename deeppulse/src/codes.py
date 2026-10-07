"""A股代码分类与符号映射（沪深主板 + 创业板 + 科创板 + 北交所）

分类规则（按序匹配）：
    688|689        → sh/star    科创板（含 689 CDR）
    60 其余        → sh/main    沪市主板
    300|301|302    → sz/chinext 创业板
    00 开头        → sz/main    深市主板
    43|83|87|92    → bj/bse     北交所
    其他（B股/基金/转债/老三板 40|42/899 指数段）→ None 拒绝

注意：43/83/87/92 与新三板挂牌共享码段，但列表端点只返回上市公司
（akshare 走北交所上市公司列表、东财 t:81 北证A股），不会误入挂牌公司。
"""

from __future__ import annotations

# 板块 → 涨跌幅限制（%）；新股上市首日不设限（另按交易所规则处理）
_PRICE_LIMITS = {"main": 10.0, "chinext": 20.0, "star": 20.0, "bse": 30.0}

_BOARD_NAMES = {"main": "主板", "chinext": "创业板", "star": "科创板", "bse": "北交所"}


def classify_board(code: str) -> tuple[str, str] | None:
    """按代码分类 (market, board)；无法识别返回 None。

    market: "sh" | "sz" | "bj"；board: "main" | "chinext" | "star" | "bse"
    """
    c = str(code).strip().zfill(6)
    if len(c) != 6 or not c.isdigit():
        return None
    if c.startswith(("688", "689")):
        return "sh", "star"
    if c.startswith("60"):
        return "sh", "main"
    if c.startswith(("300", "301", "302")):
        return "sz", "chinext"
    if c.startswith("00"):
        return "sz", "main"
    if c.startswith(("43", "83", "87", "92")):
        return "bj", "bse"
    return None


def market_of(code: str) -> str:
    """返回 market 前缀（sh/sz/bj）；无法识别抛 ValueError"""
    info = classify_board(code)
    if info is None:
        raise ValueError(f"无法识别的股票代码: {code}")
    return info[0]


def board_name(board: str) -> str:
    """board 英文名 → 中文名"""
    return _BOARD_NAMES.get(board, board)


def price_limit_pct(code: str, name: str | None = None) -> float:
    """涨跌幅限制（%）：主板 10（ST 5）/ 创业板 20 / 科创板 20 / 北交所 30。

    创业板/科创板/北交所的 ST 股与普通股同限；主板 ST 为 5%。
    新股上市首日不设涨跌幅限制，不适用本函数。无法识别的代码抛 ValueError。
    """
    info = classify_board(code)
    if info is None:
        raise ValueError(f"无法识别的股票代码: {code}")
    board = info[1]
    pct = _PRICE_LIMITS[board]
    if board == "main" and name and "ST" in name.upper():
        return 5.0
    return pct


def to_sina_symbol(code: str) -> str:
    """→ 新浪带前缀全码（sh600000 / sz000001 / bj430047）"""
    return f"{market_of(code)}{str(code).strip().zfill(6)}"


def to_tencent_symbol(code: str) -> str:
    """→ 腾讯带前缀全码（sh600000 / sz000001 / bj430047）"""
    return f"{market_of(code)}{str(code).strip().zfill(6)}"


def to_em_secid(code: str) -> str:
    """→ 东财 secid（1.600000 / 0.000001；北交所同深市为 0.92xxxx）"""
    return f"{1 if market_of(code) == 'sh' else 0}.{str(code).strip().zfill(6)}"


def to_bs_code(code: str) -> str | None:
    """→ BaoStock 格式（sh.600000 / sz.000001）；北交所不支持返回 None"""
    c = str(code).strip().zfill(6)
    market = market_of(c)
    if market == "bj":
        return None
    return f"{market}.{c}"
