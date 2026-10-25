"""分钟线批量落库测试：东财分钟解析 / fetch_all_timeframe 断点与节流 / 单位口径

全部 mock 网络层，不打真实网络（conftest.no_network 自动护栏）。
"""

import sys
from datetime import date

import pandas as pd
import pytest

from deeppulse import config
from deeppulse.agent import timeframes as tf_mod


def _minute_df(code="600000", n=3):
    # 相对「今天」构造，断点窗口断言不随时钟漂移
    base = pd.Timestamp(date.today()) + pd.Timedelta(hours=10)
    return pd.DataFrame(
        {
            "code": [code] * n,
            "trade_datetime": [base + pd.Timedelta(minutes=5 * i) for i in range(n)],
            "open": [10.0] * n,
            "high": [10.5] * n,
            "low": [9.8] * n,
            "close": [10.2] * n,
            "volume": [100000] * n,
            "amount": [1e6] * n,
            "data_source": ["eastmoney"] * n,
        }
    )


class TestEastMoneyMinuteKline:
    def test_minute_kline_parse_units_and_klt(self, monkeypatch):
        from deeppulse.src.sources.eastmoney_source import EastMoneySource

        src = EastMoneySource()
        seen = {}

        def fake_get_json(hosts, path, params):
            seen.update(params)
            return {
                "data": {
                    "klines": [
                        "2026-10-07 10:00:00,10.00,10.20,10.50,9.80,1000,1000000.00,-,-,-,-",
                        "2026-10-07 10:05:00,10.20,10.30,10.60,10.10,1200,1200000.00,-,-,-,-",
                    ]
                }
            }

        monkeypatch.setattr(src, "_get_json", fake_get_json)
        df = src._fetch_minute_kline_impl("600000", "5", date(2026, 10, 1), date(2026, 10, 7))

        assert seen["klt"] == "5"
        assert seen["fqt"] == "1"
        assert seen["beg"] == "20261001" and seen["end"] == "20261007"
        assert list(df.columns) == [
            "code",
            "trade_datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "data_source",
        ]
        row = df.iloc[0]
        assert row["trade_datetime"] == pd.Timestamp("2026-10-07 10:00:00")
        assert row["volume"] == 1000 * 100  # 手 ×100 → 股
        assert row["data_source"] == "eastmoney"

    def test_minute_kline_1m_unsupported(self):
        from deeppulse.src.sources.eastmoney_source import EastMoneySource

        with pytest.raises(NotImplementedError):
            EastMoneySource()._fetch_minute_kline_impl("600000", "1", date(2026, 10, 1), date(2026, 10, 7))

    def test_weekly_klt_mapping(self, monkeypatch):
        from deeppulse.src.sources.eastmoney_source import EastMoneySource

        src = EastMoneySource()
        seen = {}
        monkeypatch.setattr(
            src,
            "_get_json",
            lambda h, p, params: seen.update(params) or {"data": {"klines": []}},
        )
        src._fetch_minute_kline_impl("600000", "weekly", date(2026, 1, 1), date(2026, 10, 7))
        assert seen["klt"] == "102"


class TestAkshareMinuteVolumeUnit:
    def test_fetch_minute_kline_volume_x100(self, monkeypatch):
        """akshare 分钟 volume 为手：入库前必须 ×100 统一为股"""
        import deeppulse.agent.timeframes as mod

        fake = pd.DataFrame(
            {
                "时间": ["2026-10-07 10:00:00", "2026-10-07 10:05:00"],
                "开盘": [10.0, 10.2],
                "收盘": [10.2, 10.3],
                "最高": [10.5, 10.6],
                "最低": [9.8, 10.1],
                "成交量": [1000, 1200],
                "成交额": [1e6, 1.2e6],
            }
        )

        class _FakeAk:
            @staticmethod
            def stock_zh_a_hist_min_em(**kwargs):
                return fake

        monkeypatch.setitem(sys.modules, "akshare", _FakeAk)
        df = mod.fetch_minute_kline("600000", period="5", days=5)
        assert list(df["volume"]) == [1000 * 100, 1200 * 100]

    def test_fetch_weekly_kline_volume_x100(self, monkeypatch):
        import deeppulse.agent.timeframes as mod

        fake = pd.DataFrame(
            {
                "日期": ["2026-09-28"],
                "开盘": [10.0],
                "收盘": [10.2],
                "最高": [10.5],
                "最低": [9.8],
                "成交量": [5000],
                "成交额": [5e6],
                "换手率": [1.0],
            }
        )

        class _FakeAk:
            @staticmethod
            def stock_zh_a_hist(**kwargs):
                return fake

        monkeypatch.setitem(sys.modules, "akshare", _FakeAk)
        df = mod.fetch_weekly_kline("600000")
        assert (df["volume"] == 5000 * 100).all()


