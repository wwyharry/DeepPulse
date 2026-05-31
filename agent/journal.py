"""交易日志与复盘系统 - 记录操作、对比预测、生成复盘报告"""
import json
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field, asdict

from src.database import get_connection
import config


class TradeJournal:
    """交易日志管理器"""

    def __init__(self, db_path=None):
        self.db_path = db_path or config.DB_PATH
        self._init_table()

    def _init_table(self):
        conn = get_connection(self.db_path)
        conn.execute("CREATE SEQUENCE IF NOT EXISTS trade_journal_seq START 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_journal (
                id INTEGER DEFAULT nextval('trade_journal_seq'),
                code VARCHAR NOT NULL,
                name VARCHAR,
                action VARCHAR NOT NULL,
                price DOUBLE NOT NULL,
                shares INTEGER NOT NULL,
                amount DOUBLE,
                trade_date DATE NOT NULL,
                trade_time VARCHAR,
                direction VARCHAR DEFAULT 'long',
                reason VARCHAR,
                strategy VARCHAR,
                emotion VARCHAR,
                notes VARCHAR,
                related_prediction_id VARCHAR,
                created_at TIMESTAMP DEFAULT current_timestamp
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio (
                code VARCHAR PRIMARY KEY,
                name VARCHAR,
                shares INTEGER DEFAULT 0,
                avg_cost DOUBLE DEFAULT 0,
                current_price DOUBLE DEFAULT 0,
                unrealized_pnl DOUBLE DEFAULT 0,
                unrealized_pnl_pct DOUBLE DEFAULT 0,
                first_buy_date DATE,
                last_update TIMESTAMP DEFAULT current_timestamp
            )
        """)
        conn.execute("CREATE SEQUENCE IF NOT EXISTS review_notes_seq START 1")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_notes (
                id INTEGER DEFAULT nextval('review_notes_seq'),
                review_date DATE NOT NULL,
                period VARCHAR,
                summary VARCHAR,
                what_went_well VARCHAR,
                what_to_improve VARCHAR,
                key_lessons VARCHAR,
                created_at TIMESTAMP DEFAULT current_timestamp
            )
        """)
        conn.close()

    def record_trade(self, code: str, action: str, price: float, shares: int,
                     name: str = "", direction: str = "long", reason: str = "",
                     strategy: str = "", emotion: str = "", notes: str = "",
                     prediction_id: str = None) -> dict:
        """记录一笔交易

        Args:
            code: 股票代码
            action: 买入/卖出/加仓/减仓
            price: 成交价格
            shares: 成交数量
            name: 股票名称
            direction: long/short
            reason: 交易理由
            strategy: 使用的战法
            emotion: 交易时情绪状态
            notes: 备注
            prediction_id: 关联的预测ID
        """
        amount = price * shares
        now = datetime.now()

        conn = get_connection(self.db_path)
        try:
            conn.execute("""
                INSERT INTO trade_journal
                (code, name, action, price, shares, amount, trade_date, trade_time,
                 direction, reason, strategy, emotion, notes, related_prediction_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [code, name, action, price, shares, amount,
                  now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"),
                  direction, reason, strategy, emotion, notes, prediction_id])

            # 更新持仓
            self._update_portfolio(conn, code, name, action, price, shares)

            return {"status": "ok", "action": action, "code": code,
                    "price": price, "shares": shares, "amount": amount}
        finally:
            conn.close()

    def _update_portfolio(self, conn, code, name, action, price, shares):
        """更新持仓信息"""
        existing = conn.execute(
            "SELECT * FROM portfolio WHERE code = ?", [code]
        ).fetchdf()

        if action in ("买入", "加仓"):
            if existing.empty:
                conn.execute("""
                    INSERT INTO portfolio (code, name, shares, avg_cost, first_buy_date)
                    VALUES (?, ?, ?, ?, ?)
                """, [code, name, shares, price, date.today().isoformat()])
            else:
                old_shares = int(existing.iloc[0]["shares"])
                old_cost = float(existing.iloc[0]["avg_cost"])
                new_shares = old_shares + shares
                new_cost = (old_cost * old_shares + price * shares) / new_shares if new_shares > 0 else 0
                conn.execute("""
                    UPDATE portfolio SET shares = ?, avg_cost = ?, name = ?,
                    last_update = current_timestamp WHERE code = ?
                """, [new_shares, round(new_cost, 3), name, code])

        elif action in ("卖出", "减仓"):
            if not existing.empty:
                old_shares = int(existing.iloc[0]["shares"])
                new_shares = max(0, old_shares - shares)
                if new_shares == 0:
                    conn.execute("DELETE FROM portfolio WHERE code = ?", [code])
                else:
                    conn.execute("""
                        UPDATE portfolio SET shares = ?, last_update = current_timestamp
                        WHERE code = ?
                    """, [new_shares, code])

    def get_portfolio(self) -> list:
        """获取当前持仓"""
        conn = get_connection(self.db_path)
        df = conn.execute("SELECT * FROM portfolio WHERE shares > 0 ORDER BY code").fetchdf()
        conn.close()
        if df.empty:
            return []
        return df.to_dict("records")

    def get_trade_history(self, code: str = None, days: int = 30,
                          limit: int = 50) -> list:
        """获取交易历史"""
        conn = get_connection(self.db_path)
        start_date = (date.today() - timedelta(days=days)).isoformat()
        if code:
            df = conn.execute("""
                SELECT * FROM trade_journal
                WHERE code = ? AND trade_date >= ?
                ORDER BY trade_date DESC, trade_time DESC LIMIT ?
            """, [code, start_date, limit]).fetchdf()
        else:
            df = conn.execute("""
                SELECT * FROM trade_journal
                WHERE trade_date >= ?
                ORDER BY trade_date DESC, trade_time DESC LIMIT ?
            """, [start_date, limit]).fetchdf()
        conn.close()
        if df.empty:
            return []
        return df.to_dict("records")

    def calculate_pnl(self, code: str) -> dict:
        """计算某只股票的已实现盈亏"""
        trades = self.get_trade_history(code, days=365, limit=500)
        if not trades:
            return {"code": code, "total_pnl": 0, "trades": 0}

        total_buy_amount = 0
        total_sell_amount = 0
        buy_shares = 0
        sell_shares = 0

        for t in trades:
            if t["action"] in ("买入", "加仓"):
                total_buy_amount += t["amount"]
                buy_shares += t["shares"]
            elif t["action"] in ("卖出", "减仓"):
                total_sell_amount += t["amount"]
                sell_shares += t["shares"]

        # FIFO 计算已实现盈亏
        realized_pnl = total_sell_amount - total_buy_amount * (sell_shares / buy_shares) if buy_shares > 0 else 0

        return {
            "code": code,
            "total_buy": round(total_buy_amount, 2),
            "total_sell": round(total_sell_amount, 2),
            "realized_pnl": round(realized_pnl, 2),
            "buy_shares": buy_shares,
            "sell_shares": sell_shares,
            "trade_count": len(trades),
        }

    def get_period_summary(self, period: str = "week") -> dict:
        """获取某个时间段的交易汇总

        Args:
            period: week / month / quarter
        """
        if period == "week":
            start = (date.today() - timedelta(days=7)).isoformat()
        elif period == "month":
            start = (date.today() - timedelta(days=30)).isoformat()
        else:
            start = (date.today() - timedelta(days=90)).isoformat()

        conn = get_connection(self.db_path)
        df = conn.execute("""
            SELECT * FROM trade_journal WHERE trade_date >= ?
            ORDER BY trade_date
        """, [start]).fetchdf()
        conn.close()

        if df.empty:
            return {"period": period, "total_trades": 0, "message": "该时间段无交易记录"}

        # 统计
        buy_df = df[df["action"].isin(["买入", "加仓"])]
        sell_df = df[df["action"].isin(["卖出", "减仓"])]

        total_buy = float(buy_df["amount"].sum()) if not buy_df.empty else 0
        total_sell = float(sell_df["amount"].sum()) if not sell_df.empty else 0
        unique_stocks = df["code"].nunique()

        # 策略使用统计
        strategy_counts = {}
        for s in df["strategy"].dropna():
            if s:
                strategy_counts[s] = strategy_counts.get(s, 0) + 1

        # 情绪统计
        emotion_counts = {}
        for e in df["emotion"].dropna():
            if e:
                emotion_counts[e] = emotion_counts.get(e, 0) + 1

        return {
            "period": period,
            "start_date": start,
            "total_trades": len(df),
            "buy_trades": len(buy_df),
            "sell_trades": len(sell_df),
            "total_buy_amount": round(total_buy, 2),
            "total_sell_amount": round(total_sell, 2),
            "net_flow": round(total_sell - total_buy, 2),
            "unique_stocks": unique_stocks,
            "strategy_usage": strategy_counts,
            "emotion_distribution": emotion_counts,
        }

    def save_review(self, period: str, summary: str, what_went_well: str,
                    what_to_improve: str, key_lessons: str) -> dict:
        """保存复盘笔记"""
        conn = get_connection(self.db_path)
        conn.execute("""
            INSERT INTO review_notes
            (review_date, period, summary, what_went_well, what_to_improve, key_lessons)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [date.today().isoformat(), period, summary,
              what_went_well, what_to_improve, key_lessons])
        conn.close()
        return {"status": "ok", "period": period}

    def get_reviews(self, limit: int = 10) -> list:
        """获取复盘笔记"""
        conn = get_connection(self.db_path)
        df = conn.execute("""
            SELECT * FROM review_notes ORDER BY created_at DESC LIMIT ?
        """, [limit]).fetchdf()
        conn.close()
        if df.empty:
            return []
        return df.to_dict("records")


