"""选股器在线路径 — 无本地历史数据时的筛选通道

东财全市场快照粗筛（涨幅/量比等快照列直接筛）→ 候选按成交额取前
SCREEN_ONLINE_MAX_CANDIDATES 只 → 逐候选拉近 120 交易日历史精筛技术条件
（指标口径与 screener_v2._build_screening_sql 保持一致）。

限制（如实写入结果 note）：技术条件只在候选上限内评估，非全市场精确筛选；
全市场精确筛选请 /fetch all 采集后走本地路径。
"""

from __future__ import annotations

import pandas as pd

from deeppulse import config

SNAPSHOT_CODE_COL = "代码"
SNAPSHOT_NAME_COL = "名称"
SNAPSHOT_PRICE_COL = "最新价"
SNAPSHOT_PCT_COL = "涨跌幅"
SNAPSHOT_AMOUNT_COL = "成交额"
SNAPSHOT_VOLRATIO_COL = "量比"

# 可直接映射到快照列的条件（粗筛层）
_SNAPSHOT_INDICATORS = {"pct_change", "volume_ratio"}

_TECH_DAYS = 120  # 精筛拉取的历史交易日窗口（与 v2 SQL 的 120 天口径一致）


def _series_ma(close: pd.Series, n: int) -> pd.Series:
    return close.rolling(n).mean()


def _rsi14(close: pd.Series) -> pd.Series:
    """Cutler RSI14（与 screener_v2 SQL 口径一致）"""
    chg = close.diff()
    gain = chg.clip(lower=0).rolling(14).mean()
    loss = (-chg.clip(upper=0)).rolling(14).mean()
    rsi = 100 - 100.0 / (1 + gain / loss.replace(0, pd.NA))
    # avg_loss=0 → 100（有涨无跌）；全平 → 50
    flat = (gain == 0) & (loss == 0)
    rsi = rsi.mask((loss == 0) & (gain > 0), 100.0)
    rsi = rsi.mask(flat, 50.0)
    return rsi


