"""市场情绪分析 - 涨停/跌停统计、连板高度、板块排行、资金流向"""
import json
from datetime import date, datetime


def get_limit_up_pool(trade_date: str = None) -> dict:
    """获取涨停股池（东方财富）

    Args:
        trade_date: 交易日期 YYYYMMDD，默认今天
    """
    import akshare as ak
    try:
        if trade_date is None:
            trade_date = date.today().strftime("%Y%m%d")
        df = ak.stock_zt_pool_em(date=trade_date)
        if df is None or df.empty:
            return {"date": trade_date, "count": 0, "stocks": [], "message": "无涨停数据（非交易日或数据未更新）"}

        stocks = []
        for _, row in df.iterrows():
            stocks.append({
                "code": str(row.get("代码", "")).zfill(6),
                "name": str(row.get("名称", "")),
                "price": round(float(row.get("最新价", 0)), 2) if row.get("最新价") else None,
                "change_pct": round(float(row.get("涨跌幅", 0)), 2) if row.get("涨跌幅") else None,
                "turnover": round(float(row.get("换手率", 0)), 2) if row.get("换手率") else None,
                "amount": round(float(row.get("成交额", 0)) / 1e8, 2) if row.get("成交额") else None,
                "first_time": str(row.get("首次封板时间", "")),
                "last_time": str(row.get("最后封板时间", "")),
                "open_count": int(row.get("炸板次数", 0)) if row.get("炸板次数") else 0,
                "streak": int(row.get("连板数", 1)) if row.get("连板数") else 1,
                "industry": str(row.get("所属行业", "")),
            })

        return {
            "date": trade_date,
            "count": len(stocks),
            "stocks": stocks,
        }
    except Exception as e:
        return {"error": f"获取涨停数据失败: {e}"}


def get_limit_down_pool(trade_date: str = None) -> dict:
    """获取跌停股池"""
    import akshare as ak
    try:
        if trade_date is None:
            trade_date = date.today().strftime("%Y%m%d")
        df = ak.stock_zt_pool_dtgc_em(date=trade_date)
        if df is None or df.empty:
            return {"date": trade_date, "count": 0, "stocks": []}

        stocks = []
        for _, row in df.iterrows():
            stocks.append({
                "code": str(row.get("代码", "")).zfill(6),
                "name": str(row.get("名称", "")),
                "price": round(float(row.get("最新价", 0)), 2) if row.get("最新价") else None,
                "change_pct": round(float(row.get("涨跌幅", 0)), 2) if row.get("涨跌幅") else None,
            })
        return {"date": trade_date, "count": len(stocks), "stocks": stocks}
    except Exception as e:
        return {"error": f"获取跌停数据失败: {e}"}


def get_failed_limit_up(trade_date: str = None) -> dict:
    """获取炸板股池（曾涨停又打开的股票）"""
    import akshare as ak
    try:
        if trade_date is None:
            trade_date = date.today().strftime("%Y%m%d")
        df = ak.stock_zt_pool_zbgc_em(date=trade_date)
        if df is None or df.empty:
            return {"date": trade_date, "count": 0, "stocks": []}

        stocks = []
        for _, row in df.iterrows():
            stocks.append({
                "code": str(row.get("代码", "")).zfill(6),
                "name": str(row.get("名称", "")),
                "price": round(float(row.get("最新价", 0)), 2) if row.get("最新价") else None,
                "change_pct": round(float(row.get("涨跌幅", 0)), 2) if row.get("涨跌幅") else None,
            })
        return {"date": trade_date, "count": len(stocks), "stocks": stocks}
    except Exception as e:
        return {"error": f"获取炸板数据失败: {e}"}


def get_market_sentiment(trade_date: str = None) -> dict:
    """综合市场情绪统计

    返回涨停数、跌停数、炸板数、连板高度、涨跌停比、情绪评级
    """
    if trade_date is None:
        trade_date = date.today().strftime("%Y%m%d")

    zt = get_limit_up_pool(trade_date)
    dt = get_limit_down_pool(trade_date)
    zb = get_failed_limit_up(trade_date)

    zt_count = zt.get("count", 0)
    dt_count = dt.get("count", 0)
    zb_count = zb.get("count", 0)

    # 连板分析
    streak_dist = {}
    max_streak = 0
    top_streak_stock = ""
    if zt.get("stocks"):
        for s in zt["stocks"]:
            streak = s.get("streak", 1)
            streak_dist[streak] = streak_dist.get(streak, 0) + 1
            if streak > max_streak:
                max_streak = streak
                top_streak_stock = f"{s['name']}({s['code']})"

    # 炸板率
    total_touch_zt = zt_count + zb_count
    zb_rate = round(zb_count / total_touch_zt * 100, 1) if total_touch_zt > 0 else 0

    # 涨跌停比
    zt_dt_ratio = round(zt_count / dt_count, 1) if dt_count > 0 else float('inf') if zt_count > 0 else 0

    # 情绪评级
    if zt_count >= 80 and max_streak >= 5 and zb_rate < 25:
        sentiment = "🔥 高潮期"
        advice = "赚钱效应强，但需警惕退潮，逐步锁定利润"
    elif zt_count >= 40 and max_streak >= 4 and zb_rate < 35:
        sentiment = "📈 发酵期"
        advice = "情绪向好，可积极参与龙头和强势方向"
    elif zt_count >= 20 and max_streak >= 3:
        sentiment = "🌱 启动期"
        advice = "情绪回暖，小仓位试错新方向"
    elif zt_count >= 10:
        sentiment = "⏸️ 低迷期"
        advice = "赚钱效应弱，控制仓位，等待信号"
    else:
        sentiment = "🧊 冰点期"
        advice = "极端弱势，空仓等待，不盲目抄底"

    # 行业分布
    industry_count = {}
    if zt.get("stocks"):
        for s in zt["stocks"]:
            ind = s.get("industry", "未知")
            if ind:
                industry_count[ind] = industry_count.get(ind, 0) + 1
    top_industries = sorted(industry_count.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        "date": trade_date,
        "涨停数": zt_count,
        "跌停数": dt_count,
        "炸板数": zb_count,
        "炸板率": f"{zb_rate}%",
        "涨跌停比": zt_dt_ratio if zt_dt_ratio != float('inf') else "∞",
        "最大连板": max_streak,
        "最高连板股": top_streak_stock,
        "连板分布": {f"{k}板": v for k, v in sorted(streak_dist.items())},
        "情绪评级": sentiment,
        "操作建议": advice,
        "涨停行业TOP5": {k: v for k, v in top_industries},
    }


