"""Shared test fixtures for DeepPulse test suite."""

import numpy as np
import pandas as pd
import pytest

from deeppulse.src import online as _online_mod

# 在线取数的真实实现快照（禁网护栏覆盖后，测试可经 real_online fixture 取回自用）
_REAL_ONLINE_IMPLS = {
    "fetch_daily_kline_online": _online_mod.fetch_daily_kline_online,
    "fetch_index_kline_online": _online_mod.fetch_index_kline_online,
    "fetch_stock_list_online": _online_mod.fetch_stock_list_online,
    "fetch_minute_kline_online": _online_mod.fetch_minute_kline_online,
    "fetch_weekly_kline_online": _online_mod.fetch_weekly_kline_online,
    "fetch_market_snapshot": _online_mod.fetch_market_snapshot,
}


@pytest.fixture
def real_online():
    """返回在线取数真实实现 dict（需测试真实缓存/降级逻辑时与下层 mock 搭配使用）"""
    return dict(_REAL_ONLINE_IMPLS)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """测试默认禁网：关闭在线兜底 + 在线取数快速失败，防止误打真实网络。

    需要在线兜底行为的用例：自行 mock 对应 online.fetch_* 函数并显式开启
    online_fallback（构造 StockQuery(online_fallback=True) 或改 config 常量）。
    """
    from deeppulse import config

    monkeypatch.setattr(config, "DATA_ONLINE_FALLBACK", False)
    monkeypatch.setattr(config, "DATA_CACHE_TO_DB", False)  # 防止测试兜底结果写进真实库
    _online_mod.clear_session_cache()

    def _blocked(*args, **kwargs):
        raise _online_mod.OnlineFetchError("测试环境禁止联网（请 mock 在线取数函数）")

    for name in _REAL_ONLINE_IMPLS:
        monkeypatch.setattr(_online_mod, name, _blocked)
    yield


@pytest.fixture
def empty_db_path(tmp_path):
    """只建记忆表的文件库（复刻启动现状：行情表未建），用于空库兜底测试。"""
    from deeppulse.src.database import get_connection, init_memory_tables

    path = tmp_path / "empty.duckdb"
    conn = get_connection(path)
    init_memory_tables(conn)
    conn.close()
    return path


@pytest.fixture
def in_memory_db():
    """Provide an in-memory DuckDB connection with all tables initialized."""
    import duckdb

    from deeppulse.src.database import init_memory_tables, init_tables

    conn = duckdb.connect(":memory:")
    init_tables(conn)
    init_memory_tables(conn)
    yield conn
    conn.close()


@pytest.fixture
def tmp_db_path(tmp_path):
    """Provide a temporary DuckDB file path for tests that need file-based DB."""
    return tmp_path / "test.duckdb"


@pytest.fixture
def sample_kline_df():
    """Provide a synthetic DataFrame with OHLCV data for 100 trading days."""
    np.random.seed(42)
    n = 100
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    base_price = 10.0
    returns = np.random.normal(0.002, 0.02, n)
    prices = base_price * np.cumprod(1 + returns)

    return pd.DataFrame(
        {
            "trade_date": dates,
            "open": prices * (1 + np.random.uniform(-0.01, 0.01, n)),
            "high": prices * (1 + np.random.uniform(0.005, 0.03, n)),
            "low": prices * (1 - np.random.uniform(0.005, 0.03, n)),
            "close": prices,
            "volume": np.random.randint(100000, 1000000, n).astype(float),
            "amount": np.random.uniform(1e6, 1e7, n),
            "turnover": np.random.uniform(0.5, 5.0, n),
            "data_source": "test",
        }
    )


