"""analyze_stock 合成工具 + technical_snapshot 抽取测试

核心钉子：一次 get_daily_kline 共享（绝不逐模块重取）、modules 裁剪、
摘要压缩（patterns≤5 / 支撑压力各≤6）、空 df 报错信封，
以及 calc_technical 抽取薄壳后行为不漂移。
"""

import json

import pandas as pd

from deeppulse.agent.tools import analysis, market


def _mk_df(bars=130):
    """合成日K：小幅震荡上升（供指标/形态/背离等纯函数消费）"""
    dates = pd.date_range("2026-01-01", periods=bars, freq="D")
    closes = [10.0 + 0.01 * i + (0.3 if i % 7 == 0 else -0.15) for i in range(bars)]
    return pd.DataFrame(
        {
            "trade_date": dates.strftime("%Y-%m-%d"),
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [100000 + (i % 5) * 20000 for i in range(bars)],
        }
    )


class _FakeQuery:
    def __init__(self, df):
        self.df = df
        self.calls = []

    def get_daily_kline(self, code, **kw):
        self.calls.append({"code": code, **kw})
        return self.df.copy()


def _patch_query(monkeypatch, df):
    fake = _FakeQuery(df)
    monkeypatch.setattr(analysis, "get_query", lambda: fake)
    monkeypatch.setattr(market, "get_query", lambda: fake)
    return fake


class TestAnalyzeStockSingleFetch:
    def test_all_modules_share_one_fetch(self, monkeypatch):
        fake = _patch_query(monkeypatch, _mk_df())
        out = json.loads(analysis.analyze_stock("600000", days=120))
        assert len(fake.calls) == 1  # 六模块共享一次取数，不逐个重取
        assert out["code"] == "600000"
        assert out["count"] > 0
        assert out["modules"] == list(analysis._ANALYZE_MODULES)
        for key in ("technical", "patterns", "divergence", "support_resistance", "volume", "trend"):
            assert key in out
        assert "ma" in out["technical"] and "macd" in out["technical"]

    def test_one_fetch_even_for_single_module(self, monkeypatch):
        fake = _patch_query(monkeypatch, _mk_df())
        json.loads(analysis.analyze_stock("600000", modules="trend"))
        assert len(fake.calls) == 1


class TestModulesTrimming:
    def test_subset_runs_only_requested(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df())
        out = json.loads(analysis.analyze_stock("600000", modules="trend,technical"))
        assert out["modules"] == ["technical", "trend"]  # canonical 顺序
        assert "technical" in out and "trend" in out
        assert "patterns" not in out and "support_resistance" not in out

    def test_unknown_module_warned_but_keeps_valid(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df())
        out = json.loads(analysis.analyze_stock("600000", modules="trend,bogus"))
        assert "忽略未知模块" in out["warning"] and "bogus" in out["warning"]
        assert out["modules"] == ["trend"]
        assert "trend" in out

    def test_all_invalid_modules_error(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df())
        out = json.loads(analysis.analyze_stock("600000", modules="bogus,foo"))
        assert "error" in out
        assert "technical" in out["available"]

    def test_empty_modules_means_all(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df())
        out = json.loads(analysis.analyze_stock("600000", modules=""))
        assert out["modules"] == list(analysis._ANALYZE_MODULES)


class TestEmptyDfErrorEnvelope:
    def test_empty_df_error(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df(bars=0))
        out = json.loads(analysis.analyze_stock("600000"))
        assert out == {"error": "股票 600000 无数据"}  # 与细粒度工具同一信封


class TestCondensing:
    def test_patterns_capped_at_5(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df())
        fake_patterns = [
            {"name": f"形态{i}", "type": "neutral", "confidence": 0.5, "description": "d"} for i in range(8)
        ]
        monkeypatch.setattr(analysis, "_recognize_patterns", lambda df: fake_patterns)
        out = json.loads(analysis.analyze_stock("600000", modules="patterns"))
        entry = out["patterns"]
        assert entry["count"] == 8
        assert len(entry["items"]) == 5
        assert entry["omitted"] == 3

    def test_support_resistance_capped_at_6(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df())
        fake_sr = {
            "resistance": [{"price": 10 + i * 0.1, "type": "前高", "strength": 3} for i in range(10)],
            "support": [{"price": 9 - i * 0.1, "type": "前低", "strength": 3} for i in range(10)],
            "current_price": 10.5,
            "position": "中部",
        }

        import deeppulse.agent.support_resistance as sr_mod

        monkeypatch.setattr(sr_mod, "detect_support_resistance", lambda df: fake_sr)
        out = json.loads(analysis.analyze_stock("600000", modules="sr"))
        entry = out["support_resistance"]
        assert len(entry["resistance"]["items"]) == 6 and entry["resistance"]["omitted"] == 4
        assert len(entry["support"]["items"]) == 6 and entry["support"]["omitted"] == 4
        assert entry["current_price"] == 10.5

    def test_volume_nested_lists_capped(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df())
        fake_vol = {
            "vol_trend": "increasing",
            "anomalies": [f"a{i}" for i in range(10)],
            "divergences": [f"d{i}" for i in range(5)],
            "conclusion": "放量",
        }

        import deeppulse.agent.volume_analysis as vol_mod

        monkeypatch.setattr(vol_mod, "analyze_volume_price", lambda df: fake_vol)
        out = json.loads(analysis.analyze_stock("600000", modules="volume"))
        vol = out["volume"]
        assert len(vol["anomalies"]["items"]) == 3 and vol["anomalies"]["omitted"] == 7
        assert len(vol["divergences"]["items"]) == 3 and vol["divergences"]["omitted"] == 2


class TestTechnicalSnapshotExtraction:
    def test_calc_technical_wrapper_unchanged_shape(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df())
        out = json.loads(market.calc_technical("600000", days=120))
        assert out["code"] == "600000"
        assert out["count"] > 0
        assert out["data_source"] == "local"
        for key in ("ma", "macd", "rsi", "kdj", "boll", "量价分析"):
            assert key in out
        assert out["ma"]["close"] == out["boll"]["close"]  # 同一份最新收盘价

    def test_calc_technical_bad_indicators_error_preserved(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df())
        out = json.loads(market.calc_technical("600000", indicators="bogus"))
        assert "error" in out and "ma,macd,rsi,kdj,boll" in out["error"]

    def test_technical_snapshot_is_pure_no_query(self):
        df = _mk_df()
        snap = market.technical_snapshot(df, "ma,rsi")
        assert "ma" in snap and "rsi" in snap
        assert "macd" not in snap and "kdj" not in snap

    def test_analyze_stock_technical_matches_snapshot(self, monkeypatch):
        _patch_query(monkeypatch, _mk_df())
        out = json.loads(analysis.analyze_stock("600000", modules="technical"))
        snap = out["technical"]
        assert set(snap) == set(market.technical_snapshot(_mk_df()))
