"""策略计算引擎（calc_engine）与引擎工具测试

全部离线：库写入 tmp duckdb 并 monkeypatch config.DB_PATH；
降级路径（数据不足不编数字）是本套测试的核心断言。
"""

import json

import duckdb
import pytest

from deeppulse import config
from deeppulse.src.database import init_memory_tables, init_tables, insert_daily_kline, upsert_stock_info
from deeppulse.strategy import calc_engine
from deeppulse.strategy.models import BacktestResult

SIMPLE_LOGIC = {
    "name": "测试均线策略",
    "factors": [{"factor_id": "ma_trend", "params": {"fast": 5, "slow": 20}, "weight": 1.0, "direction": 1}],
    "entry_threshold": 0.5,
}


def _seed_db(path, codes=("600000",), bars=300):
    """建库并种入 codes × bars 根合成日K（小幅上升趋势）"""
    conn = duckdb.connect(str(path))
    init_tables(conn)
    init_memory_tables(conn)
    upsert_stock_info(
        conn,
        [{"code": c, "name": f"测试{c}", "market": "sh", "board": "main", "list_date": "2010-01-01"} for c in codes],
    )
    records = []
    for code in codes:
        for i in range(bars):
            price = 10.0 + 0.01 * i
            records.append(
                {
                    "code": code,
                    "trade_date": "2020-01-01",  # 占位，下面覆盖
                    "open": price,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "volume": 100000 + i * 100,
                    "amount": 1e6,
                    "turnover": 1.0,
                    "data_source": "test",
                }
            )
    # 生成合法递增日期（工作日近似：按自然日推进即可，引擎不校验节假日）
    from datetime import date, timedelta

    d0 = date(2020, 1, 1)
    idx = 0
    for _code in codes:
        for i in range(bars):
            records[idx]["trade_date"] = (d0 + timedelta(days=i)).isoformat()
            idx += 1
    insert_daily_kline(conn, records)
    conn.close()
    return path


def _result_with_trades(n_trades=25, win_rate=0.55) -> BacktestResult:
    """合成回测结果（供 _metrics_payload 口径测试）"""
    r = BacktestResult(strategy_name="t", stock_code="600000")
    r.total_trades = n_trades
    r.win_rate = win_rate
    r.expectancy = 0.004
    r.profit_factor = 1.4
    r.max_drawdown = 0.08
    r.total_return = 0.15
    r.avg_hold_days = 6.5
    r.sharpe_ratio = 1.2
    r.trades = [{"entry_date": "2020-02-01", "exit_date": "2020-02-05", "pnl_pct": 0.01, "exit_reason": "止盈"}] * n_trades
    return r


# ═══════════════════ 降级路径（数据不足不编数字） ═══════════════════


class TestDegradation:
    def test_empty_db_degraded(self, tmp_db_path, monkeypatch):
        _seed_db(tmp_db_path, bars=0)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)
        out = calc_engine.evaluate_trade_logic("600000", SIMPLE_LOGIC)
        assert out["status"] == "degraded"
        assert "metrics" not in out  # 不得输出统计数字
        assert "无法" in out["guidance"]

    def test_short_history_degraded(self, tmp_db_path, monkeypatch):
        _seed_db(tmp_db_path, bars=50)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)
        out = calc_engine.evaluate_trade_logic("600000", SIMPLE_LOGIC)
        assert out["status"] == "degraded"
        assert out["bars_available"] == 50
        assert "metrics" not in out

    def test_calibrate_short_history_degraded(self, tmp_db_path, monkeypatch):
        _seed_db(tmp_db_path, bars=150)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)
        out = calc_engine.calibrate_params("600000", SIMPLE_LOGIC)
        assert out["status"] == "degraded"
        assert out["min_bars_required"] == calc_engine.MIN_BARS_FOR_CALIBRATE
        assert "best_params" not in out

    def test_missing_code_error(self):
        out = calc_engine.evaluate_trade_logic("", SIMPLE_LOGIC)
        assert "error" in out


# ═══════════════════ 逻辑校验（一次性返回全部错误） ═══════════════════


