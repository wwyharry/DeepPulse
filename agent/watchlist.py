"""自选股管理与盯盘告警系统 - 盘中监控、信号告警、桌面通知"""

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import config
from src.database import get_connection
from src.query import StockQuery

# ============ 自选股管理 ============


class WatchlistManager:
    """自选股列表管理"""

    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH
        self._init_table()

    def _init_table(self):
        conn = get_connection(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                code VARCHAR NOT NULL,
                name VARCHAR,
                group_name VARCHAR DEFAULT '默认',
                add_date DATE DEFAULT current_date,
                notes VARCHAR,
                target_price DOUBLE,
                stop_loss_price DOUBLE,
                alert_enabled BOOLEAN DEFAULT true,
                PRIMARY KEY (code, group_name)
            )
        """)
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS alert_rules_seq START 1
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_rules (
                id INTEGER DEFAULT nextval('alert_rules_seq'),
                code VARCHAR NOT NULL,
                rule_type VARCHAR NOT NULL,
                params VARCHAR,
                enabled BOOLEAN DEFAULT true,
                created_at TIMESTAMP DEFAULT current_timestamp,
                last_triggered TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS alert_history_seq START 1
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_history (
                id INTEGER DEFAULT nextval('alert_history_seq'),
                code VARCHAR,
                rule_type VARCHAR,
                message VARCHAR,
                triggered_at TIMESTAMP DEFAULT current_timestamp,
                price DOUBLE,
                acknowledged BOOLEAN DEFAULT false
            )
        """)
        conn.close()

    def add(
        self,
        code: str,
        name: str = "",
        group: str = "默认",
        target_price: float = None,
        stop_loss: float = None,
        notes: str = "",
    ) -> dict:
        """添加自选股"""
        if not name:
            info = StockQuery().get_stock_info(code)
            if not info.empty:
                name = info.iloc[0].get("name", "")

        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO watchlist
                (code, name, group_name, target_price, stop_loss_price, notes)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                [code, name, group, target_price, stop_loss, notes],
            )
            return {"status": "ok", "code": code, "name": name, "group": group}
        finally:
            conn.close()

    def remove(self, code: str, group: str = "默认") -> dict:
        """移除自选股"""
        conn = get_connection(self.db_path)
        conn.execute("DELETE FROM watchlist WHERE code = ? AND group_name = ?", [code, group])
        conn.close()
        return {"status": "ok", "code": code}

    def list(self, group: str = None) -> list:
        """列出自选股"""
        conn = get_connection(self.db_path)
        if group:
            df = conn.execute("SELECT * FROM watchlist WHERE group_name = ? ORDER BY code", [group]).fetchdf()
        else:
            df = conn.execute("SELECT * FROM watchlist ORDER BY group_name, code").fetchdf()
        conn.close()
        if df.empty:
            return []
        return df.to_dict("records")

    def list_groups(self) -> list:
        """列出所有分组"""
        conn = get_connection(self.db_path)
        result = conn.execute("SELECT group_name, COUNT(*) as count FROM watchlist GROUP BY group_name").fetchdf()
        conn.close()
        if result.empty:
            return []
        return result.to_dict("records")

    def get_codes(self, group: str = None) -> list:
        """获取自选股代码列表"""
        items = self.list(group)
        return [item["code"] for item in items]

    def add_alert_rule(self, code: str, rule_type: str, params: dict) -> dict:
        """添加告警规则

        rule_type:
            price_above: 价格突破 {threshold}
            price_below: 价格跌破 {threshold}
            pct_change: 涨跌幅超过 {threshold}%
            volume_spike: 成交量放大 {ratio}倍
            ma_cross: 均线金叉/死叉 {fast}/{slow}
            rsi_zone: RSI进入 {zone: oversold/overbought}
        """
        conn = get_connection(self.db_path)
        conn.execute(
            """
            INSERT INTO alert_rules (code, rule_type, params)
            VALUES (?, ?, ?)
        """,
            [code, rule_type, json.dumps(params, ensure_ascii=False)],
        )
        conn.close()
        return {"status": "ok", "code": code, "rule_type": rule_type}

    def get_alert_rules(self, code: str = None) -> list:
        """获取告警规则"""
        conn = get_connection(self.db_path)
        if code:
            df = conn.execute("SELECT * FROM alert_rules WHERE code = ? AND enabled = true", [code]).fetchdf()
        else:
            df = conn.execute("SELECT * FROM alert_rules WHERE enabled = true").fetchdf()
        conn.close()
        if df.empty:
            return []
        return df.to_dict("records")

    def record_alert(self, code: str, rule_type: str, message: str, price: float = None) -> None:
        """记录告警触发"""
        conn = get_connection(self.db_path)
        conn.execute(
            """
            INSERT INTO alert_history (code, rule_type, message, price)
            VALUES (?, ?, ?, ?)
        """,
            [code, rule_type, message, price],
        )
        # 更新规则最后触发时间
        conn.execute(
            """
            UPDATE alert_rules SET last_triggered = current_timestamp
            WHERE code = ? AND rule_type = ?
        """,
            [code, rule_type],
        )
        conn.close()

    def get_alert_history(self, limit: int = 20, unack_only: bool = False) -> list:
        """获取告警历史"""
        conn = get_connection(self.db_path)
        sql = "SELECT * FROM alert_history"
        if unack_only:
            sql += " WHERE acknowledged = false"
        sql += " ORDER BY triggered_at DESC LIMIT ?"
        df = conn.execute(sql, [limit]).fetchdf()
        conn.close()
        if df.empty:
            return []
        return df.to_dict("records")


