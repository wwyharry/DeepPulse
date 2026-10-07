"""BaoStock 数据源实现"""

import logging
import threading
from datetime import date

import baostock as bs
import pandas as pd

from deeppulse import config
from deeppulse.src import codes
from deeppulse.src.resilience import RetryPolicy

from .base import DataSourceBase

logger = logging.getLogger(__name__)


class BaoStockSource(DataSourceBase):
    # BaoStock session 约 50 秒超时，主动重连间隔设为 40 秒
    SESSION_MAX_AGE = 40
    # fetch 路径有界获取会话锁：拿不到即视为会话忙，快速失败跳过该股
    IO_LOCK_TIMEOUT = 0.1

    def __init__(self):
        self._logged_in = False
        self._login_time = 0.0
        # 会话互斥：杜绝主线程 cleanup/logout 与残留采集线程并发使用全局 socket
        self._io_lock = threading.RLock()
        self._retry_policy = RetryPolicy(
            max_retries=config.RETRY_MAX_RETRIES,
            base_delay=config.RETRY_BASE_DELAY,
            max_delay=config.RETRY_MAX_DELAY,
            backoff_factor=config.RETRY_BACKOFF_FACTOR,
            retryable_errors=(RuntimeError, ConnectionError, ConnectionResetError, OSError),
        )

    def _ensure_login(self):
        """带超时预防的登录 — session 老化时主动重连，避免等到报错"""
        import time

        if self._logged_in:
            # 主动重连：session 超过最大存活时间，不等报错就刷新
            if time.time() - self._login_time > self.SESSION_MAX_AGE:
                logger.debug("[BaoStock] session 老化，主动重连")
                self._force_logout()
            else:
                return  # session 还年轻，直接用

        login_result = bs.login()
        if login_result.error_code != "0":
            raise RuntimeError(f"BaoStock登录失败: {login_result.error_code} {login_result.error_msg}")
        self._logged_in = True
        self._login_time = time.time()
        self._set_socket_timeout()

    def _set_socket_timeout(self):
        """给全局会话 socket 设超时，使阻塞 recv 有界退出，残留线程能释放会话锁"""
        try:
            import baostock.common.context as bs_context

            sock = getattr(bs_context, "default_socket", None)
            if sock is not None:
                sock.settimeout(float(config.FETCH_TIMEOUT))
        except Exception:
            pass

    def _logout(self):
        """安全登出"""
        if self._logged_in:
            try:
                bs.logout()
            except Exception:
                pass
            self._logged_in = False
            self._login_time = 0.0

    def _force_logout(self):
        """强制登出（忽略所有错误）"""
        try:
            bs.logout()
        except Exception:
            pass
        self._logged_in = False
        self._login_time = 0.0

    def cleanup(self):
        """资源清理 — 会话忙（在途/残留线程）时跳过 logout，避免竞态或阻塞"""
        self.try_cleanup()

    def try_cleanup(self, timeout: float = 0.2) -> bool:
        """有界清理：拿得到会话锁才 logout；拿不到说明残留线程仍在会话上，保留会话

        Returns:
            True 表示已 logout；False 表示会话忙被跳过
        """
        if not self._io_lock.acquire(timeout=timeout):
            return False
        try:
            self._logout()
            return True
        finally:
            self._io_lock.release()

    def health_check(self) -> bool:
        """连接健康检查"""
        if not self._io_lock.acquire(timeout=self.IO_LOCK_TIMEOUT):
            return False
        try:
            if not self._logged_in:
                return False
            try:
                rs = bs.query_trade_dates(start_date="2024-01-01", end_date="2024-01-02")
                return rs.error_code == "0"
            except Exception:
                return False
        finally:
            self._io_lock.release()

    @property
    def name(self) -> str:
        return "baostock"

    @staticmethod
    def _to_bs_code(code: str) -> str | None:
        """将6位代码转为BaoStock格式 (sh.600000 / sz.000001)；北交所不支持返回 None"""
        return codes.to_bs_code(code)

    def fetch_stock_list(self) -> list[dict]:
        """通过BaoStock获取沪深A股列表（带重试；无北交所）"""
        if not self._io_lock.acquire(timeout=self.IO_LOCK_TIMEOUT):
            raise ConnectionError("BaoStock 会话忙（残留线程占用），跳过本次股票列表查询")
        try:
            # 重试策略整体在锁内执行，避免重试窗口越锁
            return self._retry_policy.execute(self._fetch_stock_list_impl)
        finally:
            self._io_lock.release()

    def _fetch_stock_list_impl(self) -> list[dict]:
        self._ensure_login()
        rs = bs.query_stock_basic()
        if rs.error_code != "0":
            raise RuntimeError(f"BaoStock股票列表查询失败: {rs.error_code} {rs.error_msg}")
        results = []
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            # row: code, code_name, ipoDate, outDate, type, status
            bs_code = row[0]  # sh.600000 格式
            name = row[1]
            ipo_date = row[2]
            out_date = row[3]
            stock_type = row[4]  # 1=股票
            status = row[5]  # 1=上市

            if stock_type != "1" or status != "1":
                continue

            code = bs_code.split(".")[1]

            # 全板块分类（BaoStock 只有沪深代码，创业板/科创板在此归类）
            info = codes.classify_board(code)
            if info is None:
                continue
            market, board = info

            results.append(
                {
                    "code": code,
                    "name": name,
                    "market": market,
                    "board": board,
                    "list_date": ipo_date if ipo_date else None,
                    "delist_date": out_date if out_date else None,
                }
            )
        return results

    def fetch_daily_kline(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """通过BaoStock获取日K线数据（带重试）"""
        if not self._io_lock.acquire(timeout=self.IO_LOCK_TIMEOUT):
            raise ConnectionError(f"BaoStock 会话忙（残留线程占用），跳过 {code}")
        try:
            # 重试策略整体在锁内执行，避免重试窗口越锁
            return self._retry_policy.execute(self._fetch_kline_impl, code, start_date, end_date)
        finally:
            self._io_lock.release()

    def _fetch_kline_impl(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        bs_code = self._to_bs_code(code)
        if bs_code is None:
            # BaoStock 不支持北交所：直接返回空，让故障切换落到 akshare/eastmoney
            return pd.DataFrame()
        self._ensure_login()
        fields = "date,open,high,low,close,volume,amount,turn"

        rs = bs.query_history_k_data_plus(
            bs_code,
            fields,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag="2",  # 前复权
        )
        if rs.error_code != "0":
            raise RuntimeError(f"BaoStock查询失败 {bs_code}: {rs.error_code} {rs.error_msg}")

        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=fields.split(","))

        # 统一列名和类型
        df = df.rename(columns={"date": "trade_date", "turn": "turnover"})
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["code"] = str(code).zfill(6)
        df["data_source"] = self.name

        # 数值类型转换
        for col in ["open", "high", "low", "close", "amount", "turnover"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover", "data_source"]
        return df[[c for c in cols if c in df.columns]]

    def fetch_index_kline(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        """通过BaoStock获取指数日K线（带重试）

        code 为带前缀全码（sh000001 等）。BaoStock 只支持 sh./sz. 指数，
        北交所（bj 前缀）直接返回空 DataFrame（不发网络），故障切换落到 akshare/eastmoney。
        """
        c = str(code).strip()
        if len(c) != 8 or c[:2] not in ("sh", "sz") or not c[2:].isdigit():
            return pd.DataFrame()
        if not self._io_lock.acquire(timeout=self.IO_LOCK_TIMEOUT):
            raise ConnectionError(f"BaoStock 会话忙（残留线程占用），跳过 {code}")
        try:
            return self._retry_policy.execute(self._fetch_index_kline_impl, c, start_date, end_date)
        finally:
            self._io_lock.release()

    def _fetch_index_kline_impl(self, code: str, start_date: date, end_date: date) -> pd.DataFrame:
        self._ensure_login()
        bs_code = f"{code[:2]}.{code[2:]}"
        fields = "date,open,high,low,close,volume,amount"  # 指数无换手率字段

        rs = bs.query_history_k_data_plus(
            bs_code,
            fields,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            frequency="d",
        )
        if rs.error_code != "0":
            raise RuntimeError(f"BaoStock指数查询失败 {bs_code}: {rs.error_code} {rs.error_msg}")

        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=fields.split(","))
        df = df.rename(columns={"date": "trade_date"})
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        df["code"] = code  # 带前缀全码入库
        df["data_source"] = self.name
        df["turnover"] = float("nan")

        for col in ["open", "high", "low", "close", "amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").astype("Int64")

        cols = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount", "turnover", "data_source"]
        return df[[c for c in cols if c in df.columns]]