class TestValidation:
    def test_all_errors_at_once(self):
        bad = {
            "name": "bad",
            "factors": [
                {"factor_id": "不存在的因子", "weight": 1.0, "direction": 1},
                {"factor_id": "ma_trend", "params": {"fast": 30, "slow": 10}, "weight": -1, "direction": 5},
            ],
            "entry_threshold": 2.0,
        }
        out = calc_engine.evaluate_trade_logic("600000", bad)
        assert out["error"] == "策略规格非法"
        errs = out["validation_errors"]
        assert len(errs) >= 3  # 未知因子 + 权重 + direction + 阈值 一次给全
        assert any("不存在" in e or "未知" in e for e in errs)

    def test_invalid_json_shape(self):
        out = calc_engine.evaluate_trade_logic("600000", "not-a-dict")
        assert "error" in out

    def test_unknown_exit_preset(self, tmp_db_path, monkeypatch):
        _seed_db(tmp_db_path, bars=300)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)
        out = calc_engine.evaluate_trade_logic("600000", SIMPLE_LOGIC, exit_preset="yolo")
        assert "error" in out
        assert "conservative" in out["available"]


# ═══════════════════ 统计输出口径 ═══════════════════


class TestMetricsPayload:
    def test_sample_warning_when_few_trades(self):
        out = calc_engine._metrics_payload(_result_with_trades(5), bars=300)
        assert out["status"] == "ok"
        assert out["sample_warning"] is True
        assert out["in_sample"] is True
        assert any("样本不足" in n for n in out["notes"])

    def test_confidence_low_on_short_window(self):
        out = calc_engine._metrics_payload(_result_with_trades(25), bars=150)
        assert out["confidence"] == "低"
        assert out["sample_warning"] is False

    def test_confidence_medium_full(self):
        out = calc_engine._metrics_payload(_result_with_trades(25), bars=300)
        assert out["confidence"] == "中"
        assert any("in-sample" in n for n in out["notes"])

    def test_evaluate_end_to_end_ok(self, tmp_db_path, monkeypatch):
        _seed_db(tmp_db_path, bars=300)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)
        out = calc_engine.evaluate_trade_logic("600000", SIMPLE_LOGIC, days=300)
        assert out["status"] == "ok"
        assert out["exit_preset"] == "conservative"  # 安全默认
        assert out["bars_used"] >= calc_engine.MIN_BARS_FOR_BACKTEST
        for key in ("win_rate", "expectancy", "max_drawdown", "total_trades"):
            assert key in out["metrics"]
        assert "monte_carlo" in out  # 无论跑或 skip 都要有该字段

    def test_evaluate_rejects_invalid_logic_before_data(self, tmp_db_path, monkeypatch):
        _seed_db(tmp_db_path, bars=300)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)
        out = calc_engine.evaluate_trade_logic("600000", {"factors": []})
        assert out["error"] == "策略规格非法"


# ═══════════════════ 参数校准（预算与评分面） ═══════════════════


class TestCalibrate:
    def test_budget_capped(self, monkeypatch):
        calls = []

        def fake_run(code, spec, days):
            calls.append(spec)
            r = _result_with_trades(12)
            return r, float(len(calls))  # 递增评分，必有提升

        monkeypatch.setattr("deeppulse.strategy.optimizer.run_spec_backtest", fake_run)
        spec_dict = dict(SIMPLE_LOGIC)
        best, result, score, surface = calc_engine.grid_search_params(
            "600000", __import__("deeppulse.strategy.spec", fromlist=["StrategySpec"]).StrategySpec.from_dict(spec_dict),
            days=100, base_score=float("-inf"), budget=3,
        )
        assert len(calls) <= 3
        assert len(surface) <= 3
        assert result is not None

    def test_calibrate_end_to_end(self, tmp_db_path, monkeypatch):
        _seed_db(tmp_db_path, bars=300)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)

        def fake_grid(code, spec, days, base_score, budget=50):
            from deeppulse.strategy.spec import StrategySpec

            best = StrategySpec.from_dict(spec.to_dict())
            best.factors[0].params["fast"] = 3
            return best, _result_with_trades(20), 0.11, [{"param": "fast", "value": 3, "score": 0.11}]

        monkeypatch.setattr(calc_engine, "grid_search_params", fake_grid)
        out = calc_engine.calibrate_params("600000", SIMPLE_LOGIC, days=300, max_combos=10)
        assert out["status"] == "ok"
        assert out["in_sample"] is True
        assert "过拟合" in out["warning"]
        assert out["best_params"]["factors"][0]["params"]["fast"] == 3
        assert out["combos_tested"] == 1