# ============ 盯盘引擎 ============


@dataclass
class AlertEvent:
    code: str
    name: str
    rule_type: str
    message: str
    price: float = 0.0
    triggered_at: str = ""


class MarketMonitor:
    """盘中实时监控引擎"""

    def __init__(self, watchlist_mgr: WatchlistManager = None):
        self.watchlist = watchlist_mgr or WatchlistManager()
        self._running = False
        self._thread = None
        self._callbacks = []
        self._check_interval = 300  # 默认5分钟检查一次

    def on_alert(self, callback):
        """注册告警回调函数"""
        self._callbacks.append(callback)

    def _fire_alerts(self, events: list):
        for event in events:
            for cb in self._callbacks:
                try:
                    cb(event)
                except Exception:
                    pass

    def check_now(self) -> list:
        """立即检查所有自选股的告警条件"""
        from agent.backtest import add_indicators
        from src.query import StockQuery
        from src.realtime import RealtimeQuoteManager

        manager = RealtimeQuoteManager(
            priority=config.REALTIME_SOURCES,
            timeout=config.REALTIME_TIMEOUT,
        )

        codes = self.watchlist.get_codes()
        if not codes:
            return []

        # 批量获取实时行情
        quotes = manager.fetch_quotes(codes)
        realtime_data = {code: q.to_dict() for code, q in quotes.items()}

        # 获取告警规则
        rules = self.watchlist.get_alert_rules()

        alerts = []
        query = StockQuery()

        for code in codes:
            rt = realtime_data.get(code)
            if not rt:
                continue

            name = rt.get("name", "")
            if not name:
                info = query.get_stock_info(code)
                if not info.empty:
                    name = info.iloc[0].get("name", "")

            current_price = rt.get("current", 0)
            if current_price <= 0:
                continue

            # 获取该股票的规则
            code_rules = [r for r in rules if r["code"] == code]

            for rule in code_rules:
                rule_type = rule["rule_type"]
                params = json.loads(rule["params"]) if rule["params"] else {}
                triggered = False
                msg = ""

                if rule_type == "price_above":
                    threshold = params.get("threshold", 0)
                    if current_price > threshold:
                        triggered = True
                        msg = f"{name}({code}) 价格 {current_price:.2f} 突破 {threshold:.2f}"

                elif rule_type == "price_below":
                    threshold = params.get("threshold", 0)
                    if current_price < threshold:
                        triggered = True
                        msg = f"{name}({code}) 价格 {current_price:.2f} 跌破 {threshold:.2f}"

                elif rule_type == "pct_change":
                    threshold = params.get("threshold", 5)
                    pct = rt.get("change_pct", 0)
                    if abs(pct) > threshold:
                        direction = "涨" if pct > 0 else "跌"
                        triggered = True
                        msg = f"{name}({code}) {direction}幅 {pct:.2f}% 超过 {threshold}%"

                elif rule_type == "volume_spike":
                    ratio = params.get("ratio", 3)
                    # 需要历史均量对比
                    kdf = query.get_daily_kline(code, limit=6)
                    if not kdf.empty and len(kdf) >= 2:
                        avg_vol = kdf["volume"].iloc[:-1].mean()
                        vol = rt.get("volume", 0)
                        if avg_vol > 0 and vol / avg_vol > ratio:
                            triggered = True
                            actual_ratio = vol / avg_vol
                            msg = f"{name}({code}) 成交量放大 {actual_ratio:.1f}倍（超过{ratio}倍阈值）"

                elif rule_type == "rsi_zone":
                    zone = params.get("zone", "oversold")
                    kdf = query.get_daily_kline(code, limit=30)
                    if not kdf.empty:
                        kdf = add_indicators(kdf)
                        rsi = kdf["rsi6"].iloc[-1]
                        if zone == "oversold" and rsi < 20:
                            triggered = True
                            msg = f"{name}({code}) RSI6={rsi:.1f} 进入超卖区域"
                        elif zone == "overbought" and rsi > 80:
                            triggered = True
                            msg = f"{name}({code}) RSI6={rsi:.1f} 进入超买区域"

                if triggered:
                    event = AlertEvent(
                        code=code,
                        name=name,
                        rule_type=rule_type,
                        message=msg,
                        price=current_price,
                        triggered_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    alerts.append(event)
                    self.watchlist.record_alert(code, rule_type, msg, current_price)

        if alerts:
            self._fire_alerts(alerts)

        return alerts

    def start(self, interval: int = 300):
        """启动后台监控（独立线程）"""
        if self._running:
            return

        self._running = True
        self._check_interval = interval

        def _loop():
            while self._running:
                now = datetime.now()
                # 只在交易时间检查 (9:15-15:05)
                if now.weekday() < 5 and (
                    (now.hour == 9 and now.minute >= 15)
                    or (10 <= now.hour < 15)
                    or (now.hour == 15 and now.minute <= 5)
                ):
                    try:
                        self.check_now()
                    except Exception:
                        pass
                time.sleep(self._check_interval)

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止监控"""
        self._running = False

    def is_running(self) -> bool:
        return self._running


# ============ 桌面通知 ============


def send_desktop_notification(title: str, message: str) -> bool:
    """发送桌面通知（Windows Toast）"""
    try:
        from win10toast_click import ToastNotifier

        toaster = ToastNotifier()
        toaster.show_toast(title, message, duration=10, threaded=True)
        return True
    except ImportError:
        pass

    # 备用方案：Windows PowerShell toast
    try:
        import subprocess

        ps_script = f"""
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
        $template = @"
<toast>
    <visual>
        <binding template="ToastGeneric">
            <text>{title}</text>
            <text>{message}</text>
        </binding>
    </visual>
</toast>
"@
        $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
        $xml.LoadXml($template)
        $toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("StockAgent").Show($toast)
        """
        subprocess.run(["powershell", "-Command", ps_script], capture_output=True, timeout=5)
        return True
    except Exception:
        pass

    # 最终备用：蜂鸣
    try:
        import winsound

        winsound.MessageBeep()
    except Exception:
        pass

    return False


# ============ Agent 集成 ============


def format_watchlist_status(watchlist_mgr: WatchlistManager) -> str:
    """格式化自选股状态（带实时行情）"""
    from src.realtime import RealtimeQuoteManager

    codes = watchlist_mgr.get_codes()
    if not codes:
        return "自选股列表为空，请先添加股票"

    manager = RealtimeQuoteManager(
        priority=config.REALTIME_SOURCES,
        timeout=config.REALTIME_TIMEOUT,
    )
    quotes = manager.fetch_quotes(codes)
    realtime_data = {code: q.to_dict() for code, q in quotes.items()}

    items = watchlist_mgr.list()
    lines = ["=== 自选股状态 ===\n"]

    current_group = None
    for item in items:
        group = item.get("group_name", "默认")
        if group != current_group:
            lines.append(f"\n【{group}】")
            current_group = group

        code = item["code"]
        name = item.get("name", code)
        rt = realtime_data.get(code, {})

        price = rt.get("current", 0)
        pct = rt.get("change_pct", 0)
        volume = rt.get("volume", 0)

        target = item.get("target_price")
        stop_loss = item.get("stop_loss_price")

        line = f"  {name}({code})"
        if price > 0:
            arrow = "↑" if pct > 0 else "↓" if pct < 0 else "→"
            line += f" {price:.2f} {arrow}{pct:+.2f}%"
            if volume:
                line += f" 量:{volume / 1e4:.0f}万"
        else:
            line += " [无实时数据]"

        if target:
            distance = (target - price) / price * 100 if price > 0 else 0
            line += f" 目标:{target:.2f}({distance:+.1f}%)"
        if stop_loss:
            distance = (stop_loss - price) / price * 100 if price > 0 else 0
            line += f" 止损:{stop_loss:.2f}({distance:+.1f}%)"

        lines.append(line)

    return "\n".join(lines)
