"""策略计算引擎核心测试：因子库、策略规格、行情画像统计"""

import pandas as pd
import pytest

from deeppulse.agent.indicators import TechnicalIndicators
from deeppulse.strategy.factor_library import get_factor, list_factors
from deeppulse.strategy.llm_generator import build_kline_stats
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


# ============ 行情画像统计 ============


class TestBuildKlineStats:
    def test_build_kline_stats(self, kline_with_indicators):
        stats = build_kline_stats(kline_with_indicators)
        assert stats["bars"] == 100
        assert "indicator_snapshot" in stats
        assert "max_drawdown_pct" in stats

    def test_empty_df_returns_empty(self):
        assert build_kline_stats(pd.DataFrame()) == {}