# ═══════════════════ 市场状态刻画 ═══════════════════


class TestMarketRegime:
    def test_too_few_samples_degraded(self, tmp_db_path, monkeypatch):
        _seed_db(tmp_db_path, codes=("600000", "600001"), bars=100)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)
        out = calc_engine.compute_market_regime(codes="600000,600001")
        assert out["status"] == "degraded"
        assert "regime" not in out

    def test_enough_samples_ok(self, tmp_db_path, monkeypatch):
        codes = tuple(f"60000{i}" for i in range(6))
        _seed_db(tmp_db_path, codes=codes, bars=100)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)
        out = calc_engine.compute_market_regime(codes=",".join(codes))
        assert out["status"] == "ok"
        assert out["regime"] in ("trend", "range", "high-vol", "low-vol")
        assert out["sampled_stocks"] == 6
        assert "thresholds" in out
        assert out["advice_hint"]["position_bias"]

    def test_market_sample_from_stock_list(self, tmp_db_path, monkeypatch):
        codes = tuple(f"60000{i}" for i in range(8))
        _seed_db(tmp_db_path, codes=codes, bars=100)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)
        out = calc_engine.compute_market_regime(sample_size=6)
        assert out["status"] == "ok"
        assert out["sampled_from"] == "market-sample"
        assert out["sampled_stocks"] <= 6


# ═══════════════════ 保守仓位 ═══════════════════


class TestPositionSize:
    def test_basic_risk_budget(self):
        out = calc_engine.position_size(capital=100000, entry_price=10, stop_price=9.5)
        assert out["risk_per_share"] == pytest.approx(0.5)
        # 风险预算 2000 元 / 0.5 = 4000 股 → 40 手
        assert out["suggested_shares"] == 4000
        assert out["max_loss_at_stop"] == pytest.approx(2000)
        assert out["position_pct"] == pytest.approx(0.4)  # 由风险预算决定
        assert "kelly_note" in out  # 无样本不给 Kelly

    def test_atr_fallback_stop(self):
        out = calc_engine.position_size(capital=100000, entry_price=10, stop_price=0, atr=1.0)
        assert out["stop_source"] == "atr_2x"
        assert out["risk_per_share"] == pytest.approx(2.0)

    def test_invalid_stop_without_atr(self):
        out = calc_engine.position_size(capital=100000, entry_price=10, stop_price=11)
        assert "error" in out

    def test_risk_pct_capped(self):
        out = calc_engine.position_size(capital=100000, entry_price=10, stop_price=9.5, risk_pct=0.5)
        assert out["risk_pct_used"] == calc_engine.RISK_PCT_MAX

    def test_lot_rounding(self):
        out = calc_engine.position_size(capital=10000, entry_price=33, stop_price=32)
        # 风险预算 200 / 1 = 200 股 → 向下取整 200 股（2 手）
        assert out["suggested_shares"] % 100 == 0
        assert out["suggested_shares"] > 0

    def test_below_one_lot_error(self):
        out = calc_engine.position_size(capital=1000, entry_price=100, stop_price=99)
        assert "error" in out

    def test_kelly_requires_sample(self):
        # 样本 < 20 不给 Kelly
        out = calc_engine.position_size(
            capital=100000, entry_price=10, stop_price=9.5,
            win_rate=0.6, avg_win=0.05, avg_loss=0.02, sample_trades=10,
        )
        assert "kelly_used" not in out
        assert "样本不足" in out["kelly_note"]

    def test_kelly_with_sample_caps_position(self):
        out = calc_engine.position_size(
            capital=100000, entry_price=10, stop_price=9.5,
            win_rate=0.55, avg_win=0.05, avg_loss=0.025, sample_trades=50,
        )
        assert "kelly_used" in out
        assert out["kelly_used"] <= calc_engine.KELLY_POSITION_CAP
        assert out["position_pct"] <= calc_engine.KELLY_POSITION_CAP + 0.01

    def test_bad_input(self):
        assert "error" in calc_engine.position_size(capital=-1, entry_price=10, stop_price=9)
        assert "error" in calc_engine.position_size(capital="x", entry_price=10, stop_price=9)

    def test_star_board_min_200_shares(self):
        """科创板最小买入 200 股：不足 200 股的预算应报错，且股数是 200 的整数倍"""
        out = calc_engine.position_size(capital=2000, entry_price=10, stop_price=9.5, code="688001")
        # 风险预算 40 / 0.5 = 80 股 < 200 股 → 不足 1 手
        assert "error" in out and "200" in out["error"]

        out2 = calc_engine.position_size(capital=100000, entry_price=10, stop_price=9.5, code="688001")
        assert out2["suggested_shares"] % 200 == 0

    def test_main_board_min_100_shares(self):
        out = calc_engine.position_size(capital=100000, entry_price=10, stop_price=9.5, code="600000")
        assert out["suggested_shares"] % 100 == 0
        out2 = calc_engine.position_size(capital=100000, entry_price=10, stop_price=9.5, code="920000")
        assert out2["suggested_shares"] % 100 == 0  # 北交所与主板同 100 股步长


