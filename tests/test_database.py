"""Unit tests for src/database.py — DuckDB CRUD operations with in-memory database."""

from deeppulse.src.database import (
    get_last_fetch_date,
    get_latest_kline_date,
    get_stock_list,
    init_memory_tables,
    init_tables,
    insert_daily_kline,
    log_fetch,
    upsert_stock_info,
)


class TestInitTables:
    def test_creates_core_tables(self, in_memory_db):
        conn = in_memory_db
        tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
        table_names = {t[0] for t in tables}
        assert "stock_info" in table_names
        assert "daily_kline" in table_names
        assert "fetch_log" in table_names

    def test_creates_memory_tables(self, in_memory_db):
        conn = in_memory_db
        tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
        table_names = {t[0] for t in tables}
        assert "long_term_memories" in table_names
        assert "predictions" in table_names
        assert "knowledge_entities" in table_names
        assert "user_profile" in table_names

    def test_idempotent(self, in_memory_db):
        # Should not raise when called twice
        init_tables(in_memory_db)
        init_memory_tables(in_memory_db)


class TestUpsertStockInfo:
    def test_insert(self, in_memory_db):
        records = [{"code": "600000", "name": "浦发银行", "market": "sh", "board": "main", "list_date": "1999-11-10"}]
        count = upsert_stock_info(in_memory_db, records)
        assert count == 1
        result = in_memory_db.execute("SELECT name FROM stock_info WHERE code='600000'").fetchone()
        assert result[0] == "浦发银行"

    def test_update(self, in_memory_db):
        records = [{"code": "600000", "name": "浦发银行", "market": "sh", "board": "main", "list_date": "1999-11-10"}]
        upsert_stock_info(in_memory_db, records)
        records[0]["name"] = "浦发银行(更新)"
        upsert_stock_info(in_memory_db, records)
        result = in_memory_db.execute("SELECT name FROM stock_info WHERE code='600000'").fetchone()
        assert result[0] == "浦发银行(更新)"

    def test_empty_list(self, in_memory_db):
        assert upsert_stock_info(in_memory_db, []) == 0


class TestInsertDailyKline:
    def test_insert(self, in_memory_db):
        records = [
            {
                "code": "600000",
                "trade_date": "2024-01-15",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.3,
                "volume": 100000,
                "amount": 1e6,
                "turnover": 1.5,
                "data_source": "test",
            }
        ]
        count = insert_daily_kline(in_memory_db, records)
        assert count == 1

    def test_duplicate_ignored(self, in_memory_db):
        records = [
            {
                "code": "600000",
                "trade_date": "2024-01-15",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.3,
                "volume": 100000,
                "amount": 1e6,
                "turnover": 1.5,
                "data_source": "test",
            }
        ]
        insert_daily_kline(in_memory_db, records)
        count = insert_daily_kline(in_memory_db, records)
        assert count == 0

    def test_empty_list(self, in_memory_db):
        assert insert_daily_kline(in_memory_db, []) == 0


class TestLogFetch:
    def test_log_and_retrieve(self, in_memory_db):
        log_fetch(in_memory_db, "600000", "akshare", "2024-01-01", "2024-01-15", 100, "success")
        result = in_memory_db.execute("SELECT code, status FROM fetch_log").fetchone()
        assert result[0] == "600000"
        assert result[1] == "success"

    def test_auto_increment_id(self, in_memory_db):
        log_fetch(in_memory_db, "600000", "akshare", "2024-01-01", "2024-01-15", 100, "success")
        log_fetch(in_memory_db, "600001", "akshare", "2024-01-01", "2024-01-15", 50, "success")
        ids = in_memory_db.execute("SELECT id FROM fetch_log ORDER BY id").fetchall()
        assert ids[0][0] == 1
        assert ids[1][0] == 2


class TestGetLastFetchDate:
    def test_returns_date(self, in_memory_db):
        log_fetch(in_memory_db, "600000", "akshare", "2024-01-01", "2024-01-15", 100, "success")
        result = get_last_fetch_date(in_memory_db, "600000", "akshare")
        assert result is not None
        assert "2024-01-15" in result

    def test_returns_none_no_data(self, in_memory_db):
        result = get_last_fetch_date(in_memory_db, "999999", "akshare")
        assert result is None

    def test_ignores_failed_status(self, in_memory_db):
        log_fetch(in_memory_db, "600000", "akshare", "2024-01-01", "2024-01-15", 0, "failed", "timeout")
        result = get_last_fetch_date(in_memory_db, "600000", "akshare")
        assert result is None


class TestGetLatestKlineDate:
    def test_returns_max_date(self, in_memory_db):
        records = [
            {
                "code": "600000",
                "trade_date": "2024-01-15",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.3,
                "volume": 100000,
                "amount": 1e6,
                "turnover": 1.5,
                "data_source": "test",
            },
            {
                "code": "600000",
                "trade_date": "2024-01-16",
                "open": 10.3,
                "high": 10.8,
                "low": 10.1,
                "close": 10.6,
                "volume": 120000,
                "amount": 1.2e6,
                "turnover": 1.8,
                "data_source": "test",
            },
        ]
        insert_daily_kline(in_memory_db, records)
        result = get_latest_kline_date(in_memory_db, "600000")
        assert "2024-01-16" in result


class TestGetStockList:
    def test_active_only(self, in_memory_db):
        records = [
            {"code": "600000", "name": "浦发银行", "market": "sh", "board": "main", "list_date": "1999-11-10"},
            {
                "code": "600001",
                "name": "退市股",
                "market": "sh",
                "board": "main",
                "list_date": "2000-01-01",
                "delist_date": "2020-01-01",
            },
        ]
        upsert_stock_info(in_memory_db, records)
        active = get_stock_list(in_memory_db, active_only=True)
        assert "600000" in active
        assert "600001" not in active

    def test_all(self, in_memory_db):
        records = [
            {"code": "600000", "name": "浦发银行", "market": "sh", "board": "main", "list_date": "1999-11-10"},
            {
                "code": "600001",
                "name": "退市股",
                "market": "sh",
                "board": "main",
                "list_date": "2000-01-01",
                "delist_date": "2020-01-01",
            },
        ]
        upsert_stock_info(in_memory_db, records)
        all_stocks = get_stock_list(in_memory_db, active_only=False)
        assert "600000" in all_stocks
        assert "600001" in all_stocks
