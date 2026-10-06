"""Unit tests for src/sources/ 新数据源解析与 SourceFailover 故障切换（禁网，mock HTTP）。"""

from datetime import date

import pandas as pd
import pytest

from deeppulse.src.collector import SourceFailover
from deeppulse.src.sources import make_source
from deeppulse.src.sources.eastmoney_source import EastMoneySource
from deeppulse.src.sources.sina_source import SinaSource
from deeppulse.src.sources.tencent_source import TencentSource


class _FakeSource:
    """可控成败/空结果的假数据源（name 可指定）"""

    def __init__(self, name, behavior="rows", rows=3):
        self._name = name
        self._behavior = behavior  # rows / empty / fail
        self._rows = rows
        self.call_count = 0
        self.cleanup_count = 0

    @property
    def name(self):
        return self._name

    def fetch_daily_kline(self, code, start, end):
        self.call_count += 1
        if self._behavior == "fail":
            raise ConnectionError(f"{self._name} down")
        if self._behavior == "empty":
            return pd.DataFrame()
        return pd.DataFrame(
            {
                "code": [code] * self._rows,
                "trade_date": [date(2026, 9, 28 + i) for i in range(self._rows)],
                "open": [10.0] * self._rows,
                "high": [10.5] * self._rows,
                "low": [9.8] * self._rows,
                "close": [10.2] * self._rows,
                "volume": [1000] * self._rows,
            }
        )

    def cleanup(self):
        self.cleanup_count += 1

    try_cleanup = cleanup


# ── EastMoneySource ─────────────────────────────────────────────────


class TestEastMoneySource:
    def test_secid_mapping(self):
        assert EastMoneySource._secid("600000") == "1.600000"
        assert EastMoneySource._secid("000001") == "0.000001"

    def test_kline_parse_units_and_columns(self, monkeypatch):
        src = EastMoneySource()
        payload = {
            "data": {
                "klines": [
                    # 日期,开,收,高,低,成交量(手),成交额(元),振幅,涨跌幅,涨跌额,换手率
                    "2026-09-29,9.20,9.30,9.35,9.15,1026696,952382710.00,2.84,2.07,0.19,0.31",
                    "2026-09-30,9.30,9.48,9.49,9.16,1474848,1386209937.00,3.59,1.94,0.18,0.44",
                ]
            }
        }
        monkeypatch.setattr(src, "_get_json", lambda *a, **k: payload)
        df = src._fetch_kline_impl("600000", date(2026, 9, 1), date(2026, 9, 30))

        assert list(df.columns) == [
            "code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "turnover",
            "data_source",
        ]
        row = df.iloc[0]
        assert row["trade_date"] == date(2026, 9, 29)
        assert row["close"] == pytest.approx(9.30)
        assert row["volume"] == 102669600  # 手 ×100 → 股
        assert row["amount"] == pytest.approx(952382710.0)
        assert row["turnover"] == pytest.approx(0.31)  # 换手率字段 f61
        assert row["data_source"] == "eastmoney"

    def test_kline_empty(self, monkeypatch):
        src = EastMoneySource()
        monkeypatch.setattr(src, "_get_json", lambda *a, **k: {"data": {"klines": []}})
        assert src._fetch_kline_impl("600000", date(2026, 9, 1), date(2026, 9, 30)).empty

    def test_stock_list_filters_main_board_and_list_date(self, monkeypatch):
        src = EastMoneySource()
        payload = {
            "data": {
                "diff": [
                    {"f12": "600000", "f14": "浦发银行", "f26": "19991110"},
                    {"f12": "688001", "f14": "华兴源创", "f26": "20190722"},  # 科创板，排除
                    {"f12": "000001", "f14": "平安银行", "f26": "19910403"},
                    {"f12": "300750", "f14": "宁德时代", "f26": "20180611"},  # 创业板，排除
                ]
            }
        }
        monkeypatch.setattr(src, "_get_json", lambda *a, **k: payload)
        stocks = src._fetch_stock_list_impl()
        codes = {s["code"] for s in stocks}
        assert codes == {"600000", "000001"}
        by_code = {s["code"]: s for s in stocks}
        assert by_code["600000"]["list_date"] == "1999-11-10"
        assert by_code["600000"]["market"] == "sh"
        assert by_code["000001"]["market"] == "sz"

    def test_unknown_source_raises(self):
        with pytest.raises(KeyError, match="未知历史数据源"):
            make_source("not-a-source")