def get_sector_ranking(board_type: str = "industry", top_n: int = 10) -> dict:
    """获取板块涨跌排行

    Args:
        board_type: "industry"=行业板块, "concept"=概念板块
        top_n: 返回前N个板块
    """
    import akshare as ak
    try:
        if board_type == "concept":
            df = ak.stock_board_concept_name_em()
        else:
            df = ak.stock_board_industry_name_em()

        if df is None or df.empty:
            return {"error": "无板块数据"}

        # 统一列名
        cols = df.columns.tolist()
        name_col = cols[0] if cols else "板块名称"
        change_col = None
        for c in cols:
            if "涨跌幅" in str(c):
                change_col = c
                break

        if change_col is None and len(cols) > 3:
            change_col = cols[3]

        df_sorted = df.sort_values(by=change_col, ascending=False) if change_col else df

        top = []
        for _, row in df_sorted.head(top_n).iterrows():
            item = {"name": str(row[name_col])}
            if change_col:
                item["change_pct"] = round(float(row[change_col]), 2)
            # 尝试获取其他有用字段
            for c in cols:
                if "主力净流入" in str(c):
                    try:
                        item["main_inflow"] = round(float(row[c]) / 1e8, 2)
                    except (ValueError, TypeError):
                        pass
                elif "领涨股" in str(c) or "上涨比" in str(c):
                    item[str(c)] = str(row[c])
            top.append(item)

        bottom = []
        for _, row in df_sorted.tail(5).iterrows():
            item = {"name": str(row[name_col])}
            if change_col:
                item["change_pct"] = round(float(row[change_col]), 2)
            bottom.append(item)

        board_label = "概念板块" if board_type == "concept" else "行业板块"
        return {
            "type": board_label,
            "top": top,
            "bottom": bottom,
        }
    except Exception as e:
        return {"error": f"获取板块数据失败: {e}"}


def get_stock_fund_flow(code: str, market: str = "sh") -> dict:
    """获取个股资金流向（东方财富）

    Args:
        code: 6位股票代码
        market: sh 或 sz
    """
    import akshare as ak
    try:
        if code.startswith("6"):
            market = "sh"
        else:
            market = "sz"
        symbol = f"{market}{code}"
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is None or df.empty:
            return {"error": f"股票 {code} 无资金流向数据"}

        # 取最近5天
        recent = df.tail(5)
        flows = []
        for _, row in recent.iterrows():
            item = {}
            for c in df.columns:
                val = row[c]
                if "日期" in str(c) or "date" in str(c).lower():
                    item["date"] = str(val)
                elif "主力净流入" in str(c):
                    try:
                        item["main_net"] = round(float(val) / 1e8, 2)
                    except (ValueError, TypeError):
                        item["main_net"] = str(val)
                elif "主力净流入占比" in str(c):
                    item["main_pct"] = str(val)
                elif "超大单" in str(c) and "净流入" in str(c):
                    try:
                        item["super_large"] = round(float(val) / 1e8, 2)
                    except (ValueError, TypeError):
                        pass
                elif "大单" in str(c) and "净流入" in str(c) and "超" not in str(c):
                    try:
                        item["large"] = round(float(val) / 1e8, 2)
                    except (ValueError, TypeError):
                        pass
            if item:
                flows.append(item)

        # 判断趋势
        if len(flows) >= 3:
            recent_main = [f.get("main_net", 0) for f in flows[-3:] if isinstance(f.get("main_net"), (int, float))]
            if recent_main and all(x > 0 for x in recent_main):
                trend = "连续主力净流入"
            elif recent_main and all(x < 0 for x in recent_main):
                trend = "连续主力净流出"
            else:
                trend = "资金流向分歧"
        else:
            trend = "数据不足"

        return {"code": code, "flows": flows, "trend": trend}
    except Exception as e:
        return {"error": f"获取资金流向失败: {e}"}
