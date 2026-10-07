"""Agent友好的查询接口 - 封装常用查询操作

股票数据可选化（B 项）：本地缺数（表缺失或结果为空）时可在线兜底取数
（config.DATA_ONLINE_FALLBACK / StockQuery(online_fallback=...)）。
兜底结果经 df.attrs["data_source"] 标注来源（result_source() 读取；注意
DataFrame.transform 等操作会丢 attrs，需在 transform 前读取）。

缺数语义：查询结果为空才兜底，不做本地/在线合并补洞（本地部分数据只返回
本地；补尾部请用 /fetch update）。采集链路不走本类（collector/database 纯 SQL），
兜底不会污染采集断点续传逻辑。
"""

from datetime import date, timedelta

import duckdb
import pandas as pd

from deeppulse import config
from deeppulse.src.database import get_connection

SOURCE_LOCAL = "local"

# get_latest_price 在线兜底时回看的自然日窗口
_LATEST_PRICE_LOOKBACK_DAYS = 15
# get_multi_stock_kline 在线兜底的代码数上限（超出截断并在 attrs 注明）
MULTI_KLINE_ONLINE_MAX_CODES = 20


def result_source(df: pd.DataFrame) -> str:
    """读取结果来源标注（local / online-* / mixed），缺省 'local'。须在 transform 前调用。"""
    if df is None:
        return SOURCE_LOCAL
    return str(df.attrs.get("data_source") or SOURCE_LOCAL)


def _source_priority_expr(col: str = "data_source") -> str:
    """多源同日期去重的排序表达式：按 config.DATA_SOURCES 优先级，未知名排最后"""
    order = list(getattr(config, "DATA_SOURCES", []) or [])
    case = " ".join(f"WHEN '{name}' THEN {i}" for i, name in enumerate(order))
    return f"CASE COALESCE({col}, '') {case} ELSE {len(order)} END"


def _dedup_by_trade_date_sql(extra_partition: str = "") -> str:
    """同一天多源各有一行时按优先级保留一行的 QUALIFY 子句（数据源互为备份，读者不看副本）"""
    partition = ", ".join(p for p in (extra_partition, "trade_date") if p)
    return f"QUALIFY ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY {_source_priority_expr()}) = 1"


