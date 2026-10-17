"""交易日志与复盘系统 - 记录操作、对比预测、生成复盘报告"""

from datetime import date, datetime, timedelta

from deeppulse import config
from deeppulse.src.database import get_connection

# action 白名单与别名归一化（入口校验，保证流水与持仓始终一致）
_ACTION_ALIASES: dict = {}
for _canon, _aliases in (
    ("买入", ("买入", "建仓", "buy", "open")),
    ("加仓", ("加仓", "补仓", "add")),
    ("卖出", ("卖出", "sell")),
    ("减仓", ("减仓", "reduce")),
    ("清仓", ("清仓", "平仓", "止损", "止盈", "liquidate", "clear", "close")),
):
    for _alias in _aliases:
        _ACTION_ALIASES[_alias.casefold()] = _canon


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

    def record_trade(
        self,
        code: str,
        action: str,
        price: float,
        shares: int,
        name: str = "",
        direction: str = "long",
        reason: str = "",
        strategy: str = "",
        emotion: str = "",
        notes: str = "",
        prediction_id: str = None,
    ) -> dict:
        """记录一笔交易

        Args:
            code: 股票代码
            action: 买入/加仓/卖出/减仓/清仓（支持 buy/sell 等别名）
            price: 成交价格
            shares: 成交数量（清仓时忽略，以实际持仓为准；卖出/减仓超过持仓返回 error 不写库）
            name: 股票名称
            direction: long/short
            reason: 交易理由
            strategy: 使用的战法
            emotion: 交易时情绪状态
            notes: 备注
            prediction_id: 关联的预测ID
        """
        # 入口白名单校验 + 别名归一化（任何写表之前），避免流水与持仓不一致
        norm = _ACTION_ALIASES.get(str(action).strip().casefold())
        if norm is None:
            return {"status": "error", "message": f"不支持的 action: {action}"}

        # 股票归一：6位代码或名称 → (code, name)，识别不了拒绝写库（不产生垃圾流水）
        from deeppulse.src.query import StockQuery

        info, candidates = StockQuery(db_path=self.db_path).resolve_stock(code)
        if info is None:
            return {
                "status": "error",
                "message": f"无法识别股票 {code!r}：请使用6位股票代码或准确名称",
                "candidates": candidates,
            }
        code = info["code"]
        if not name:
            name = info.get("name") or ""

        now = datetime.now()

        conn = get_connection(self.db_path)
        try:
            existing = conn.execute("SELECT shares FROM portfolio WHERE code = ?", [code]).fetchdf()
            held = int(existing.iloc[0]["shares"]) if not existing.empty else 0

            if norm == "清仓":
                if held <= 0:
                    return {"status": "error", "message": "无持仓可清仓"}
                shares = held  # 全量卖出，覆盖调用方可能错误的数量
                norm = "卖出"
            elif norm in ("卖出", "减仓"):
                if held <= 0:
                    return {"status": "error", "message": f"无持仓可{norm}"}
                # 卖出/减仓不得超持仓（与 tools 层同语义），拒绝时不写库，
                # 否则 max 截断持仓但流水按超卖数量入账，已实现盈亏虚增
                if shares > held:
                    return {"status": "error", "message": f"卖出数量{shares}超过持仓{held}股，未记录"}

            amount = price * shares
            conn.execute(
                """
                INSERT INTO trade_journal
                (code, name, action, price, shares, amount, trade_date, trade_time,
                 direction, reason, strategy, emotion, notes, related_prediction_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                [
                    code,
                    name,
                    norm,
                    price,
                    shares,
                    amount,
                    now.strftime("%Y-%m-%d"),
                    now.strftime("%H:%M:%S"),
                    direction,
                    reason,
                    strategy,
                    emotion,
                    notes,
                    prediction_id,
                ],
            )

            # 更新持仓
            self._update_portfolio(conn, code, name, norm, price, shares)

            return {"status": "ok", "action": norm, "code": code, "price": price, "shares": shares, "amount": amount}
        finally:
            conn.close()

    def _update_portfolio(self, conn, code, name, action, price, shares):
        """更新持仓信息"""
        existing = conn.execute("SELECT * FROM portfolio WHERE code = ?", [code]).fetchdf()

        if action in ("买入", "加仓"):
            if existing.empty:
                conn.execute(
                    """
                    INSERT INTO portfolio (code, name, shares, avg_cost, first_buy_date)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    [code, name, shares, price, date.today().isoformat()],
                )
            else:
                old_shares = int(existing.iloc[0]["shares"])
                old_cost = float(existing.iloc[0]["avg_cost"])
                new_shares = old_shares + shares
                new_cost = (old_cost * old_shares + price * shares) / new_shares if new_shares > 0 else 0
                conn.execute(
                    """
                    UPDATE portfolio SET shares = ?, avg_cost = ?, name = ?,
                    last_update = current_timestamp WHERE code = ?
                """,
                    [new_shares, round(new_cost, 3), name, code],
                )

        elif action in ("卖出", "减仓"):
            if not existing.empty:
                old_shares = int(existing.iloc[0]["shares"])
                new_shares = max(0, old_shares - shares)
                if new_shares == 0:
                    conn.execute("DELETE FROM portfolio WHERE code = ?", [code])
                else:
                    conn.execute(
                        """
                        UPDATE portfolio SET shares = ?, last_update = current_timestamp
                        WHERE code = ?
                    """,
                        [new_shares, code],
                    )

    def get_portfolio(self) -> list:
        """获取当前持仓"""
        conn = get_connection(self.db_path)
        df = conn.execute("SELECT * FROM portfolio WHERE shares > 0 ORDER BY code").fetchdf()
        conn.close()
        if df.empty:
            return []
        return df.to_dict("records")

    def get_trade_history(self, code: str = None, days: int = 30, limit: int = 50) -> list:
        """获取交易历史"""
        conn = get_connection(self.db_path)
        start_date = (date.today() - timedelta(days=days)).isoformat()
        if code:
            df = conn.execute(
                """
                SELECT * FROM trade_journal
                WHERE code = ? AND trade_date >= ?
                ORDER BY trade_date DESC, trade_time DESC LIMIT ?
            """,
                [code, start_date, limit],
            ).fetchdf()
        else:
            df = conn.execute(
                """
                SELECT * FROM trade_journal
                WHERE trade_date >= ?
                ORDER BY trade_date DESC, trade_time DESC LIMIT ?
            """,
                [start_date, limit],
            ).fetchdf()
        conn.close()
        if df.empty:
            return []
        return df.to_dict("records")

    def calculate_pnl(self, code: str) -> dict:
        """计算某只股票的已实现盈亏（FIFO 先进先出配对，覆盖全量交易）"""
        # 全量交易：不限 365 天窗口，避免跨窗口买入被漏计导致盈亏错误
        trades = self.get_trade_history(code, days=36500, limit=10**6)
        if not trades:
            return {
                "code": code,
                "total_buy": 0,
                "total_sell": 0,
                "realized_pnl": 0,
                "buy_shares": 0,
                "sell_shares": 0,
                "trade_count": 0,
                "unmatched_sell_shares": 0,
            }

        # get_trade_history 返回 DESC，FIFO 需要按时间升序
        trades = sorted(trades, key=lambda t: (str(t.get("trade_date")), str(t.get("trade_time") or "")))

        lots: list = []  # FIFO 买入队列 [price, remaining_shares]
        realized_pnl = 0.0
        total_buy_amount = 0.0
        total_sell_amount = 0.0
        buy_shares = 0
        sell_shares = 0
        unmatched_sell = 0

        for t in trades:
            price = float(t["price"])
            shares = int(t["shares"])
            amount = float(t["amount"]) if t.get("amount") is not None else price * shares
            if t["action"] in ("买入", "加仓"):
                total_buy_amount += amount
                buy_shares += shares
                lots.append([price, shares])
            elif t["action"] in ("卖出", "减仓"):
                total_sell_amount += amount
                sell_shares += shares
                remaining = shares
                while remaining > 0 and lots:
                    lot_price, lot_rem = lots[0]
                    matched = min(remaining, lot_rem)
                    realized_pnl += (price - lot_price) * matched
                    lots[0][1] = lot_rem - matched
                    remaining -= matched
                    if lots[0][1] == 0:
                        lots.pop(0)
                if remaining > 0:
                    # 无配对买入的卖出股：按零成本计入，勿丢弃也勿除零
                    unmatched_sell += remaining
                    realized_pnl += price * remaining

        return {
            "code": code,
            "total_buy": round(total_buy_amount, 2),
            "total_sell": round(total_sell_amount, 2),
            "realized_pnl": round(realized_pnl, 2),
            "buy_shares": buy_shares,
            "sell_shares": sell_shares,
            "trade_count": len(trades),
            "unmatched_sell_shares": unmatched_sell,
            "open_lots_shares": sum(l[1] for l in lots),
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
        df = conn.execute(
            """
            SELECT * FROM trade_journal WHERE trade_date >= ?
            ORDER BY trade_date
        """,
            [start],
        ).fetchdf()
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

    def save_review(
        self, period: str, summary: str, what_went_well: str, what_to_improve: str, key_lessons: str
    ) -> dict:
        """保存复盘笔记"""
        conn = get_connection(self.db_path)
        conn.execute(
            """
            INSERT INTO review_notes
            (review_date, period, summary, what_went_well, what_to_improve, key_lessons)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            [date.today().isoformat(), period, summary, what_went_well, what_to_improve, key_lessons],
        )
        conn.close()
        return {"status": "ok", "period": period}

    def get_reviews(self, limit: int = 10) -> list:
        """获取复盘笔记"""
        conn = get_connection(self.db_path)
        df = conn.execute(
            """
            SELECT * FROM review_notes ORDER BY created_at DESC LIMIT ?
        """,
            [limit],
        ).fetchdf()
        conn.close()
        if df.empty:
            return []
        return df.to_dict("records")


def format_portfolio_status(journal: TradeJournal) -> str:
    """格式化持仓状态"""
    from deeppulse.src.realtime import RealtimeQuoteManager

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
