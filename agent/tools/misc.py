import sys
import time
import json
from datetime import date, timedelta
from pathlib import Path

from agent.tools.decorator import tool

import config as _config
from agent.market import get_limit_up_pool as _get_limit_up_pool
from agent.market import get_market_sentiment as _get_market_sentiment
from agent.market import get_sector_ranking as _get_sector_ranking
from agent.market import get_stock_fund_flow as _get_stock_fund_flow
from agent.news import search_baidu_news, search_market_hot_news, search_stock_news
from agent.patterns import format_patterns as _format_patterns
from agent.patterns import recognize_patterns as _recognize_patterns
from agent.screener import screen_stocks as _screen_stocks
from src.collector import fetch_kline_with_timeout
from src.database import (
    get_connection,
    get_latest_kline_date,
    get_stock_list,
    init_tables,
    insert_daily_kline,
    log_fetch,
    upsert_stock_info,
)
from src.query import StockQuery
from src.realtime import RealtimeQuoteManager
from src.sources import BaoStockSource

_query = StockQuery()

# 实时行情管理器（多源冗余）
_realtime_manager = RealtimeQuoteManager(
    priority=_config.REALTIME_SOURCES,
    timeout=_config.REALTIME_TIMEOUT,
)

# ============ 工具函数实现 ============


@tool(
    "按名称或代码模糊搜索股票。用于查找用户提到的股票对应的代码。",
    param_desc=[
        "搜索关键词，可以是股票名称或代码",
    ],
)
def search_stock(keyword: str) -> str:
    """按名称或代码搜索股票"""
    df = _query.search_stocks(keyword)
    if df.empty:
        return json.dumps({"error": f"未找到匹配 '{keyword}' 的股票"}, ensure_ascii=False)
    results = df[["code", "name", "market", "list_date"]].head(
        20).to_dict("records")
    for r in results:
        r["list_date"] = str(r["list_date"]) if r["list_date"] else None
    return json.dumps({"results": results, "total": len(df)}, ensure_ascii=False)


@tool(
    "查询单只股票的基本信息（名称、市场、上市日期等）",
    param_desc=[
        "6位股票代码，如 600000",
    ],
)
def query_stock_info(code: str) -> str:
    """查询单只股票的基本信息"""
    df = _query.get_stock_info(code)
    if df.empty:
        return json.dumps({"error": f"未找到股票 {code}"}, ensure_ascii=False)
    row = df.iloc[0].to_dict()
    for k, v in row.items():
        if v is not None and hasattr(v, "isoformat"):
            row[k] = str(v)
    return json.dumps(row, ensure_ascii=False, default=str)


@tool(
    "查询股票最近N个交易日的日K线数据（开高低收、成交量、成交额、换手率）。注意：天数指交易日（剔除周末和法定节假日），非自然日。",
    param_desc=[
        "6位股票代码",
        "获取最近N个交易日的数据（非自然日），默认60",
    ],
)
def query_kline(code: str, days: int = 60, source: str = None) -> str:
    """查询股票日K线数据

    Args:
        code: 股票代码（6位数字）
        days: 获取最近N个交易日的数据，默认60。注意：这里的天数指交易日（剔除周末和法定节假日），非自然日。
        source: 指定数据源（可选）
    """
    end = date.today()
    # 交易日≈自然日*5/7，再留余量应对节假日，取3倍较稳妥
    start = end - timedelta(days=int(days) * 3)
    df = _query.get_daily_kline(code, start_date=str(
        start), end_date=str(end), source=source, limit=int(days))
    if df.empty:
        return json.dumps({"error": f"股票 {code} 无K线数据"}, ensure_ascii=False)
    df = df.sort_values("trade_date")
    records = df.to_dict("records")
    for r in records:
        r["trade_date"] = str(r["trade_date"])
        for k in ["open", "high", "low", "close", "amount", "turnover"]:
            if r.get(k) is not None:
                r[k] = round(float(r[k]), 4)
        if r.get("volume") is not None:
            r["volume"] = int(r["volume"])
    return json.dumps({"code": code, "count": len(records), "kline": records}, ensure_ascii=False)


