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
