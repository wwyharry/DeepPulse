"""资金流向数据模块 - 多数据源、自动降级"""

from datetime import date


def get_stock_fund_flow(code: str) -> dict:
    """获取个股资金流向（多数据源）

    Args:
        code: 6位股票代码

    Returns:
        资金流向数据
    """
    # 数据源1: 东方财富个股资金流
    try:
        import akshare as ak

        market = "sh" if code.startswith("6") else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            return {
                "code": code,
                "source": "东方财富",
                "date": str(latest.get("日期", "")),
                "main_net_inflow": _safe_float(latest.get("主力净流入-净额")),
                "main_net_pct": _safe_float(latest.get("主力净流入-净占比")),
                "super_large_net_inflow": _safe_float(latest.get("超大单净流入-净额")),
                "large_net_inflow": _safe_float(latest.get("大单净流入-净额")),
                "medium_net_inflow": _safe_float(latest.get("中单净流入-净额")),
                "small_net_inflow": _safe_float(latest.get("小单净流入-净额")),
            }
    except Exception:
        pass

    # 数据源2: 从龙虎榜数据推断
    try:
        import akshare as ak

        df = ak.stock_lhb_stock_statistic_em(symbol="近一月")
        if df is not None and not df.empty:
            stock_row = df[df["代码"] == code]
            if not stock_row.empty:
                row = stock_row.iloc[0]
                return {
                    "code": code,
                    "source": "龙虎榜",
                    "note": "基于龙虎榜数据，非实时资金流",
                    "net_buy": _safe_float(row.get("龙虎榜净买额")),
                    "lhb_count": int(row.get("上榜次数", 0)) if row.get("上榜次数") else 0,
                    "last_date": str(row.get("最近上榜日", "")),
                }
    except Exception:
        pass

    # 数据源3: 基于成交量估算
    try:
        from deeppulse.src.query import StockQuery

        query = StockQuery()
        # 尝试获取最近数据（不限制日期，取最新可用）
        df = query.get_daily_kline(code, limit=10)
        if df is not None and not df.empty and len(df) >= 2:
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            vol_change = (latest["volume"] - prev["volume"]) / prev["volume"] if prev["volume"] > 0 else 0
            price_change = (latest["close"] - prev["close"]) / prev["close"] if prev["close"] > 0 else 0

            # 简单量价分析
            if vol_change > 0.5 and price_change > 0:
                trend = "放量上涨，资金流入"
            elif vol_change > 0.5 and price_change < 0:
                trend = "放量下跌，资金流出"
            elif vol_change < -0.3 and price_change > 0:
                trend = "缩量上涨，观望"
            elif vol_change < -0.3 and price_change < 0:
                trend = "缩量下跌，惜售"
            else:
                trend = "量价平稳"

            return {
                "code": code,
                "source": "量价分析",
                "note": "基于量价关系估算，非实时资金流",
                "date": str(latest.get("trade_date", "")),
                "volume_change": round(vol_change * 100, 2),
                "price_change": round(price_change * 100, 2),
                "volume": int(latest["volume"]),
                "amount": round(float(latest.get("amount", 0)) / 1e8, 2),
                "trend": trend,
            }
    except Exception:
        pass

    return {"error": f"获取 {code} 资金流向失败，所有数据源不可用"}


def get_sector_fund_flow() -> dict:
    """获取板块资金流向（多数据源）"""
    # 数据源1: 同花顺行业板块（含净流入）
    try:
        import akshare as ak

        df = ak.stock_board_industry_summary_ths()
        if df is not None and not df.empty:
            sectors = []
            for _, row in df.head(20).iterrows():
                inflow = _safe_float(row.get("净流入"))
                if inflow is not None:
                    sectors.append(
                        {
                            "name": str(row.get("板块", "")),
                            "inflow": inflow,
                            "change_pct": _safe_float(row.get("涨跌幅")),
                        }
                    )
            if sectors:
                # 按净流入排序
                sectors.sort(key=lambda x: x.get("inflow", 0), reverse=True)
                return {
                    "source": "同花顺",
                    "top_inflow": sectors[:10],
                    "top_outflow": sectors[-10:][::-1] if len(sectors) >= 10 else [],
                }
    except Exception:
        pass

    # 数据源2: 从涨停股行业分布推断资金流向
    try:
        from collections import Counter

        import akshare as ak

        df = ak.stock_zt_pool_em(date=date.today().strftime("%Y%m%d"))
        if df is not None and not df.empty:
            industry_col = None
            for col in df.columns:
                if "行业" in str(col):
                    industry_col = col
                    break
            if industry_col:
                counts = Counter(df[industry_col].dropna())
                hot_sectors = [{"name": k, "zt_count": v} for k, v in counts.most_common(10)]
                return {
                    "source": "涨停推断",
                    "note": "基于涨停股行业分布推断热门资金方向",
                    "hot_sectors": hot_sectors,
                }
    except Exception:
        pass

    return {"error": "获取板块资金流向失败"}


def get_market_fund_flow() -> dict:
    """获取大盘资金流向"""
    # 数据源: 从板块资金流推断
    sector_flow = get_sector_fund_flow()
    if "error" not in sector_flow:
        return {
            "source": sector_flow.get("source"),
            "sector_flow": sector_flow,
        }

    return {"error": "获取大盘资金流向失败"}


def _safe_float(val) -> float:
    """安全转换为浮点数"""
    if val is None:
        return 0.0
    try:
        return round(float(val), 2)
    except (ValueError, TypeError):
        return 0.0