class TestFetchAllTimeframe:
    def _patch_source(self, monkeypatch, rows_factory, calls=None, fail_times=0):
        from deeppulse.src.sources import eastmoney_source as em

        state = {"fails_left": fail_times}

        class _FakeSrc:
            def __init__(self):
                self.cleaned = False

            def fetch_minute_kline(self, code, period, start, end):
                if calls is not None:
                    calls.append((code, period))
                if state["fails_left"] > 0:
                    state["fails_left"] -= 1
                    raise ConnectionError("remote disconnected")
                return rows_factory(code)

            def cleanup(self):
                self.cleaned = True

        monkeypatch.setattr(em, "EastMoneySource", _FakeSrc)

    def test_resume_skips_codes_with_window_data(self, monkeypatch, tmp_db_path):
        calls = []
        self._patch_source(monkeypatch, lambda c: _minute_df(c), calls=calls)
        sleeps = []
        monkeypatch.setattr(tf_mod.time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(config, "MINUTE_FETCH_DELAY_SECONDS", 0.5)

        # 预写 600000 的窗口内数据 → 断点跳过；000001 走采集
        conn = tf_mod.get_connection(tmp_db_path)
        tf_mod.init_timeframe_tables(conn)
        from deeppulse.agent.timeframes import save_timeframe_data

        save_timeframe_data(conn, _minute_df("600000"), "kline_5m")
        conn.close()

        stats = tf_mod.fetch_all_timeframe("5m", days=5, codes=["600000", "000001"], db_path=tmp_db_path)
        assert stats["total"] == 2
        assert stats["skipped"] == 1
        assert stats["success"] == 1
        assert [c for c, _ in calls] == ["000001"]
        assert sleeps == [0.5]  # 只对实际采集的代码节流

    def test_throttle_between_codes(self, monkeypatch, tmp_db_path):
        self._patch_source(monkeypatch, lambda c: _minute_df(c))
        sleeps = []
        monkeypatch.setattr(tf_mod.time, "sleep", lambda s: sleeps.append(s))
        monkeypatch.setattr(config, "MINUTE_FETCH_DELAY_SECONDS", 0.25)

        stats = tf_mod.fetch_all_timeframe("5m", days=5, codes=["600000", "000001", "000002"], db_path=tmp_db_path)
        assert stats["success"] == 3
        assert sleeps == [0.25, 0.25, 0.25]

    def test_retry_once_then_skip(self, monkeypatch, tmp_db_path):
        calls = []
        # 第一只：两次都失败 → failed；第二只：第一次失败第二次成功 → success
        from deeppulse.src.sources import eastmoney_source as em

        class _Flaky:
            def __init__(self):
                self.state = {}

            def fetch_minute_kline(self, code, period, start, end):
                calls.append(code)
                n = self.state.get(code, 0) + 1
                self.state[code] = n
                if code == "600000":
                    raise ConnectionError("boom")  # 每次都失败
                if n == 1:
                    raise ConnectionError("boom")
                return _minute_df(code)

            def cleanup(self):
                pass

        monkeypatch.setattr(em, "EastMoneySource", _Flaky)
        monkeypatch.setattr(tf_mod.time, "sleep", lambda s: None)

        stats = tf_mod.fetch_all_timeframe("5m", days=5, codes=["600000", "000001"], db_path=tmp_db_path)
        assert stats["failed"] == 1
        assert stats["success"] == 1
        assert calls.count("600000") == 2  # 重试一次
        assert calls.count("000001") == 2  # 首次失败后重试成功

    def test_db_path_explicit_not_global(self, monkeypatch, tmp_path):
        """铁律：写库走显式 db_path，不落到全局 config.DB_PATH"""
        self._patch_source(monkeypatch, lambda c: _minute_df(c))
        monkeypatch.setattr(tf_mod.time, "sleep", lambda s: None)
        real_db = tmp_path / "real.duckdb"
        monkeypatch.setattr(config, "DB_PATH", real_db)

        target = tmp_path / "target.duckdb"
        stats = tf_mod.fetch_all_timeframe("5m", days=5, codes=["600000"], db_path=target)
        assert stats["success"] == 1
        assert target.exists()
        assert not real_db.exists()

    def test_1m_batch_rejected(self):
        out = tf_mod.fetch_all_timeframe("1m", days=5, codes=["600000"])
        assert "error" in out and "1m" in out["error"]

    def test_unknown_timeframe_rejected(self):
        out = tf_mod.fetch_all_timeframe("99m", days=5, codes=["600000"])
        assert "error" in out

    def test_batch_defaults_to_stock_list(self, monkeypatch, tmp_db_path):
        from deeppulse.src.database import get_connection, init_tables, upsert_stock_info

        conn = get_connection(tmp_db_path)
        init_tables(conn)
        upsert_stock_info(
            conn,
            [
                {
                    "code": "600000",
                    "name": "浦发",
                    "market": "sh",
                    "board": "main",
                    "list_date": None,
                    "delist_date": None,
                }
            ],
        )
        conn.close()

        calls = []
        self._patch_source(monkeypatch, lambda c: _minute_df(c), calls=calls)
        monkeypatch.setattr(tf_mod.time, "sleep", lambda s: None)
        stats = tf_mod.fetch_all_timeframe("5m", days=5, db_path=tmp_db_path)
        assert stats["total"] == 1
        assert [c for c, _ in calls] == ["600000"]


class TestQueryFallbackStillWorks:
    def test_empty_table_falls_back_online(self, tmp_db_path, monkeypatch):
        """表已建但为空 → 仍走在线分钟兜底"""
        conn = tf_mod.get_connection(tmp_db_path)
        tf_mod.init_timeframe_tables(conn)
        conn.close()
        df = _minute_df().head(2)
        monkeypatch.setattr(
            tf_mod, "_fallback_timeframe_online", lambda code, tf, limit, db_path=None: (df, "online-akshare")
        )
        monkeypatch.setattr(config, "DATA_ONLINE_FALLBACK", True)

        out = tf_mod.query_timeframe("600000", "5m", limit=5, db_path=tmp_db_path)
        assert out["count"] == 2
        assert out["data_source"] == "online-akshare"

    def test_volume_unit_db_is_shares(self, tmp_db_path):
        """库内 volume 为股（工具层再转手）"""
        conn = tf_mod.get_connection(tmp_db_path)
        tf_mod.init_timeframe_tables(conn)
        tf_mod.save_timeframe_data(conn, _minute_df(), "kline_5m")
        conn.close()
        out = tf_mod.query_timeframe("600000", "5m", limit=10, db_path=tmp_db_path)
        assert out["count"] == 3
        assert out["kline"][0]["volume"] == 100000  # 股，未再除 100


class TestFetchCommandDispatch:
    def test_timeframe_dispatch_all(self, monkeypatch):
        from deeppulse.agent.fetch_commands import run_fetch_command

        calls = []
        monkeypatch.setattr(
            tf_mod,
            "fetch_all_timeframe",
            lambda tf, days=5, **k: (
                calls.append((tf, days)) or {"total": 1, "success": 1, "skipped": 0, "failed": 0, "rows": 9}
            ),
        )
        out = run_fetch_command("timeframe 3 5m", log=lambda m: None)
        assert calls == [("5m", 3)]
        assert "采集完成" in out and "5m" in out

        calls.clear()
        out = run_fetch_command("timeframe", log=lambda m: None)
        assert [t for t, _ in calls] == tf_mod.BATCH_TIMEFRAMES  # 默认 all
        assert all(d == 5 for _, d in calls)

    def test_timeframe_bad_tf(self):
        from deeppulse.agent.fetch_commands import run_fetch_command

        out = run_fetch_command("timeframe 99m")
        assert "用法" in out
