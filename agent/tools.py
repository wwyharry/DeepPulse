"""Agent 工具函数定义 - 供 LLM tool-calling 使用"""
import json
import sys
import time
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.query import StockQuery
from src.database import (
    get_connection, init_tables, upsert_stock_info,
    insert_daily_kline, log_fetch, get_latest_kline_date, get_stock_list,
)
from src.sources import BaoStockSource
from src.collector import fetch_kline_with_timeout
from agent.news import search_stock_news, search_market_hot_news, search_baidu_news
from agent.market import get_market_sentiment as _get_market_sentiment, get_limit_up_pool as _get_limit_up_pool, get_sector_ranking as _get_sector_ranking, get_stock_fund_flow as _get_stock_fund_flow
from agent.patterns import recognize_patterns as _recognize_patterns, format_patterns as _format_patterns
from agent.screener import screen_stocks as _screen_stocks
from src.realtime import RealtimeQuoteManager
import config as _config

_query = StockQuery()

# 实时行情管理器（多源冗余）
_realtime_manager = RealtimeQuoteManager(
    priority=_config.REALTIME_SOURCES,
    timeout=_config.REALTIME_TIMEOUT,
)

# 记忆系统
from agent.memory import MemoryManager
try:
    from agent.client import load_setting as _load_setting
    _setting = _load_setting()
except Exception:
    _setting = None
_memory = MemoryManager(setting=_setting)
_memory.init_tables()

# 战法系统
from agent.strategy_loader import get_strategy_loader
_strategy_loader = get_strategy_loader()

# ============ 工具函数实现 ============


def search_stock(keyword: str) -> str:
    """按名称或代码搜索股票"""
    df = _query.search_stocks(keyword)
    if df.empty:
        return json.dumps({"error": f"未找到匹配 '{keyword}' 的股票"}, ensure_ascii=False)
    results = df[["code", "name", "market", "list_date"]].head(20).to_dict("records")
    for r in results:
        r["list_date"] = str(r["list_date"]) if r["list_date"] else None
    return json.dumps({"results": results, "total": len(df)}, ensure_ascii=False)


def query_stock_info(code: str) -> str:
    """查询单只股票的基本信息"""
    df = _query.get_stock_info(code)
    if df.empty:
        return json.dumps({"error": f"未找到股票 {code}"}, ensure_ascii=False)
    row = df.iloc[0].to_dict()
    for k, v in row.items():
        if v is not None and hasattr(v, 'isoformat'):
            row[k] = str(v)
    return json.dumps(row, ensure_ascii=False, default=str)


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
    df = _query.get_daily_kline(code, start_date=str(start), end_date=str(end),
                                source=source, limit=int(days))
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
    return json.dumps({"code": code, "count": len(records), "kline": records},
                      ensure_ascii=False)


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
            if v is not None and hasattr(v, 'isoformat'):
                r[k] = str(v)
            elif isinstance(v, float):
                r[k] = round(v, 4)
            elif hasattr(v, 'item'):
                r[k] = v.item()
    return json.dumps({"results": records}, ensure_ascii=False, default=str)


def realtime_price(code: str) -> str:
    """获取单只股票的实时行情（盘中/盘后均可获取）。支持多数据源自动故障切换（新浪、东财）。分析股票时应配合K线数据使用，注意日期去重。

    Args:
        code: 股票代码（6位数字）
    """
    code = str(code).zfill(6)
    quote = _realtime_manager.fetch_quote(code)
    if quote is None:
        return json.dumps({"error": f"股票 {code} 无实时数据（可能代码错误或停牌）"},
                          ensure_ascii=False)
    return json.dumps(quote.to_dict(), ensure_ascii=False)


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

    return json.dumps({
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
        }
    }, ensure_ascii=False, default=str)


def market_sentiment(trade_date: str = None) -> str:
    """获取市场情绪综合分析（涨停/跌停/炸板/连板/情绪评级）

    Args:
        trade_date: 交易日期 YYYYMMDD，默认今天
    """
    result = _get_market_sentiment(trade_date)
    return json.dumps(result, ensure_ascii=False, default=str)


def limit_up_pool(trade_date: str = None) -> str:
    """获取今日涨停股池（代码、名称、涨停时间、连板数、所属行业）

    Args:
        trade_date: 交易日期 YYYYMMDD，默认今天
    """
    result = _get_limit_up_pool(trade_date)
    return json.dumps(result, ensure_ascii=False, default=str)


def sector_ranking(board_type: str = "industry", top_n: int = 10) -> str:
    """获取板块涨跌排行

    Args:
        board_type: "industry"=行业板块, "concept"=概念板块
        top_n: 返回前N个板块，默认10
    """
    result = _get_sector_ranking(board_type, top_n)
    return json.dumps(result, ensure_ascii=False, default=str)


def stock_fund_flow(code: str) -> str:
    """获取个股资金流向（近5日主力净流入趋势）

    Args:
        code: 6位股票代码
    """
    result = _get_stock_fund_flow(code)
    return json.dumps(result, ensure_ascii=False, default=str)


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
    return json.dumps({
        "code": code,
        "patterns": patterns,
        "summary": formatted,
    }, ensure_ascii=False, default=str)


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
            return json.dumps({
                "code": code,
                "status": "already_latest",
                "latest_date": str(last_d),
                "message": f"股票{code}数据已是最新（{last_d}）"
            }, ensure_ascii=False)
        actual_start = last_d + timedelta(days=1)
    else:
        actual_start = start

    # 增量采集
    try:
        source = _baostock_login()
        df = fetch_kline_with_timeout(source, code, actual_start, end, timeout=30)
        if df is not None and not df.empty:
            records = df.to_dict("records")
            count = insert_daily_kline(conn, records)
            log_fetch(conn, code, "baostock", actual_start, end, count, "success")
        else:
            count = 0
            log_fetch(conn, code, "baostock", actual_start, end, 0, "success")
        try:
            source._logout()
        except Exception:
            pass
        latest = get_latest_kline_date(conn, code, "baostock")
        conn.close()
        return json.dumps({
            "code": code,
            "status": "updated",
            "from_date": str(actual_start),
            "to_date": str(end),
            "latest_date": latest,
            "new_rows": count,
            "message": f"股票{code}已更新：{actual_start}~{end}，新增{count}行"
        }, ensure_ascii=False)
    except Exception as e:
        conn.close()
        return json.dumps({
            "code": code,
            "status": "error",
            "message": f"更新失败: {e}"
        }, ensure_ascii=False)


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

    stats = {"total": len(codes), "success": 0, "failed": 0, "rows": 0, "skipped": 0}

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
            df = fetch_kline_with_timeout(source, code, actual_start, end, timeout=30)
            if df is not None and not df.empty:
                records = df.to_dict("records")
                count = insert_daily_kline(conn, records)
                log_fetch(conn, code, "baostock", actual_start, end, count, "success")
                stats["rows"] += count
            else:
                log_fetch(conn, code, "baostock", actual_start, end, 0, "success")
            stats["success"] += 1
        except Exception as e:
            stats["failed"] += 1
            log_fetch(conn, code, "baostock", actual_start, end, 0, "failed", str(e))
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

    return json.dumps({
        "status": "completed",
        "total": stats["total"],
        "success": stats["success"],
        "skipped": stats["skipped"],
        "failed": stats["failed"],
        "new_rows": stats["rows"],
        "message": (f"全量更新完成：共{stats['total']}只，"
                    f"更新{stats['success']}只，跳过{stats['skipped']}只，"
                    f"失败{stats['failed']}只，新增{stats['rows']}行")
    }, ensure_ascii=False)


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
            "状态": "金叉看多" if latest["dif"] > latest["dea"] and prev["dif"] <= prev["dea"]
                     else "死叉看空" if latest["dif"] < latest["dea"] and prev["dif"] >= prev["dea"]
                     else "多头" if latest["dif"] > latest["dea"] else "空头",
        }

    if "rsi" in indicator_list:
        for period in [6, 12, 24]:
            delta = df["close"].diff()
            gain = delta.where(delta > 0, 0).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            df[f"rsi{period}"] = 100 - (100 / (1 + gain / loss))
        latest = df.iloc[-1]
        rsi6 = float(latest["rsi6"]) if latest["rsi6"] == latest["rsi6"] else None
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
        mid = float(latest["boll_mid"]) if latest["boll_mid"] == latest["boll_mid"] else None
        upper = float(latest["boll_upper"]) if latest["boll_upper"] == latest["boll_upper"] else None
        lower = float(latest["boll_lower"]) if latest["boll_lower"] == latest["boll_lower"] else None
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
        vol_prev = df.tail(10).head(5)["volume"].mean() if len(df) >= 10 else vol_avg
        price_change = (float(df.iloc[-1]["close"]) - float(df.iloc[-2]["close"])) / float(df.iloc[-2]["close"]) * 100 if len(df) > 1 else 0
        vol_change = (vol_avg - vol_prev) / vol_prev * 100 if vol_prev > 0 else 0
        result["量价分析"] = {
            "近5日均量": int(vol_avg),
            "前5日均量": int(vol_prev),
            "量变化": f"{vol_change:+.1f}%",
            "今日涨跌": f"{price_change:+.2f}%",
            "判断": "放量上涨" if vol_change > 20 and price_change > 0
                    else "放量下跌" if vol_change > 20 and price_change < 0
                    else "缩量上涨" if vol_change < -20 and price_change > 0
                    else "缩量下跌" if vol_change < -20 and price_change < 0
                    else "量价平稳"
        }

    return json.dumps(result, ensure_ascii=False, default=str)


