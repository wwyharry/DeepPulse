"""LLM 量化策略生成平台测试：因子库、策略规格、LLM 生成器、自动优化循环、API"""

import json

import pandas as pd
import pytest

from deeppulse.agent.indicators import TechnicalIndicators
from deeppulse.strategy.factor_library import get_factor, list_factors
from deeppulse.strategy.llm_generator import (
    LLMGenerationError,
    LLMStrategyGenerator,
    apply_actions,
    build_kline_stats,
)
from deeppulse.strategy.models import BacktestResult
from deeppulse.strategy.optimizer import AutoOptimizer, OptimizeReport
from deeppulse.strategy.spec import (
    FactorSpec,
    StrategySpec,
    compile_signal_func,
    default_spec,
)


@pytest.fixture
def kline_with_indicators(sample_kline_df):
    """带技术指标的合成K线"""
    return TechnicalIndicators.add_all(sample_kline_df)


class FakeLLMClient:
    """模拟 LLM 客户端，返回预设内容"""

    def __init__(self, content: str = "{}"):
        self.content = content
        self.calls = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        return {"content": self.content, "tool_calls": None, "stop_reason": "stop"}


VALID_SPEC_JSON = json.dumps({
    "name": "测试策略",
    "factors": [
        {"factor_id": "ma_trend", "params": {"fast": 5, "slow": 20}, "weight": 1.0, "direction": 1},
        {"factor_id": "macd_trend", "params": {}, "weight": 0.8, "direction": 1},
    ],
    "entry_threshold": 0.6,
    "exit_preset": "balanced",
    "llm_rationale": "趋势共振",
}, ensure_ascii=False)


# ============ 因子库 ============


class TestFactorLibrary:
    def test_factor_count_and_unique_ids(self):
        factors = list_factors()
        assert len(factors) >= 15
        ids = [f.id for f in factors]
        assert len(ids) == len(set(ids))

    def test_get_factor_unknown_raises(self):
        with pytest.raises(KeyError):
            get_factor("not_a_factor")

    def test_all_factors_score_in_range(self, kline_with_indicators):
        for factor in list_factors():
            s = factor.score(kline_with_indicators)
            assert isinstance(s, pd.Series), factor.id
            assert len(s) == len(kline_with_indicators), factor.id
            assert s.min() >= 0.0 and s.max() <= 1.0, factor.id
            assert not s.isna().any(), factor.id

    def test_factor_missing_columns_safe(self):
        """缺少指标列时因子得分为 0 而非抛异常"""
        df = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        for factor in list_factors():
            s = factor.score(df)
            assert len(s) == 3
            assert (s >= 0).all() and (s <= 1).all()

    def test_param_grid_contains_default(self):
        for factor in list_factors():
            for param in factor.params.values():
                grid = param.grid()
                assert param.min <= min(grid) and max(grid) <= param.max
                assert param.default in grid


# ============ 策略规格 ============


class TestStrategySpec:
    def test_default_spec_valid(self):
        assert default_spec().validate() == []

    def test_json_roundtrip(self):
        spec = default_spec()
        restored = StrategySpec.from_json(spec.to_json())
        assert restored.to_dict() == spec.to_dict()

    def test_validate_unknown_factor(self):
        spec = StrategySpec(name="x", factors=[FactorSpec(factor_id="nope")])
        assert any("未知因子" in e for e in spec.validate())

    def test_validate_param_out_of_range(self):
        spec = StrategySpec(
            name="x",
            factors=[FactorSpec(factor_id="ma_trend", params={"fast": 999})],
        )
        assert any("越界" in e for e in spec.validate())

    def test_validate_zero_weight(self):
        spec = StrategySpec(
            name="x",
            factors=[FactorSpec(factor_id="ma_trend", weight=0)],
        )
        assert any("权重" in e for e in spec.validate())

    def test_compile_signal_func_output(self, kline_with_indicators):
        signal = compile_signal_func(default_spec())(kline_with_indicators)
        assert len(signal) == len(kline_with_indicators)
        assert set(signal.unique()).issubset({0, 1})

    def test_compile_invalid_spec_raises(self):
        spec = StrategySpec(name="x", factors=[FactorSpec(factor_id="nope")])
        with pytest.raises(ValueError):
            compile_signal_func(spec)


# ============ LLM 生成器 ============


