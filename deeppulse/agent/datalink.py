"""扩展数据源 - 龙虎榜、北向资金、融资融券、板块资金流"""

from datetime import date, timedelta


def get_dragon_tiger(trade_date: str = None) -> dict:
    """获取龙虎榜数据（东方财富）

    Args:
        trade_date: 交易日期 YYYYMMDD，默认今天
    """
    import akshare as ak

    try:
        if trade_date is None:
            trade_date = date.today().strftime("%Y%m%d")

        # 龙虎榜详情
        df = ak.stock_lhb_detail_em(
            start_date=trade_date,
            end_date=trade_date,
        )

        if df is None or df.empty:
            return {"date": trade_date, "count": 0, "stocks": [], "message": "无龙虎榜数据（非交易日或数据未更新）"}

        stocks = []
        for _, row in df.head(30).iterrows():
            stocks.append(
                {
                    "code": str(row.get("代码", "")).zfill(6),
                    "name": str(row.get("名称", "")),
                    "close": round(float(row.get("收盘价", 0)), 2) if row.get("收盘价") else None,
                    "change_pct": round(float(row.get("涨跌幅", 0)), 2) if row.get("涨跌幅") else None,
                    "reason": str(row.get("解读", row.get("上榜原因", ""))),
                    "net_buy": round(float(row.get("龙虎榜净买额", 0)) / 1e8, 2) if row.get("龙虎榜净买额") else None,
                    "buy_total": round(float(row.get("龙虎榜买入额", 0)) / 1e8, 2) if row.get("龙虎榜买入额") else None,
                    "sell_total": round(float(row.get("龙虎榜卖出额", 0)) / 1e8, 2)
                    if row.get("龙虎榜卖出额")
                    else None,
                    "turnover": round(float(row.get("换手率", 0)), 2) if row.get("换手率") else None,
                    "amount": round(float(row.get("成交额", 0)) / 1e8, 2) if row.get("成交额") else None,
                }
            )

        return {"date": trade_date, "count": len(stocks), "stocks": stocks}
    except Exception as e:
        return {"error": f"获取龙虎榜数据失败: {e}"}


def get_northbound_flow(trade_date: str = None) -> dict:
    """获取北向资金（沪股通+深股通）当日流入数据"""
    import akshare as ak

    try:
        # 沪深港通资金流向汇总
        df = ak.stock_hsgt_fund_flow_summary_em()

        if df is None or df.empty:
            return {"error": "无北向资金数据"}

        # 筛选北向数据
        col_map = {}
        for c in df.columns:
            if "日期" in c or "date" in c.lower():
                col_map["date"] = c
            elif "资金流入" in c or "净流入" in c:
                col_map["net_flow"] = c
            elif "当日" in c and "流入" in c:
                col_map["net_flow"] = c

        # 取最新数据
        latest_rows = df.tail(6)
        flows = []
        for _, row in latest_rows.iterrows():
            date_val = str(row.get(col_map.get("date", df.columns[0]), ""))
            flow_val = 0
            for c in df.columns:
                try:
                    v = float(row.get(c, 0))
                    if abs(v) > 100:  # 资金数额通常较大
                        flow_val = v
                        break
                except (ValueError, TypeError):
                    continue
            flows.append(
                {
                    "date": date_val,
                    "net_flow_billion": round(flow_val / 1e8, 2) if abs(flow_val) > 1 else round(flow_val, 2),
                }
            )

        latest = flows[-1] if flows else {}
        total_recent = sum(f["net_flow_billion"] for f in flows[-3:])

        return {
            "latest_date": latest.get("date", ""),
            "latest_net_flow_billion": latest.get("net_flow_billion", 0),
            "total_recent_net_flow_billion": round(total_recent, 2),
            "daily_flows": flows[-5:],
            "trend": "持续流入" if total_recent > 0 else "持续流出" if total_recent < 0 else "平衡",
        }
    except Exception as e:
        return {"error": f"获取北向资金数据失败: {e}"}