def _check_technical(df: pd.DataFrame, cond: dict) -> bool:
    """在候选日线上评估单个技术条件（最新一根 bar 口径，与 v2 一致）"""
    ind = cond.get("indicator", "")
    if df is None or df.empty:
        return False
    close = pd.to_numeric(df["close"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")
    if close.empty or close.isna().all():
        return False

    ma5, ma10, ma20, ma60 = (_series_ma(close, n) for n in (5, 10, 20, 60))
    vol_ma5 = volume.rolling(5).mean()
    last = -1

    try:
        if ind == "ma_cross":
            fast = int(cond.get("params", {}).get("fast", 5))
            slow = int(cond.get("params", {}).get("slow", 10))
            f = _series_ma(close, fast).iloc[last]
            s = _series_ma(close, slow).iloc[last]
            return bool(pd.notna(f) and pd.notna(s) and f > s)
        if ind == "ma_alignment":
            vals = [x.iloc[last] for x in (ma5, ma10, ma20, ma60)]
            if any(pd.isna(v) for v in vals):
                return False
            if cond.get("type") == "bearish":
                return bool(vals[0] < vals[1] < vals[2] < vals[3])
            return bool(vals[0] > vals[1] > vals[2] > vals[3])
        if ind == "rsi":
            rsi = _rsi14(close).iloc[last]
            if pd.isna(rsi):
                return False
            return _compare(rsi, cond.get("op", ">"), float(cond.get("value", 0)))
        if ind == "volume_ratio":
            v = volume.iloc[last]
            avg = vol_ma5.iloc[last]
            if pd.isna(v) or pd.isna(avg) or avg == 0:
                return False
            return _compare(v / avg, cond.get("op", ">"), float(cond.get("value", 0)))
        if ind == "pct_change":
            prev = close.iloc[last - 1] if len(close) > 1 else None
            cur = close.iloc[last]
            if prev is None or pd.isna(prev) or prev == 0 or pd.isna(cur):
                return False
            return _compare((cur - prev) / prev * 100, cond.get("op", ">"), float(cond.get("value", 0)))
        if ind == "macd":
            # 与 screener_v2 SQL 口径一致：金叉→ma5>ma10，死叉→ma5<ma10
            if cond.get("type") == "death_cross":
                return bool(pd.notna(ma5.iloc[last]) and ma5.iloc[last] < ma10.iloc[last])
            return bool(pd.notna(ma5.iloc[last]) and ma5.iloc[last] > ma10.iloc[last])
        if ind == "price_above_ma":
            period = int(cond.get("params", {}).get("period", 20))
            ma = _series_ma(close, period).iloc[last]
            return bool(pd.notna(ma) and close.iloc[last] > ma)
    except (IndexError, TypeError, ValueError):
        return False
    return False


def _compare(value: float, op: str, threshold: float) -> bool:
    if op == "<":
        return value < threshold
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    return value > threshold


def _check_snapshot(snap_row: dict, cond: dict) -> bool:
    """快照列可映射条件（pct_change / volume_ratio）"""
    ind = cond.get("indicator", "")
    op = cond.get("op", ">")
    value = float(cond.get("value", 0))
    col = SNAPSHOT_PCT_COL if ind == "pct_change" else SNAPSHOT_VOLRATIO_COL
    raw = snap_row.get(col)
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return False
    return _compare(v, op, value)


def screen_online(conditions: list[dict], limit: int = 20) -> dict:
    """无本地数据时的在线选股：spot 粗筛 + 候选历史精筛。返回结果 dict（含来源与限制说明）。"""
    from deeppulse.src.online import fetch_daily_kline_online, fetch_market_snapshot

    snap = fetch_market_snapshot()
    if snap is None or snap.empty:
        return {"results": [], "data_source": "online-spot+kline", "note": "全市场快照为空"}

    snap_conds = [c for c in conditions if c.get("indicator") in _SNAPSHOT_INDICATORS]
    tech_conds = [c for c in conditions if c.get("indicator") not in _SNAPSHOT_INDICATORS]

    # ① 快照粗筛
    rows = snap.to_dict("records")
    coarse = [r for r in rows if all(_check_snapshot(r, c) for c in snap_conds)]

    # ② 候选按成交额降序取前 N（纯技术条件时也按成交额预筛）
    def _amount(r):
        try:
            return float(r.get(SNAPSHOT_AMOUNT_COL) or 0)
        except (TypeError, ValueError):
            return 0.0

    coarse.sort(key=_amount, reverse=True)
    candidates = coarse[: config.SCREEN_ONLINE_MAX_CANDIDATES]

    # ③ 逐候选历史精筛
    results = []
    from datetime import date, timedelta

    from deeppulse.src.online import OnlineFetchError

    end = date.today()
    start = end - timedelta(days=_TECH_DAYS * 2)
    evaluated = 0
    for r in candidates:
        code = str(r.get(SNAPSHOT_CODE_COL, "")).zfill(6)
        if tech_conds:
            try:
                df, _label = fetch_daily_kline_online(code, start, end)
            except OnlineFetchError:
                continue
            evaluated += 1
            if df is None or df.empty or not all(_check_technical(df, c) for c in tech_conds):
                continue
        else:
            evaluated += 1
        results.append(
            {
                "code": code,
                "name": r.get(SNAPSHOT_NAME_COL),
                "close": r.get(SNAPSHOT_PRICE_COL),
                "pct_change": r.get(SNAPSHOT_PCT_COL),
                "volume_ratio": r.get(SNAPSHOT_VOLRATIO_COL),
                "amount": r.get(SNAPSHOT_AMOUNT_COL),
            }
        )
        if len(results) >= int(limit):
            break

    note = (
        f"无本地历史数据：快照粗筛 {len(coarse)} 只，技术条件仅在成交额前 {len(candidates)} 只候选上评估"
        f"（已评估 {evaluated} 只）；全市场精确筛选请 /fetch all 采集后使用本地路径"
    )
    return {
        "results": results,
        "count": len(results),
        "data_source": "online-spot+kline",
        "screened_universe": len(coarse),
        "candidates_evaluated": evaluated,
        "note": note,
    }
