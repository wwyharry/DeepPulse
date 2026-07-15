"""Shared test fixtures for DeepPulse test suite."""

import numpy as np
import pandas as pd
import pytest


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


@pytest.fixture
def sample_kline_rows():
    """Provide synthetic kline data as list-of-tuples (screener format).

    Format: [(trade_date, open, high, low, close, volume, amount), ...]
    """
    np.random.seed(42)
    n = 60
    base = 10.0
    rows = []
    for i in range(n):
        price = base * (1 + 0.001 * i + np.random.normal(0, 0.015))
        o = price * (1 + np.random.uniform(-0.01, 0.01))
        h = price * (1 + abs(np.random.normal(0, 0.015)))
        lo = price * (1 - abs(np.random.normal(0, 0.015)))
        c = price
        v = float(np.random.randint(100000, 500000))
        a = c * v
        rows.append((f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}", o, h, lo, c, v, a))
    return rows