def search_news(query: str, num: int = 8) -> str:
    """搜索财经新闻热点。可搜索个股新闻或市场整体热点。

    Args:
        query: 搜索关键词。可以是股票名称/代码（如"平安银行"、"600000"），
               也可以是财经热点（如"A股"、"半导体"、"降息"）
        num: 返回条数，默认8
    """
    results = search_baidu_news(query, num=int(num))
    if not results:
        return json.dumps({"query": query, "results": [], "message": "未搜索到相关新闻"},
                          ensure_ascii=False)
    if "error" in results[0]:
        return json.dumps({"query": query, "error": results[0]["error"]},
                          ensure_ascii=False)
    return json.dumps({"query": query, "count": len(results), "results": results},
                      ensure_ascii=False)


def stock_news(code_or_name: str, num: int = 8) -> str:
    """搜索某只股票的相关新闻，综合百度新闻和东方财富快讯。

    Args:
        code_or_name: 股票代码或名称（如"000001"或"平安银行"）
        num: 返回条数，默认8
    """
    result = search_stock_news(code_or_name, num=int(num))
    return json.dumps(result, ensure_ascii=False)


def market_hot_news(num: int = 10) -> str:
    """获取A股市场今日热点新闻，综合东方财富、新浪财经、百度新闻多源。

    Args:
        num: 每个源返回条数，默认10
    """
    result = search_market_hot_news(num=int(num))
    return json.dumps(result, ensure_ascii=False)


# ============ 记忆系统工具 ============


def save_memory(content: str, memory_type: str = "insight",
                keywords: str = "", tags: str = "",
                importance: float = 0.5,
                learned_what: str = "", learned_why: str = "",
                apply_when: str = "") -> str:
    """保存一条长期记忆，供未来分析时参考。

    Args:
        content: 记忆内容（分析结论、用户偏好、市场事实等）
        memory_type: 记忆类型 - 'preference'(用户偏好), 'insight'(分析结论), 'fact'(事实记录), 'context'(上下文线索), 'summary'(会话摘要), 'learning'(用户教学/纠错)
        keywords: 逗号分隔的关键词，用于检索（如 '600519,贵州茅台,支撑位'）
        tags: 逗号分隔的标签（如 '技术分析,短线,白酒'）
        importance: 重要度 0.0-1.0，默认0.5。越重要的记忆越不容易被遗忘
        learned_what: 学到了什么（用于learning类型，简明扼要）
        learned_why: 为什么重要（用于learning类型）
        apply_when: 什么情况下应用这个知识（用于learning类型）
    """
    return _memory.save_memory(content, memory_type, keywords, tags, importance,
                               learned_what=learned_what, learned_why=learned_why,
                               apply_when=apply_when)


def search_memory(query: str, memory_type: str = "",
                  top_k: int = 5) -> str:
    """搜索长期记忆。在开始新的分析前，先搜索是否有相关的历史记忆可以帮助分析。

    Args:
        query: 搜索关键词，可以包含股票代码、名称或主题词
        memory_type: 按类型筛选（可选）: preference/insight/fact/context/summary
        top_k: 返回条数，默认5
    """
    return _memory.search_memories(query, memory_type, int(top_k))


def update_memory(memory_id: str, content: str = "",
                  importance: float = -1, tags: str = "") -> str:
    """更新已有记忆的内容、重要度或标签。

    Args:
        memory_id: 要更新的记忆ID
        content: 新内容（可选，不填则保持原内容）
        importance: 新的重要度（可选，-1表示保持原值）
        tags: 新的标签（可选，逗号分隔）
    """
    return _memory.update_memory(memory_id, content, float(importance), tags)


def delete_memory(memory_id: str) -> str:
    """删除一条记忆（软删除，标记为归档）。

    Args:
        memory_id: 要删除的记忆ID
    """
    return _memory.delete_memory(memory_id)


def list_memories(memory_type: str = "", limit: int = 20) -> str:
    """列出已保存的长期记忆。

    Args:
        memory_type: 按类型筛选（可选）
        limit: 最多返回条数，默认20
    """
    return _memory.list_memories(memory_type, int(limit))


def save_session_context(stock_code: str = "", topic: str = "",
                         notes: str = "") -> str:
    """保存当前会话的上下文信息，用于跨会话的分析延续。

    Args:
        stock_code: 当前正在分析的股票代码
        topic: 当前分析主题
        notes: 临时笔记
    """
    content_parts = []
    if stock_code:
        content_parts.append(f"股票: {stock_code}")
    if topic:
        content_parts.append(f"主题: {topic}")
    if notes:
        content_parts.append(f"笔记: {notes}")
    content = " | ".join(content_parts) if content_parts else "空上下文"
    keywords = stock_code if stock_code else ""
    return _memory.save_memory(content, "context", keywords, "会话上下文", 0.3)


# ============ 短线战法工具 ============


def search_strategy(query: str, top_k: int = 3) -> str:
    """搜索匹配当前分析场景的短线战法。在分析股票时调用此工具查找适用的战法策略。

    Args:
        query: 描述当前分析场景，包含技术指标状态、市场信号等。
               例如 'RSI超卖+缩量下跌+均线支撑' 或 '放量突破MA20'
        top_k: 返回最匹配的战法数量，默认3
    """
    results = _strategy_loader.search(query, int(top_k))
    if not results:
        return json.dumps({"query": query, "results": [], "message": "未找到匹配的战法"},
                          ensure_ascii=False)
    return json.dumps({"query": query, "count": len(results), "results": results},
                      ensure_ascii=False)