# ═══════════════════ 工具接线 ═══════════════════


class TestEngineTools:
    def test_dispatch_registered(self):
        from deeppulse.agent.tools import TOOL_DISPATCH

        for name in ("list_factors", "evaluate_trade_logic", "calibrate_params", "market_regime", "position_size"):
            assert name in TOOL_DISPATCH

    def test_list_factors_catalog(self):
        from deeppulse.agent.tools.engine import list_factors

        out = json.loads(list_factors())
        cats = out["categories"]
        assert len(cats) == 13  # 13 个因子分类
        total = sum(len(c["factors"]) for c in cats)
        assert total >= 100  # 全量因子
        ids = {f["id"] for c in cats for f in c["factors"]}
        assert "ma_trend" in ids
        for c in cats:
            assert c["category"]
            for f in c["factors"]:
                assert f["id"] and f["name"]
                for p in f["params"].values():
                    assert set(p) == {"default", "min", "max", "step"}

    def test_get_factor_error_slim(self):
        from deeppulse.strategy.factor_library import get_factor

        with pytest.raises(KeyError) as ei:
            get_factor("不存在的因子")
        msg = str(ei.value)
        assert len(msg) < 200  # 不再罗列 100+ 全部 id
        assert "list_factors" in msg

    def test_tools_mentioned_in_prompt(self):
        from deeppulse.agent.prompts import build_system_prompt

        p = build_system_prompt()
        for name in ("evaluate_trade_logic", "calibrate_params", "market_regime", "position_size"):
            assert name in p

    def test_evaluate_tool_json_roundtrip(self, tmp_db_path, monkeypatch):
        _seed_db(tmp_db_path, bars=300)
        monkeypatch.setattr(config, "DB_PATH", tmp_db_path)
        from deeppulse.agent.tools.engine import evaluate_trade_logic

        out = json.loads(evaluate_trade_logic("600000", json.dumps(SIMPLE_LOGIC), days=300))
        assert out["status"] == "ok"
        assert out["exit_preset"] == "conservative"

    def test_evaluate_tool_bad_json(self):
        from deeppulse.agent.tools.engine import evaluate_trade_logic

        out = json.loads(evaluate_trade_logic("600000", "{bad json"))
        assert "error" in out

    def test_position_tool_json(self):
        from deeppulse.agent.tools.engine import position_size

        out = json.loads(position_size(capital=100000, entry_price=10, stop_price=9.5))
        assert out["suggested_shares"] > 0
