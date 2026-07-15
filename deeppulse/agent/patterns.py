"""K线形态识别 - 单根和组合形态检测"""


def recognize_patterns(df) -> list:
    """识别K线形态

    Args:
        df: 包含 open/high/low/close/volume 列的 DataFrame，需按 trade_date 升序排列

    Returns:
        [{"name": str, "type": "bullish"/"bearish"/"neutral", "confidence": 0-1, "description": str}]
    """
    if df is None or len(df) < 3:
        return []

    patterns = []
    patterns.extend(_single_candle_patterns(df))
    patterns.extend(_double_candle_patterns(df))
    patterns.extend(_triple_candle_patterns(df))
    patterns.extend(_platform_patterns(df))
    return patterns


def _body(row):
    """实体长度"""
    return abs(float(row["close"]) - float(row["open"]))


def _upper_shadow(row):
    """上影线长度"""
    return float(row["high"]) - max(float(row["close"]), float(row["open"]))


def _lower_shadow(row):
    """下影线长度"""
    return min(float(row["close"]), float(row["open"])) - float(row["low"])


def _range(row):
    """振幅"""
    return float(row["high"]) - float(row["low"])


def _is_bullish(row):
    return float(row["close"]) > float(row["open"])


def _is_bearish(row):
    return float(row["close"]) < float(row["open"])


def _body_ratio(row):
    """实体占振幅比例"""
    r = _range(row)
    return _body(row) / r if r > 0 else 0


def _single_candle_patterns(df):
    """单根K线形态"""
    patterns = []
    last = df.iloc[-1]
    r = _range(last)
    if r == 0:
        return patterns

    body = _body(last)
    upper = _upper_shadow(last)
    lower = _lower_shadow(last)
    body_ratio = body / r

    # 十字星（实体很小，上下影线接近）
    if body_ratio < 0.1 and r > 0:
        if abs(upper - lower) / r < 0.3:
            patterns.append(
                {
                    "name": "十字星",
                    "type": "neutral",
                    "confidence": 0.7,
                    "description": "多空平衡，可能变盘信号。需结合后续K线确认方向",
                }
            )

    # 锤子线（下影线长，实体小，在下跌末端）
    if lower > body * 2 and upper < body * 0.5 and body > 0:
        recent_close = [float(df.iloc[i]["close"]) for i in range(-5, -1)]
        if len(recent_close) >= 3 and recent_close[-1] < recent_close[0]:
            patterns.append(
                {
                    "name": "锤子线",
                    "type": "bullish",
                    "confidence": 0.65,
                    "description": "下跌末端出现，下影线长说明低位有承接，可能止跌反弹",
                }
            )

    # 倒锤子线（上影线长，实体小，在下跌末端）
    if upper > body * 2 and lower < body * 0.5 and body > 0:
        recent_close = [float(df.iloc[i]["close"]) for i in range(-5, -1)]
        if len(recent_close) >= 3 and recent_close[-1] < recent_close[0]:
            patterns.append(
                {
                    "name": "倒锤子线",
                    "type": "bullish",
                    "confidence": 0.5,
                    "description": "下跌末端出现，虽有抛压但未创新低，关注次日能否确认",
                }
            )

    # 射击之星（上影线长，实体小，在上涨末端）
    if upper > body * 2 and lower < body * 0.5 and body > 0:
        recent_close = [float(df.iloc[i]["close"]) for i in range(-5, -1)]
        if len(recent_close) >= 3 and recent_close[-1] > recent_close[0]:
            patterns.append(
                {
                    "name": "射击之星",
                    "type": "bearish",
                    "confidence": 0.6,
                    "description": "上涨末端出现，上方压力大，注意回调风险",
                }
            )

    # 大阳线（实体占振幅70%以上，涨幅>3%）
    if _is_bullish(last) and body_ratio > 0.7:
        prev_close = float(df.iloc[-2]["close"]) if len(df) > 1 else float(last["open"])
        change_pct = (float(last["close"]) - prev_close) / prev_close * 100 if prev_close > 0 else 0
        if change_pct > 3:
            patterns.append(
                {
                    "name": "大阳线",
                    "type": "bullish",
                    "confidence": min(0.5 + change_pct / 20, 0.9),
                    "description": f"涨幅{change_pct:.1f}%，多方强势，关注能否延续",
                }
            )

    # 大阴线
    if _is_bearish(last) and body_ratio > 0.7:
        prev_close = float(df.iloc[-2]["close"]) if len(df) > 1 else float(last["open"])
        change_pct = (float(last["close"]) - prev_close) / prev_close * 100 if prev_close > 0 else 0
        if change_pct < -3:
            patterns.append(
                {
                    "name": "大阴线",
                    "type": "bearish",
                    "confidence": min(0.5 + abs(change_pct) / 20, 0.9),
                    "description": f"跌幅{change_pct:.1f}%，空方强势，注意止损",
                }
            )

    return patterns