def list_strategies() -> str:
    """列出所有可用的短线战法。用于了解当前系统中有哪些战法策略。"""
    strategies = _strategy_loader.list_strategies()
    return json.dumps({
        "total": len(strategies),
        "strategies": strategies,
    }, ensure_ascii=False)


def get_strategy(filename: str) -> str:
    """获取指定战法的完整内容。

    Args:
        filename: 战法文件名（如 '01-放量突破战法.md'）
    """
    content = _strategy_loader.get_strategy_content(filename)
    if not content:
        return json.dumps({"error": f"未找到战法文件: {filename}"}, ensure_ascii=False)
    return json.dumps({"filename": filename, "content": content}, ensure_ascii=False)


# ============ 预测跟踪工具 ============


def save_prediction(stock_code: str, direction: str,
                    stock_name: str = "", prediction_type: str = "direction",
                    target_price: float = 0, stop_loss: float = 0,
                    timeframe_days: int = 5, reasoning: str = "",
                    confidence: float = 0.5) -> str:
    """保存一条投资预测，用于后续验证准确率并从对错中学习。在给出分析结论时应调用此工具。

    Args:
        stock_code: 6位股票代码
        direction: 预测方向 - 'bullish'(看涨)/'bearish'(看跌)/'neutral'(中性)
        stock_name: 股票名称（可选）
        prediction_type: 预测类型 - 'direction'(方向)/'price_range'(价位)/'pattern'(形态)
        target_price: 目标价位（可选）
        stop_loss: 止损价位（可选）
        timeframe_days: 预测有效天数，默认5
        reasoning: 预测理由（简述关键依据）
        confidence: 置信度 0.0-1.0
    """
    # 收集关联的记忆ID
    memory_ids = _memory._session_new_memory_ids[-3:] if _memory._session_new_memory_ids else []
    return _memory.prediction_tracker.save_prediction(
        stock_code=stock_code, stock_name=stock_name,
        prediction_type=prediction_type, direction=direction,
        target_price=target_price, stop_loss=stop_loss,
        timeframe_days=timeframe_days, reasoning=reasoning,
        confidence=confidence, memory_ids=memory_ids,
    )


def check_predictions(stock_code: str = "") -> str:
    """检查待验证的预测。分析某只股票时会自动调用，也可手动查看所有到期预测。

    Args:
        stock_code: 股票代码（可选，不填则检查所有到期预测）
    """
    return _memory.prediction_tracker.check_predictions(stock_code if stock_code else None)


def verify_prediction(prediction_id: str, actual_price: float,
                      actual_return_pct: float = 0) -> str:
    """验证预测结果，更新准确率并自动调整关联记忆的重要性。

    Args:
        prediction_id: 预测ID
        actual_price: 实际价格
        actual_return_pct: 实际收益率（百分比，如 5.2 表示涨5.2%）
    """
    return _memory.prediction_tracker.verify_prediction(
        prediction_id, actual_price, actual_return_pct
    )


def prediction_stats() -> str:
    """查看预测准确率统计，包括总验证数、正确/错误数、按方向分类统计。"""
    return _memory.prediction_tracker.get_accuracy_stats()


# ============ 用户画像工具 ============


def get_user_profile() -> str:
    """获取用户交易画像，包括交易风格、风险偏好、关注板块等。"""
    return _memory.user_profile.get_profile()


def update_user_profile(key: str, value: str, confidence: float = 0.5) -> str:
    """更新用户画像信息。

    Args:
        key: 画像维度 - 'trading_style'(交易风格)/'risk_tolerance'(风险偏好)/'preferred_indicators'(偏好指标)/'watched_sectors'(关注板块)/'stop_loss_habit'(止损习惯)/'position_sizing'(仓位习惯)
        value: 对应的值
        confidence: 置信度 0.0-1.0，默认0.5
    """
    return _memory.user_profile.update_profile(key, value, confidence)


# ============ 学习记录工具 ============


def record_learning(learned_what: str, learned_why: str, apply_when: str,
                    category: str = "user_correction",
                    related_indicators: str = "", related_stocks: str = "",
                    importance: float = 0.8) -> str:
    """显式记录一条学习知识。当检测到用户教学或纠错时调用此工具。

    Args:
        learned_what: 学到了什么（简明扼要）
        learned_why: 为什么这个知识重要
        apply_when: 什么情况下应该应用这个知识
        category: 学习类别 - 'indicator_usage'(指标用法)/'risk_management'(风险管理)/'market_pattern'(市场规律)/'user_correction'(用户纠错)/'trading_technique'(交易技巧)
        related_indicators: 相关技术指标（逗号分隔）
        related_stocks: 相关股票代码（逗号分隔）
        importance: 重要度 0.0-1.0，默认0.8
    """
    content = f"学到: {learned_what}"
    if related_indicators:
        content += f" | 相关指标: {related_indicators}"
    if related_stocks:
        content += f" | 相关股票: {related_stocks}"

    keywords = ",".join(filter(None, [related_indicators, related_stocks, category]))
    tags = f"学习,{category}"

    return _memory.save_memory(
        content=content,
        memory_type="learning",
        keywords=keywords,
        tags=tags,
        importance=importance,
        learned_what=learned_what,
        learned_why=learned_why,
        apply_when=apply_when,
    )


# ============ 知识图谱工具 ============


def query_knowledge(entity_name: str = "", entity_type: str = "",
                    relation_type: str = "") -> str:
    """查询知识图谱中的实体和关系。用于查找技术指标、战法、股票之间的关联关系。

    Args:
        entity_name: 实体名称（模糊匹配，可选）
        entity_type: 实体类型 - 'stock'/'indicator'/'pattern'/'strategy'/'concept'/'rule'（可选）
        relation_type: 关系类型 - 'triggers'/'requires'/'contradicts'/'supports'/'applies_to'/'user_prefers'（可选）
    """
    return _memory.knowledge_graph.query_related(
        entity_name if entity_name else None,
        entity_type if entity_type else None,
        relation_type if relation_type else None,
    )


def add_knowledge(source_name: str, source_type: str,
                  target_name: str, target_type: str,
                  relation_type: str, weight: float = 1.0) -> str:
    """向知识图谱添加一条关系。当发现技术指标、战法、市场规律之间的关联时调用。

    Args:
        source_name: 源实体名称（如 'RSI超卖'）
        source_type: 源实体类型（如 'indicator'）
        target_name: 目标实体名称（如 '低吸战法'）
        target_type: 目标实体类型（如 'strategy'）
        relation_type: 关系类型 - 'triggers'(触发)/'requires'(需要)/'contradicts'(矛盾)/'supports'(支持)/'applies_to'(适用于)/'user_prefers'(用户偏好)
        weight: 关系权重 0.0-1.0，默认1.0
    """
    source_id = _memory.knowledge_graph.add_entity(source_type, source_name)
    target_id = _memory.knowledge_graph.add_entity(target_type, target_name)
    rel_id = _memory.knowledge_graph.add_relation(source_id, target_id, relation_type, weight)
    return json.dumps({
        "status": "added",
        "source": f"{source_name}({source_type})",
        "relation": relation_type,
        "target": f"{target_name}({target_type})",
        "relation_id": rel_id,
    }, ensure_ascii=False)


