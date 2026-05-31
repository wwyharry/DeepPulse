"""DuckDB 数据库连接管理与表操作"""

from pathlib import Path

import duckdb

import config


def get_connection(db_path: Path = None) -> duckdb.DuckDBPyConnection:
    """获取DuckDB连接"""
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path))


def init_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """初始化所有表结构"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_info (
            code VARCHAR PRIMARY KEY,
            name VARCHAR,
            market VARCHAR,
            board VARCHAR,
            list_date DATE,
            delist_date DATE,
            updated_at TIMESTAMP DEFAULT current_timestamp
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_kline (
            code VARCHAR NOT NULL,
            trade_date DATE NOT NULL,
            open DOUBLE,
            high DOUBLE,
            low DOUBLE,
            close DOUBLE,
            volume BIGINT,
            amount DOUBLE,
            turnover DOUBLE,
            data_source VARCHAR NOT NULL,
            PRIMARY KEY (code, trade_date, data_source)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY,
            code VARCHAR,
            data_source VARCHAR,
            start_date DATE,
            end_date DATE,
            row_count INTEGER,
            status VARCHAR,
            error_msg VARCHAR,
            fetched_at TIMESTAMP DEFAULT current_timestamp
        )
    """)


def upsert_stock_info(conn: duckdb.DuckDBPyConnection, records: list[dict]) -> int:
    """批量写入/更新股票基本信息，返回写入行数"""
    if not records:
        return 0
    import pandas as pd

    df = pd.DataFrame(records)
    df["updated_at"] = pd.Timestamp.now()
    # 确保列顺序，缺失列补None
    for col in ["delist_date"]:
        if col not in df.columns:
            df[col] = None
    df = df[["code", "name", "market", "board", "list_date", "delist_date", "updated_at"]]
    # 先删除已存在的，再插入
    codes = df["code"].tolist()
    if codes:
        placeholders = ",".join(["?" for _ in codes])
        conn.execute(f"DELETE FROM stock_info WHERE code IN ({placeholders})", codes)
    conn.execute("INSERT INTO stock_info SELECT * FROM df")
    return len(df)


def insert_daily_kline(conn: duckdb.DuckDBPyConnection, records: list[dict]) -> int:
    """批量写入日K数据（忽略重复），返回真实新增行数"""
    if not records:
        return 0
    import pandas as pd

    df = pd.DataFrame(records)
    # 确保列顺序匹配表结构
    cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover", "data_source"]
    df = df[[c for c in cols if c in df.columns]]
    # 使用INSERT OR IGNORE (DuckDB支持此语法)
    try:
        before = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO daily_kline SELECT * FROM df")
        after = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
        return after - before
    except Exception:
        # 回退：逐行插入，跳过重复
        inserted = 0
        for _, row in df.iterrows():
            try:
                conn.execute("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", list(row))
                inserted += 1
            except Exception:
                pass
        return inserted


def log_fetch(
    conn: duckdb.DuckDBPyConnection,
    code: str,
    data_source: str,
    start_date,
    end_date,
    row_count: int,
    status: str,
    error_msg: str = None,
) -> None:
    """记录采集日志"""
    # 使用自增ID：取当前最大ID+1
    max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM fetch_log").fetchone()[0]
    new_id = max_id + 1
    conn.execute(
        """
        INSERT INTO fetch_log (id, code, data_source, start_date, end_date,
                               row_count, status, error_msg)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """,
        [new_id, code, data_source, start_date, end_date, row_count, status, error_msg],
    )


def get_last_fetch_date(conn: duckdb.DuckDBPyConnection, code: str, data_source: str) -> str | None:
    """获取某股票某数据源最后采集日期，用于断点续传"""
    result = conn.execute(
        """
        SELECT MAX(end_date) FROM fetch_log
        WHERE code = ? AND data_source = ? AND status = 'success'
    """,
        [code, data_source],
    ).fetchone()
    return str(result[0]) if result and result[0] else None


def get_latest_kline_date(conn: duckdb.DuckDBPyConnection, code: str, data_source: str = None) -> str | None:
    """获取某股票已有K线的真实最新交易日，用于增量更新断点。"""
    sql = "SELECT MAX(trade_date) FROM daily_kline WHERE code = ?"
    params = [code]
    if data_source:
        sql += " AND data_source = ?"
        params.append(data_source)

    result = conn.execute(sql, params).fetchone()
    return str(result[0]) if result and result[0] else None