class StockQuery:
    """A股日K数据库查询接口，供Agent使用"""

    def __init__(self, db_path=None, online_fallback: bool | None = None):
        self.db_path = db_path or config.DB_PATH
        # None → 按配置解析；False → 强制纯本地（诊断/采集入口用）
        self.online_fallback = config.DATA_ONLINE_FALLBACK if online_fallback is None else bool(online_fallback)

    def _conn(self):
        return get_connection(self.db_path)

    # ── 在线兜底 ──────────────────────────────────────

    def _fallback_enabled(self) -> bool:
        return self.online_fallback

    def _fetch_df(self, sql: str, params: list) -> pd.DataFrame:
        """执行 SQL；行情表缺失（CatalogException）视同空结果（空库开箱即用）。"""
        conn = self._conn()
        try:
            return conn.execute(sql, params).fetchdf()
        except duckdb.CatalogException:
            return pd.DataFrame()
        finally:
            conn.close()

    def _fallback_daily_kline(self, code: str, start_date: str = None, end_date: str = None, limit: int = None):
        """在线拉日线 → (df, source_label)。limit 无日期时按 2 倍交易日窗口近似。"""
        from deeppulse.src.online import fetch_daily_kline_online, maybe_cache_kline

        end = date.fromisoformat(end_date) if end_date else date.today()
        if start_date:
            start = date.fromisoformat(start_date)
        elif limit:
            start = end - timedelta(days=int(limit) * 2 + 30)
        else:
            start = end - timedelta(days=365)

        df, source = fetch_daily_kline_online(code, start, end)
        if limit and len(df) > int(limit):
            df = df.tail(int(limit)).reset_index(drop=True)
        maybe_cache_kline(df, db_path=self.db_path)
        return df, source

    # ── 查询方法 ──────────────────────────────────────

    def _stock_list_unavailable(self) -> bool:
        """stock_info 表缺失或为空（区别于「过滤无匹配」——列表有数据时不兜底）"""
        from deeppulse.src.online import local_stock_list_available

        return not local_stock_list_available(self.db_path)

    def get_stock_info(self, code: str = None) -> pd.DataFrame:
        """获取股票信息。不传code返回全部。本地无股票列表时在线拉取兜底。"""
        if code:
            df = self._fetch_df("SELECT * FROM stock_info WHERE code = ?", [code])
        else:
            df = self._fetch_df("SELECT * FROM stock_info ORDER BY code", [])

        if self._fallback_enabled() and df.empty and self._stock_list_unavailable():
            from deeppulse.src.online import fetch_stock_list_online, maybe_cache_stock_list

            records = fetch_stock_list_online()
            maybe_cache_stock_list(records, db_path=self.db_path)
            df = pd.DataFrame(records)
            if code:
                df = df[df["code"].astype(str) == str(code)].reset_index(drop=True)
            else:
                df = df.sort_values("code").reset_index(drop=True) if not df.empty else df
            df.attrs["data_source"] = "online-stock-list"
        else:
            df.attrs["data_source"] = SOURCE_LOCAL
        return df

    def get_daily_kline(
        self, code: str, start_date: str = None, end_date: str = None, source: str = None, limit: int = None
    ) -> pd.DataFrame:
        """获取单只股票日K线数据。本地缺数时在线兜底（见模块 docstring）。

        Args:
            code: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD)
            source: 指定数据源
            limit: 返回行数限制
        """
        sql = "SELECT * FROM daily_kline WHERE code = ?"
        params = [code]

        if start_date:
            sql += " AND trade_date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND trade_date <= ?"
            params.append(end_date)
        if source:
            sql += " AND data_source = ?"
            params.append(source)
        else:
            # 多源同日期去重（源间互为备份）；指定 source 时保留该源原始行
            sql += " " + _dedup_by_trade_date_sql()

        sql += " ORDER BY trade_date"
        if limit:
            sql = f"""
                SELECT * FROM (
                    {sql.replace("ORDER BY trade_date", "ORDER BY trade_date DESC")}
                    LIMIT ?
                ) ORDER BY trade_date
            """
            params.append(int(limit))

        df = self._fetch_df(sql, params)
        if df.empty and self._fallback_enabled():
            df, label = self._fallback_daily_kline(code, start_date, end_date, limit)
            df.attrs["data_source"] = label
        else:
            df.attrs["data_source"] = SOURCE_LOCAL
        return df

    def _fallback_index_kline(self, code: str, start_date: str = None, end_date: str = None, limit: int = None):
        """在线拉指数日线 → (df, source_label)。limit 无日期时按 2 倍交易日窗口近似。"""
        from deeppulse.src.online import fetch_index_kline_online, maybe_cache_index_kline

        end = date.fromisoformat(end_date) if end_date else date.today()
        if start_date:
            start = date.fromisoformat(start_date)
        elif limit:
            start = end - timedelta(days=int(limit) * 2 + 30)
        else:
            start = end - timedelta(days=365)

        df, source = fetch_index_kline_online(code, start, end)
        if limit and len(df) > int(limit):
            df = df.tail(int(limit)).reset_index(drop=True)
        maybe_cache_index_kline(df, db_path=self.db_path)
        return df, source

    def get_index_kline(
        self, code: str, start_date: str = None, end_date: str = None, source: str = None, limit: int = None
    ) -> pd.DataFrame:
        """获取指数日K线数据（code 带前缀全码，如 sh000001）。本地缺数时在线兜底。

        指数无独立实时接口，语义与 get_daily_kline 一致：多源同日期按优先级
        去重（指定 source 保留原始行），在线结果按 cache_to_db 决定是否写回。
        """
        sql = "SELECT * FROM index_daily_kline WHERE code = ?"
        params = [code]

        if start_date:
            sql += " AND trade_date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND trade_date <= ?"
            params.append(end_date)
        if source:
            sql += " AND data_source = ?"
            params.append(source)
        else:
            sql += " " + _dedup_by_trade_date_sql()

        sql += " ORDER BY trade_date"
        if limit:
            sql = f"""
                SELECT * FROM (
                    {sql.replace("ORDER BY trade_date", "ORDER BY trade_date DESC")}
                    LIMIT ?
                ) ORDER BY trade_date
            """
            params.append(int(limit))

        df = self._fetch_df(sql, params)
        if df.empty and self._fallback_enabled():
            df, label = self._fallback_index_kline(code, start_date, end_date, limit)
            df.attrs["data_source"] = label
        else:
            df.attrs["data_source"] = SOURCE_LOCAL
        return df

    def get_multi_stock_kline(self, codes: list[str], start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """获取多只股票日K数据，用于横向对比。本地缺数时逐只在线兜底（有代码数上限）。"""
        if not codes:
            return pd.DataFrame()
        placeholders = ",".join(["?" for _ in codes])
        sql = f"SELECT * FROM daily_kline WHERE code IN ({placeholders})"
        params = list(codes)

        if start_date:
            sql += " AND trade_date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND trade_date <= ?"
            params.append(end_date)

        # 多源同日期去重（源间互为备份）
        sql += " " + _dedup_by_trade_date_sql(extra_partition="code")
        sql += " ORDER BY code, trade_date"
        df = self._fetch_df(sql, params)
        if df.empty and self._fallback_enabled():
            from deeppulse.src.online import OnlineFetchError

            truncated = len(codes) > MULTI_KLINE_ONLINE_MAX_CODES
            work_codes = codes[:MULTI_KLINE_ONLINE_MAX_CODES]
            frames, sources = [], set()
            for c in work_codes:
                try:
                    f, label = self._fallback_daily_kline(c, start_date, end_date)
                    frames.append(f)
                    sources.add(label)
                except OnlineFetchError:
                    continue
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            label = sources.pop() if len(sources) == 1 else ("mixed" if sources else "online")
            df.attrs["data_source"] = label
            if truncated:
                df.attrs["note"] = f"在线兜底仅取前 {MULTI_KLINE_ONLINE_MAX_CODES} 只代码"
        else:
            df.attrs["data_source"] = SOURCE_LOCAL
        return df

    def get_latest_price(self, codes: list[str] = None) -> pd.DataFrame:
        """获取最新价格。不传codes返回全部股票最新价。本地缺数时在线兜底（逐只近 N 日取末行）。"""
        preferred = config.DATA_SOURCES[0] if getattr(config, "DATA_SOURCES", None) else "baostock"
        sql = """
            SELECT code, name, trade_date, open, high, low,
                   close, volume, amount, turnover, data_source
            FROM (
                SELECT k.code, s.name, k.trade_date, k.open, k.high, k.low,
                       k.close, k.volume, k.amount, k.turnover, k.data_source,
                       ROW_NUMBER() OVER (
                           PARTITION BY k.code
                           ORDER BY k.trade_date DESC,
                                    CASE WHEN k.data_source = ? THEN 0 ELSE 1 END
                       ) AS rn
                FROM daily_kline k
                JOIN stock_info s ON k.code = s.code
            ) t
            WHERE rn = 1
        """
        params = [preferred]
        if codes:
            placeholders = ",".join(["?" for _ in codes])
            sql += f" AND code IN ({placeholders})"
            params.extend(codes)

        sql += " ORDER BY code"
        df = self._fetch_df(sql, params)
        if df.empty and self._fallback_enabled():
            from deeppulse.src.online import OnlineFetchError

            end = date.today()
            start = end - timedelta(days=_LATEST_PRICE_LOOKBACK_DAYS)
            rows, sources = [], set()
            targets = codes or []
            if not targets:
                # 全市场最新价在线拉取不现实（限频），如实注明
                df = pd.DataFrame()
                df.attrs["data_source"] = SOURCE_LOCAL
                df.attrs["note"] = "在线兜底需指定 codes；全市场最新价请采集本地数据（/fetch all）"
                return df
            for c in targets:
                try:
                    f, label = self._fallback_daily_kline(c, str(start), str(end))
                except OnlineFetchError:
                    continue
                if f.empty:
                    continue
                sources.add(label)
                last = f.iloc[-1]
                rows.append(
                    {
                        "code": str(c).zfill(6),
                        "name": None,
                        "trade_date": last.get("trade_date"),
                        "open": last.get("open"),
                        "high": last.get("high"),
                        "low": last.get("low"),
                        "close": last.get("close"),
                        "volume": last.get("volume"),
                        "amount": last.get("amount"),
                        "turnover": last.get("turnover"),
                        "data_source": label,
                    }
                )
            df = pd.DataFrame(rows)
            df.attrs["data_source"] = sources.pop() if len(sources) == 1 else ("mixed" if sources else "online")
        else:
            df.attrs["data_source"] = SOURCE_LOCAL
        return df

    def search_stocks(self, keyword: str) -> pd.DataFrame:
        """按名称或代码搜索股票。本地无股票列表时在线兜底。"""
        df = self._fetch_df(
            "SELECT * FROM stock_info WHERE code LIKE ? OR name LIKE ? ORDER BY code",
            [f"%{keyword}%", f"%{keyword}%"],
        )
        if self._fallback_enabled() and df.empty and self._stock_list_unavailable():
            from deeppulse.src.online import fetch_stock_list_online, maybe_cache_stock_list

            records = fetch_stock_list_online()
            maybe_cache_stock_list(records, db_path=self.db_path)
            all_df = pd.DataFrame(records)
            if all_df.empty:
                return all_df
            mask = all_df["code"].astype(str).str.contains(keyword, na=False) | all_df["name"].astype(str).str.contains(
                keyword, na=False
            )
            df = all_df[mask].reset_index(drop=True)
            df.attrs["data_source"] = "online-stock-list"
        else:
            df.attrs["data_source"] = SOURCE_LOCAL
        return df

    def get_data_stats(self) -> dict:
        """获取数据库统计信息（纯本地库自省，不触发在线兜底；表缺返回空统计）"""
        stats = {"stock_count": 0, "kline_count": 0, "date_range": (None, None), "sources": []}
        try:
            conn = self._conn()
            try:
                stats["stock_count"] = conn.execute("SELECT COUNT(*) FROM stock_info").fetchone()[0]
                stats["kline_count"] = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
                stats["date_range"] = conn.execute(
                    "SELECT MIN(trade_date), MAX(trade_date) FROM daily_kline"
                ).fetchone()
                stats["sources"] = (
                    conn.execute("SELECT data_source, COUNT(*) FROM daily_kline GROUP BY data_source")
                    .fetchdf()
                    .to_dict("records")
                )
            finally:
                conn.close()
        except duckdb.CatalogException:
            pass
        return stats

    def get_fetch_log(self, code: str = None, status: str = None, limit: int = 100) -> pd.DataFrame:
        """查询采集日志（纯本地；表缺返回空）"""
        limit = int(limit)  # 强制整数，非法输入直接抛错（防 SQL 注入）
        if limit < 0:
            raise ValueError(f"limit must be non-negative, got {limit}")
        sql = "SELECT * FROM fetch_log WHERE 1=1"
        params = []
        if code:
            sql += " AND code = ?"
            params.append(code)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY fetched_at DESC LIMIT ?"
        params.append(limit)
        return self._fetch_df(sql, params)