# ============ 多周期数据工具 ============


def update_timeframe_data(code: str, timeframe: str = "5m", days: int = 5) -> str:
    """更新股票的多周期K线数据（分钟级/周线）。

    Args:
        code: 股票代码
        timeframe: 周期 - 1m(1分钟)/5m(5分钟)/15m(15分钟)/30m(30分钟)/60m(60分钟)/weekly(周线)
        days: 分钟数据天数，默认5
    """
    from agent.timeframes import update_timeframe
    result = update_timeframe(code, timeframe, int(days))
    return json.dumps(result, ensure_ascii=False, default=str)


def query_timeframe_kline(code: str, timeframe: str = "5m", limit: int = 100) -> str:
    """查询股票多周期K线数据。

    Args:
        code: 股票代码
        timeframe: 周期 - 1m/5m/15m/30m/60m/weekly
        limit: 返回条数，默认100
    """
    from agent.timeframes import query_timeframe
    result = query_timeframe(code, timeframe, int(limit))
    return json.dumps(result, ensure_ascii=False, default=str)


def multi_timeframe_analysis(code: str, limit: int = 50) -> str:
    """获取多周期K线数据（日线+60分钟+15分钟），用于多周期共振分析。

    Args:
        code: 股票代码
        limit: 每个周期返回条数，默认50
    """
    from agent.timeframes import get_multi_timeframe_data
    result = get_multi_timeframe_data(code, ["daily", "60m", "15m"], int(limit))
    return json.dumps(result, ensure_ascii=False, default=str)


# ============ 回测工具 ============


def backtest_stock(code: str, strategy: str = "macd_cross", days: int = 500,
                   stop_loss: float = 0.05, take_profit: float = 0.10,
                   max_hold: int = 20) -> str:
    """对单只股票运行指定策略的历史回测，统计胜率、盈亏比、最大回撤等指标。

    Args:
        code: 股票代码
        strategy: 策略名 - ma_cross(均线金叉)/macd_cross(MACD金叉)/rsi_oversold(RSI超卖)/volume_breakout(放量突破)/boll_bounce(布林带反弹)/kdj_cross(KDJ金叉)/multi_confirm(多指标共振)
        days: 回测数据天数（交易日），默认500
        stop_loss: 止损比例，默认0.05(5%)
        take_profit: 止盈比例，默认0.10(10%)
        max_hold: 最大持仓天数，默认20
    """
    from agent.backtest import backtest_stock as _backtest_stock
    try:
        result = _backtest_stock(code, strategy=strategy, days=int(days),
                                  stop_loss=float(stop_loss),
                                  take_profit=float(take_profit),
                                  max_hold=int(max_hold))
        return json.dumps(result.to_dict(), ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"回测失败: {e}"}, ensure_ascii=False)


def backtest_multi_stock(strategy: str = "macd_cross", codes: str = "",
                          days: int = 500, top_n: int = 10) -> str:
    """在多只股票上回测同一策略，汇总统计胜率和收益率。

    Args:
        strategy: 策略名
        codes: 股票代码（逗号分隔），为空则随机抽样测试
        days: 回测天数
        top_n: 返回表现最好的N只
    """
    from agent.backtest import backtest_strategy_multi_stock
    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
        result = backtest_strategy_multi_stock(strategy=strategy, codes=code_list,
                                                days=int(days), top_n=int(top_n))
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"error": f"批量回测失败: {e}"}, ensure_ascii=False)


# ============ 龙虎榜与北向资金工具 ============


def dragon_tiger_list(trade_date: str = None) -> str:
    """获取龙虎榜数据（当日上榜个股、净买入额、上榜原因）。

    Args:
        trade_date: 交易日期 YYYYMMDD，默认今天
    """
    from agent.datalink import get_dragon_tiger
    result = get_dragon_tiger(trade_date)
    return json.dumps(result, ensure_ascii=False, default=str)


def northbound_flow() -> str:
    """获取北向资金（沪股通+深股通）近5日净流入数据和趋势判断。"""
    from agent.datalink import get_northbound_flow
    result = get_northbound_flow()
    return json.dumps(result, ensure_ascii=False, default=str)


def sector_fund_flow() -> str:
    """获取板块资金流向（行业板块+概念板块主力净流入排行Top10）。"""
    from agent.datalink import get_sector_fund_flow
    result = get_sector_fund_flow()
    return json.dumps(result, ensure_ascii=False, default=str)


def stock_dragon_tiger(code: str, days: int = 30) -> str:
    """查询个股龙虎榜历史明细，查看游资和机构席位动向。

    Args:
        code: 股票代码
        days: 查询天数，默认30
    """
    from agent.datalink import get_stock_dragon_tiger_detail
    result = get_stock_dragon_tiger_detail(code, int(days))
    return json.dumps(result, ensure_ascii=False, default=str)


# ============ K线图工具 ============


def generate_chart(code: str, days: int = 120, show_macd: bool = True,
                   show_rsi: bool = False) -> str:
    """生成K线图（带均线、MACD等技术指标），自动在浏览器中打开。

    Args:
        code: 股票代码
        days: 显示天数（交易日），默认120
        show_macd: 是否显示MACD，默认true
        show_rsi: 是否显示RSI，默认false
    """
    from agent.charts import generate_kline_chart
    try:
        path = generate_kline_chart(code, days=int(days), show_macd=show_macd,
                                     show_rsi=show_rsi, auto_open=True)
        return json.dumps({"status": "ok", "path": path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"生成图表失败: {e}"}, ensure_ascii=False)


def compare_stocks_chart(codes: str, days: int = 60) -> str:
    """生成多只股票涨幅对比图（归一化），自动在浏览器中打开。

    Args:
        codes: 股票代码，逗号分隔（如 '600519,000858,000568'）
        days: 对比天数，默认60
    """
    from agent.charts import generate_comparison_chart
    try:
        code_list = [c.strip() for c in codes.split(",") if c.strip()]
        path = generate_comparison_chart(code_list, days=int(days), auto_open=True)
        return json.dumps({"status": "ok", "path": path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"生成对比图失败: {e}"}, ensure_ascii=False)


# ============ 自选股管理工具 ============


_watchlist = None

def _get_watchlist():
    global _watchlist
    if _watchlist is None:
        from agent.watchlist import WatchlistManager
        _watchlist = WatchlistManager()
    return _watchlist


def add_to_watchlist(code: str, group: str = "默认", target_price: float = 0,
                     stop_loss: float = 0, notes: str = "") -> str:
    """将股票添加到自选股列表。

    Args:
        code: 股票代码
        group: 分组名称，默认'默认'
        target_price: 目标价位（可选）
        stop_loss: 止损价位（可选）
        notes: 备注
    """
    wl = _get_watchlist()
    result = wl.add(code, group=group,
                     target_price=float(target_price) if target_price else None,
                     stop_loss=float(stop_loss) if stop_loss else None,
                     notes=notes)
    return json.dumps(result, ensure_ascii=False)


def remove_from_watchlist(code: str, group: str = "默认") -> str:
    """从自选股列表移除。

    Args:
        code: 股票代码
        group: 分组名称
    """
    wl = _get_watchlist()
    result = wl.remove(code, group)
    return json.dumps(result, ensure_ascii=False)


