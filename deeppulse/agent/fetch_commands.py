"""`/fetch` 斜杠命令 — 交互式数据采集（CLI/TUI 共用）

非技术用户无需运行 scripts/ 脚本：在 CLI 或 TUI 里直接 /fetch 触发
股票列表/日K 采集与增量更新。包装现有 collector（断点续传/重连复用），
进度经 logging.Handler 转发到 log 回调（CLI=print，TUI=chat_log）。
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

FETCH_HELP = """用法:
  /fetch stocks            采集股票列表（约1分钟）
  /fetch kline <code>      采集单只股票全部历史日K
  /fetch index             采集大盘指数日K（6大指数，秒级）
  /fetch timeframe [days=5] [tf=all|5m|15m|30m|60m|weekly]
                           批量采集分钟/周线（东财直连，断点续传；每周期约 1-2 小时）
  /fetch all               全量采集（列表+全部日K+指数，约50-100分钟，可中断续传）
  /fetch update [days]     增量更新（默认回溯5天）
  /fetch status            查看本地数据统计与最近采集日志"""

_LOGGER_NAME = "deeppulse.src.collector"


class _LogForwarder(logging.Handler):
    """把 collector 的日志转发到 UI 回调"""

    def __init__(self, log):
        super().__init__(level=logging.INFO)
        self._log = log

    def emit(self, record):
        try:
            self._log(record.getMessage())
        except Exception:  # noqa: BLE001 — UI 回调异常不影响采集
            pass


def _fmt_stats(stats: dict, elapsed: float) -> str:
    return (
        f"采集完成：共 {stats.get('total', 0)} 只，成功 {stats.get('success', 0)}，"
        f"失败 {stats.get('failed', 0)}，新增 {stats.get('rows', 0)} 行，耗时 {elapsed:.1f}s"
    )


def _fetch_status() -> str:
    from deeppulse.src.query import StockQuery

    query = StockQuery(online_fallback=False)
    stats = query.get_data_stats()
    dr = stats.get("date_range") or (None, None)
    lines = [
        "📊 本地数据统计",
        f"  股票数量: {stats.get('stock_count', 0)}",
        f"  K线总行数: {stats.get('kline_count', 0)}",
        f"  日期范围: {dr[0]} ~ {dr[1]}",
    ]
    try:
        log_df = query.get_fetch_log(limit=5)
        if not log_df.empty:
            lines.append("  最近采集:")
            for _, row in log_df.iterrows():
                lines.append(
                    f"    {row.get('fetched_at', '')} {row.get('code', '')} "
                    f"{row.get('status', '')} +{row.get('row_count', 0)}行"
                )
    except Exception:  # noqa: BLE001 — 日志表缺不阻塞统计
        pass
    return "\n".join(lines)


def run_fetch_command(arg: str, log=print) -> str:
    """解析并执行 /fetch 子命令。log 为进度输出回调；返回结果摘要文本。"""
    arg = (arg or "").strip()
    if not arg or arg in ("help", "帮助"):
        return FETCH_HELP

    parts = arg.split()
    sub = parts[0].lower()
    from deeppulse.src import collector

    handler = _LogForwarder(log)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.addHandler(handler)
    t0 = time.time()
    try:
        if sub == "status":
            return _fetch_status()

        if sub == "stocks":
            log("开始采集股票列表...")
            n = collector.fetch_and_store_stock_list()
            return f"✅ 股票列表采集完成：{n} 只，耗时 {time.time() - t0:.1f}s"

        if sub == "kline":
            if len(parts) < 2:
                return "用法: /fetch kline <code>，如 /fetch kline 600519"
            code = parts[1].strip()
            log(f"开始采集 {code} 历史日K...")
            stats = collector.fetch_all_kline(codes=[code])
            return _fmt_stats(stats, time.time() - t0)

        if sub == "index":
            log("开始采集大盘指数日K...")
            stats = collector.fetch_all_index_kline()
            return _fmt_stats(stats, time.time() - t0)

        if sub == "timeframe":
            days = 5
            tf = "all"
            rest = parts[1:]
            for token in rest:
                if token.isdigit():
                    days = max(1, int(token))
                else:
                    tf = token.lower()
            valid = ("all", "5m", "15m", "30m", "60m", "weekly")
            if tf not in valid:
                return f"用法: /fetch timeframe [days=5] [tf={'|'.join(valid)}]"
            from deeppulse.agent import timeframes as tf_mod

            targets = tf_mod.BATCH_TIMEFRAMES if tf == "all" else [tf]
            lines = []
            for one in targets:
                log(f"开始批量采集 {one}（窗口 {days} 天，断点续传）...")
                stats = tf_mod.fetch_all_timeframe(one, days=days)
                if "error" in stats:
                    lines.append(f"{one}: {stats['error']}")
                    continue
                lines.append(
                    f"{one}: 成功 {stats.get('success', 0)}/跳过 {stats.get('skipped', 0)}/"
                    f"失败 {stats.get('failed', 0)}，新增 {stats.get('rows', 0)} 行"
                )
                log(lines[-1])
            return f"✅ 分钟/周线批量采集完成（总耗时 {time.time() - t0:.1f}s）\n" + "\n".join(lines)

        if sub == "all":
            log("开始全量采集（先股票列表，后全部日K+指数，可中断续传）...")
            n = collector.fetch_and_store_stock_list()
            log(f"股票列表完成：{n} 只，开始采集日K...")
            stats = collector.fetch_all_kline()
            idx_stats = collector.fetch_all_index_kline()
            log(f"指数采集完成：{idx_stats.get('rows', 0)} 行")
            return _fmt_stats(stats, time.time() - t0)

        if sub == "update":
            days = 5
            if len(parts) > 1:
                try:
                    days = max(1, int(parts[1]))
                except ValueError:
                    return "用法: /fetch update [days]，days 为正整数"
            start = date.today() - timedelta(days=days)
            log(f"增量更新：回溯 {days} 天（{start} 起）...")
            stats = collector.fetch_all_kline(start_date=start)
            return _fmt_stats(stats, time.time() - t0)

        return f"未知子命令: {sub}\n{FETCH_HELP}"
    finally:
        logger.removeHandler(handler)