@tool(
    "获取股票最新交易日的价格数据。支持批量查询多只股票。",
    param_desc=[
        "股票代码，多个用逗号分隔，如 '600000,000001'",
    ],
)
def latest_price(codes: str) -> str:
    """获取股票最新价格

    Args:
        codes: 股票代码，多个用逗号分隔（如 "600000,000001"）
    """
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    df = _query.get_latest_price(code_list if code_list else None)
    if df.empty:
        return json.dumps({"error": "无数据"}, ensure_ascii=False)
    records = df.to_dict("records")
    for r in records:
        for k, v in r.items():
            if v is not None and hasattr(v, "isoformat"):
                r[k] = str(v)
            elif isinstance(v, float):
                r[k] = round(v, 4)
            elif hasattr(v, "item"):
                r[k] = v.item()
    return json.dumps({"results": records}, ensure_ascii=False, default=str)


@tool(
    "获取单只股票的实时行情（当前价、涨跌幅、成交量等）。支持多数据源自动故障切换（新浪、东财）。盘中可获取实时数据，盘后获取收盘数据。返回结果包含日期，注意与K线数据去重。",
    param_desc=[
        "6位股票代码",
    ],
)
def realtime_price(code: str) -> str:
    """获取单只股票的实时行情（盘中/盘后均可获取）。支持多数据源自动故障切换（新浪、东财）。分析股票时应配合K线数据使用，注意日期去重。

    Args:
        code: 股票代码（6位数字）
    """
    code = str(code).zfill(6)
    quote = _realtime_manager.fetch_quote(code)
    if quote is None:
        return json.dumps({"error": f"股票 {code} 无实时数据（可能代码错误或停牌）"}, ensure_ascii=False)
    return json.dumps(quote.to_dict(), ensure_ascii=False)


@tool(
    "批量获取多只股票的实时行情（当前价、涨跌幅、成交量等）。支持多数据源自动故障切换，比逐个调用 realtime_price 更高效。",
    param_desc=[
        "股票代码，多个用逗号分隔，如 '600000,000001'",
    ],
)
def realtime_prices(codes: str) -> str:
    """批量获取多只股票的实时行情。支持多数据源自动故障切换，比逐个调用 realtime_price 更高效。

    Args:
        codes: 股票代码，多个用逗号分隔（如 "600000,000001"）
    """
    code_list = [c.strip().zfill(6) for c in codes.split(",") if c.strip()]
    if not code_list:
        return json.dumps({"error": "未提供股票代码"}, ensure_ascii=False)

    quotes = _realtime_manager.fetch_quotes(code_list)
    if not quotes:
        return json.dumps({"error": "所有股票均无实时数据"}, ensure_ascii=False)

    results = [quotes[c].to_dict() for c in code_list if c in quotes]
    return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)


@tool("获取A股市场概览，包括数据库统计和最近交易日涨跌家数")
def market_overview() -> str:
    """获取市场概览统计信息"""
    stats = _query.get_data_stats()
    # 获取涨跌统计
    import duckdb

    conn = duckdb.connect(str(_query.db_path))
    today_stats = conn.execute("""
        WITH ranked AS (
            SELECT code, close, open,
                   ROW_NUMBER() OVER (PARTITION BY code ORDER BY trade_date DESC) AS rn
            FROM daily_kline
            WHERE trade_date >= current_date - INTERVAL '7' DAY
        )
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN close > open THEN 1 ELSE 0 END) AS up_count,
            SUM(CASE WHEN close < open THEN 1 ELSE 0 END) AS down_count,
            SUM(CASE WHEN close = open THEN 1 ELSE 0 END) AS flat_count
        FROM ranked WHERE rn = 1
    """).fetchone()
    conn.close()

    return json.dumps(
        {
            "数据库统计": {
                "股票数量": stats["stock_count"],
                "K线总行数": stats["kline_count"],
                "日期范围": f"{stats['date_range'][0]} ~ {stats['date_range'][1]}",
            },
            "最近交易日涨跌": {
                "总数": today_stats[0],
                "上涨": today_stats[1],
                "下跌": today_stats[2],
                "平盘": today_stats[3],
            },
        },
        ensure_ascii=False,
        default=str,
    )


