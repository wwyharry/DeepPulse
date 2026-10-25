"""Unit tests for 股票数据可选化 — 在线兜底 / 来源标注 / /fetch 命令。

全部 mock 在线取数，不打真实网络（conftest.no_network 自动护栏）。
"""

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from deeppulse import config
from deeppulse.src import online
from deeppulse.src.query import StockQuery, result_source


def _online_df(code="600519", n=30):
    """在线日线 mock 数据（列与 daily_kline 一致）"""
    end = date.today()
    dates = [end - timedelta(days=n - 1 - i) for i in range(n)]
    return pd.DataFrame(
        {
            "code": code,
            "trade_date": dates,
            "open": [10.0 + i * 0.1 for i in range(n)],
            "high": [10.5 + i * 0.1 for i in range(n)],
            "low": [9.5 + i * 0.1 for i in range(n)],
            "close": [10.2 + i * 0.1 for i in range(n)],
            "volume": [100000 + i * 100 for i in range(n)],
            "amount": [1e6 + i * 1000 for i in range(n)],
            "turnover": [1.0] * n,
            "data_source": ["baostock"] * n,
        }
    )


def _mock_daily(monkeypatch, calls=None, df=None):
    def fake(code, start, end, source_name=None):
        if calls is not None:
            calls.append((code, start, end))
        return (df if df is not None else _online_df(code)), "online-baostock"

    monkeypatch.setattr(online, "fetch_daily_kline_online", fake)


def _mock_stock_list(monkeypatch):
    records = [
        {"code": "600519", "name": "贵州茅台", "market": "sh", "board": "main", "list_date": None},
        {"code": "000001", "name": "平安银行", "market": "sz", "board": "main", "list_date": None},
    ]
    monkeypatch.setattr(online, "fetch_stock_list_online", lambda: records)
    return records