def get_stock_list(conn: duckdb.DuckDBPyConnection, active_only: bool = True) -> list[str]:
    """获取股票代码列表，默认只返回未退市股票。"""
    sql = "SELECT code FROM stock_info"
    if active_only:
        sql += " WHERE delist_date IS NULL OR delist_date >= current_date"
    sql += " ORDER BY code"
    results = conn.execute(sql).fetchall()
    return [r[0] for r in results]


def init_memory_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """初始化记忆系统表结构"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS long_term_memories (
            id VARCHAR PRIMARY KEY,
            memory_type VARCHAR NOT NULL,
            content VARCHAR NOT NULL,
            keywords_json VARCHAR NOT NULL,
            tags_json VARCHAR NOT NULL,
            importance FLOAT DEFAULT 0.5,
            access_count INTEGER DEFAULT 0,
            last_accessed_at TIMESTAMP,
            decay_halflife_days INTEGER DEFAULT 30,
            source_session_id VARCHAR,
            source_tool VARCHAR,
            compressed_from VARCHAR,
            is_archived BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT current_timestamp,
            updated_at TIMESTAMP DEFAULT current_timestamp
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_memories (
            id VARCHAR PRIMARY KEY,
            session_id VARCHAR NOT NULL,
            memory_type VARCHAR NOT NULL,
            content VARCHAR NOT NULL,
            metadata_json VARCHAR,
            created_at TIMESTAMP DEFAULT current_timestamp,
            expires_at TIMESTAMP NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS memory_sessions (
            session_id VARCHAR PRIMARY KEY,
            started_at TIMESTAMP DEFAULT current_timestamp,
            ended_at TIMESTAMP,
            summary VARCHAR,
            topics_json VARCHAR,
            stocks_json VARCHAR,
            message_count INTEGER DEFAULT 0
        )
    """)

    # 新增：learning 结构化字段（幂等操作，重复执行无影响）
    try:
        conn.execute("ALTER TABLE long_term_memories ADD COLUMN IF NOT EXISTS learned_what VARCHAR DEFAULT ''")
        conn.execute("ALTER TABLE long_term_memories ADD COLUMN IF NOT EXISTS learned_why VARCHAR DEFAULT ''")
        conn.execute("ALTER TABLE long_term_memories ADD COLUMN IF NOT EXISTS apply_when VARCHAR DEFAULT ''")
    except Exception:
        pass  # 列已存在时忽略

    # 新增：embedding 向量列
    try:
        conn.execute("ALTER TABLE long_term_memories ADD COLUMN IF NOT EXISTS embedding BLOB")
    except Exception:
        pass

    # ── 预测跟踪表 ──────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id VARCHAR PRIMARY KEY,
            stock_code VARCHAR NOT NULL,
            stock_name VARCHAR,
            prediction_type VARCHAR,
            direction VARCHAR,
            target_price DOUBLE,
            stop_loss DOUBLE,
            timeframe_days INTEGER,
            reasoning TEXT,
            confidence FLOAT,
            memory_ids_json VARCHAR,
            created_at TIMESTAMP,
            check_after_date DATE,
            actual_direction VARCHAR,
            actual_price DOUBLE,
            actual_return_pct FLOAT,
            outcome VARCHAR DEFAULT 'pending',
            outcome_notes TEXT,
            checked_at TIMESTAMP,
            learning_extracted BOOLEAN DEFAULT FALSE
        )
    """)

    # ── 知识图谱表 ──────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_entities (
            id VARCHAR PRIMARY KEY,
            entity_type VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            attributes_json TEXT,
            created_at TIMESTAMP DEFAULT current_timestamp,
            updated_at TIMESTAMP DEFAULT current_timestamp
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_relations (
            id VARCHAR PRIMARY KEY,
            source_id VARCHAR NOT NULL,
            target_id VARCHAR NOT NULL,
            relation_type VARCHAR NOT NULL,
            weight FLOAT DEFAULT 1.0,
            evidence_memory_ids VARCHAR,
            created_at TIMESTAMP DEFAULT current_timestamp
        )
    """)

    # ── 用户画像表 ──────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profile (
            key VARCHAR PRIMARY KEY,
            value TEXT,
            confidence FLOAT DEFAULT 0.5,
            source_memory_ids VARCHAR,
            updated_at TIMESTAMP DEFAULT current_timestamp
        )
    """)