# ── TencentSource ───────────────────────────────────────────────────


class TestTencentSource:
    def test_kline_parse_units_and_nan_fields(self, monkeypatch):
        src = TencentSource()
        rows = [
            ["2026-09-29", "9.200", "9.300", "9.350", "9.150", "1026696.000"],
            ["2026-09-30", "9.300", "9.480", "9.490", "9.160", "1474848.000"],
        ]
        monkeypatch.setattr(src, "_fetch_window", lambda *a, **k: rows)
        df = src._fetch_kline_impl("600000", date(2026, 9, 1), date(2026, 9, 30))

        assert len(df) == 2
        row = df.iloc[1]
        assert row["trade_date"] == date(2026, 9, 30)
        assert row["close"] == pytest.approx(9.48)
        assert row["volume"] == 147484800  # 手 ×100 → 股
        assert pd.isna(row["amount"])  # 腾讯日K无成交额
        assert pd.isna(row["turnover"])
        assert row["data_source"] == "tencent"

    def test_kline_pagination_stops_at_window_start(self, monkeypatch):
        """首条晚于请求起点时向左翻页，直到覆盖起点（或触达分页上限）"""
        src = TencentSource()
        calls = []

        def fake_window(symbol, start, end):
            calls.append((start, end))
            if len(calls) == 1:
                # 只回了窗口右端 2 条，左侧被截断
                return [["2026-09-29", "9", "9.3", "9.4", "9", "100"], ["2026-09-30", "9.3", "9.5", "9.6", "9.2", "100"]]
            return [["2026-09-25", "9", "9.1", "9.2", "9", "100"], ["2026-09-28", "9.1", "9.2", "9.3", "9", "100"]]

        monkeypatch.setattr(src, "_fetch_window", fake_window)
        df = src._fetch_kline_impl("600000", date(2026, 9, 25), date(2026, 9, 30))
        assert len(calls) == 2
        assert calls[1][1] == date(2026, 9, 28)  # 第二页 end = 首条前一日
        assert list(df["trade_date"]) == [
            date(2026, 9, 25),
            date(2026, 9, 28),
            date(2026, 9, 29),
            date(2026, 9, 30),
        ]

    def test_stock_list_unsupported(self):
        src = TencentSource()
        with pytest.raises(RuntimeError, match="不提供股票列表"):
            src.fetch_stock_list()


# ── SinaSource ──────────────────────────────────────────────────────


class TestSinaSource:
    def test_kline_parse_keeps_shares_and_filters_window(self, monkeypatch):
        src = SinaSource()

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return [
                    {"day": "2026-09-25", "open": "9.10", "high": "9.20", "low": "9.00", "close": "9.15", "volume": "51085389"},
                    {"day": "2026-09-30", "open": "9.30", "high": "9.49", "low": "9.16", "close": "9.48", "volume": "134548971"},
                    {"day": "2026-10-01", "open": "9.30", "high": "9.49", "low": "9.16", "close": "9.48", "volume": "1"},  # 窗口外
                ]

        class _Sess:
            def get(self, *a, **k):
                return _Resp()

        monkeypatch.setattr(src, "_get_session", lambda: _Sess())
        df = src._fetch_kline_impl("600000", date(2026, 9, 26), date(2026, 9, 30))

        assert len(df) == 1
        row = df.iloc[0]
        assert row["trade_date"] == date(2026, 9, 30)
        assert row["volume"] == 134548971  # 新浪已是股，不换算
        assert pd.isna(row["amount"])
        assert row["data_source"] == "sina"

    def test_stock_list_filters_main_board(self, monkeypatch):
        src = SinaSource()
        pages = {
            1: [
                {"symbol": "sh600000", "code": "600000", "name": "浦发银行"},
                {"symbol": "sh688001", "code": "688001", "name": "华兴源创"},  # 科创板，排除
                {"symbol": "sz000001", "code": "000001", "name": "平安银行"},
            ],
            2: [
                {"symbol": "bj920000", "code": "920000", "name": "北证股"},  # 北交所，排除
                {"symbol": "sz300750", "code": "300750", "name": "宁德时代"},  # 创业板，排除
            ],
            3: [],
        }

        class _Resp:
            def __init__(self, data):
                self._data = data

            def raise_for_status(self):
                return None

            def json(self):
                return self._data

        class _Sess:
            def get(self, url, params=None, **k):
                return _Resp(pages[params["page"]])

        monkeypatch.setattr(src, "_get_session", lambda: _Sess())
        stocks = src._fetch_stock_list_impl()
        codes = {s["code"] for s in stocks}
        assert codes == {"600000", "000001"}
        assert all(s["board"] == "main" for s in stocks)


