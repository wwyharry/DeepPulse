"""在线数据源实测（integration，真实网络）——新数据源在实际接口上的可用性验证

默认测试禁网（conftest.no_network）；本文件全部用例带 integration 标记，
显式运行：`pytest tests/test_sources_live.py -m integration`。

日K用例取固定历史窗口（2026-09-01 ~ 2026-09-30），基准为浦发银行 600000
2026-09-30 收盘：开 9.22 / 高 9.49 / 低 9.16 / 收 9.48 / 量 1474848 手。
固定窗口让断言不随时钟漂移；实时/列表用例只做结构性断言。
各源均为免费公开接口，偶发限频时单源用例可能失败——多源互备的故障切换
用例（test_failover_chain_yields_data）应仍然通过。
"""

from datetime import date, timedelta

import pytest

# 浦发银行 2026-09-30 收盘基准（来自 qt.gtimg.cn 实时快照 20260930161454）
_BASE_DATE = date(2026, 9, 30)
_WIN_START = date(2026, 9, 1)
_WIN_END = date(2026, 9, 30)
_BASE_CLOSE = 9.48
_BASE_OPEN = 9.22
_BASE_HIGH = 9.49
_BASE_LOW = 9.16
_BASE_VOLUME_LOTS = 1474848  # 手

pytestmark = pytest.mark.integration


# ── 单源可用性（固定历史窗口 + 基准值） ────────────────────────────


class TestEastMoneyLive:
    def test_kline_live(self):
        from deeppulse.src.sources.eastmoney_source import EastMoneySource

        df = EastMoneySource().fetch_daily_kline("600000", _WIN_START, _WIN_END)
        assert not df.empty, "东财日K无数据"
        last = df.iloc[-1]
        assert last["trade_date"] == _BASE_DATE
        assert last["close"] == pytest.approx(_BASE_CLOSE, abs=0.02)
        assert last["volume"] == _BASE_VOLUME_LOTS * 100  # 手 ×100 → 股
        assert last["amount"] > 0 and last["turnover"] > 0
        assert (df["volume"] > 0).all()
        assert df["trade_date"].is_monotonic_increasing

    def test_stock_list_live(self):
        from deeppulse.src.sources.eastmoney_source import EastMoneySource

        stocks = EastMoneySource().fetch_stock_list()
        codes = {s["code"] for s in stocks}
        assert len(stocks) > 1000, f"东财股票列表过少: {len(stocks)}"
        assert "600000" in codes and "000001" in codes
        assert all(s["code"].startswith(("6", "0")) and not s["code"].startswith("68") for s in stocks)
        by_code = {s["code"]: s for s in stocks}
        assert by_code["600000"]["name"] == "浦发银行"
        assert by_code["600000"]["list_date"]  # 东财提供上市日期


class TestTencentLive:
    def test_kline_live(self):
        from deeppulse.src.sources.tencent_source import TencentSource

        df = TencentSource().fetch_daily_kline("600000", _WIN_START, _WIN_END)
        assert not df.empty, "腾讯日K无数据"
        last = df.iloc[-1]
        assert last["trade_date"] == _BASE_DATE
        assert last["close"] == pytest.approx(_BASE_CLOSE, abs=0.02)
        assert last["open"] == pytest.approx(_BASE_OPEN, abs=0.02)
        assert last["high"] == pytest.approx(_BASE_HIGH, abs=0.02)
        assert last["low"] == pytest.approx(_BASE_LOW, abs=0.02)
        assert last["volume"] == _BASE_VOLUME_LOTS * 100  # 手 ×100 → 股

    def test_kline_pagination_covers_long_window(self):
        """5 年窗口超过单次 800 条上限，分页应补齐左端"""
        from deeppulse.src.sources.tencent_source import TencentSource

        end = date.today()
        start = end - timedelta(days=5 * 365)
        df = TencentSource().fetch_daily_kline("600000", start, end)
        assert len(df) > 800, f"分页未补齐: {len(df)} 行"
        assert df.iloc[0]["trade_date"] <= start + timedelta(days=90)  # 左端覆盖到请求窗口附近
        assert df["trade_date"].is_monotonic_increasing


class TestSinaLive:
    def test_kline_live(self):
        from deeppulse.src.sources.sina_source import SinaSource

        df = SinaSource().fetch_daily_kline("600000", _WIN_START, _WIN_END)
        assert not df.empty, "新浪日K无数据"
        last = df.iloc[-1]
        assert last["trade_date"] == _BASE_DATE
        # 新浪为不复权价；2026-09-30 附近无除权，与各前复权源一致
        assert last["close"] == pytest.approx(_BASE_CLOSE, abs=0.02)
        # 成交量单位为股（与库表口径一致），与腾讯源同日量级一致（手口径舍入差 <0.1%）
        assert abs(int(last["volume"]) - _BASE_VOLUME_LOTS * 100) < _BASE_VOLUME_LOTS * 100 * 0.001

    def test_stock_list_live(self):
        from deeppulse.src.sources.sina_source import SinaSource

        stocks = SinaSource().fetch_stock_list()
        codes = {s["code"] for s in stocks}
        assert len(stocks) > 1000, f"新浪股票列表过少: {len(stocks)}"
        assert "600000" in codes and "000001" in codes
        assert all(s["code"].startswith(("6", "0")) and not s["code"].startswith("68") for s in stocks)