def get_sector_fund_flow() -> dict:
    """获取板块资金流向（行业+概念）"""
    import akshare as ak

    try:
        # 行业板块资金流
        df_industry = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
        industries = []
        if df_industry is not None and not df_industry.empty:
            for _, row in df_industry.head(10).iterrows():
                industries.append(
                    {
                        "name": str(row.get("名称", "")),
                        "change_pct": round(float(row.get("今日涨跌幅", 0)), 2) if row.get("今日涨跌幅") else 0,
                        "net_flow_billion": round(float(row.get("今日主力净流入-净额", 0)) / 1e8, 2)
                        if row.get("今日主力净流入-净额")
                        else 0,
                    }
                )

        # 概念板块资金流
        df_concept = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="概念资金流")
        concepts = []
        if df_concept is not None and not df_concept.empty:
            for _, row in df_concept.head(10).iterrows():
                concepts.append(
                    {
                        "name": str(row.get("名称", "")),
                        "change_pct": round(float(row.get("今日涨跌幅", 0)), 2) if row.get("今日涨跌幅") else 0,
                        "net_flow_billion": round(float(row.get("今日主力净流入-净额", 0)) / 1e8, 2)
                        if row.get("今日主力净流入-净额")
                        else 0,
                    }
                )

        return {
            "industries_top10": industries,
            "concepts_top10": concepts,
        }
    except Exception as e:
        return {"error": f"获取板块资金流向失败: {e}"}


def get_margin_trading(trade_date: str = None) -> dict:
    """获取融资融券数据"""
    import akshare as ak

    try:
        df = ak.stock_margin_sse(
            start_date=(date.today() - timedelta(days=10)).strftime("%Y%m%d"), end_date=date.today().strftime("%Y%m%d")
        )
        if df is None or df.empty:
            return {"message": "无融资融券数据"}

        df = df.tail(5)
        records = []
        for _, row in df.iterrows():
            records.append(
                {
                    "date": str(row.get("信用交易日期", "")),
                    "margin_balance_billion": round(float(row.get("融资余额(元)", 0)) / 1e8, 2)
                    if row.get("融资余额(元)")
                    else 0,
                    "short_balance_billion": round(float(row.get("融券余量金额(元)", 0)) / 1e8, 2)
                    if row.get("融券余量金额(元)")
                    else 0,
                }
            )

        return {"data": records}
    except Exception as e:
        return {"error": f"获取融资融券数据失败: {e}"}


def get_stock_dragon_tiger_detail(code: str, days: int = 30) -> dict:
    """获取个股龙虎榜历史明细"""
    import akshare as ak

    try:
        end = date.today().strftime("%Y%m%d")
        start = (date.today() - timedelta(days=days)).strftime("%Y%m%d")

        df = ak.stock_lhb_detail_em(start_date=start, end_date=end)

        if df is None or df.empty:
            return {"code": code, "message": "无龙虎榜数据"}

        # 筛选指定股票
        df = df[df["代码"].astype(str).str.zfill(6) == code.zfill(6)]

        if df.empty:
            return {"code": code, "message": f"{code} 近{days}天未上龙虎榜"}

        records = []
        for _, row in df.iterrows():
            records.append(
                {
                    "date": str(row.get("日期", "")),
                    "close": round(float(row.get("收盘价", 0)), 2) if row.get("收盘价") else None,
                    "change_pct": round(float(row.get("涨跌幅", 0)), 2) if row.get("涨跌幅") else None,
                    "net_buy": round(float(row.get("龙虎榜净买额", 0)) / 1e8, 2) if row.get("龙虎榜净买额") else None,
                    "reason": str(row.get("解读", row.get("上榜原因", ""))),
                }
            )

        return {"code": code, "count": len(records), "records": records}
    except Exception as e:
        return {"error": f"获取个股龙虎榜数据失败: {e}"}