def format_portfolio_status(journal: TradeJournal) -> str:
    """格式化持仓状态"""
    from src.realtime import RealtimeQuoteManager

    portfolio = journal.get_portfolio()
    if not portfolio:
        return "当前无持仓"

    codes = [p["code"] for p in portfolio]
    manager = RealtimeQuoteManager(
        priority=config.REALTIME_SOURCES,
        timeout=config.REALTIME_TIMEOUT,
    )
    quotes = manager.fetch_quotes(codes)
    realtime = {code: q.to_dict() for code, q in quotes.items()}

    lines = ["=== 当前持仓 ===\n"]
    total_cost = 0
    total_value = 0

    for p in portfolio:
        code = p["code"]
        name = p.get("name", code)
        shares = p["shares"]
        avg_cost = p["avg_cost"]
        rt = realtime.get(code, {})
        current_price = rt.get("current", 0)

        cost = avg_cost * shares
        value = current_price * shares
        pnl = value - cost
        pnl_pct = (current_price - avg_cost) / avg_cost * 100 if avg_cost > 0 else 0

        total_cost += cost
        total_value += value

        arrow = "↑" if pnl > 0 else "↓" if pnl < 0 else "→"
        lines.append(
            f"  {name}({code}) {shares}股 成本:{avg_cost:.2f} "
            f"现价:{current_price:.2f} {arrow}{pnl_pct:+.2f}% "
            f"盈亏:{pnl:+,.0f}"
        )

    total_pnl = total_value - total_cost
    total_pnl_pct = (total_value / total_cost - 1) * 100 if total_cost > 0 else 0
    lines.append(f"\n总成本: {total_cost:,.0f} | 总市值: {total_value:,.0f}")
    lines.append(f"总盈亏: {total_pnl:+,.0f} ({total_pnl_pct:+.2f}%)")

    return "\n".join(lines)


