"""K线图与技术指标可视化 - 生成专业K线图并自动打开"""

import html as html_mod
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from deeppulse.agent.backtest import add_indicators, calc_ma, calc_macd, calc_rsi
from deeppulse.src.query import StockQuery

# 输出目录
CHARTS_DIR = Path(__file__).parent.parent / "charts"
CHARTS_DIR.mkdir(exist_ok=True)


def generate_kline_chart(
    code: str,
    days: int = 120,
    title: str = None,
    show_volume: bool = True,
    show_macd: bool = True,
    show_rsi: bool = False,
    show_kdj: bool = False,
    mark_signals: list = None,
    save_path: str = None,
    auto_open: bool = True,
) -> str:
    """
    生成K线图（带技术指标叠加）

    Args:
        code: 股票代码
        days: 显示天数（交易日）
        title: 图表标题
        show_volume: 是否显示成交量
        show_macd: 是否显示MACD
        show_rsi: 是否显示RSI
        show_kdj: 是否显示KDJ
        mark_signals: 标记信号点 [{"date": "2024-01-15", "type": "buy/sell", "label": "金叉"}]
        save_path: 保存路径，默认 charts/{code}_{date}.html
        auto_open: 是否自动打开浏览器

    Returns:
        生成的文件路径
    """
    try:
        import matplotlib

        matplotlib.use("Agg")  # 使用非交互后端，避免在子线程初始化 Tcl/Tk
        import mplfinance as mpf
    except ImportError:
        return _generate_html_chart(
            code, days, title, show_volume, show_macd, show_rsi, show_kdj, mark_signals, save_path, auto_open
        )

    query = StockQuery()
    df = query.get_daily_kline(code, limit=days)

    if df.empty:
        return f"错误: 未找到 {code} 的K线数据"

    # 获取股票名称
    name = ""
    info = query.get_stock_info(code)
    if not info.empty:
        name = info.iloc[0].get("name", "")

    chart_title = title or f"{name}({code})"

    # 准备数据格式
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df.set_index("trade_date", inplace=True)
    df.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"}, inplace=True
    )

    # 添加技术指标
    for p in [5, 10, 20, 60]:
        df[f"MA{p}"] = calc_ma(df["Close"], p)

    # 构建附加图
    addplots = []

    # 均线
    colors = ["#FF6600", "#0066FF", "#00CC00", "#CC00CC"]
    for i, p in enumerate([5, 10, 20, 60]):
        if f"MA{p}" in df.columns:
            addplots.append(mpf.make_addplot(df[f"MA{p}"], panel=0, color=colors[i], width=0.8, label=f"MA{p}"))

    # MACD
    if show_macd:
        dif, dea, macd_hist = calc_macd(df["Close"])
        df["DIF"] = dif
        df["DEA"] = dea
        df["MACD_HIST"] = macd_hist

        macd_colors = ["#FF0000" if v >= 0 else "#00FF00" for v in macd_hist.dropna()]
        if len(macd_colors) > 0:
            addplots.append(mpf.make_addplot(df["DIF"], panel=2, color="#FF6600", width=0.8, ylabel="MACD"))
            addplots.append(mpf.make_addplot(df["DEA"], panel=2, color="#0066FF", width=0.8))

    # RSI
    if show_rsi:
        df["RSI6"] = calc_rsi(df["Close"], 6)
        df["RSI14"] = calc_rsi(df["Close"], 14)
        panel_id = 3 if show_macd else 2
        addplots.append(mpf.make_addplot(df["RSI6"], panel=panel_id, color="#FF6600", width=0.8, ylabel="RSI"))
        addplots.append(mpf.make_addplot(df["RSI14"], panel=panel_id, color="#0066FF", width=0.8))

    # 保存路径
    if save_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = str(CHARTS_DIR / f"{code}_{timestamp}.png")

    # 绘图风格 - 自动选择可用的中文字体
    import matplotlib

    matplotlib.rcParams["axes.unicode_minus"] = False

    # 尝试多种中文字体
    font_found = False
    for font in [
        "Microsoft YaHei",
        "SimHei",
        "PingFang SC",
        "Heiti SC",
        "WenQuanYi Micro Hei",
        "Noto Sans CJK SC",
        "sans-serif",
    ]:
        try:
            from matplotlib.font_manager import FontProperties

            fp = FontProperties(family=font)
            if fp.get_name() != font and font != "sans-serif":
                continue
            matplotlib.rcParams["font.sans-serif"] = [font] + matplotlib.rcParams.get("font.sans-serif", [])
            font_found = True
            break
        except Exception:
            continue

    if not font_found:
        # 使用默认字体，中文可能显示为方块但不会报错
        matplotlib.rcParams["font.sans-serif"] = ["DejaVu Sans"]

    mc = mpf.make_marketcolors(up="#FF3333", down="#00AA00", inherit=True)
    style = mpf.make_mpf_style(marketcolors=mc, gridstyle="--", gridcolor="#E0E0E0")

    # 面板比例
    panel_ratios = [4, 1]  # K线 + 成交量
    if show_macd:
        panel_ratios.append(1)
    if show_rsi:
        panel_ratios.append(1)

    kwargs = dict(
        type="candle",
        style=style,
        title=chart_title,
        addplot=addplots if addplots else None,
        volume=show_volume,
        figsize=(16, 10),
        panel_ratios=panel_ratios,
        savefig=dict(fname=save_path, dpi=150, bbox_inches="tight"),
    )

    mpf.plot(df, **kwargs)

    if auto_open:
        import webbrowser

        webbrowser.open(f"file:///{save_path.replace(chr(92), '/')}")

    return save_path


