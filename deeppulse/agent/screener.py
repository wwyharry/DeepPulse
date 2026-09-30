"""股票筛选器 - 基于技术指标的条件筛选"""

import json
import re

# _check_conditions 实际支持的条件关键字（解析不到任何关键字的条件串必须整体拒绝）
_SUPPORTED_CONDITION_KEYWORDS = (
    "MA5",
    "MA10",
    "MA20",
    "MA60",
    "多头排列",
    "RSI6",
    "RSI12",
    "RSI超卖",
    "RSI超买",
    "超卖",
    "超买",
    "MACD金叉",
    "MACD金",
    "MACD死叉",
    "MACD死",
    "MACD多头",
    "放量",
    "缩量",
    "成交量",
    "VOL",
    "涨幅",
    "涨跌幅",
    "跌幅",
    "强势",
    "突破",
    "弱势",
    "下跌",
    "超跌",
)

SUPPORTED_CONDITIONS_DOC = [
    "MA5>MA10",
    "MA10>MA20",
    "多头排列",
    "RSI6<20",
    "RSI6>80",
    "RSI超卖",
    "RSI超买",
    "MACD金叉",
    "MACD死叉",
    "MACD多头",
    "放量",
    "缩量",
    "成交量>5日均量*1.5",
    "涨幅>N%",
    "跌幅>N%",
    "强势",
    "突破",
    "弱势",
    "下跌",
    "超跌",
]


def _unsupported_condition_fragments(conditions: str) -> list:
    """拆分条件串，返回无法解析的片段列表（空表示全部受支持）"""
    fragments = [f for f in re.split(r"[,，、;；\s]+", conditions) if f]
    return [f for f in fragments if not any(k in f.upper() for k in _SUPPORTED_CONDITION_KEYWORDS)]