@tool(
    "获取市场情绪综合分析，包括涨停数、跌停数、炸板数、连板高度、情绪评级和操作建议。用于判断市场整体情绪状态（高潮/发酵/启动/低迷/冰点）。",
    param_desc=[
        "交易日期 YYYYMMDD，默认今天",
    ],
)
def market_sentiment(trade_date: str = None) -> str:
    """获取市场情绪综合分析（涨停/跌停/炸板/连板/情绪评级）

    Args:
        trade_date: 交易日期 YYYYMMDD，默认今天
    """
    result = _get_market_sentiment(trade_date)
    return json.dumps(result, ensure_ascii=False, default=str)


@tool(
    "获取涨停股池，包含涨停股票的代码、名称、首次封板时间、连板数、所属行业等。用于分析市场热点方向和连板梯队。",
    param_desc=[
        "交易日期 YYYYMMDD，默认今天",
    ],
)
def limit_up_pool(trade_date: str = None) -> str:
    """获取今日涨停股池（代码、名称、涨停时间、连板数、所属行业）

    Args:
        trade_date: 交易日期 YYYYMMDD，默认今天
    """
    result = _get_limit_up_pool(trade_date)
    return json.dumps(result, ensure_ascii=False, default=str)


@tool(
    "获取行业板块或概念板块的涨跌排行，包含涨跌幅和主力净流入。用于发现当日市场热点板块和资金流向。",
    param_desc=[
        ("板块类型：industry=行业板块，concept=概念板块", {'enum': ["industry", "concept"]}),
        "返回前N个板块，默认10",
    ],
)
def sector_ranking(board_type: str = "industry", top_n: int = 10) -> str:
    """获取板块涨跌排行

    Args:
        board_type: "industry"=行业板块, "concept"=概念板块
        top_n: 返回前N个板块，默认10
    """
    result = _get_sector_ranking(board_type, top_n)
    return json.dumps(result, ensure_ascii=False, default=str)


@tool(
    "获取个股近5日资金流向（主力净流入/流出趋势），判断资金是连续流入还是流出。用于辅助判断主力动向。",
    param_desc=[
        "6位股票代码",
    ],
)
def stock_fund_flow(code: str) -> str:
    """获取个股资金流向（近5日主力净流入趋势）

    Args:
        code: 6位股票代码
    """
    result = _get_stock_fund_flow(code)
    return json.dumps(result, ensure_ascii=False, default=str)


@tool(
    "识别股票的K线形态（十字星、锤子线、射击之星、大阳线、大阴线、看涨/看跌吞没、乌云盖顶、曙光初现、早晨/黄昏之星、三连阳/阴、红三兵、横盘整理、N字上攻等），返回形态名称、多空方向、置信度和说明。",
    param_desc=[
        "股票代码",
        "分析的交易日数量，默认60",
    ],
)
def recognize_kline_patterns(code: str, days: int = 60) -> str:
    """识别股票K线形态（十字星、锤子线、吞没、早晨之星等）

    Args:
        code: 股票代码
        days: 分析的交易日数量，默认60
    """
    end = date.today()
    start = end - timedelta(days=int(days) * 3)
    df = _query.get_daily_kline(code, start_date=str(start), end_date=str(end))
    if df.empty:
        return json.dumps({"error": f"股票 {code} 无数据"}, ensure_ascii=False)

    df = df.sort_values("trade_date").reset_index(drop=True)
    patterns = _recognize_patterns(df)
    formatted = _format_patterns(patterns)
    return json.dumps(
        {
            "code": code,
            "patterns": patterns,
            "summary": formatted,
        },
        ensure_ascii=False,
        default=str,
    )