class TestLLMStrategyGenerator:
    def test_build_kline_stats(self, kline_with_indicators):
        stats = build_kline_stats(kline_with_indicators)
        assert stats["bars"] == 100
        assert "indicator_snapshot" in stats
        assert "max_drawdown_pct" in stats

    def test_generate_success(self):
        client = FakeLLMClient(f"```json\n{VALID_SPEC_JSON}\n```")
        gen = LLMStrategyGenerator(llm_client=client)
        spec = gen.generate("600519", {"bars": 100}, hint="偏好趋势")
        assert spec.name == "测试策略"
        assert len(spec.factors) == 2
        assert client.calls == 1

    def test_generate_retries_then_raises(self):
        client = FakeLLMClient("这不是JSON")
        gen = LLMStrategyGenerator(llm_client=client)
        with pytest.raises(LLMGenerationError):
            gen.generate("600519", {})
        assert client.calls == 3  # 首次 + 2 次重试

    def test_generate_invalid_spec_retries(self):
        """LLM 返回合法 JSON 但因子不存在 → 视为失败并重试"""
        bad = json.dumps({"name": "x", "factors": [{"factor_id": "nope"}]})
        client = FakeLLMClient(bad)
        gen = LLMStrategyGenerator(llm_client=client)
        with pytest.raises(LLMGenerationError):
            gen.generate("600519", {})

    def test_analyze_backtest_success(self):
        payload = json.dumps({
            "comment": "交易次数偏少，建议降低阈值",
            "actions": [{"type": "adjust_threshold", "value": 0.5}],
        })
        gen = LLMStrategyGenerator(llm_client=FakeLLMClient(payload))
        result = gen.analyze_backtest(default_spec(), {"total_return": 0.1}, [])
        assert result["comment"]
        assert result["actions"][0]["type"] == "adjust_threshold"

    def test_analyze_backtest_parse_failure_returns_empty_actions(self):
        gen = LLMStrategyGenerator(llm_client=FakeLLMClient("无法解析的输出"))
        result = gen.analyze_backtest(default_spec(), {}, [])
        assert result["actions"] == []

    def test_apply_actions(self):
        spec = default_spec()
        actions = [
            {"type": "adjust_threshold", "value": 0.45},
            {"type": "adjust_weight", "factor_id": "ma_trend", "weight": 1.5},
            {"type": "adjust_param", "factor_id": "ma_trend", "param": "fast", "value": 10},
            {"type": "add_factor", "factor_id": "rsi_momentum", "params": {}, "weight": 0.7},
            {"type": "remove_factor", "factor_id": "volume_breakout"},
        ]
        new_spec = apply_actions(spec, actions)
        assert new_spec.entry_threshold == 0.45
        by_id = {f.factor_id: f for f in new_spec.factors}
        assert by_id["ma_trend"].weight == 1.5
        assert by_id["ma_trend"].params["fast"] == 10
        assert "rsi_momentum" in by_id
        assert "volume_breakout" not in by_id

    def test_apply_actions_invalid_falls_back(self):
        """动作导致整体非法时回退为原规格"""
        spec = default_spec()
        bad = [{"type": "add_factor", "factor_id": "nope", "weight": 1.0}]
        assert apply_actions(spec, bad).to_dict() == spec.to_dict()


# ============ 自动优化循环 ============


def _fake_result(score_hint: float) -> BacktestResult:
    r = BacktestResult()
    r.total_return = score_hint
    r.sharpe_ratio = 1.0
    r.win_rate = 0.5
    r.max_drawdown = 0.1
    r.total_trades = 10
    return r


class TestAutoOptimizer:
    def test_optimize_full_loop(self, monkeypatch):
        """mock LLM + mock 回测，验证完整循环不崩溃且结构正确"""
        monkeypatch.setattr(
            "deeppulse.strategy.optimizer.backtest_stock",
            lambda code, **kwargs: _fake_result(0.08),
        )
        client = FakeLLMClient(VALID_SPEC_JSON)
        optimizer = AutoOptimizer(llm_client=client)
        report = optimizer.optimize("600519", days=250, max_iterations=3, kline_stats={"bars": 100})
        assert isinstance(report, OptimizeReport)
        assert 1 <= len(report.history) <= 3
        assert report.best_spec.validate() == []
        d = report.to_dict()
        assert "best_spec" in d and "history" in d and "best_score" in d

    def test_optimize_llm_failure_uses_default_spec(self, monkeypatch):
        monkeypatch.setattr(
            "deeppulse.strategy.optimizer.backtest_stock",
            lambda code, **kwargs: _fake_result(0.05),
        )
        client = FakeLLMClient("垃圾输出")  # generate/analyze 均失败
        optimizer = AutoOptimizer(llm_client=client)
        report = optimizer.optimize("600519", days=250, max_iterations=2, kline_stats={"bars": 1})
        assert report.best_spec.validate() == []
        assert len(report.history) >= 1

    def test_optimize_converges_early(self, monkeypatch):
        """评分无提升时应提前收敛"""
        monkeypatch.setattr(
            "deeppulse.strategy.optimizer.backtest_stock",
            lambda code, **kwargs: _fake_result(0.1),
        )
        client = FakeLLMClient(VALID_SPEC_JSON)
        optimizer = AutoOptimizer(llm_client=client)
        report = optimizer.optimize("600519", days=250, max_iterations=10, kline_stats={"bars": 1})
        assert len(report.history) < 10
        assert report.converged

    def test_optimize_backtest_failure_graceful(self, monkeypatch):
        def boom(code, **kwargs):
            raise RuntimeError("数据缺失")

        monkeypatch.setattr("deeppulse.strategy.optimizer.backtest_stock", boom)
        optimizer = AutoOptimizer(llm_client=FakeLLMClient("垃圾"))
        report = optimizer.optimize("600519", days=250, max_iterations=2, kline_stats=None)
        assert report.history[0]["score"] is None
        assert report.best_spec.validate() == []  # 兜底默认策略