def list_watchlist(group: str = "") -> str:
    """查看自选股列表及实时状态（含当前价、涨跌幅、目标价距离）。

    Args:
        group: 按分组筛选（可选）
    """
    from agent.watchlist import format_watchlist_status
    wl = _get_watchlist()
    if group:
        codes = wl.get_codes(group)
        if not codes:
            return json.dumps({"message": f"分组'{group}'无自选股"}, ensure_ascii=False)
    result = format_watchlist_status(wl)
    return json.dumps({"status": "ok", "display": result}, ensure_ascii=False)


def set_alert_rule(code: str, rule_type: str, threshold: float = 0,
                   zone: str = "", ratio: float = 3) -> str:
    """为自选股设置告警规则。触发时会发送桌面通知。

    Args:
        code: 股票代码
        rule_type: 告警类型 - price_above(价格突破)/price_below(价格跌破)/pct_change(涨跌幅超阈值)/volume_spike(放量)/rsi_zone(RSI区域)
        threshold: 阈值（price_above/below为价格，pct_change为百分比）
        zone: RSI区域（oversold=超卖/overbought=超买），仅rsi_zone类型使用
        ratio: 放量倍数，仅volume_spike类型使用
    """
    wl = _get_watchlist()
    params = {}
    if rule_type in ("price_above", "price_below"):
        params["threshold"] = float(threshold)
    elif rule_type == "pct_change":
        params["threshold"] = float(threshold)
    elif rule_type == "rsi_zone":
        params["zone"] = zone or "oversold"
    elif rule_type == "volume_spike":
        params["ratio"] = float(ratio)

    result = wl.add_alert_rule(code, rule_type, params)
    return json.dumps(result, ensure_ascii=False)


def check_alerts() -> str:
    """立即检查自选股告警条件，返回触发的告警列表。"""
    from agent.watchlist import MarketMonitor
    wl = _get_watchlist()
    monitor = MarketMonitor(wl)
    alerts = monitor.check_now()
    if not alerts:
        return json.dumps({"message": "无告警触发", "alerts": []}, ensure_ascii=False)
    alert_list = [{"code": a.code, "name": a.name, "message": a.message,
                    "price": a.price, "time": a.triggered_at} for a in alerts]
    return json.dumps({"triggered": len(alerts), "alerts": alert_list},
                       ensure_ascii=False)


def get_alert_history(limit: int = 20) -> str:
    """查看告警历史记录。"""
    wl = _get_watchlist()
    history = wl.get_alert_history(int(limit))
    return json.dumps({"count": len(history), "history": history},
                       ensure_ascii=False, default=str)


# ============ 交易日志工具 ============


_journal = None

def _get_journal():
    global _journal
    if _journal is None:
        from agent.journal import TradeJournal
        _journal = TradeJournal()
    return _journal


def record_trade(code: str, action: str, price: float, shares: int,
                 name: str = "", reason: str = "", strategy: str = "",
                 emotion: str = "") -> str:
    """记录一笔交易操作。

    Args:
        code: 股票代码
        action: 操作 - 买入/卖出/加仓/减仓
        price: 成交价格
        shares: 成交数量
        name: 股票名称
        reason: 交易理由
        strategy: 使用的战法
        emotion: 交易时情绪（自信/犹豫/恐惧/贪婪/平静）
    """
    j = _get_journal()
    result = j.record_trade(code, action, float(price), int(shares),
                             name=name, reason=reason, strategy=strategy,
                             emotion=emotion)
    return json.dumps(result, ensure_ascii=False)


def view_portfolio() -> str:
    """查看当前持仓状态（含实时价格和盈亏）。"""
    from agent.journal import format_portfolio_status
    j = _get_journal()
    result = format_portfolio_status(j)
    return json.dumps({"display": result}, ensure_ascii=False)


def view_trade_history(days: int = 7) -> str:
    """查看近期交易记录。

    Args:
        days: 查看天数，默认7
    """
    from agent.journal import format_trade_history
    j = _get_journal()
    result = format_trade_history(j, int(days))
    return json.dumps({"display": result}, ensure_ascii=False)


def generate_review(period: str = "week") -> str:
    """自动生成交易复盘报告。

    Args:
        period: 复盘周期 - week(本周)/month(本月)/quarter(本季度)
    """
    from agent.journal import generate_auto_review
    j = _get_journal()
    result = generate_auto_review(j, period)
    return json.dumps({"display": result}, ensure_ascii=False)


def save_review(period: str, summary: str, what_went_well: str,
                what_to_improve: str, key_lessons: str) -> str:
    """保存复盘笔记。

    Args:
        period: 复盘周期
        summary: 总结
        what_went_well: 做得好的方面
        what_to_improve: 需要改进的方面
        key_lessons: 关键教训
    """
    j = _get_journal()
    result = j.save_review(period, summary, what_went_well,
                            what_to_improve, key_lessons)
    return json.dumps(result, ensure_ascii=False)