@tool(
    "根据技术条件从全市场筛选股票。支持条件：MA均线比较（MA5>MA10）、RSI（RSI6<20）、MACD金叉/死叉、成交量（放量/缩量）、涨跌幅、多头排列等。返回满足条件的股票列表。",
    param_desc=[
        "筛选条件，如 'MA5>MA10', 'RSI6<20', 'MACD金叉', '放量', '涨幅>3%', '多头排列'",
        "最多返回数量，默认20",
    ],
)
def screen_stocks(conditions: str, limit: int = 20) -> str:
    """根据技术条件筛选股票（支持MA、RSI、MACD、成交量、涨跌幅等条件）

    Args:
        conditions: 筛选条件，如 "MA5>MA10", "RSI6<20", "MACD金叉", "放量", "涨幅>3%", "多头排列"
        limit: 最多返回数量，默认20
    """
    result = _screen_stocks(conditions, limit)
    return result


def _baostock_login():
    """登录BaoStock并返回source实例"""
    source = BaoStockSource()
    source._ensure_login()
    return source


@tool(
    "检查并更新单只股票的K线数据到最新。在分析某只股票前，应先调用此工具确保数据是最新的。只需几秒钟即可完成。",
    param_desc=[
        "6位股票代码",
        "最多回溯天数，默认5",
    ],
)
def update_stock(code: str, max_days: int = 5) -> str:
    """检查并更新单只股票的K线数据到最新。分析股票前应先调用此工具确保数据是最新的。

    Args:
        code: 股票代码（6位数字）
        max_days: 最多回溯天数，默认5天
    """
    conn = get_connection()
    init_tables(conn)
    end = date.today()
    start = end - timedelta(days=max_days)

    # 检查真实K线最新日期，避免被fetch_log中的空成功记录卡住
    last = get_latest_kline_date(conn, code, "baostock")
    if last:
        last_d = date.fromisoformat(last)
        if last_d >= end:
            conn.close()
            return json.dumps(
                {
                    "code": code,
                    "status": "already_latest",
                    "latest_date": str(last_d),
                    "message": f"股票{code}数据已是最新（{last_d}）",
                },
                ensure_ascii=False,
            )
        actual_start = last_d + timedelta(days=1)
    else:
        actual_start = start

    # 增量采集
    try:
        source = _baostock_login()
        df = fetch_kline_with_timeout(
            source, code, actual_start, end, timeout=30)
        if df is not None and not df.empty:
            records = df.to_dict("records")
            count = insert_daily_kline(conn, records)
            log_fetch(conn, code, "baostock",
                      actual_start, end, count, "success")
        else:
            count = 0
            log_fetch(conn, code, "baostock", actual_start, end, 0, "success")
        try:
            source._logout()
        except Exception:
            pass
        latest = get_latest_kline_date(conn, code, "baostock")
        conn.close()
        return json.dumps(
            {
                "code": code,
                "status": "updated",
                "from_date": str(actual_start),
                "to_date": str(end),
                "latest_date": latest,
                "new_rows": count,
                "message": f"股票{code}已更新：{actual_start}~{end}，新增{count}行",
            },
            ensure_ascii=False,
        )
    except Exception as e:
        conn.close()
        return json.dumps({"code": code, "status": "error", "message": f"更新失败: {e}"}, ensure_ascii=False)


