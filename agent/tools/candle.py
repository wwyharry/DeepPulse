import json

from agent.tools.decorator import tool

# ============ K线图工具 ============

@tool(
    "生成K线图（带均线、MACD等技术指标叠加），自动在浏览器中打开。比纯文字更直观地展示走势。",
    param_desc=[
            "股票代码",
            "显示天数",
            "显示MACD",
            "显示RSI",
        ],
)
def generate_chart(code: str, days: int = 120, show_macd: bool = True, show_rsi: bool = False) -> str:
    """生成K线图（带均线、MACD等技术指标），自动在浏览器中打开。

    Args:
        code: 股票代码
        days: 显示天数（交易日），默认120
        show_macd: 是否显示MACD，默认true
        show_rsi: 是否显示RSI，默认false
    """
    from agent.charts import generate_kline_chart

    try:
        path = generate_kline_chart(code, days=int(days), show_macd=show_macd, show_rsi=show_rsi, auto_open=True)
        return json.dumps({"status": "ok", "path": path}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"生成图表失败: {e}"}, ensure_ascii=False)


@tool(
    "生成多只股票涨幅对比图（归一化），自动在浏览器中打开。用于横向对比多只股票的强弱。",
    param_desc=[
            "股票代码，逗号分隔",
            "对比天数",
        ],
)
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