def _double_candle_patterns(df):
    """双根K线形态"""
    patterns = []
    if len(df) < 2:
        return patterns

    prev = df.iloc[-2]
    last = df.iloc[-1]

    # 看涨吞没（阳包阴）
    if _is_bearish(prev) and _is_bullish(last):
        if float(last["open"]) <= float(prev["close"]) and float(last["close"]) >= float(prev["open"]):
            patterns.append(
                {
                    "name": "看涨吞没",
                    "type": "bullish",
                    "confidence": 0.7,
                    "description": "阳线完全包裹前一根阴线，多方反攻信号",
                }
            )

    # 看跌吞没（阴包阳）
    if _is_bullish(prev) and _is_bearish(last):
        if float(last["open"]) >= float(prev["close"]) and float(last["close"]) <= float(prev["open"]):
            patterns.append(
                {
                    "name": "看跌吞没",
                    "type": "bearish",
                    "confidence": 0.7,
                    "description": "阴线完全包裹前一根阳线，空方反攻信号",
                }
            )

    # 乌云盖顶
    if _is_bullish(prev) and _is_bearish(last):
        if (
            float(last["open"]) > float(prev["high"])
            and float(last["close"]) < (float(prev["open"]) + float(prev["close"])) / 2
        ):
            patterns.append(
                {
                    "name": "乌云盖顶",
                    "type": "bearish",
                    "confidence": 0.65,
                    "description": "高开低走，收盘低于前一根实体中点，看跌信号",
                }
            )

    # 曙光初现
    if _is_bearish(prev) and _is_bullish(last):
        if (
            float(last["open"]) < float(prev["low"])
            and float(last["close"]) > (float(prev["open"]) + float(prev["close"])) / 2
        ):
            patterns.append(
                {
                    "name": "曙光初现",
                    "type": "bullish",
                    "confidence": 0.65,
                    "description": "低开高走，收盘高于前一根实体中点，看涨信号",
                }
            )

    return patterns