@tool(
    "更新全部沪深主板股票的K线数据到最新。当用户要求更新完整数据库时调用。耗时较长（约10-15分钟），调用前应告知用户。",
    param_desc=[
        "最多回溯天数，默认5",
    ],
)
def update_all_stocks(max_days: int = 5) -> str:
    """更新全部股票的K线数据。当用户要求更新完整数据库时调用。

    Args:
        max_days: 最多回溯天数，默认5天
    """
    conn = get_connection()
    init_tables(conn)
    codes = get_stock_list(conn)
    if not codes:
        conn.close()
        return json.dumps({"error": "股票列表为空，请先采集股票列表"}, ensure_ascii=False)

    end = date.today()
    start = end - timedelta(days=max_days)
    source = _baostock_login()

    stats = {"total": len(codes), "success": 0,
             "failed": 0, "rows": 0, "skipped": 0}

    for i, code in enumerate(codes):
        last = get_latest_kline_date(conn, code, "baostock")
        if last:
            last_d = date.fromisoformat(last)
            if last_d >= end:
                stats["skipped"] += 1
                continue
            actual_start = last_d + timedelta(days=1)
        else:
            actual_start = start

        try:
            df = fetch_kline_with_timeout(
                source, code, actual_start, end, timeout=30)
            if df is not None and not df.empty:
                records = df.to_dict("records")
                count = insert_daily_kline(conn, records)
                log_fetch(conn, code, "baostock",
                          actual_start, end, count, "success")
                stats["rows"] += count
            else:
                log_fetch(conn, code, "baostock",
                          actual_start, end, 0, "success")
            stats["success"] += 1
        except Exception as e:
            stats["failed"] += 1
            log_fetch(conn, code, "baostock", actual_start,
                      end, 0, "failed", str(e))
            try:
                source._logout()
            except Exception:
                pass
            time.sleep(1)
            source = _baostock_login()

        # 定期重连（每300只）
        if (i + 1) % 300 == 0:
            try:
                source._logout()
            except Exception:
                pass
            time.sleep(1)
            source = _baostock_login()

        time.sleep(0.1)

    try:
        source._logout()
    except Exception:
        pass
    conn.close()

    return json.dumps(
        {
            "status": "completed",
            "total": stats["total"],
            "success": stats["success"],
            "skipped": stats["skipped"],
            "failed": stats["failed"],
            "new_rows": stats["rows"],
            "message": (
                f"全量更新完成：共{stats['total']}只，"
                f"更新{stats['success']}只，跳过{stats['skipped']}只，"
                f"失败{stats['failed']}只，新增{stats['rows']}行"
            ),
        },
        ensure_ascii=False,
    )