# ── SourceFailover ──────────────────────────────────────────────────


class TestSourceFailover:
    def test_primary_success_skips_backup(self):
        a, b = _FakeSource("a"), _FakeSource("b")
        f = SourceFailover([a, b])
        df, name = f.fetch("600000", date(2026, 9, 1), date(2026, 9, 30))
        assert name == "a" and not df.empty
        assert a.call_count == 1 and b.call_count == 0

    def test_failover_on_exception(self):
        a, b = _FakeSource("a", behavior="fail"), _FakeSource("b")
        f = SourceFailover([a, b])
        df, name = f.fetch("600000", date(2026, 9, 1), date(2026, 9, 30))
        assert name == "b" and not df.empty
        assert a.cleanup_count == 1  # 失败源会话被清理

    def test_failover_on_empty_stale_source(self):
        """主源返回空（数据滞后）也换源——拿到备源的新数据"""
        a, b = _FakeSource("a", behavior="empty"), _FakeSource("b")
        f = SourceFailover([a, b])
        df, name = f.fetch("600000", date(2026, 9, 1), date(2026, 9, 30))
        assert name == "b" and not df.empty

    def test_learns_working_source_for_next_call(self):
        a, b = _FakeSource("a", behavior="fail"), _FakeSource("b")
        f = SourceFailover([a, b])
        f.fetch("600000", date(2026, 9, 1), date(2026, 9, 30))
        assert f.active.name == "b"
        f.fetch("000001", date(2026, 9, 1), date(2026, 9, 30))
        assert a.call_count == 1  # 第二只不再打挂掉的 a

    def test_all_empty_returns_empty_not_raise(self):
        a, b = _FakeSource("a", behavior="empty"), _FakeSource("b", behavior="empty")
        f = SourceFailover([a, b])
        df, name = f.fetch("600000", date(2026, 9, 1), date(2026, 9, 30))
        assert df.empty and name == "a"

    def test_all_fail_raises(self):
        a, b = _FakeSource("a", behavior="fail"), _FakeSource("b", behavior="fail")
        f = SourceFailover([a, b])
        with pytest.raises(RuntimeError, match="所有数据源均失败"):
            f.fetch("600000", date(2026, 9, 1), date(2026, 9, 30))

    def test_quiet_mode_after_streak_limits_to_active(self):
        """连续全空进入安静模式后只打活动源——避免无新数据时打爆所有源"""
        a, b = _FakeSource("a", behavior="empty"), _FakeSource("b", behavior="empty")
        f = SourceFailover([a, b])
        for _ in range(SourceFailover.QUIET_EMPTY_STREAK):
            f.fetch("600000", date(2026, 9, 1), date(2026, 9, 30))
        assert a.call_count == 3 and b.call_count == 3
        f.fetch("600000", date(2026, 9, 1), date(2026, 9, 30))
        assert a.call_count == 4 and b.call_count == 3  # 安静模式不再打 b

    def test_quiet_mode_resets_on_data(self):
        a = _FakeSource("a", behavior="empty")
        f = SourceFailover([a])
        for _ in range(SourceFailover.QUIET_EMPTY_STREAK):
            f.fetch("600000", date(2026, 9, 1), date(2026, 9, 30))
        a._behavior = "rows"
        df, _ = f.fetch("000001", date(2026, 9, 1), date(2026, 9, 30))
        assert not df.empty
        assert not f._quiet

    def test_quiet_mode_exception_falls_back_to_cascade(self):
        a, b = _FakeSource("a", behavior="empty"), _FakeSource("b", behavior="empty")
        f = SourceFailover([a, b])
        for _ in range(SourceFailover.QUIET_EMPTY_STREAK):
            f.fetch("600000", date(2026, 9, 1), date(2026, 9, 30))
        assert f._quiet
        a._behavior = "fail"  # 活动源坏了
        b._behavior = "rows"  # 备源恢复
        df, name = f.fetch("000001", date(2026, 9, 1), date(2026, 9, 30))
        assert name == "b" and not df.empty

    def test_requires_sources(self):
        with pytest.raises(ValueError, match="无可用数据源"):
            SourceFailover([])