def _triple_candle_patterns(df):
    """三根K线形态"""
    patterns = []
    if len(df) < 3:
        return patterns

    d1 = df.iloc[-3]
    d2 = df.iloc[-2]
    d3 = df.iloc[-1]

    # 早晨之星
    if _is_bearish(d1) and _body(d2) < _body(d1) * 0.3 and _is_bullish(d3):
        if float(d3["close"]) > (float(d1["open"]) + float(d1["close"])) / 2:
            patterns.append(
                {
                    "name": "早晨之星",
                    "type": "bullish",
                    "confidence": 0.75,
                    "description": "下跌后出现小实体+阳线反攻，经典的底部反转形态",
                }
            )

    # 黄昏之星
    if _is_bullish(d1) and _body(d2) < _body(d1) * 0.3 and _is_bearish(d3):
        if float(d3["close"]) < (float(d1["open"]) + float(d1["close"])) / 2:
            patterns.append(
                {
                    "name": "黄昏之星",
                    "type": "bearish",
                    "confidence": 0.75,
                    "description": "上涨后出现小实体+阴线下杀，经典的顶部反转形态",
                }
            )

    # 三连阳
    if _is_bullish(d1) and _is_bullish(d2) and _is_bullish(d3):
        if float(d2["close"]) > float(d1["close"]) and float(d3["close"]) > float(d2["close"]):
            patterns.append(
                {
                    "name": "三连阳",
                    "type": "bullish",
                    "confidence": 0.6,
                    "description": "连续三根阳线且逐步走高，多方持续进攻",
                }
            )

    # 三连阴
    if _is_bearish(d1) and _is_bearish(d2) and _is_bearish(d3):
        if float(d2["close"]) < float(d1["close"]) and float(d3["close"]) < float(d2["close"]):
            patterns.append(
                {
                    "name": "三连阴",
                    "type": "bearish",
                    "confidence": 0.6,
                    "description": "连续三根阴线且逐步走低，空方持续施压",
                }
            )

    # 红三兵（三连阳的加强版，实体逐渐增大）
    if _is_bullish(d1) and _is_bullish(d2) and _is_bullish(d3):
        if _body(d2) > _body(d1) and _body(d3) > _body(d2):
            patterns.append(
                {
                    "name": "红三兵",
                    "type": "bullish",
                    "confidence": 0.7,
                    "description": "三根阳线实体逐渐增大，多方力量加速释放，强势信号",
                }
            )

    return patterns


def _platform_patterns(df):
    """平台/箱体形态识别"""
    patterns = []
    if len(df) < 10:
        return patterns

    # 取最近20根K线（或全部）
    recent = df.tail(min(20, len(df)))
    closes = [float(r["close"]) for _, r in recent.iterrows()]
    [float(r["high"]) for _, r in recent.iterrows()]
    [float(r["low"]) for _, r in recent.iterrows()]

    avg_price = sum(closes) / len(closes)
    if avg_price == 0:
        return patterns

    # 计算价格波动率
    max_dev = max(abs(c - avg_price) / avg_price for c in closes)

    # 横盘整理（价格波动在5%以内）
    if max_dev < 0.05 and len(closes) >= 10:
        # 检查是否在末端
        closes[-1]
        (closes[-1] - closes[-3]) / closes[-3] * 100 if closes[-3] > 0 else 0
        patterns.append(
            {
                "name": "横盘整理",
                "type": "neutral",
                "confidence": 0.6,
                "description": f"近{len(closes)}日价格波动{max_dev * 100:.1f}%，窄幅整理中，关注突破方向",
            }
        )

    # N字形态（上涨-回调-再上涨）
    if len(closes) >= 8:
        # 找局部高点和低点
        peaks = []
        troughs = []
        for i in range(1, len(closes) - 1):
            if closes[i] > closes[i - 1] and closes[i] > closes[i + 1]:
                peaks.append((i, closes[i]))
            if closes[i] < closes[i - 1] and closes[i] < closes[i + 1]:
                troughs.append((i, closes[i]))

        if len(peaks) >= 2 and len(troughs) >= 1:
            p1, p2 = peaks[-2], peaks[-1]
            t = troughs[-1]
            if p1[0] < t[0] < p2[0] and p2[1] > p1[1] and t[1] > p1[1] * 0.95:
                patterns.append(
                    {
                        "name": "N字上攻",
                        "type": "bullish",
                        "confidence": 0.6,
                        "description": "上涨-回调-再创新高，上升趋势延续形态",
                    }
                )

    return patterns


def format_patterns(patterns: list) -> str:
    """将形态列表格式化为可读文本"""
    if not patterns:
        return "未识别到典型K线形态"

    lines = []
    for p in patterns:
        emoji = "🟢" if p["type"] == "bullish" else "🔴" if p["type"] == "bearish" else "⚪"
        conf = "●" * int(p["confidence"] * 5) + "○" * (5 - int(p["confidence"] * 5))
        lines.append(f"{emoji} {p['name']} [{conf}] — {p['description']}")
    return "\n".join(lines)