def _generate_html_chart(
    code: str,
    days: int = 120,
    title: str = None,
    show_volume: bool = True,
    show_macd: bool = True,
    show_rsi: bool = False,
    show_kdj: bool = False,
    mark_signals: list = None,
    save_path: str = None,
    auto_open: bool = True,
) -> str:
    """备用方案：生成 HTML 交互式K线图（ECharts）"""
    query = StockQuery()
    df = query.get_daily_kline(code, limit=days)

    if df.empty:
        return f"错误: 未找到 {code} 的K线数据"

    name = ""
    info = query.get_stock_info(code)
    if not info.empty:
        name = info.iloc[0].get("name", "")

    chart_title = title or f"{name}({code})"
    df = add_indicators(df)

    # 准备数据（日期统一 YYYY-MM-DD，与 mark_signals 的 docstring 约定一致）
    dates = [str(d)[:10] for d in df["trade_date"]]
    ohlc = df[["open", "high", "low", "close"]].values.tolist()
    volumes = df["volume"].tolist()
    ma5 = df["ma5"].round(2).where(df["ma5"].notna(), None).tolist()
    ma10 = df["ma10"].round(2).where(df["ma10"].notna(), None).tolist()
    ma20 = df["ma20"].round(2).where(df["ma20"].notna(), None).tolist()
    ma60 = df["ma60"].round(2).where(df["ma60"].notna(), None).tolist()

    # MACD
    macd_data = []
    if show_macd:
        dif = df["dif"].round(4).where(df["dif"].notna(), None).tolist()
        dea = df["dea"].round(4).where(df["dea"].notna(), None).tolist()
        macd_hist = df["macd"].round(4).where(df["macd"].notna(), None).tolist()
        macd_data = [dif, dea, macd_hist]

    # RSI
    rsi_data = []
    if show_rsi:
        rsi_data = [
            df["rsi6"].round(2).where(df["rsi6"].notna(), None).tolist(),
            df["rsi14"].round(2).where(df["rsi14"].notna(), None).tolist(),
        ]

    # 信号标记
    mark_points = []
    if mark_signals:
        for sig in mark_signals:
            mark_points.append(
                {
                    "date": sig.get("date", ""),
                    "type": sig.get("type", "buy"),
                    "label": sig.get("label", ""),
                }
            )

    # 生成HTML
    if save_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = str(CHARTS_DIR / f"{code}_{timestamp}.html")

    html = _build_echarts_html(
        chart_title, dates, ohlc, volumes, ma5, ma10, ma20, ma60, macd_data, rsi_data, mark_points
    )

    Path(save_path).write_text(html, encoding="utf-8")

    if auto_open:
        import webbrowser

        webbrowser.open(f"file:///{save_path.replace(chr(92), '/')}")

    return save_path