@tool(
    "计算股票的技术指标，包括MA均线、MACD、RSI、KDJ、布林带等，并给出量价分析和信号判断。短线分析的核心工具。注意：天数指交易日（剔除周末和法定节假日），非自然日。",
    param_desc=[
        "6位股票代码",
        "计算数据天数（交易日，非自然日），默认120",
        "要计算的指标，逗号分隔。可选: ma,macd,rsi,kdj,boll。默认全部计算",
    ],
)
def calc_technical(code: str, days: int = 120, indicators: str = "ma,macd,rsi") -> str:
    """计算股票技术指标

    Args:
        code: 股票代码
        days: 计算天数（交易日），默认120。注意：这里的天数指交易日（剔除周末和法定节假日），非自然日。
        indicators: 要计算的指标，逗号分隔。可选: ma,macd,rsi,kdj,boll
    """
    end = date.today()
    start = end - timedelta(days=int(days) * 3)
    df = _query.get_daily_kline(code, start_date=str(start), end_date=str(end))
    if df.empty:
        return json.dumps({"error": f"股票 {code} 无数据"}, ensure_ascii=False)

    df = df.sort_values("trade_date").reset_index(drop=True)
    indicator_list = [i.strip().lower() for i in indicators.split(",")]
    result = {"code": code, "count": len(df)}

    if "ma" in indicator_list:
        for w in [5, 10, 20, 60]:
            df[f"ma{w}"] = df["close"].rolling(window=w).mean()
        # 取最新数据
        latest = df.iloc[-1]
        result["ma"] = {
            "date": str(latest["trade_date"]),
            "close": round(float(latest["close"]), 2),
            "ma5": round(float(latest["ma5"]), 2) if latest["ma5"] == latest["ma5"] else None,
            "ma10": round(float(latest["ma10"]), 2) if latest["ma10"] == latest["ma10"] else None,
            "ma20": round(float(latest["ma20"]), 2) if latest["ma20"] == latest["ma20"] else None,
            "ma60": round(float(latest["ma60"]), 2) if latest["ma60"] == latest["ma60"] else None,
        }
        # MA 排列状态
        ma_values = [result["ma"].get(f"ma{w}") for w in [5, 10, 20, 60]]
        ma_values = [v for v in ma_values if v is not None]
        if len(ma_values) == 4:
            if ma_values == sorted(ma_values, reverse=True):
                result["ma"]["排列"] = "多头排列（强势）"
            elif ma_values == sorted(ma_values):
                result["ma"]["排列"] = "空头排列（弱势）"
            else:
                result["ma"]["排列"] = "交叉整理"

    if "macd" in indicator_list:
        ema12 = df["close"].ewm(span=12).mean()
        ema26 = df["close"].ewm(span=26).mean()
        df["dif"] = ema12 - ema26
        df["dea"] = df["dif"].ewm(span=9).mean()
        df["macd_hist"] = (df["dif"] - df["dea"]) * 2
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        result["macd"] = {
            "date": str(latest["trade_date"]),
            "dif": round(float(latest["dif"]), 4),
            "dea": round(float(latest["dea"]), 4),
            "macd": round(float(latest["macd_hist"]), 4),
            "状态": "金叉看多"
            if latest["dif"] > latest["dea"] and prev["dif"] <= prev["dea"]
            else "死叉看空"
            if latest["dif"] < latest["dea"] and prev["dif"] >= prev["dea"]
            else "多头"
            if latest["dif"] > latest["dea"]
            else "空头",
        }

    if "rsi" in indicator_list:
        for period in [6, 12, 24]:
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            df[f"rsi{period}"] = 100 - (100 / (1 + gain / loss))
        latest = df.iloc[-1]
        rsi6 = float(
            latest["rsi6"]) if latest["rsi6"] == latest["rsi6"] else None
        result["rsi"] = {
            "date": str(latest["trade_date"]),
            "rsi6": round(rsi6, 2) if rsi6 else None,
            "rsi12": round(float(latest["rsi12"]), 2) if latest["rsi12"] == latest["rsi12"] else None,
            "rsi24": round(float(latest["rsi24"]), 2) if latest["rsi24"] == latest["rsi24"] else None,
        }
        if rsi6:
            if rsi6 > 80:
                result["rsi"]["信号"] = "超买区间，注意回调风险"
            elif rsi6 < 20:
                result["rsi"]["信号"] = "超卖区间，可能有反弹机会"
            elif rsi6 > 50:
                result["rsi"]["信号"] = "偏强"
            else:
                result["rsi"]["信号"] = "偏弱"

    if "kdj" in indicator_list:
        low_min = df["low"].rolling(9).min()
        high_max = df["high"].rolling(9).max()
        rsv = (df["close"] - low_min) / (high_max - low_min) * 100
        df["k"] = rsv.ewm(com=2).mean()
        df["d"] = df["k"].ewm(com=2).mean()
        df["j"] = 3 * df["k"] - 2 * df["d"]
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else latest
        result["kdj"] = {
            "date": str(latest["trade_date"]),
            "k": round(float(latest["k"]), 2) if latest["k"] == latest["k"] else None,
            "d": round(float(latest["d"]), 2) if latest["d"] == latest["d"] else None,
            "j": round(float(latest["j"]), 2) if latest["j"] == latest["j"] else None,
        }
        k, d, j = latest["k"], latest["d"], latest["j"]
        if k == k and d == d and j == j:
            if j > 100:
                result["kdj"]["信号"] = "超买，短期可能回调"
            elif j < 0:
                result["kdj"]["信号"] = "超卖，短期可能反弹"
            elif k > d and prev["k"] <= prev["d"]:
                result["kdj"]["信号"] = "金叉，看多"
            elif k < d and prev["k"] >= prev["d"]:
                result["kdj"]["信号"] = "死叉，看空"

    if "boll" in indicator_list:
        df["boll_mid"] = df["close"].rolling(20).mean()
        std = df["close"].rolling(20).std()
        df["boll_upper"] = df["boll_mid"] + 2 * std
        df["boll_lower"] = df["boll_mid"] - 2 * std
        latest = df.iloc[-1]
        close = float(latest["close"])
        mid = float(
            latest["boll_mid"]) if latest["boll_mid"] == latest["boll_mid"] else None
        upper = float(
            latest["boll_upper"]) if latest["boll_upper"] == latest["boll_upper"] else None
        lower = float(
            latest["boll_lower"]) if latest["boll_lower"] == latest["boll_lower"] else None
        result["boll"] = {
            "date": str(latest["trade_date"]),
            "upper": round(upper, 2) if upper else None,
            "mid": round(mid, 2) if mid else None,
            "lower": round(lower, 2) if lower else None,
            "close": round(close, 2),
        }
        if mid and upper and lower:
            width = (upper - lower) / mid * 100
            result["boll"]["带宽"] = f"{width:.2f}%"
            if close > upper:
                result["boll"]["信号"] = "突破上轨，可能超买"
            elif close < lower:
                result["boll"]["信号"] = "跌破下轨，可能超卖"
            elif close > mid:
                result["boll"]["信号"] = "中轨上方，偏强"
            else:
                result["boll"]["信号"] = "中轨下方，偏弱"

    # 量价分析
    if len(df) >= 5:
        recent = df.tail(5)
        vol_avg = recent["volume"].mean()
        vol_prev = df.tail(10).head(
            5)["volume"].mean() if len(df) >= 10 else vol_avg
        price_change = (
            (float(df.iloc[-1]["close"]) - float(df.iloc[-2]
             ["close"])) / float(df.iloc[-2]["close"]) * 100
            if len(df) > 1
            else 0
        )
        vol_change = (vol_avg - vol_prev) / vol_prev * \
            100 if vol_prev > 0 else 0
        result["量价分析"] = {
            "近5日均量": int(vol_avg),
            "前5日均量": int(vol_prev),
            "量变化": f"{vol_change:+.1f}%",
            "今日涨跌": f"{price_change:+.2f}%",
            "判断": "放量上涨"
            if vol_change > 20 and price_change > 0
            else "放量下跌"
            if vol_change > 20 and price_change < 0
            else "缩量上涨"
            if vol_change < -20 and price_change > 0
            else "缩量下跌"
            if vol_change < -20 and price_change < 0
            else "量价平稳",
        }

    return json.dumps(result, ensure_ascii=False, default=str)