# ── RetryPolicy 连接错误重试 ───────────────────────────────────────


class TestRetryPolicyConnErrors:
    def test_http_sources_retry_on_remote_disconnected(self):
        """限频表现为连接被掐断：HTTP 源应退避重试而非快速失败"""
        from deeppulse.src.resilience import RetryPolicy

        calls = []

        def boom():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("Connection aborted. Remote disconnected")
            return "ok"

        policy = RetryPolicy(max_retries=3, base_delay=0.01, retryable_errors=(Exception,), fast_fail_conn_errors=False)
        assert policy.execute(boom) == "ok"
        assert len(calls) == 3

    def test_default_keeps_fast_fail(self):
        """默认（长连接会话）保持快速失败语义不变"""
        from deeppulse.src.resilience import RetryPolicy

        calls = []

        def boom():
            calls.append(1)
            raise ConnectionResetError("Connection reset by peer")

        policy = RetryPolicy(max_retries=3, base_delay=0.01)
        with pytest.raises(ConnectionResetError):
            policy.execute(boom)
        assert len(calls) == 1


# ── 缺成交额/换手率的源入库对齐 ────────────────────────────────────


class TestKlineInsertSchema:
    def test_nan_amount_turnover_rows_insert(self, in_memory_db):
        """腾讯/新浪源的 NaN 成交额/换手率行可正常入库（10 列 schema 对齐）"""
        from deeppulse.src.database import insert_daily_kline

        src = TencentSource()

        monkey_rows = [
            {"trade_date": date(2026, 9, 29), "open": 9.2, "close": 9.3, "high": 9.35, "low": 9.15, "volume": 100000},
            {"trade_date": date(2026, 9, 30), "open": 9.3, "close": 9.48, "high": 9.49, "low": 9.16, "volume": 120000},
        ]
        df = pd.DataFrame(monkey_rows)
        df["code"] = "600000"
        df["data_source"] = src.name
        df["amount"] = float("nan")
        df["turnover"] = float("nan")
        cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover", "data_source"]
        df = df[cols]

        n = insert_daily_kline(in_memory_db, df.to_dict("records"))
        assert n == 2
        row = in_memory_db.execute(
            "SELECT close, amount, turnover FROM daily_kline WHERE code='600000' ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        assert row[0] == pytest.approx(9.48)


# ── 多源同日期去重（查询层） ───────────────────────────────────────


class TestQueryDedupMultiSource:
    def _seed_two_sources(self, db_path):
        from deeppulse.src.database import get_connection, init_tables, insert_daily_kline

        conn = get_connection(db_path)
        init_tables(conn)
        rows = []
        for src, close in (("baostock", 10.0), ("tencent", 10.1), ("sina", 10.2)):
            rows.append(
                {
                    "code": "600000",
                    "trade_date": date(2026, 9, 30),
                    "open": 9.9,
                    "high": 10.3,
                    "low": 9.8,
                    "close": close,
                    "volume": 100000,
                    "amount": 1.0,
                    "turnover": 0.1,
                    "data_source": src,
                }
            )
        insert_daily_kline(conn, rows)
        conn.close()

    def test_same_date_collapses_to_priority_source(self, tmp_db_path):
        from deeppulse.src.query import StockQuery

        self._seed_two_sources(tmp_db_path)
        q = StockQuery(db_path=tmp_db_path, online_fallback=False)
        df = q.get_daily_kline("600000")
        assert len(df) == 1  # 三源同日只留一行
        assert df.iloc[0]["data_source"] == "baostock"  # 按 DATA_SOURCES 优先级
        assert df.iloc[0]["close"] == pytest.approx(10.0)

    def test_explicit_source_keeps_raw_rows(self, tmp_db_path):
        from deeppulse.src.query import StockQuery

        self._seed_two_sources(tmp_db_path)
        q = StockQuery(db_path=tmp_db_path, online_fallback=False)
        df = q.get_daily_kline("600000", source="sina")
        assert len(df) == 1
        assert df.iloc[0]["data_source"] == "sina"


# ── online 源降级链 ─────────────────────────────────────────────────


class TestOnlineSourceChain:
    def test_chain_is_full_with_default_first(self, monkeypatch):
        from deeppulse import config
        from deeppulse.src import online

        monkeypatch.setattr(config, "DATA_SOURCES", ["baostock", "akshare", "eastmoney", "tencent", "sina"])
        monkeypatch.setattr(config, "DATA_DEFAULT_SOURCE", "tencent")
        names = online._source_names()
        assert names[0] == "tencent"
        assert set(names) == {"baostock", "akshare", "eastmoney", "tencent", "sina"}

    def test_chain_without_default(self, monkeypatch):
        from deeppulse import config
        from deeppulse.src import online

        monkeypatch.setattr(config, "DATA_SOURCES", ["baostock", "akshare", "eastmoney"])
        monkeypatch.setattr(config, "DATA_DEFAULT_SOURCE", None)
        assert online._source_names() == ["baostock", "akshare", "eastmoney"]

    def test_make_source_registry(self):
        from deeppulse.src.online import _make_source

        assert _make_source("eastmoney").name == "eastmoney"
        assert _make_source("tencent").name == "tencent"
        assert _make_source("sina").name == "sina"


# ── TencentRealtimeSource 解析 ─────────────────────────────────────


class TestTencentRealtimeParse:
    def test_parse_real_payload(self):
        from deeppulse.src.realtime.tencent_source import TencentRealtimeSource

        src = TencentRealtimeSource()
        # 真实抓包（2026-09-30 收盘后 qt.gtimg.cn 原样返回）
        text = (
            'v_sh600000="1~浦发银行~600000~9.48~9.18~9.22~1474848~986826~488022~9.47~1698~9.46~3703~9.45~2708~'
            "9.44~729~9.43~1244~9.48~10808~9.49~29728~9.50~39954~9.51~18403~9.52~11369~~20260930161454~0.30~3.27~"
            "9.49~9.16~9.48/1474848/1386209937~1474848~138621~0.44~6.16~~9.49~9.16~3.59~3157.39~3157.39~0.42~"
            '10.10~8.26~2.07~-100180~9.40~5.10~6.31~~~-0.00~138620.9937~42.1860~445~   A~GP-A~";'
        )
        quote = src._parse_response("600000", text)
        assert quote is not None
        assert quote.code == "600000"
        assert quote.name == "浦发银行"
        assert quote.current == pytest.approx(9.48)
        assert quote.yesterday_close == pytest.approx(9.18)
        assert quote.open == pytest.approx(9.22)
        assert quote.high == pytest.approx(9.49)
        assert quote.low == pytest.approx(9.16)
        assert quote.change_amount == pytest.approx(0.30)
        assert quote.change_pct == pytest.approx(3.27)
        assert quote.volume == 147484800  # 手 ×100 → 股
        assert quote.amount == pytest.approx(1386209937.0)  # 字段35 精确元值
        assert quote.trade_date == "2026-09-30"
        assert quote.trade_time == "16:14:54"  # 真实成交时间戳
        assert quote.stale_hint == ""  # 无需保守标注
        assert quote.data_source == "tencent"

    def test_parse_zero_price_returns_none(self):
        from deeppulse.src.realtime.tencent_source import TencentRealtimeSource

        src = TencentRealtimeSource()
        text = 'v_sz000001="51~平安银行~000001~0~11.35~0~0~0~0~' + "~".join([""] * 30) + '~";'
        assert src._parse_response("000001", text) is None

    def test_parse_empty_response_returns_none(self):
        from deeppulse.src.realtime.tencent_source import TencentRealtimeSource

        src = TencentRealtimeSource()
        assert src._parse_response("600000", 'v_pv_none_match="1";') is None

    def test_parse_malformed_raises(self):
        from deeppulse.src.realtime.tencent_source import TencentRealtimeSource

        src = TencentRealtimeSource()
        with pytest.raises(ValueError):
            src._parse_response("600000", "garbage-no-quotes")

    def test_manager_registry_includes_tencent(self):
        from deeppulse.src.realtime.manager import DEFAULT_PRIORITY, SOURCE_REGISTRY

        assert "tencent" in SOURCE_REGISTRY
        assert DEFAULT_PRIORITY.index("tencent") < DEFAULT_PRIORITY.index("eastmoney")