def _build_echarts_html(title, dates, ohlc, volumes, ma5, ma10, ma20, ma60, macd_data, rsi_data, mark_points) -> str:
    """构建 ECharts K线图 HTML"""

    # 计算面板数量
    sub_panels = []
    if volumes:
        sub_panels.append("volume")
    if macd_data:
        sub_panels.append("macd")
    if rsi_data:
        sub_panels.append("rsi")

    # grid 配置
    grids = []
    y_axes = []
    x_axes = []

    # 主图 grid
    main_height = "45%" if len(sub_panels) >= 2 else "55%"
    grids.append({"left": "8%", "right": "3%", "top": "8%", "height": main_height})
    y_axes.append({"gridIndex": 0, "scale": True, "splitArea": {"show": True}})
    x_axes.append({"gridIndex": 0, "data": dates, "show": False})

    # 子图
    grid_top_offset = int(main_height.rstrip("%")) + 12
    for i, _panel in enumerate(sub_panels):
        top = f"{grid_top_offset + i * 15}%"
        grids.append({"left": "8%", "right": "3%", "top": top, "height": "12%"})
        y_axes.append({"gridIndex": i + 1, "scale": True, "splitNumber": 2})
        x_axes.append(
            {"gridIndex": i + 1, "data": dates, "show": i == len(sub_panels) - 1, "axisLabel": {"fontSize": 10}}
        )

    # K线 series
    series = [
        {
            "name": "K线",
            "type": "candlestick",
            "data": ohlc,
            "xAxisIndex": 0,
            "yAxisIndex": 0,
            "itemStyle": {
                "color": "#ef232a",
                "color0": "#14b143",
                "borderColor": "#ef232a",
                "borderColor0": "#14b143",
            },
        }
    ]

    # 均线
    ma_configs = [
        (ma5, "MA5", "#FF6600"),
        (ma10, "MA10", "#0066FF"),
        (ma20, "MA20", "#00CC00"),
        (ma60, "MA60", "#CC00CC"),
    ]
    for data, name, color in ma_configs:
        series.append(
            {
                "name": name,
                "type": "line",
                "data": data,
                "xAxisIndex": 0,
                "yAxisIndex": 0,
                "lineStyle": {"width": 1},
                "symbol": "none",
                "itemStyle": {"color": color},
            }
        )

    # 信号标记（锚定真实价位，避免 y=0 远离K线并拉扁坐标轴）
    if mark_points:
        date_index = {d: i for i, d in enumerate(dates)}

        def _mp_items(points, is_buy):
            items = []
            for p in points:
                d = str(p.get("date", ""))[:10]
                i = date_index.get(d)
                if i is None or i >= len(ohlc):
                    continue  # 日期不在窗口内则跳过，不落在 0 价位
                _o, high, low, _c = (float(v) for v in ohlc[i])
                price = low * 0.99 if is_buy else high * 1.01
                items.append(
                    {
                        "coord": [d, round(price, 2)],
                        "value": p.get("label", ""),
                        "itemStyle": {"color": "#FF0000" if is_buy else "#00AA00"},
                        "symbolRotate": 0 if is_buy else 180,
                    }
                )
            return items

        mp_data = _mp_items([p for p in mark_points if p["type"] == "buy"], True)
        mp_data += _mp_items([p for p in mark_points if p["type"] == "sell"], False)
        if mp_data:
            series[0]["markPoint"] = {
                "data": mp_data,
                "symbol": "triangle",
                "symbolSize": 15,
            }

    # 成交量
    vol_colors = []
    for i in range(len(ohlc)):
        if i == 0:
            vol_colors.append("#ef232a")
        else:
            vol_colors.append("#ef232a" if ohlc[i][3] >= ohlc[i - 1][3] else "#14b143")

    if volumes:
        vol_idx = sub_panels.index("volume") + 1
        series.append(
            {
                "name": "成交量",
                "type": "bar",
                # 数据点级 itemStyle：纯 JSON 可序列化，按涨跌着色
                "data": [
                    {
                        "value": v,
                        "itemStyle": {"color": vol_colors[i] if i < len(vol_colors) else "#ef232a"},
                    }
                    for i, v in enumerate(volumes)
                ],
                "xAxisIndex": vol_idx,
                "yAxisIndex": vol_idx,
            }
        )

    # MACD
    if macd_data:
        macd_idx = sub_panels.index("macd") + 1
        dif, dea, hist = macd_data
        hist_colors = ["#ef232a" if (v is not None and v >= 0) else "#14b143" for v in hist]
        series.extend(
            [
                {
                    "name": "DIF",
                    "type": "line",
                    "data": dif,
                    "xAxisIndex": macd_idx,
                    "yAxisIndex": macd_idx,
                    "lineStyle": {"width": 1},
                    "symbol": "none",
                    "itemStyle": {"color": "#FF6600"},
                },
                {
                    "name": "DEA",
                    "type": "line",
                    "data": dea,
                    "xAxisIndex": macd_idx,
                    "yAxisIndex": macd_idx,
                    "lineStyle": {"width": 1},
                    "symbol": "none",
                    "itemStyle": {"color": "#0066FF"},
                },
                {
                    "name": "MACD",
                    "type": "bar",
                    # 数据点级 itemStyle：None 保持 value: null 不画柱
                    "data": [
                        (
                            v
                            if v is None
                            else {
                                "value": v,
                                "itemStyle": {"color": hist_colors[i] if i < len(hist_colors) else "#ef232a"},
                            }
                        )
                        for i, v in enumerate(hist)
                    ],
                    "xAxisIndex": macd_idx,
                    "yAxisIndex": macd_idx,
                },
            ]
        )

    # RSI
    if rsi_data:
        rsi_idx = sub_panels.index("rsi") + 1
        series.extend(
            [
                {
                    "name": "RSI6",
                    "type": "line",
                    "data": rsi_data[0],
                    "xAxisIndex": rsi_idx,
                    "yAxisIndex": rsi_idx,
                    "lineStyle": {"width": 1},
                    "symbol": "none",
                    "itemStyle": {"color": "#FF6600"},
                },
                {
                    "name": "RSI14",
                    "type": "line",
                    "data": rsi_data[1],
                    "xAxisIndex": rsi_idx,
                    "yAxisIndex": rsi_idx,
                    "lineStyle": {"width": 1},
                    "symbol": "none",
                    "itemStyle": {"color": "#0066FF"},
                },
            ]
        )

    # 标题按上下文分别转义：JS 字符串用 json.dumps，HTML 文本用 html.escape
    title_js = json.dumps(title, ensure_ascii=False).replace("</", "<\\/")
    title_html = html_mod.escape(title)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title_html}</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>body {{ margin:0; background:#1a1a2e; }}</style>
</head>
<body>
<div id="chart" style="width:100%;height:100vh;"></div>
<script>
var chart = echarts.init(document.getElementById('chart'), 'dark');
var option = {{
    title: {{ text: {title_js}, left: 'center', top: 10,
              textStyle: {{ color: '#eee', fontSize: 16 }} }},
    tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
    legend: {{ top: 40, data: ['MA5','MA10','MA20','MA60'],
               textStyle: {{ color: '#aaa' }} }},
    axisPointer: {{ link: [{{ xAxisIndex: 'all' }}] }},
    dataZoom: [
        {{ type: 'inside', xAxisIndex: {list(range(len(x_axes)))}, start: 50, end: 100 }},
        {{ type: 'slider', xAxisIndex: {list(range(len(x_axes)))}, bottom: 5, height: 20,
           start: 50, end: 100 }}
    ],
    grid: {json.dumps(grids)},
    xAxis: {json.dumps(x_axes)},
    yAxis: {json.dumps(y_axes)},
    series: {json.dumps(series, default=str)}
}};
chart.setOption(option);
window.addEventListener('resize', () => chart.resize());
</script>
</body>
</html>"""


def generate_comparison_chart(
    codes: list[str], days: int = 60, metric: str = "close", save_path: str = None, auto_open: bool = True
) -> str:
    """生成多只股票对比图（归一化涨幅对比）"""
    query = StockQuery()
    all_data = {}

    for code in codes:
        df = query.get_daily_kline(code, limit=days)
        if df.empty:
            continue
        info = query.get_stock_info(code)
        name = info.iloc[0]["name"] if not info.empty else code
        # 归一化
        base = df["close"].iloc[0]
        df["normalized"] = df["close"] / base * 100
        all_data[f"{name}({code})"] = df.set_index("trade_date")["normalized"]

    if not all_data:
        return "错误: 无有效数据"

    # 构建HTML
    combined = pd.DataFrame(all_data)
    dates = [str(d) for d in combined.index]

    if save_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = str(CHARTS_DIR / f"compare_{timestamp}.html")

    series = []
    for col in combined.columns:
        series.append(
            {
                "name": col,
                "type": "line",
                "data": combined[col].round(2).where(combined[col].notna(), None).tolist(),
                "symbol": "none",
                "lineStyle": {"width": 2},
            }
        )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>股票对比</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<style>body {{ margin:0; background:#1a1a2e; }}</style>
</head>
<body>
<div id="chart" style="width:100%;height:100vh;"></div>
<script>
var chart = echarts.init(document.getElementById('chart'), 'dark');
chart.setOption({{
    title: {{ text: '涨幅对比（基期=100）', left: 'center', top: 10 }},
    tooltip: {{ trigger: 'axis' }},
    legend: {{ top: 40 }},
    grid: {{ left: '8%', right: '5%', top: '15%', bottom: '10%' }},
    xAxis: {{ data: {json.dumps(dates)}, axisLabel: {{ rotate: 30 }} }},
    yAxis: {{ name: '归一化涨幅' }},
    dataZoom: [{{ type: 'inside', start: 30, end: 100 }},
               {{ type: 'slider', bottom: 5, height: 20, start: 30, end: 100 }}],
    series: {json.dumps(series)}
}});
window.addEventListener('resize', () => chart.resize());
</script>
</body>
</html>"""

    Path(save_path).write_text(html, encoding="utf-8")

    if auto_open:
        import webbrowser

        webbrowser.open(f"file:///{save_path.replace(chr(92), '/')}")

    return save_path
