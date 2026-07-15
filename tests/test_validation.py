"""数据验证层测试"""

import numpy as np
import pandas as pd
import pytest


class TestValidateKlineDf:
    """验证 K 线数据清洗逻辑"""

    def _make_df(self, **overrides):
        """构造合法的 K 线 DataFrame"""
        data = {
            "trade_date": pd.date_range("2024-01-01", periods=5),
            "open": [10.0, 10.5, 10.2, 10.8, 11.0],
            "high": [10.5, 10.8, 10.5, 11.0, 11.2],
            "low": [9.8, 10.2, 10.0, 10.5, 10.8],
            "close": [10.3, 10.4, 10.3, 10.9, 11.1],
            "volume": [1000, 1200, 800, 1500, 1100],
        }
        data.update(overrides)
        return pd.DataFrame(data)

    def test_valid_df_passes(self):
        """合法数据应全部保留"""
        from deeppulse.src.validation import validate_kline_df

        df = self._make_df()
        result = validate_kline_df(df, "600519")
        assert len(result) == 5

    def test_nan_price_removed(self):
        """NaN 价格行应被剔除"""
        from deeppulse.src.validation import validate_kline_df

        df = self._make_df(close=[10.3, np.nan, 10.3, 10.9, 11.1])
        result = validate_kline_df(df, "600519")
        assert len(result) == 4  # 一行被剔除

    def test_high_less_than_low_removed(self):
        """high < low 的行应被剔除"""
        from deeppulse.src.validation import validate_kline_df

        df = self._make_df(high=[9.0, 10.8, 10.5, 11.0, 11.2])  # 第一行 high=9.0 < low=9.8
        result = validate_kline_df(df, "600519")
        assert len(result) < 5

    def test_negative_volume_corrected(self):
        """负成交量应被修正为0"""
        from deeppulse.src.validation import validate_kline_df

        df = self._make_df(volume=[1000, -1200, 800, 1500, 1100])
        result = validate_kline_df(df, "600519")
        assert len(result) == 5  # 行不被删除，而是修正
        assert all(result["volume"] >= 0)

    def test_empty_df_returns_empty(self):
        """空 DataFrame 应返回空"""
        from deeppulse.src.validation import validate_kline_df

        df = pd.DataFrame(columns=["trade_date", "open", "high", "low", "close", "volume"])
        result = validate_kline_df(df, "600519")
        assert len(result) == 0

    def test_missing_columns_raises(self):
        """缺少必要列应抛出 ValueError"""
        from deeppulse.src.validation import validate_kline_df

        df = pd.DataFrame({"trade_date": ["2024-01-01"], "open": [10.0]})
        with pytest.raises(ValueError):
            validate_kline_df(df, "600519")

    def test_sorted_by_date(self):
        """结果应按日期排序"""
        from deeppulse.src.validation import validate_kline_df

        df = self._make_df()
        # 打乱顺序
        df = df.iloc[::-1].reset_index(drop=True)
        result = validate_kline_df(df, "600519")
        dates = result["trade_date"].tolist()
        assert dates == sorted(dates)
