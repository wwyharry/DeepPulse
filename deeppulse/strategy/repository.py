"""策略仓库 - 策略的持久化存储和检索"""

import json
import re
import time
from pathlib import Path
from typing import Optional

from deeppulse.strategy.models import (
    BacktestResult,
    Signal,
    Strategy,
    StrategyVersion,
)


class StrategyRepository:
    """策略仓库 - 支持文件系统和DuckDB存储"""

    def __init__(self, strategies_dir: Path = None, db_path: Path = None):
        from deeppulse import config

        self.strategies_dir = strategies_dir or Path(__file__).parent / "strategies"
        self.db_path = db_path or config.DB_PATH
        self._cache_dir = Path(__file__).parent.parent / ".cache"
        self._cache_file = self._cache_dir / "strategy_cache.json"

        # 内存缓存
        self._strategies: dict[str, Strategy] = {}
        self._loaded = False

    def load_all(self) -> list[Strategy]:
        """加载所有策略"""
        if self._loaded:
            return list(self._strategies.values())

        self._strategies = {}

        # 从文件系统加载
        if self.strategies_dir.exists():
            for f in sorted(self.strategies_dir.glob("*.md")):
                if f.name == "README.md":
                    continue
                try:
                    content = f.read_text(encoding="utf-8").strip()
                    if content:
                        strategy = Strategy.from_markdown(f.name, content)
                        self._strategies[strategy.id] = strategy
                except Exception as e:
                    print(f"加载策略文件 {f.name} 失败: {e}")

        # 从DuckDB加载（如果有自定义策略）
        self._load_from_db()

        self._loaded = True
        return list(self._strategies.values())

    def _load_from_db(self):
        """从DuckDB加载自定义策略"""
        try:
            import duckduckdb

            if not self.db_path.exists():
                return

            conn = duckdb.connect(str(self.db_path), read_only=True)
            try:
                # 检查表是否存在
                tables = conn.execute("SHOW TABLES").fetchall()
                table_names = [t[0] for t in tables]

                if "strategies" in table_names:
                    rows = conn.execute(
                        "SELECT * FROM strategies WHERE status != 'archived'"
                    ).fetchall()
                    columns = [desc[0] for desc in conn.description]

                    for row in rows:
                        data = dict(zip(columns, row))
                        strategy = Strategy.from_dict(data)
                        if strategy.id not in self._strategies:
                            self._strategies[strategy.id] = strategy
            finally:
                conn.close()
        except Exception:
            pass  # DuckDB不可用时忽略

    def get_strategy(self, strategy_id: str) -> Optional[Strategy]:
        """获取策略"""
        if not self._loaded:
            self.load_all()
        return self._strategies.get(strategy_id)

    def get_strategy_by_filename(self, filename: str) -> Optional[Strategy]:
        """根据文件名获取策略"""
        if not self._loaded:
            self.load_all()
        for s in self._strategies.values():
            if s.filename == filename:
                return s
        return None

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """搜索策略"""
        if not self._loaded:
            self.load_all()

        if not self._strategies:
            return []

        # 提取查询词
        query_terms = set()

        # 技术指标
        for pattern in [
            r"MA\d+", r"MACD", r"RSI", r"KDJ", r"BOLL", r"布林",
            r"金叉", r"死叉", r"超买", r"超卖", r"放量", r"缩量",
            r"突破", r"回踩", r"支撑", r"压力", r"均线", r"背离",
            r"涨停", r"跌停",
        ]:
            if re.search(pattern, query, re.IGNORECASE):
                query_terms.add(pattern)

        # 数值条件
        numeric = re.findall(r"(?:MA|RSI|KDJ|MACD|BOLL|J|K|D)\s*[<>]=?\s*[\d.]+", query)
        query_terms.update(numeric)

        # 中文关键术语
        cn_terms = re.findall(r"[一-鿿]{2,4}", query)
        query_terms.update(cn_terms)

        if not query_terms:
            return [
                {
                    "id": s.id,
                    "name": s.name,
                    "filename": s.filename,
                    "score": 0.1,
                    "match_reason": "无明确匹配",
                    "preview": s.content[:200],
                }
                for s in list(self._strategies.values())[:top_k]
            ]

        # 评分
        scored = []
        for strategy in self._strategies.values():
            score = 0
            matched = []

            # 关键词匹配
            for kw in strategy.conditions.get("keywords", []):
                for qt in query_terms:
                    if qt in kw or kw in qt:
                        score += 1
                        matched.append(kw)
                        break

            # 标签匹配
            for tag in strategy.tags:
                for qt in query_terms:
                    if qt in tag:
                        score += 0.5
                        matched.append(tag)
                        break

            # 内容全文匹配
            for qt in query_terms:
                if len(qt) >= 2 and qt in strategy.content:
                    score += 0.2

            if score > 0:
                scored.append(
                    {
                        "id": strategy.id,
                        "name": strategy.name,
                        "filename": strategy.filename,
                        "score": round(score, 2),
                        "match_reason": "匹配: " + ", ".join(matched[:5]),
                        "preview": strategy.content[:300],
                    }
                )

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def save_strategy(self, strategy: Strategy) -> bool:
        """保存策略"""
        try:
            # 保存到文件系统
            filepath = self.strategies_dir / strategy.filename
            filepath.write_text(strategy.content, encoding="utf-8")

            # 更新内存缓存
            self._strategies[strategy.id] = strategy

            # 保存到DuckDB
            self._save_to_db(strategy)

            return True
        except Exception as e:
            print(f"保存策略失败: {e}")
            return False

    def _save_to_db(self, strategy: Strategy):
        """保存策略到DuckDB"""
        try:
            import duckdb

            if not self.db_path.exists():
                return

            conn = duckdb.connect(str(self.db_path))
            try:
                # 创建表（如果不存在）
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS strategies (
                        id VARCHAR PRIMARY KEY,
                        name VARCHAR,
                        category VARCHAR,
                        tags VARCHAR,
                        description TEXT,
                        content TEXT,
                        conditions VARCHAR,
                        parameters VARCHAR,
                        risk_level VARCHAR,
                        status VARCHAR,
                        version INTEGER,
                        filename VARCHAR,
                        created_at TIMESTAMP,
                        updated_at TIMESTAMP
                    )
                """)

                # 插入或更新
                conn.execute(
                    """
                    INSERT OR REPLACE INTO strategies
                    (id, name, category, tags, description, content, conditions, parameters,
                     risk_level, status, version, filename, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [
                        strategy.id,
                        strategy.name,
                        strategy.category,
                        json.dumps(strategy.tags, ensure_ascii=False),
                        strategy.description,
                        strategy.content,
                        json.dumps(strategy.conditions, ensure_ascii=False),
                        json.dumps(strategy.parameters, ensure_ascii=False),
                        strategy.risk_level,
                        strategy.status,
                        strategy.version,
                        strategy.filename,
                        strategy.created_at,
                        strategy.updated_at,
                    ],
                )
            finally:
                conn.close()
        except Exception:
            pass  # DuckDB不可用时忽略

    def delete_strategy(self, strategy_id: str) -> bool:
        """删除策略"""
        strategy = self.get_strategy(strategy_id)
        if not strategy:
            return False

        try:
            # 删除文件
            filepath = self.strategies_dir / strategy.filename
            if filepath.exists():
                filepath.unlink()

            # 从内存缓存删除
            self._strategies.pop(strategy_id, None)

            # 从DuckDB删除
            self._delete_from_db(strategy_id)

            return True
        except Exception as e:
            print(f"删除策略失败: {e}")
            return False

    def _delete_from_db(self, strategy_id: str):
        """从DuckDB删除策略"""
        try:
            import duckdb

            if not self.db_path.exists():
                return

            conn = duckdb.connect(str(self.db_path))
            try:
                conn.execute("DELETE FROM strategies WHERE id = ?", [strategy_id])
            finally:
                conn.close()
        except Exception:
            pass

    def create_strategy(self, name: str, content: str, category: str = "", **kwargs) -> Strategy:
        """创建新策略"""
        # 生成文件名
        existing_files = list(self.strategies_dir.glob("*.md"))
        max_num = 0
        for f in existing_files:
            match = re.match(r"(\d+)-", f.name)
            if match:
                max_num = max(max_num, int(match.group(1)))

        filename = f"{max_num + 1:02d}-{name}.md"

        strategy = Strategy(
            name=name,
            category=category,
            content=content,
            filename=filename,
            **kwargs,
        )

        self.save_strategy(strategy)
        return strategy

    def update_strategy(self, strategy_id: str, **kwargs) -> Optional[Strategy]:
        """更新策略"""
        strategy = self.get_strategy(strategy_id)
        if not strategy:
            return None

        # 更新字段
        for key, value in kwargs.items():
            if hasattr(strategy, key):
                setattr(strategy, key, value)

        strategy.updated_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        strategy.version += 1

        # 保存版本历史
        self._save_version(strategy)

        self.save_strategy(strategy)
        return strategy

    def _save_version(self, strategy: Strategy):
        """保存策略版本"""
        try:
            import duckdb

            if not self.db_path.exists():
                return

            conn = duckdb.connect(str(self.db_path))
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS strategy_versions (
                        id VARCHAR PRIMARY KEY,
                        strategy_id VARCHAR,
                        version INTEGER,
                        content TEXT,
                        conditions VARCHAR,
                        parameters VARCHAR,
                        created_at TIMESTAMP,
                        comment VARCHAR
                    )
                """)

                version = StrategyVersion(
                    strategy_id=strategy.id,
                    version=strategy.version,
                    content=strategy.content,
                    conditions=strategy.conditions,
                    parameters=strategy.parameters,
                )

                conn.execute(
                    """
                    INSERT INTO strategy_versions
                    (id, strategy_id, version, content, conditions, parameters, created_at, comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [
                        version.id,
                        version.strategy_id,
                        version.version,
                        version.content,
                        json.dumps(version.conditions, ensure_ascii=False),
                        json.dumps(version.parameters, ensure_ascii=False),
                        version.created_at,
                        version.comment,
                    ],
                )
            finally:
                conn.close()
        except Exception:
            pass

    def get_versions(self, strategy_id: str) -> list[StrategyVersion]:
        """获取策略版本历史"""
        try:
            import duckdb

            if not self.db_path.exists():
                return []

            conn = duckdb.connect(str(self.db_path), read_only=True)
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM strategy_versions
                    WHERE strategy_id = ?
                    ORDER BY version DESC
                """,
                    [strategy_id],
                ).fetchall()

                columns = [desc[0] for desc in conn.description]
                return [StrategyVersion(**dict(zip(columns, row))) for row in rows]
            finally:
                conn.close()
        except Exception:
            return []

    def save_backtest_result(self, result: BacktestResult) -> bool:
        """保存回测结果"""
        try:
            import duckdb

            if not self.db_path.exists():
                return False

            conn = duckdb.connect(str(self.db_path))
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS backtest_results (
                        id VARCHAR PRIMARY KEY,
                        strategy_id VARCHAR,
                        strategy_name VARCHAR,
                        stock_code VARCHAR,
                        stock_name VARCHAR,
                        start_date VARCHAR,
                        end_date VARCHAR,
                        initial_capital DOUBLE,
                        final_capital DOUBLE,
                        total_return DOUBLE,
                        annual_return DOUBLE,
                        max_drawdown DOUBLE,
                        win_rate DOUBLE,
                        sharpe_ratio DOUBLE,
                        total_trades INTEGER,
                        parameters VARCHAR,
                        created_at TIMESTAMP
                    )
                """)

                conn.execute(
                    """
                    INSERT INTO backtest_results
                    (id, strategy_id, strategy_name, stock_code, stock_name,
                     start_date, end_date, initial_capital, final_capital,
                     total_return, annual_return, max_drawdown, win_rate,
                     sharpe_ratio, total_trades, parameters, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [
                        result.id,
                        result.strategy_id,
                        result.strategy_name,
                        result.stock_code,
                        result.stock_name,
                        result.start_date,
                        result.end_date,
                        result.initial_capital,
                        result.final_capital,
                        result.total_return,
                        result.annual_return,
                        result.max_drawdown,
                        result.win_rate,
                        result.sharpe_ratio,
                        result.total_trades,
                        json.dumps(result.parameters, ensure_ascii=False),
                        result.created_at,
                    ],
                )
                return True
            finally:
                conn.close()
        except Exception:
            return False

    def save_signal(self, signal: Signal) -> bool:
        """保存信号"""
        try:
            import duckdb

            if not self.db_path.exists():
                return False

            conn = duckdb.connect(str(self.db_path))
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS strategy_signals (
                        id VARCHAR PRIMARY KEY,
                        strategy_id VARCHAR,
                        strategy_name VARCHAR,
                        stock_code VARCHAR,
                        stock_name VARCHAR,
                        signal_type VARCHAR,
                        signal_price DOUBLE,
                        signal_time TIMESTAMP,
                        confidence DOUBLE,
                        matched_conditions VARCHAR,
                        status VARCHAR,
                        notes VARCHAR,
                        created_at TIMESTAMP
                    )
                """)

                conn.execute(
                    """
                    INSERT INTO strategy_signals
                    (id, strategy_id, strategy_name, stock_code, stock_name,
                     signal_type, signal_price, signal_time, confidence,
                     matched_conditions, status, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    [
                        signal.id,
                        signal.strategy_id,
                        signal.strategy_name,
                        signal.stock_code,
                        signal.stock_name,
                        signal.signal_type,
                        signal.signal_price,
                        signal.signal_time,
                        signal.confidence,
                        json.dumps(signal.matched_conditions, ensure_ascii=False),
                        signal.status,
                        signal.notes,
                        signal.created_at,
                    ],
                )
                return True
            finally:
                conn.close()
        except Exception:
            return False

    def get_signals(self, strategy_id: str = None, limit: int = 100) -> list[Signal]:
        """获取信号列表"""
        try:
            import duckdb

            if not self.db_path.exists():
                return []

            conn = duckdb.connect(str(self.db_path), read_only=True)
            try:
                if strategy_id:
                    rows = conn.execute(
                        """
                        SELECT * FROM strategy_signals
                        WHERE strategy_id = ?
                        ORDER BY signal_time DESC
                        LIMIT ?
                    """,
                        [strategy_id, limit],
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT * FROM strategy_signals
                        ORDER BY signal_time DESC
                        LIMIT ?
                    """,
                        [limit],
                    ).fetchall()

                columns = [desc[0] for desc in conn.description]
                return [Signal(**dict(zip(columns, row))) for row in rows]
            finally:
                conn.close()
        except Exception:
            return []


# 全局实例
_repository = None


def get_repository() -> StrategyRepository:
    """获取全局策略仓库实例"""
    global _repository
    if _repository is None:
        _repository = StrategyRepository()
    return _repository


def reload_repository():
    """重新加载策略仓库"""
    global _repository
    _repository = StrategyRepository()
    return _repository