def screen_stocks(conditions: str, limit: int = 20) -> str:
    """根据技术条件筛选股票

    Args:
        conditions: 筛选条件描述（自然语言或简单表达式），逗号分隔可叠加（AND 语义）
            支持: "MA5>MA10", "RSI6<20", "成交量>5日均量*1.5", "MACD金叉", "涨跌幅>3%"
        limit: 最多返回数量
    """
    from deeppulse import config
    from deeppulse.src.database import get_connection

    # 无法解析的条件必须整体拒绝，不能把全部股票当成命中
    unsupported = _unsupported_condition_fragments(conditions)
    if unsupported:
        return json.dumps(
            {
                "error": f"不支持的条件: {', '.join(unsupported)}",
                "supported": SUPPORTED_CONDITIONS_DOC,
            },
            ensure_ascii=False,
        )

    conn = get_connection(config.DB_PATH)
    try:
        # 获取活跃股票列表
        stocks = conn.execute("""
            SELECT code, name FROM stock_info
            WHERE (delist_date IS NULL OR delist_date >= current_date)
            AND (code LIKE '6%' OR code LIKE '0%')
            ORDER BY code
        """).fetchall()

        if not stocks:
            return json.dumps({"error": "股票列表为空"}, ensure_ascii=False)

        results = []
        checked = 0
        for code, name in stocks:
            if len(results) >= limit:
                break
            checked += 1

            # 取最近60个交易日数据（过滤掉含NULL的异常行）
            rows = conn.execute(
                """
                SELECT trade_date, open, high, low, close, volume, amount
                FROM daily_kline WHERE code = ?
                  AND open IS NOT NULL AND high IS NOT NULL
                  AND low IS NOT NULL AND close IS NOT NULL
                  AND volume IS NOT NULL
                ORDER BY trade_date DESC LIMIT 60
            """,
                [code],
            ).fetchall()

            if len(rows) < 20:
                continue

            # 转为正序
            rows = rows[::-1]
            match, detail = _check_conditions(rows, conditions)
            if match:
                last = rows[-1]
                prev = rows[-2]
                last_close = float(last[4])
                prev_close = float(prev[4])
                change_pct = (last_close - prev_close) / prev_close * 100 if prev_close > 0 else 0
                results.append(
                    {
                        "code": code,
                        "name": name,
                        "price": round(last_close, 2),
                        "change_pct": round(change_pct, 2),
                        "match_detail": detail,
                    }
                )

        return json.dumps(
            {
                "conditions": conditions,
                "checked": checked,
                "matched": len(results),
                "results": results,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": f"筛选失败: {e}"}, ensure_ascii=False)
    finally:
        conn.close()


def _check_conditions(rows, conditions: str) -> tuple:
    """检查单只股票是否满足条件

    Args:
        rows: [(trade_date, open, high, low, close, volume, amount), ...] 正序
        conditions: 条件字符串

    Returns:
        (matched: bool, detail: str)
    """
    conds = conditions.upper()
    matches = []
    fails = []

    # 过滤含 None 的行（停牌/异常数据）
    rows = [r for r in rows if all(r[i] is not None for i in [1, 2, 3, 4, 5])]
    if len(rows) < 20:
        return False, "有效数据不足"

    closes = [float(r[4]) for r in rows]
    [float(r[1]) for r in rows]
    [float(r[2]) for r in rows]
    [float(r[3]) for r in rows]
    volumes = [float(r[5]) for r in rows]
    last_close = closes[-1]
    last_vol = volumes[-1]

    # MA 计算
    ma = {}
    for w in [5, 10, 20, 60]:
        if len(closes) >= w:
            ma[w] = sum(closes[-w:]) / w

    # RSI 计算
    def calc_rsi(period):
        if len(closes) < period + 1:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        recent = deltas[-period:]
        gains = sum(d for d in recent if d > 0) / period
        losses = sum(-d for d in recent if d < 0) / period
        if losses == 0:
            return 100
        rs = gains / losses
        return 100 - (100 / (1 + rs))

    rsi = {6: calc_rsi(6), 12: calc_rsi(12)}

    # MACD
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26, strict=False)]
    dea = _ema(dif, 9)
    [(d - e) * 2 for d, e in zip(dif, dea, strict=False)]

    # 5日均量
    vol_avg5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else last_vol

    # 涨跌幅
    prev_close = closes[-2] if len(closes) > 1 else closes[-1]
    change_pct = (last_close - prev_close) / prev_close * 100 if prev_close > 0 else 0

    # --- 条件解析和匹配 ---

    # MA 条件: MA5>MA10, MA5上穿MA10
    if "MA5" in conds and "MA10" in conds:
        if ma.get(5) and ma.get(10):
            if ">" in conds or "上穿" in conds or "金叉" in conds:
                if ma[5] > ma[10]:
                    matches.append(f"MA5({ma[5]:.2f})>MA10({ma[10]:.2f})")
                else:
                    fails.append("MA5<MA10")
            elif "<" in conds:
                if ma[5] < ma[10]:
                    matches.append(f"MA5({ma[5]:.2f})<MA10({ma[10]:.2f})")
                else:
                    fails.append("MA5>=MA10")

    if "MA10" in conds and "MA20" in conds:
        if ma.get(10) and ma.get(20):
            if ">" in conds:
                if ma[10] > ma[20]:
                    matches.append(f"MA10({ma[10]:.2f})>MA20({ma[20]:.2f})")
                else:
                    fails.append("MA10<MA20")

    # 多头排列
    if "多头排列" in conds:
        if all(ma.get(w) for w in [5, 10, 20, 60]):
            if ma[5] > ma[10] > ma[20] > ma[60]:
                matches.append("MA多头排列")
            else:
                fails.append("非多头排列")

    # RSI 条件（threshold is None：未找到阈值；0 是合法阈值，不能当假值跳过）
    for period, val in rsi.items():
        key = f"RSI{period}"
        if key in conds:
            if val is not None:
                if "<" in conds:
                    threshold = _extract_number(conds, key + "<")
                    if threshold is not None and val < threshold:
                        matches.append(f"RSI{period}={val:.1f}<{threshold}")
                    else:
                        fails.append(f"RSI{period}={val:.1f}")
                elif ">" in conds:
                    threshold = _extract_number(conds, key + ">")
                    if threshold is not None and val > threshold:
                        matches.append(f"RSI{period}={val:.1f}>{threshold}")
                    else:
                        fails.append(f"RSI{period}={val:.1f}")

    # RSI 超卖/超买（is not None：RSI==0 是最极端超卖，不能当假值跳过）
    if "RSI超卖" in conds or "超卖" in conds:
        if rsi.get(6) is not None and rsi[6] < 20:
            matches.append(f"RSI6={rsi[6]:.1f} 超卖")
        else:
            fails.append("未超卖")
    if "RSI超买" in conds or "超买" in conds:
        if rsi.get(6) is not None and rsi[6] > 80:
            matches.append(f"RSI6={rsi[6]:.1f} 超买")
        else:
            fails.append("未超买")

    # MACD 金叉/死叉
    if "MACD金叉" in conds or "MACD金" in conds:
        if len(dif) >= 2 and len(dea) >= 2:
            if dif[-1] > dea[-1] and dif[-2] <= dea[-2]:
                matches.append("MACD金叉")
            else:
                fails.append("非MACD金叉")

    if "MACD死叉" in conds or "MACD死" in conds:
        if len(dif) >= 2 and len(dea) >= 2:
            if dif[-1] < dea[-1] and dif[-2] >= dea[-2]:
                matches.append("MACD死叉")
            else:
                fails.append("非MACD死叉")

    # MACD 多头
    if "MACD多头" in conds:
        if dif[-1] > dea[-1]:
            matches.append(f"DIF({dif[-1]:.3f})>DEA({dea[-1]:.3f})")
        else:
            fails.append("MACD空头")

    # 成交量条件
    if "放量" in conds or "成交量" in conds.upper() or "VOL" in conds:
        vol_ratio = last_vol / vol_avg5 if vol_avg5 > 0 else 1
        multiplier = _extract_number(conds, "*")
        if multiplier is None:
            multiplier = 1.5
        if "缩量" in conds:
            if vol_ratio < 0.7:
                matches.append(f"缩量(量比{vol_ratio:.2f})")
            else:
                fails.append(f"未缩量(量比{vol_ratio:.2f})")
        else:
            if vol_ratio >= multiplier:
                matches.append(f"放量(量比{vol_ratio:.2f})")
            else:
                fails.append(f"量比{vol_ratio:.2f}不足{multiplier}")

    # 涨跌幅条件（跌幅语义：change_pct < -阈值，"跌幅>3%" 即跌超 3%）
    if "涨跌幅" in conds or "涨幅" in conds or "跌幅" in conds:
        if "涨跌幅" in conds:
            _ctx = "涨跌幅"
        elif "跌幅" in conds:
            _ctx = "跌幅"
        else:
            _ctx = "涨幅"
        threshold = _extract_number(conds, _ctx + ">")
        if threshold is None:
            threshold = _extract_number(conds, _ctx + "<")
        if threshold is None:
            threshold = 3
        if _ctx == "跌幅":
            if ">" in conds:
                if change_pct < -threshold:
                    matches.append(f"跌幅{change_pct:.2f}%超过{threshold}%")
                else:
                    fails.append(f"跌幅不足{threshold}%")
            elif "<" in conds:
                if change_pct > -threshold:
                    matches.append(f"跌幅{change_pct:.2f}%小于{threshold}%")
                else:
                    fails.append(f"跌幅超过{threshold}%")
        elif ">" in conds:
            if change_pct > threshold:
                matches.append(f"涨幅{change_pct:.2f}%>{threshold}%")
            else:
                fails.append(f"涨幅{change_pct:.2f}%")
        elif "<" in conds:
            if change_pct < -threshold:
                matches.append(f"跌幅{change_pct:.2f}%")
            else:
                fails.append(f"跌幅不足{threshold}%")

    # 如果没有匹配到任何已知条件，尝试宽松匹配
    if not matches and not fails:
        # 简单的关键字匹配
        if any(kw in conds for kw in ["强势", "突破", "放量"]):
            if last_close > max(closes[-6:-1]) if len(closes) >= 6 else False:
                matches.append("价格突破近5日新高")
            elif last_vol > vol_avg5 * 1.5:
                matches.append("成交量放大")
            else:
                fails.append("不满足强势/突破/放量条件")
        elif any(kw in conds for kw in ["弱势", "下跌", "超跌"]):
            if change_pct < -3:
                matches.append(f"跌幅{change_pct:.2f}%")
            else:
                fails.append("不满足弱势/下跌条件")
        else:
            # 条件无法解析：不能把股票当成命中（screen_stocks 入口已提前拒绝）
            return False, f"条件'{conditions}'无法解析"

    # 多条件 AND 语义：任一条件不满足即不命中（与"逗号分隔可叠加"一致）
    if fails:
        detail = "; ".join(fails)
        if matches:
            detail = f"未满足[{detail}]；满足[{' ; '.join(matches)}]"
        return False, detail
    return True, "; ".join(matches)


