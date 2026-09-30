"""信号监控器 - 基于已保存的个股AI策略实时扫描信号"""

import threading
import time
from collections.abc import Callable
from datetime import datetime

from deeppulse.strategy.models import Signal, SignalStatus
from deeppulse.strategy.repository import get_repository
from deeppulse.strategy.signals import get_signal_generator


class SignalMonitor:
    """信号监控器 - 后台运行，扫描已保存的个股AI策略信号"""

    def __init__(self):
        self.repository = get_repository()
        self.signal_generator = get_signal_generator()
        self._running = False
        self._thread: threading.Thread | None = None
        self._callbacks: list[Callable] = []
        self._scan_interval = 300  # 5分钟扫描一次
        self._last_scan_time: datetime | None = None
        self._scan_count = 0
        self._signal_count = 0

    def start(self, interval: int = 300):
        """启动监控"""
        if self._running:
            return

        self._running = True
        self._scan_interval = interval
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止监控"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def add_callback(self, callback: Callable):
        """添加信号回调"""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable):
        """移除信号回调"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _monitor_loop(self):
        """监控主循环"""
        while self._running:
            try:
                self._scan_once()
                time.sleep(self._scan_interval)
            except Exception as e:
                print(f"监控循环异常: {e}")
                time.sleep(60)

    def _scan_once(self):
        """执行一次扫描 - 扫描所有已保存的个股AI策略"""
        try:
            # 扫描所有已保存的个股策略
            signals = self.signal_generator.scan_all_stock_strategies()

            # 更新统计
            self._last_scan_time = datetime.now()
            self._scan_count += 1
            self._signal_count += len(signals)

            # 触发回调
            for signal in signals:
                self._notify_callbacks(signal)

        except Exception as e:
            print(f"扫描失败: {e}")

    def _get_stock_list(self) -> list[str]:
        """获取监控股票列表"""
        try:
            from deeppulse.agent.watchlist import get_watchlist

            watchlist = get_watchlist()
            return [item["code"] for item in watchlist[:50]]
        except Exception:
            return []

    def _notify_callbacks(self, signal: Signal):
        """通知所有回调"""
        for callback in self._callbacks:
            try:
                callback(signal)
            except Exception as e:
                print(f"回调执行失败: {e}")

    def get_status(self) -> dict:
        """获取监控状态"""
        return {
            "running": self._running,
            "scan_interval": self._scan_interval,
            "last_scan_time": self._last_scan_time.isoformat() if self._last_scan_time else None,
            "scan_count": self._scan_count,
            "signal_count": self._signal_count,
            "callback_count": len(self._callbacks),
        }

    def scan_now(self) -> list[Signal]:
        """立即执行一次扫描"""
        signals = []
        try:
            signals = self.signal_generator.scan_all_stock_strategies()
            self._last_scan_time = datetime.now()
            self._scan_count += 1
            self._signal_count += len(signals)
        except Exception as e:
            print(f"立即扫描失败: {e}")
        return signals

    def confirm_signal(self, signal_id: str) -> bool:
        """确认信号"""
        try:
            import duckdb

            from deeppulse import config

            if not config.DB_PATH.exists():
                return False

            conn = duckdb.connect(str(config.DB_PATH))
            try:
                conn.execute(
                    "UPDATE strategy_signals SET status = ? WHERE id = ?",
                    [SignalStatus.CONFIRMED, signal_id],
                )
                return True
            finally:
                conn.close()
        except Exception:
            return False

    def expire_signal(self, signal_id: str) -> bool:
        """过期信号"""
        try:
            import duckdb

            from deeppulse import config

            if not config.DB_PATH.exists():
                return False

            conn = duckdb.connect(str(config.DB_PATH))
            try:
                conn.execute(
                    "UPDATE strategy_signals SET status = ? WHERE id = ?",
                    [SignalStatus.EXPIRED, signal_id],
                )
                return True
            finally:
                conn.close()
        except Exception:
            return False

    def get_signal_statistics(self) -> dict:
        """获取信号统计"""
        try:
            import duckdb

            from deeppulse import config

            if not config.DB_PATH.exists():
                return {}

            conn = duckdb.connect(str(config.DB_PATH), read_only=True)
            try:
                # 总信号数
                total = conn.execute("SELECT COUNT(*) FROM strategy_signals").fetchone()[0]

                # 各状态信号数
                pending = conn.execute("SELECT COUNT(*) FROM strategy_signals WHERE status = 'pending'").fetchone()[0]

                confirmed = conn.execute("SELECT COUNT(*) FROM strategy_signals WHERE status = 'confirmed'").fetchone()[
                    0
                ]

                expired = conn.execute("SELECT COUNT(*) FROM strategy_signals WHERE status = 'expired'").fetchone()[0]

                # 各类型信号数
                buy_signals = conn.execute(
                    "SELECT COUNT(*) FROM strategy_signals WHERE signal_type = 'buy'"
                ).fetchone()[0]

                sell_signals = conn.execute(
                    "SELECT COUNT(*) FROM strategy_signals WHERE signal_type = 'sell'"
                ).fetchone()[0]

                # 平均置信度
                avg_confidence = conn.execute("SELECT AVG(confidence) FROM strategy_signals").fetchone()[0] or 0

                return {
                    "total": total,
                    "pending": pending,
                    "confirmed": confirmed,
                    "expired": expired,
                    "buy_signals": buy_signals,
                    "sell_signals": sell_signals,
                    "avg_confidence": round(float(avg_confidence), 2),
                }
            finally:
                conn.close()
        except Exception:
            return {}


# 全局实例
_monitor = None


def get_signal_monitor() -> SignalMonitor:
    """获取全局信号监控器实例"""
    global _monitor
    if _monitor is None:
        _monitor = SignalMonitor()
    return _monitor