# ── 多源一致性（前复权口径互验 + 故障切换） ─────────────────────────


class TestCrossSourceConsistency:
    def test_qfq_sources_agree(self):
        """东财 fqt=1 与腾讯 qfq 同为前复权：重叠日期收盘价/成交量应一致"""
        from deeppulse.src.sources.eastmoney_source import EastMoneySource
        from deeppulse.src.sources.tencent_source import TencentSource

        em = EastMoneySource().fetch_daily_kline("600000", date(2026, 8, 1), _WIN_END)
        tx = TencentSource().fetch_daily_kline("600000", date(2026, 8, 1), _WIN_END)
        merged = em.merge(tx, on="trade_date", suffixes=("_em", "_tx"))
        assert len(merged) >= 5, f"重叠日期过少: {len(merged)}"
        for _, row in merged.iterrows():
            assert row["close_em"] == pytest.approx(row["close_tx"], abs=0.02), f"{row['trade_date']} 收盘不一致"
            assert row["volume_em"] == pytest.approx(row["volume_tx"], abs=row["volume_tx"] * 0.001 + 200)

    def test_failover_chain_yields_data(self):
        """东财挂了/限频时 SourceFailover 自动切到腾讯/新浪，结果仍有数据"""
        from deeppulse.src.collector import SourceFailover
        from deeppulse.src.sources import make_source

        failover = SourceFailover([make_source(n) for n in ("eastmoney", "tencent", "sina")], timeout=30)
        try:
            df, src_name = failover.fetch("600000", _WIN_START, _WIN_END)
            assert not df.empty
            assert src_name in ("eastmoney", "tencent", "sina")
            assert df.iloc[-1]["trade_date"] == _BASE_DATE
            assert df.iloc[-1]["close"] == pytest.approx(_BASE_CLOSE, abs=0.02)
        finally:
            failover.cleanup()


class TestRealtimeLive:
    def test_tencent_quote_structural(self):
        """腾讯实时带真实成交时间戳（非请求时刻），可直接采信 trade_date/trade_time"""
        from deeppulse.src.realtime.tencent_source import TencentRealtimeSource

        quote = TencentRealtimeSource().fetch_quote("600000")
        assert quote is not None
        assert quote.trade_date and quote.trade_time  # 真实成交时间戳必有值
        assert quote.trade_date <= date.today().isoformat()
        assert quote.current and quote.current > 0
        assert quote.volume and quote.volume > 0
        assert quote.volume % 100 == 0  # 手 ×100 归一为股
        assert quote.amount and quote.amount > 0
        assert quote.stale_hint == ""  # 时间戳精确，无需保守标注
        assert quote.data_source == "tencent"

    def test_manager_failover_to_next_source(self):
        """首位源不存在时熔断管理器应切换到下一源拿到行情"""
        from deeppulse.src.realtime.manager import RealtimeQuoteManager

        mgr = RealtimeQuoteManager(priority=["__missing__", "tencent", "sina"], timeout=10)
        try:
            quote = mgr.fetch_quote("600000")
            assert quote is not None
            assert quote.data_source in ("tencent", "sina")
            assert quote.current and quote.current > 0
        finally:
            mgr.cleanup()

    def test_manager_batch(self):
        from deeppulse.src.realtime.manager import RealtimeQuoteManager

        mgr = RealtimeQuoteManager(timeout=10)
        try:
            quotes = mgr.fetch_quotes(["600000", "000001"])
            assert set(quotes) == {"600000", "000001"}
            assert all(q.current and q.current > 0 for q in quotes.values())
        finally:
            mgr.cleanup()


# ── 在线兜底链（online.fetch_daily_kline_online 全链） ─────────────


class TestOnlineFallbackLive:
    def test_daily_kline_fallback_chain(self, real_online):
        """本地缺数 → 在线兜底按降级链取数并标注来源"""
        from deeppulse.src import online as _online_mod

        _online_mod.clear_session_cache()
        df, label = real_online["fetch_daily_kline_online"]("600000", _WIN_START, _WIN_END)
        assert not df.empty
        assert label.startswith("online-")
        assert df.iloc[-1]["trade_date"] == _BASE_DATE
        assert df.iloc[-1]["close"] == pytest.approx(_BASE_CLOSE, abs=0.02)
