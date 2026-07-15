"""Unit tests for src/query.py — StockQuery high-level query API."""

import duckdb
import pytest

from deeppulse.src.database import init_memory_tables, init_tables, insert_daily_kline, log_fetch, upsert_stock_info
from deeppulse.src.query import StockQuery


@pytest.fixture
def populated_db(tmp_db_path):
    """Create a temp DuckDB with test data and return a StockQuery instance."""
    conn = duckdb.connect(str(tmp_db_path))
    init_tables(conn)
    init_memory_tables(conn)

    # Insert stock info
    upsert_stock_info(
        conn,
        [
            {"code": "600000", "name": "浦发银行", "market": "sh", "board": "main", "list_date": "1999-11-10"},
            {"code": "000001", "name": "平安银行", "market": "sz", "board": "main", "list_date": "1991-04-03"},
            {
                "code": "600001",
                "name": "退市股",
                "market": "sh",
                "board": "main",
                "list_date": "2000-01-01",
                "delist_date": "2020-01-01",
            },
        ],
    )

    # Insert kline data
    kline_records = []
    for i in range(60):
        kline_records.append(
            {
                "code": "600000",
                "trade_date": f"2024-{(i // 30) + 1:02d}-{(i % 30) + 1:02d}",
                "open": 10.0 + i * 0.01,
                "high": 10.5 + i * 0.01,
                "low": 9.5 + i * 0.01,
                "close": 10.2 + i * 0.01,
                "volume": 100000 + i * 1000,
                "amount": 1e6 + i * 10000,
                "turnover": 1.5,
                "data_source": "test",
            }
        )
    insert_daily_kline(conn, kline_records)

    # Insert fetch log
    log_fetch(conn, "600000", "test", "2024-01-01", "2024-02-29", 60, "success")

    conn.close()
    return StockQuery(db_path=tmp_db_path)


class TestGetStockInfo:
    def test_single(self, populated_db):
        df = populated_db.get_stock_info("600000")
        assert len(df) == 1
        assert df.iloc[0]["name"] == "浦发银行"

    def test_all(self, populated_db):
        df = populated_db.get_stock_info()
        assert len(df) >= 3

    def test_not_found(self, populated_db):
        df = populated_db.get_stock_info("999999")
        assert len(df) == 0


class TestGetDailyKline:
    def test_with_limit(self, populated_db):
        df = populated_db.get_daily_kline("600000", limit=10)
        assert len(df) == 10

    def test_ascending_order(self, populated_db):
        df = populated_db.get_daily_kline("600000", limit=10)
        dates = df["trade_date"].tolist()
        assert dates == sorted(dates)

    def test_with_date_range(self, populated_db):
        df = populated_db.get_daily_kline("600000", start_date="2024-02-01")
        for d in df["trade_date"]:
            assert str(d) >= "2024-02-01"


class TestSearchStocks:
    def test_by_name(self, populated_db):
        df = populated_db.search_stocks("浦发")
        assert len(df) >= 1
        assert "浦发银行" in df["name"].values

    def test_by_code(self, populated_db):
        df = populated_db.search_stocks("600000")
        assert len(df) >= 1


class TestGetDataStats:
    def test_returns_keys(self, populated_db):
        stats = populated_db.get_data_stats()
        assert "stock_count" in stats
        assert "kline_count" in stats


class TestGetFetchLog:
    def test_basic(self, populated_db):
        df = populated_db.get_fetch_log(code="600000")
        assert len(df) >= 1