def _ema(data: list, span: int) -> list:
    """计算 EMA"""
    if not data:
        return []
    multiplier = 2 / (span + 1)
    ema = [data[0]]
    for i in range(1, len(data)):
        ema.append(data[i] * multiplier + ema[-1] * (1 - multiplier))
    return ema


def _extract_number(text: str, context: str = "") -> float | None:
    """从文本中提取与 context 相关的数字。

    context 非空时定位 context 并取其后紧跟的数字；context 不在文本中（或其后
    无数字）返回 None，由调用方用 is None 判断给默认值——找到的合法 0 阈值
    （如 "涨幅>0"）原样返回，不再被当成"未找到"。context 为空时取文本中第一个
    数字，未找到返回 0（历史契约，无生产调用方）。
    """
    import re

    t = re.sub(r"\s+", "", text)
    if not context:
        m = re.search(r"(\d+\.?\d*)", t)
        return float(m.group(1)) if m else 0

    c = re.sub(r"\s+", "", context)
    i = t.find(c)
    if i >= 0:
        seg = t[i + len(c) :].lstrip("=")  # 容忍 "RSI6<=15" / ">=" 等
        m = re.match(r"(\d+\.?\d*)", seg)
        if m:
            return float(m.group(1))
    # 兜底：context 后紧跟数字的其它出现（跳过 "MA5>MA10" 这类）
    m = re.search(re.escape(c) + r"(\d+\.?\d*)", t)
    return float(m.group(1)) if m else None