@tool(
    "搜索财经新闻热点。可用于搜索个股相关新闻、行业新闻或宏观财经消息。数据来源为百度新闻。",
    param_desc=[
        "搜索关键词，如股票名称、行业、宏观事件等",
        "返回条数，默认8",
    ],
)
def search_news(query: str, num: int = 8) -> str:
    """搜索财经新闻热点。可搜索个股新闻或市场整体热点。

    Args:
        query: 搜索关键词。可以是股票名称/代码（如"平安银行"、"600000"），
               也可以是财经热点（如"A股"、"半导体"、"降息"）
        num: 返回条数，默认8
    """
    results = search_baidu_news(query, num=int(num))
    if not results:
        return json.dumps({"query": query, "results": [], "message": "未搜索到相关新闻"}, ensure_ascii=False)
    if "error" in results[0]:
        return json.dumps({"query": query, "error": results[0]["error"]}, ensure_ascii=False)
    return json.dumps({"query": query, "count": len(results), "results": results}, ensure_ascii=False)


@tool(
    "搜索某只股票的相关新闻，综合百度新闻和东方财富快讯多源搜索，结果更全面。",
    param_desc=[
        "股票代码或名称，如'000001'或'平安银行'",
        "返回条数，默认8",
    ],
)
def stock_news(code_or_name: str, num: int = 8) -> str:
    """搜索某只股票的相关新闻，综合百度新闻和东方财富快讯。

    Args:
        code_or_name: 股票代码或名称（如"000001"或"平安银行"）
        num: 返回条数，默认8
    """
    result = search_stock_news(code_or_name, num=int(num))
    return json.dumps(result, ensure_ascii=False)


@tool(
    "获取A股市场今日热点新闻，综合东方财富、新浪财经、百度新闻多源聚合。适合了解市场整体动态。",
    param_desc=[
        "每个源返回条数，默认10",
    ],
)
def market_hot_news(num: int = 10) -> str:
    """获取A股市场今日热点新闻，综合东方财富、新浪财经、百度新闻多源。

    Args:
        num: 每个源返回条数，默认10
    """
    result = search_market_hot_news(num=int(num))
    return json.dumps(result, ensure_ascii=False)