def format_trade_history(journal: TradeJournal, days: int = 7) -> str:
    """格式化近期交易历史"""
    trades = journal.get_trade_history(days=days)
    if not trades:
        return f"近{days}天无交易记录"

    lines = [f"=== 近{days}天交易记录 ===\n"]
    for t in trades:
        emoji = "🟢" if t["action"] in ("买入", "加仓") else "🔴"
        lines.append(
            f"  {emoji} {t['trade_date']} {t['trade_time']} "
            f"{t['action']} {t.get('name', t['code'])} "
            f"{t['shares']}股 @ {t['price']:.2f} "
            f"({t.get('strategy', '')}) {t.get('reason', '')}"
        )

    return "\n".join(lines)


def generate_auto_review(journal: TradeJournal, period: str = "week") -> str:
    """自动生成复盘报告（供 Agent 使用）"""
    summary = journal.get_period_summary(period)
    portfolio = journal.get_portfolio()

    if summary.get("total_trades", 0) == 0:
        return f"本{period}无交易记录，无需复盘。"

    lines = [f"=== 自动复盘报告 ({period}) ===\n"]

    lines.append(f"交易次数: {summary['total_trades']}")
    lines.append(f"买入 {summary['buy_trades']} 笔 / 卖出 {summary['sell_trades']} 笔")
    lines.append(f"涉及股票: {summary['unique_stocks']} 只")
    lines.append(f"资金流向: 净流入 {summary['net_flow']:+,.0f}")

    if summary.get("strategy_usage"):
        lines.append("\n战法使用:")
        for s, c in sorted(summary["strategy_usage"].items(), key=lambda x: -x[1]):
            lines.append(f"  {s}: {c}次")

    if summary.get("emotion_distribution"):
        lines.append("\n交易情绪分布:")
        for e, c in sorted(summary["emotion_distribution"].items(), key=lambda x: -x[1]):
            lines.append(f"  {e}: {c}次")

    if portfolio:
        lines.append(f"\n当前持仓 {len(portfolio)} 只:")
        for p in portfolio:
            lines.append(f"  {p.get('name', p['code'])}({p['code']}) {p['shares']}股 成本:{p['avg_cost']:.2f}")

    return "\n".join(lines)