# ============ 工具定义（OpenAI function calling 格式） ============

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "update_stock",
            "description": "检查并更新单只股票的K线数据到最新。在分析某只股票前，应先调用此工具确保数据是最新的。只需几秒钟即可完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "6位股票代码"
                    },
                    "max_days": {
                        "type": "integer",
                        "description": "最多回溯天数，默认5",
                        "default": 5
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_all_stocks",
            "description": "更新全部沪深主板股票的K线数据到最新。当用户要求更新完整数据库时调用。耗时较长（约10-15分钟），调用前应告知用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_days": {
                        "type": "integer",
                        "description": "最多回溯天数，默认5",
                        "default": 5
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "realtime_price",
            "description": "获取单只股票的实时行情（当前价、涨跌幅、成交量等）。支持多数据源自动故障切换（新浪、东财）。盘中可获取实时数据，盘后获取收盘数据。返回结果包含日期，注意与K线数据去重。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "6位股票代码"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "realtime_prices",
            "description": "批量获取多只股票的实时行情（当前价、涨跌幅、成交量等）。支持多数据源自动故障切换，比逐个调用 realtime_price 更高效。",
            "parameters": {
                "type": "object",
                "properties": {
                    "codes": {
                        "type": "string",
                        "description": "股票代码，多个用逗号分隔，如 '600000,000001'"
                    }
                },
                "required": ["codes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_stock",
            "description": "按名称或代码模糊搜索股票。用于查找用户提到的股票对应的代码。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词，可以是股票名称或代码"
                    }
                },
                "required": ["keyword"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_stock_info",
            "description": "查询单只股票的基本信息（名称、市场、上市日期等）",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "6位股票代码，如 600000"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_kline",
            "description": "查询股票最近N个交易日的日K线数据（开高低收、成交量、成交额、换手率）。注意：天数指交易日（剔除周末和法定节假日），非自然日。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "6位股票代码"
                    },
                    "days": {
                        "type": "integer",
                        "description": "获取最近N个交易日的数据（非自然日），默认60",
                        "default": 60
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "latest_price",
            "description": "获取股票最新交易日的价格数据。支持批量查询多只股票。",
            "parameters": {
                "type": "object",
                "properties": {
                    "codes": {
                        "type": "string",
                        "description": "股票代码，多个用逗号分隔，如 '600000,000001'"
                    }
                },
                "required": ["codes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calc_technical",
            "description": "计算股票的技术指标，包括MA均线、MACD、RSI、KDJ、布林带等，并给出量价分析和信号判断。短线分析的核心工具。注意：天数指交易日（剔除周末和法定节假日），非自然日。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "6位股票代码"
                    },
                    "days": {
                        "type": "integer",
                        "description": "计算数据天数（交易日，非自然日），默认120",
                        "default": 120
                    },
                    "indicators": {
                        "type": "string",
                        "description": "要计算的指标，逗号分隔。可选: ma,macd,rsi,kdj,boll。默认全部计算",
                        "default": "ma,macd,rsi,kdj,boll"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "market_overview",
            "description": "获取A股市场概览，包括数据库统计和最近交易日涨跌家数",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_news",
            "description": "搜索财经新闻热点。可用于搜索个股相关新闻、行业新闻或宏观财经消息。数据来源为百度新闻。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如股票名称、行业、宏观事件等"
                    },
                    "num": {
                        "type": "integer",
                        "description": "返回条数，默认8",
                        "default": 8
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stock_news",
            "description": "搜索某只股票的相关新闻，综合百度新闻和东方财富快讯多源搜索，结果更全面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code_or_name": {
                        "type": "string",
                        "description": "股票代码或名称，如'000001'或'平安银行'"
                    },
                    "num": {
                        "type": "integer",
                        "description": "返回条数，默认8",
                        "default": 8
                    }
                },
                "required": ["code_or_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "market_hot_news",
            "description": "获取A股市场今日热点新闻，综合东方财富、新浪财经、百度新闻多源聚合。适合了解市场整体动态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "num": {
                        "type": "integer",
                        "description": "每个源返回条数，默认10",
                        "default": 10
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "保存一条长期记忆，供未来分析时参考。完成股票分析后应保存结论，用户表达偏好时也应保存。检测到用户教学/纠错时，必须保存为learning类型。",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "记忆内容（分析结论、用户偏好、市场事实、用户教学等）"
                    },
                    "memory_type": {
                        "type": "string",
                        "description": "记忆类型: preference(偏好), insight(分析结论), fact(事实), context(上下文), summary(摘要), learning(用户教学/纠错)",
                        "default": "insight"
                    },
                    "keywords": {
                        "type": "string",
                        "description": "逗号分隔的关键词，如 '600519,贵州茅台,支撑位'",
                        "default": ""
                    },
                    "tags": {
                        "type": "string",
                        "description": "逗号分隔的标签，如 '技术分析,短线'",
                        "default": ""
                    },
                    "importance": {
                        "type": "number",
                        "description": "重要度 0.0-1.0，越重要越不容易被遗忘。用户教学建议0.7-0.9",
                        "default": 0.5
                    },
                    "learned_what": {
                        "type": "string",
                        "description": "学到了什么（用于learning类型，简明扼要）",
                        "default": ""
                    },
                    "learned_why": {
                        "type": "string",
                        "description": "为什么这个知识重要",
                        "default": ""
                    },
                    "apply_when": {
                        "type": "string",
                        "description": "什么情况下应该应用这个知识",
                        "default": ""
                    }
                },
                "required": ["content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "搜索长期记忆。在开始新的分析前，先搜索是否有相关的历史记忆可以帮助分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，可以包含股票代码、名称或主题词"
                    },
                    "memory_type": {
                        "type": "string",
                        "description": "按类型筛选（可选）: preference/insight/fact/context/summary/learning",
                        "default": ""
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，默认5",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_memory",
            "description": "更新已有记忆的内容、重要度或标签。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "要更新的记忆ID"
                    },
                    "content": {
                        "type": "string",
                        "description": "新内容（可选）",
                        "default": ""
                    },
                    "importance": {
                        "type": "number",
                        "description": "新的重要度（可选，-1表示保持原值）",
                        "default": -1
                    },
                    "tags": {
                        "type": "string",
                        "description": "新的标签（可选，逗号分隔）",
                        "default": ""
                    }
                },
                "required": ["memory_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_memory",
            "description": "删除一条记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "string",
                        "description": "要删除的记忆ID"
                    }
                },
                "required": ["memory_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_memories",
            "description": "列出已保存的长期记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "memory_type": {
                        "type": "string",
                        "description": "按类型筛选（可选）",
                        "default": ""
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回条数，默认20",
                        "default": 20
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_session_context",
            "description": "保存当前会话上下文，用于跨会话的分析延续。",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {
                        "type": "string",
                        "description": "当前正在分析的股票代码",
                        "default": ""
                    },
                    "topic": {
                        "type": "string",
                        "description": "当前分析主题",
                        "default": ""
                    },
                    "notes": {
                        "type": "string",
                        "description": "临时笔记",
                        "default": ""
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_strategy",
            "description": "搜索匹配当前分析场景的短线战法。当分析股票的技术指标、量价关系后，调用此工具查找适用的战法策略来指导操作建议。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "描述当前技术面状态，如 'RSI超卖+缩量下跌+均线支撑' 或 '放量突破MA20+MACD金叉'"
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回最匹配的战法数量，默认3",
                        "default": 3
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_strategies",
            "description": "列出所有可用的短线战法，了解系统中有哪些战法策略。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_strategy",
            "description": "获取指定战法的完整内容，包括详细的买入卖出条件、适用场景等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "战法文件名，如 '01-放量突破战法.md'"
                    }
                },
                "required": ["filename"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "market_sentiment",
            "description": "获取市场情绪综合分析，包括涨停数、跌停数、炸板数、连板高度、情绪评级和操作建议。用于判断市场整体情绪状态（高潮/发酵/启动/低迷/冰点）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "trade_date": {
                        "type": "string",
                        "description": "交易日期 YYYYMMDD，默认今天"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "limit_up_pool",
            "description": "获取涨停股池，包含涨停股票的代码、名称、首次封板时间、连板数、所属行业等。用于分析市场热点方向和连板梯队。",
            "parameters": {
                "type": "object",
                "properties": {
                    "trade_date": {
                        "type": "string",
                        "description": "交易日期 YYYYMMDD，默认今天"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sector_ranking",
            "description": "获取行业板块或概念板块的涨跌排行，包含涨跌幅和主力净流入。用于发现当日市场热点板块和资金流向。",
            "parameters": {
                "type": "object",
                "properties": {
                    "board_type": {
                        "type": "string",
                        "description": "板块类型：industry=行业板块，concept=概念板块",
                        "enum": ["industry", "concept"]
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "返回前N个板块，默认10"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stock_fund_flow",
            "description": "获取个股近5日资金流向（主力净流入/流出趋势），判断资金是连续流入还是流出。用于辅助判断主力动向。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "6位股票代码"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recognize_kline_patterns",
            "description": "识别股票的K线形态（十字星、锤子线、射击之星、大阳线、大阴线、看涨/看跌吞没、乌云盖顶、曙光初现、早晨/黄昏之星、三连阳/阴、红三兵、横盘整理、N字上攻等），返回形态名称、多空方向、置信度和说明。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "股票代码"
                    },
                    "days": {
                        "type": "integer",
                        "description": "分析的交易日数量，默认60"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "screen_stocks",
            "description": "根据技术条件从全市场筛选股票。支持条件：MA均线比较（MA5>MA10）、RSI（RSI6<20）、MACD金叉/死叉、成交量（放量/缩量）、涨跌幅、多头排列等。返回满足条件的股票列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "conditions": {
                        "type": "string",
                        "description": "筛选条件，如 'MA5>MA10', 'RSI6<20', 'MACD金叉', '放量', '涨幅>3%', '多头排列'"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回数量，默认20"
                    }
                },
                "required": ["conditions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_prediction",
            "description": "保存一条投资预测，用于后续验证准确率。在给出股票分析结论（看涨/看跌/中性）时必须调用此工具，以便后续自动验证预测结果并从对错中学习。",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string", "description": "6位股票代码"},
                    "direction": {"type": "string", "description": "预测方向: bullish(看涨)/bearish(看跌)/neutral(中性)", "enum": ["bullish", "bearish", "neutral"]},
                    "stock_name": {"type": "string", "description": "股票名称（可选）", "default": ""},
                    "prediction_type": {"type": "string", "description": "预测类型: direction(方向)/price_range(价位)/pattern(形态)", "default": "direction"},
                    "target_price": {"type": "number", "description": "目标价位（可选）", "default": 0},
                    "stop_loss": {"type": "number", "description": "止损价位（可选）", "default": 0},
                    "timeframe_days": {"type": "integer", "description": "预测有效天数，默认5", "default": 5},
                    "reasoning": {"type": "string", "description": "预测理由简述"},
                    "confidence": {"type": "number", "description": "置信度 0.0-1.0", "default": 0.5}
                },
                "required": ["stock_code", "direction"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_predictions",
            "description": "检查待验证的投资预测。分析某只股票时自动调用以查看之前的预测是否应验，也可手动查看所有到期预测。",
            "parameters": {
                "type": "object",
                "properties": {
                    "stock_code": {"type": "string", "description": "股票代码（可选，不填则检查所有到期预测）", "default": ""}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verify_prediction",
            "description": "验证一条预测的实际结果。提供实际价格和收益率，系统自动判断对错并调整关联记忆的重要性。",
            "parameters": {
                "type": "object",
                "properties": {
                    "prediction_id": {"type": "string", "description": "预测ID"},
                    "actual_price": {"type": "number", "description": "实际价格"},
                    "actual_return_pct": {"type": "number", "description": "实际收益率（百分比，如5.2表示涨5.2%）", "default": 0}
                },
                "required": ["prediction_id", "actual_price"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "prediction_stats",
            "description": "查看预测准确率统计，包括总验证数、正确/错误数、按方向分类的准确率。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_profile",
            "description": "获取用户交易画像，包括交易风格、风险偏好、关注板块、止损习惯等。分析时应参考用户画像给出个性化建议。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_user_profile",
            "description": "更新用户画像。当用户表达交易偏好、风险态度、关注方向时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "画像维度: trading_style(交易风格)/risk_tolerance(风险偏好)/preferred_indicators(偏好指标)/watched_sectors(关注板块)/stop_loss_habit(止损习惯)/position_sizing(仓位习惯)/trading_frequency(交易频率)"},
                    "value": {"type": "string", "description": "对应的值"},
                    "confidence": {"type": "number", "description": "置信度 0.0-1.0", "default": 0.5}
                },
                "required": ["key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "record_learning",
            "description": "显式记录一条学习知识。当检测到用户在教你新知识或纠正你的分析时，必须调用此工具保存为结构化学习记录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "learned_what": {"type": "string", "description": "学到了什么（简明扼要）"},
                    "learned_why": {"type": "string", "description": "为什么这个知识重要"},
                    "apply_when": {"type": "string", "description": "什么情况下应该应用这个知识"},
                    "category": {"type": "string", "description": "学习类别: indicator_usage(指标用法)/risk_management(风险管理)/market_pattern(市场规律)/user_correction(用户纠错)/trading_technique(交易技巧)", "default": "user_correction"},
                    "related_indicators": {"type": "string", "description": "相关技术指标（逗号分隔）", "default": ""},
                    "related_stocks": {"type": "string", "description": "相关股票代码（逗号分隔）", "default": ""},
                    "importance": {"type": "number", "description": "重要度 0.0-1.0", "default": 0.8}
                },
                "required": ["learned_what", "learned_why", "apply_when"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_knowledge",
            "description": "查询知识图谱中的实体和关系。用于查找技术指标、战法、股票之间的关联关系，辅助推理分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "实体名称（模糊匹配，可选）", "default": ""},
                    "entity_type": {"type": "string", "description": "实体类型（可选）: stock/indicator/pattern/strategy/concept/rule", "default": ""},
                    "relation_type": {"type": "string", "description": "关系类型（可选）: triggers/requires/contradicts/supports/applies_to/user_prefers", "default": ""}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_knowledge",
            "description": "向知识图谱添加一条关系。当发现技术指标、战法、市场规律之间的关联时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_name": {"type": "string", "description": "源实体名称（如'RSI超卖'）"},
                    "source_type": {"type": "string", "description": "源实体类型: stock/indicator/pattern/strategy/concept/rule"},
                    "target_name": {"type": "string", "description": "目标实体名称（如'低吸战法'）"},
                    "target_type": {"type": "string", "description": "目标实体类型: stock/indicator/pattern/strategy/concept/rule"},
                    "relation_type": {"type": "string", "description": "关系类型: triggers(触发)/requires(需要)/contradicts(矛盾)/supports(支持)/applies_to(适用于)/user_prefers(用户偏好)"},
                    "weight": {"type": "number", "description": "关系权重 0.0-1.0", "default": 1.0}
                },
                "required": ["source_name", "source_type", "target_name", "target_type", "relation_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_timeframe_data",
            "description": "更新股票的多周期K线数据（分钟级或周线）。用于多周期共振分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "timeframe": {"type": "string", "description": "周期: 1m/5m/15m/30m/60m/weekly", "default": "5m"},
                    "days": {"type": "integer", "description": "分钟数据天数", "default": 5}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_timeframe_kline",
            "description": "查询股票多周期K线数据（分钟级或周线）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "timeframe": {"type": "string", "description": "周期: 1m/5m/15m/30m/60m/weekly", "default": "5m"},
                    "limit": {"type": "integer", "description": "返回条数", "default": 100}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "multi_timeframe_analysis",
            "description": "获取多周期K线数据（日线+60分钟+15分钟），用于多周期共振分析。日线看方向、60分钟找买点、15分钟精确入场。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "limit": {"type": "integer", "description": "每周期条数", "default": 50}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_stock",
            "description": "对单只股票运行指定策略的历史回测，统计胜率、盈亏比、最大回撤、夏普比率等指标。用于验证战法在历史数据上的表现。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "strategy": {"type": "string", "description": "策略: ma_cross(macd_cross)/rsi_oversold/volume_breakout/boll_bounce/kdj_cross/multi_confirm", "default": "macd_cross"},
                    "days": {"type": "integer", "description": "回测天数（交易日）", "default": 500},
                    "stop_loss": {"type": "number", "description": "止损比例", "default": 0.05},
                    "take_profit": {"type": "number", "description": "止盈比例", "default": 0.10},
                    "max_hold": {"type": "integer", "description": "最大持仓天数", "default": 20}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_multi_stock",
            "description": "在多只股票上回测同一策略，汇总统计平均胜率、收益率、正收益占比。用于评估策略的普适性。",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy": {"type": "string", "description": "策略名", "default": "macd_cross"},
                    "codes": {"type": "string", "description": "股票代码（逗号分隔），为空则随机抽样", "default": ""},
                    "days": {"type": "integer", "description": "回测天数", "default": 500},
                    "top_n": {"type": "integer", "description": "返回表现最好的N只", "default": 10}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "dragon_tiger_list",
            "description": "获取龙虎榜数据（当日上榜个股、净买入额、上榜原因）。用于分析游资和机构动向。",
            "parameters": {
                "type": "object",
                "properties": {
                    "trade_date": {"type": "string", "description": "日期YYYYMMDD，默认今天"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "northbound_flow",
            "description": "获取北向资金（沪股通+深股通）近5日净流入数据和趋势判断。北向资金是重要的市场风向标。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sector_fund_flow",
            "description": "获取板块资金流向（行业+概念板块主力净流入排行Top10）。用于发现资金主攻方向。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "stock_dragon_tiger",
            "description": "查询个股龙虎榜历史明细，查看游资和机构席位的买卖动向。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "days": {"type": "integer", "description": "查询天数", "default": 30}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_chart",
            "description": "生成K线图（带均线、MACD等技术指标叠加），自动在浏览器中打开。比纯文字更直观地展示走势。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "days": {"type": "integer", "description": "显示天数", "default": 120},
                    "show_macd": {"type": "boolean", "description": "显示MACD", "default": True},
                    "show_rsi": {"type": "boolean", "description": "显示RSI", "default": False}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compare_stocks_chart",
            "description": "生成多只股票涨幅对比图（归一化），自动在浏览器中打开。用于横向对比多只股票的强弱。",
            "parameters": {
                "type": "object",
                "properties": {
                    "codes": {"type": "string", "description": "股票代码，逗号分隔"},
                    "days": {"type": "integer", "description": "对比天数", "default": 60}
                },
                "required": ["codes"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_watchlist",
            "description": "将股票添加到自选股列表，支持分组、设置目标价和止损价。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "group": {"type": "string", "description": "分组名", "default": "默认"},
                    "target_price": {"type": "number", "description": "目标价位", "default": 0},
                    "stop_loss": {"type": "number", "description": "止损价位", "default": 0},
                    "notes": {"type": "string", "description": "备注", "default": ""}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "remove_from_watchlist",
            "description": "从自选股列表移除股票。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "group": {"type": "string", "description": "分组名", "default": "默认"}
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_watchlist",
            "description": "查看自选股列表及实时状态（含当前价、涨跌幅、目标价距离）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "group": {"type": "string", "description": "按分组筛选", "default": ""}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_alert_rule",
            "description": "为自选股设置告警规则，触发时发送桌面通知。支持价格突破/跌破、涨跌幅、放量、RSI区域等告警。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "rule_type": {"type": "string", "description": "告警类型: price_above/price_below/pct_change/volume_spike/rsi_zone", "enum": ["price_above", "price_below", "pct_change", "volume_spike", "rsi_zone"]},
                    "threshold": {"type": "number", "description": "阈值", "default": 0},
                    "zone": {"type": "string", "description": "RSI区域(oversold/overbought)", "default": ""},
                    "ratio": {"type": "number", "description": "放量倍数", "default": 3}
                },
                "required": ["code", "rule_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_alerts",
            "description": "立即检查自选股告警条件，返回触发的告警列表。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_alert_history",
            "description": "查看告警历史记录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "返回条数", "default": 20}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "record_trade",
            "description": "记录一笔交易操作（买入/卖出/加仓/减仓），用于交易日志和复盘。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "股票代码"},
                    "action": {"type": "string", "description": "操作: 买入/卖出/加仓/减仓"},
                    "price": {"type": "number", "description": "成交价格"},
                    "shares": {"type": "integer", "description": "成交数量"},
                    "name": {"type": "string", "description": "股票名称", "default": ""},
                    "reason": {"type": "string", "description": "交易理由", "default": ""},
                    "strategy": {"type": "string", "description": "使用的战法", "default": ""},
                    "emotion": {"type": "string", "description": "情绪状态", "default": ""}
                },
                "required": ["code", "action", "price", "shares"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "view_portfolio",
            "description": "查看当前持仓状态（含实时价格和盈亏）。",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "view_trade_history",
            "description": "查看近期交易记录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "查看天数", "default": 7}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_review",
            "description": "自动生成交易复盘报告（本周/本月/本季度），包含交易统计、战法使用、情绪分布。",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "description": "周期: week/month/quarter", "default": "week"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_review",
            "description": "保存复盘笔记（总结、做得好的、需改进的、关键教训）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "description": "复盘周期"},
                    "summary": {"type": "string", "description": "总结"},
                    "what_went_well": {"type": "string", "description": "做得好的方面"},
                    "what_to_improve": {"type": "string", "description": "需要改进的方面"},
                    "key_lessons": {"type": "string", "description": "关键教训"}
                },
                "required": ["period", "summary", "what_went_well", "what_to_improve", "key_lessons"]
            }
        }
    },
]

# 工具名 -> 函数映射
TOOL_DISPATCH = {
    "update_stock": update_stock,
    "update_all_stocks": update_all_stocks,
    "realtime_price": realtime_price,
    "realtime_prices": realtime_prices,
    "search_stock": search_stock,
    "query_stock_info": query_stock_info,
    "query_kline": query_kline,
    "latest_price": latest_price,
    "calc_technical": calc_technical,
    "market_overview": market_overview,
    "search_news": search_news,
    "stock_news": stock_news,
    "market_hot_news": market_hot_news,
    "save_memory": save_memory,
    "search_memory": search_memory,
    "update_memory": update_memory,
    "delete_memory": delete_memory,
    "list_memories": list_memories,
    "save_session_context": save_session_context,
    "search_strategy": search_strategy,
    "list_strategies": list_strategies,
    "get_strategy": get_strategy,
    "market_sentiment": market_sentiment,
    "limit_up_pool": limit_up_pool,
    "sector_ranking": sector_ranking,
    "stock_fund_flow": stock_fund_flow,
    "recognize_kline_patterns": recognize_kline_patterns,
    "screen_stocks": screen_stocks,
    "save_prediction": save_prediction,
    "check_predictions": check_predictions,
    "verify_prediction": verify_prediction,
    "prediction_stats": prediction_stats,
    "get_user_profile": get_user_profile,
    "update_user_profile": update_user_profile,
    "record_learning": record_learning,
    "query_knowledge": query_knowledge,
    "add_knowledge": add_knowledge,
    # 多周期数据
    "update_timeframe_data": update_timeframe_data,
    "query_timeframe_kline": query_timeframe_kline,
    "multi_timeframe_analysis": multi_timeframe_analysis,
    # 回测
    "backtest_stock": backtest_stock,
    "backtest_multi_stock": backtest_multi_stock,
    # 龙虎榜与北向资金
    "dragon_tiger_list": dragon_tiger_list,
    "northbound_flow": northbound_flow,
    "sector_fund_flow": sector_fund_flow,
    "stock_dragon_tiger": stock_dragon_tiger,
    # K线图
    "generate_chart": generate_chart,
    "compare_stocks_chart": compare_stocks_chart,
    # 自选股
    "add_to_watchlist": add_to_watchlist,
    "remove_from_watchlist": remove_from_watchlist,
    "list_watchlist": list_watchlist,
    "set_alert_rule": set_alert_rule,
    "check_alerts": check_alerts,
    "get_alert_history": get_alert_history,
    # 交易日志
    "record_trade": record_trade,
    "view_portfolio": view_portfolio,
    "view_trade_history": view_trade_history,
    "generate_review": generate_review,
    "save_review": save_review,
}