class TestDailyKlineFallback:
    def test_empty_db_falls_back_online(self, empty_db_path, monkeypatch):
        _mock_daily(monkeypatch)
        q = StockQuery(empty_db_path, online_fallback=True)
        df = q.get_daily_kline("600519", limit=10)
        assert not df.empty
        assert result_source(df) == "online-baostock"
        assert len(df) == 10  # limit 生效

    def test_default_no_cache_to_db(self, empty_db_path, monkeypatch):
        _mock_daily(monkeypatch)
        q = StockQuery(empty_db_path, online_fallback=True)
        q.get_daily_kline("600519", limit=10)
        q2 = StockQuery(empty_db_path, online_fallback=False)
        assert q2.get_daily_kline("600519").empty  # 默认未写库

    def test_cache_to_db_writes(self, empty_db_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_CACHE_TO_DB", True)
        _mock_daily(monkeypatch)
        q = StockQuery(empty_db_path, online_fallback=True)
        q.get_daily_kline("600519", limit=10)
        df = StockQuery(empty_db_path, online_fallback=False).get_daily_kline("600519")
        assert len(df) == 10
        assert result_source(df) == "local"

    def test_online_error_propagates(self, empty_db_path, monkeypatch):
        def boom(*a, **k):
            raise online.OnlineFetchError("本地无数据，且在线获取失败")

        monkeypatch.setattr(online, "fetch_daily_kline_online", boom)
        q = StockQuery(empty_db_path, online_fallback=True)
        with pytest.raises(online.OnlineFetchError):
            q.get_daily_kline("600519")

    def test_session_cache_single_fetch(self, empty_db_path, monkeypatch, real_online):
        # 恢复真实 fetch_daily_kline_online（禁网护栏默认替换），源层 mock 让缓存逻辑生效
        calls = []

        def fake_fetch(source, code, start, end, timeout=None):
            calls.append((code, start, end))
            return _online_df(code)

        monkeypatch.setattr(online, "fetch_daily_kline_online", real_online["fetch_daily_kline_online"])
        monkeypatch.setattr("deeppulse.src.collector.fetch_kline_with_timeout", fake_fetch)
        monkeypatch.setattr(online, "_make_source", lambda name: SimpleNamespace(name=name))
        q = StockQuery(empty_db_path, online_fallback=True)
        q.get_daily_kline("600519", start_date="2026-01-01", end_date="2026-02-01")
        q.get_daily_kline("600519", start_date="2026-01-01", end_date="2026-02-01")
        assert len(calls) == 1

    def test_local_hit_skips_online(self, empty_db_path, monkeypatch, sample_kline_df):
        # 先写库
        monkeypatch.setattr(config, "DATA_CACHE_TO_DB", True)
        _mock_daily(monkeypatch)
        q = StockQuery(empty_db_path, online_fallback=True)
        q.get_daily_kline("600519", limit=10)
        # 再查应走本地、不再触发在线
        calls = []
        _mock_daily(monkeypatch, calls=calls)
        df = q.get_daily_kline("600519", limit=10)
        assert result_source(df) == "local"
        assert calls == []

    def test_offline_fallback_disabled(self, empty_db_path):
        q = StockQuery(empty_db_path, online_fallback=False)
        df = q.get_daily_kline("600519")
        assert df.empty
        assert result_source(df) == "local"


class TestStockListFallback:
    def test_stock_info_online_list(self, empty_db_path, monkeypatch):
        records = _mock_stock_list(monkeypatch)
        q = StockQuery(empty_db_path, online_fallback=True)
        df = q.get_stock_info()
        assert len(df) == len(records)
        assert result_source(df) == "online-stock-list"
        one = q.get_stock_info("600519")
        assert len(one) == 1 and one.iloc[0]["name"] == "贵州茅台"

    def test_search_online_list(self, empty_db_path, monkeypatch):
        _mock_stock_list(monkeypatch)
        q = StockQuery(empty_db_path, online_fallback=True)
        df = q.search_stocks("茅台")
        assert len(df) == 1
        assert result_source(df) == "online-stock-list"

    def test_no_fallback_when_list_populated(self, empty_db_path, monkeypatch):
        # stock_info 有数据但过滤无匹配 → 不触发在线

        from deeppulse.src.database import get_connection, init_tables

        conn = get_connection(empty_db_path)
        init_tables(conn)
        conn.execute("INSERT INTO stock_info VALUES ('000001','平安银行','sz','main',NULL,NULL,now())")
        conn.close()

        calls = []
        _mock_stock_list(monkeypatch)
        monkeypatch.setattr(online, "fetch_stock_list_online", lambda: calls.append(1) or [])

        q = StockQuery(empty_db_path, online_fallback=True)
        df = q.search_stocks("不存在的关键词")
        assert df.empty
        assert calls == []


class TestLatestPriceFallback:
    def test_latest_price_online(self, empty_db_path, monkeypatch):
        _mock_daily(monkeypatch)
        q = StockQuery(empty_db_path, online_fallback=True)
        df = q.get_latest_price(["600519"])
        assert len(df) == 1
        assert result_source(df) == "online-baostock"

    def test_latest_price_all_requires_local(self, empty_db_path, monkeypatch):
        _mock_daily(monkeypatch)
        q = StockQuery(empty_db_path, online_fallback=True)
        df = q.get_latest_price(None)
        assert df.empty
        assert "codes" in str(df.attrs.get("note", ""))


class TestToolLayer:
    def test_query_kline_tool_data_source(self, empty_db_path, monkeypatch):
        from deeppulse.agent.tools import market as market_tools

        _mock_daily(monkeypatch)
        monkeypatch.setattr(market_tools, "get_query", lambda: StockQuery(empty_db_path, online_fallback=True))
        out = json.loads(market_tools.query_kline("600519", days=10))
        assert out["count"] == 10
        assert out["data_source"] == "online-baostock"

    def test_tool_online_error_message(self, empty_db_path, monkeypatch):
        """OnlineFetchError 携带可操作提示；agent._run_tool 会兜成 error JSON（不裸抛给 LLM）"""
        from deeppulse.agent.tools import market as market_tools

        def boom(*a, **k):
            raise online.OnlineFetchError(
                "本地无数据，且在线获取失败（网络不可用或数据源限频）。请检查网络后重试，或运行 /fetch update 采集到本地。"
            )

        monkeypatch.setattr(online, "fetch_daily_kline_online", boom)
        monkeypatch.setattr(market_tools, "get_query", lambda: StockQuery(empty_db_path, online_fallback=True))
        with pytest.raises(online.OnlineFetchError) as ei:
            market_tools.query_kline("600519")
        assert "/fetch update" in str(ei.value)

    def test_market_overview_online(self, empty_db_path, monkeypatch):
        from deeppulse.agent.tools import market as market_tools

        snap = pd.DataFrame(
            {
                "代码": ["000001", "000002", "000003"],
                "名称": ["涨", "跌", "平"],
                "最新价": [11.0, 9.0, 10.0],
                "涨跌幅": [10.0, -5.0, 0.0],
                "成交额": [1e8, 5e7, 1e7],
                "量比": [2.0, 0.5, 1.0],
            }
        )
        monkeypatch.setattr(online, "fetch_market_snapshot", lambda: snap)
        monkeypatch.setattr(config, "DATA_ONLINE_FALLBACK", True)
        monkeypatch.setattr(market_tools, "get_query", lambda: StockQuery(empty_db_path, online_fallback=True))
        out = json.loads(market_tools.market_overview())
        assert out["data_source"] == "online-eastmoney-spot"
        assert out["最近交易日涨跌"]["上涨"] == 1
        assert out["最近交易日涨跌"]["下跌"] == 1
        assert out["最近交易日涨跌"]["平盘"] == 1

    def test_screen_stocks_v2_online(self, empty_db_path, monkeypatch):
        from deeppulse.agent.tools import analysis as analysis_tools

        snap = pd.DataFrame(
            {
                "代码": ["600519", "000001"],
                "名称": ["贵州茅台", "平安银行"],
                "最新价": [10.0, 11.0],
                "涨跌幅": [5.0, -2.0],
                "成交额": [2e8, 1e8],
                "量比": [3.0, 0.5],
            }
        )
        monkeypatch.setattr(online, "fetch_market_snapshot", lambda: snap)
        _mock_daily(monkeypatch, df=_online_df("600519", n=80))
        monkeypatch.setattr(config, "DATA_ONLINE_FALLBACK", True)
        monkeypatch.setattr(config, "DB_PATH", empty_db_path)  # 选股器按 config.DB_PATH 判可用性
        out = json.loads(analysis_tools.screen_stocks_v2("涨幅>3,量比>2", limit=5))
        assert out["data_source"] == "online-spot+kline"
        assert out["candidates_evaluated"] <= config.SCREEN_ONLINE_MAX_CANDIDATES
        assert "note" in out
        assert any(r["code"] == "600519" for r in out["results"])

    def test_backtest_multi_guidance_error(self, empty_db_path, monkeypatch):
        from deeppulse.agent.tools import strategy as strategy_tools

        monkeypatch.setattr(online, "local_kline_available", lambda db_path=None: False)
        out = json.loads(strategy_tools.backtest_multi_stock(codes=""))
        assert "/fetch all" in out["error"]

        out2 = json.loads(strategy_tools.backtest_multi_stock(codes=",".join(str(i) for i in range(15))))
        assert "最多" in out2["error"]


class TestIndexFallback:
    def _index_df(self, code="sh000001", n=30):
        end = date.today()
        dates = [end - timedelta(days=n - 1 - i) for i in range(n)]
        return pd.DataFrame(
            {
                "code": code,
                "trade_date": dates,
                "open": [3800.0 + i for i in range(n)],
                "high": [3810.0 + i for i in range(n)],
                "low": [3790.0 + i for i in range(n)],
                "close": [3805.0 + i for i in range(n)],
                "volume": [100000 + i * 100 for i in range(n)],
                "amount": [1e9 + i * 1000 for i in range(n)],
                "turnover": [None] * n,
                "data_source": ["eastmoney"] * n,
            }
        )

    def _mock_index_online(self, monkeypatch, calls=None, df=None):
        def fake(code, start, end, source_name=None):
            if calls is not None:
                calls.append((code, start, end))
            return (df if df is not None else self._index_df(code)), "online-eastmoney"

        monkeypatch.setattr(online, "fetch_index_kline_online", fake)

    def test_empty_db_index_falls_back_online(self, empty_db_path, monkeypatch):
        self._mock_index_online(monkeypatch)
        q = StockQuery(empty_db_path, online_fallback=True)
        df = q.get_index_kline("sh000001", limit=10)
        assert not df.empty
        assert result_source(df) == "online-eastmoney"
        assert len(df) == 10
        assert df.iloc[0]["code"] == "sh000001"

    def test_index_cache_to_db_and_dedup(self, empty_db_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_CACHE_TO_DB", True)
        self._mock_index_online(monkeypatch)
        q = StockQuery(empty_db_path, online_fallback=True)
        q.get_index_kline("sh000001", limit=10)

        # 写库后本地命中；再造一行低优先级副本，验证 QUALIFY 去重
        from deeppulse.src.database import get_connection

        conn = get_connection(empty_db_path)
        conn.execute(
            "INSERT INTO index_daily_kline VALUES ('sh000001', ?, 1, 1, 1, 999, 1, 1, NULL, 'sina')",
            [str(date.today())],
        )
        conn.close()

        q2 = StockQuery(empty_db_path, online_fallback=False)
        df = q2.get_index_kline("sh000001")
        assert result_source(df) == "local"
        assert df["trade_date"].duplicated().sum() == 0
        # 同一天 sina 低优先级副本被丢弃，保留在线缓存写回的 eastmoney 行
        today_rows = df[df["trade_date"].astype(str) == str(date.today())]
        assert list(today_rows["data_source"]) == ["eastmoney"]

    def test_index_offline_no_fallback(self, empty_db_path):
        q = StockQuery(empty_db_path, online_fallback=False)
        df = q.get_index_kline("sh000001")
        assert df.empty
        assert result_source(df) == "local"

    def test_init_tables_creates_index_table(self, empty_db_path):
        from deeppulse.src.database import get_connection, init_tables

        conn = get_connection(empty_db_path)
        init_tables(conn)
        tables = {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        conn.close()
        assert "index_daily_kline" in tables
        assert "daily_kline" in tables
        # 多周期六表随 init_tables 一并下沉创建
        for t in ("kline_1m", "kline_5m", "kline_15m", "kline_30m", "kline_60m", "kline_weekly"):
            assert t in tables

    def test_query_index_tool_resolves_name_and_rejects_bare_code(self, empty_db_path, monkeypatch):
        from deeppulse.agent.tools import market as market_tools

        self._mock_index_online(monkeypatch)
        monkeypatch.setattr(market_tools, "get_query", lambda: StockQuery(empty_db_path, online_fallback=True))

        out = json.loads(market_tools.query_index("上证指数", days=5))
        assert out["code"] == "sh000001"
        assert out["name"] == "上证指数"
        assert out["count"] == 5

        bad = json.loads(market_tools.query_index("000001"))
        assert "error" in bad and "歧义" in bad["hint"]

    def test_fetch_index_dispatch(self, monkeypatch):
        from deeppulse.src import collector

        monkeypatch.setattr(
            collector, "fetch_all_index_kline", lambda **k: {"total": 6, "success": 6, "failed": 0, "rows": 300}
        )
        from deeppulse.agent.fetch_commands import run_fetch_command

        out = run_fetch_command("index", log=lambda m: None)
        assert "采集完成" in out and "300" in out

    def test_fetch_all_also_collects_index(self, monkeypatch):
        from deeppulse.src import collector

        calls = []
        monkeypatch.setattr(
            collector, "fetch_all_kline", lambda **k: {"total": 1, "success": 1, "failed": 0, "rows": 10}
        )
        monkeypatch.setattr(
            collector,
            "fetch_all_index_kline",
            lambda **k: calls.append(1) or {"total": 6, "success": 6, "failed": 0, "rows": 20},
        )
        monkeypatch.setattr(collector, "fetch_and_store_stock_list", lambda: 1)
        from deeppulse.agent.fetch_commands import run_fetch_command

        out = run_fetch_command("all", log=lambda m: None)
        assert "采集完成" in out
        assert calls == [1]  # /fetch all 顺带采指数

    def test_fetch_status_shows_row_count(self, empty_db_path, monkeypatch):
        """回归：/fetch status 此前误用 rows_inserted 列名恒显示 0"""
        from deeppulse.agent.fetch_commands import run_fetch_command
        from deeppulse.src.database import get_connection, init_tables, log_fetch

        conn = get_connection(empty_db_path)
        init_tables(conn)
        log_fetch(conn, "600519", "baostock", "2026-09-01", "2026-09-30", 42, "success")
        conn.close()
        monkeypatch.setattr(config, "DB_PATH", empty_db_path)
        out = run_fetch_command("status")
        assert "+42行" in out


class TestTimeframeFallback:
    def test_query_timeframe_online(self, empty_db_path, monkeypatch):
        from deeppulse.agent import timeframes

        df = pd.DataFrame(
            {
                "code": ["600519"] * 5,
                "trade_datetime": [f"2026-01-0{i + 1} 10:00:00" for i in range(5)],
                "open": [10.0] * 5,
                "high": [11.0] * 5,
                "low": [9.0] * 5,
                "close": [10.5] * 5,
                "volume": [1000] * 5,
                "amount": [10000.0] * 5,
                "data_source": ["akshare"] * 5,
            }
        )
        monkeypatch.setattr(online, "fetch_minute_kline_online", lambda code, period, days: df)
        monkeypatch.setattr(config, "DATA_ONLINE_FALLBACK", True)
        # 让 query_timeframe 用临时库（表未建 → 走兜底）
        monkeypatch.setattr(
            timeframes, "get_connection", lambda *a, **k: __import__("duckdb").connect(str(empty_db_path))
        )
        out = timeframes.query_timeframe("600519", "5m", limit=5)
        assert out["count"] == 5
        assert out["data_source"] == "online-akshare"


class TestFixesAndInvariants:
    def test_eastmoney_db_last_trade_date_empty_db(self, empty_db_path, monkeypatch):
        from deeppulse.src.realtime.eastmoney_source import EastMoneyRealtimeSource

        monkeypatch.setattr(config, "DB_PATH", empty_db_path)
        assert EastMoneyRealtimeSource._db_last_trade_date() is None

    def test_collector_never_triggers_online_fallback(self):
        """不变量：采集链路（collector/database）不得依赖在线兜底取数"""
        import ast
        from pathlib import Path

        banned = {"deeppulse.src.online", "deeppulse.src.query"}
        for rel in ("deeppulse/src/collector.py", "deeppulse/src/database.py"):
            tree = ast.parse((Path(config.PROJECT_ROOT) / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in banned:
                    pytest.fail(f"{rel} 引用了 {node.module}（采集链路禁止依赖在线兜底）")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in banned:
                            pytest.fail(f"{rel} 引用了 {alias.name}（采集链路禁止依赖在线兜底）")


class TestFetchCommands:
    def test_help(self):
        from deeppulse.agent.fetch_commands import FETCH_HELP, run_fetch_command

        assert run_fetch_command("") == FETCH_HELP
        assert "用法" in run_fetch_command("help")

    def test_unknown_subcommand(self):
        from deeppulse.agent.fetch_commands import run_fetch_command

        out = run_fetch_command("nope")
        assert "未知子命令" in out and "用法" in out

    def test_status(self, empty_db_path, monkeypatch):
        from deeppulse.agent.fetch_commands import run_fetch_command

        monkeypatch.setattr(config, "DB_PATH", empty_db_path)
        out = run_fetch_command("status")
        assert "本地数据统计" in out

    def test_kline_dispatch(self, monkeypatch):
        from deeppulse.src import collector

        monkeypatch.setattr(
            collector, "fetch_all_kline", lambda **k: {"total": 1, "success": 1, "failed": 0, "rows": 300}
        )
        from deeppulse.agent.fetch_commands import run_fetch_command

        out = run_fetch_command("kline 600519", log=lambda m: None)
        assert "采集完成" in out and "300" in out

    def test_kline_requires_code(self):
        from deeppulse.agent.fetch_commands import run_fetch_command

        out = run_fetch_command("kline")
        assert "用法" in out
