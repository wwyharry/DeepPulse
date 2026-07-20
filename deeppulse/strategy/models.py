"""策略平台数据模型"""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class StrategyStatus(str, Enum):
    """策略状态"""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SignalType(str, Enum):
    """信号类型"""

    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class SignalStatus(str, Enum):
    """信号状态"""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"


class RiskLevel(str, Enum):
    """风险等级"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"


@dataclass
class Strategy:
    """策略模型"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    description: str = ""
    content: str = ""  # Markdown内容
    conditions: dict = field(default_factory=dict)  # 结构化条件
    parameters: dict = field(default_factory=dict)  # 可调参数
    risk_level: str = RiskLevel.MEDIUM
    status: str = StrategyStatus.DRAFT
    version: int = 1
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # 文件信息（兼容现有文件系统）
    filename: str = ""

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Strategy":
        """从字典创建"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_markdown(cls, filename: str, content: str) -> "Strategy":
        """从Markdown文件创建策略"""
        import re

        # 提取标题
        title_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        name = title_match.group(1).strip() if title_match else filename.replace(".md", "")

        # 提取分类（从文件名）
        category_match = re.match(r"(\d+)-(.+)\.md", filename)
        category = ""
        if category_match:
            # 根据序号推断分类
            num = int(category_match.group(1))
            if num <= 3:
                category = "三大基础"
            elif num <= 9:
                category = "首板细分"
            elif num <= 16:
                category = "接力细分"
            elif num <= 24:
                category = "低吸细分"
            elif num <= 28:
                category = "半路细分"
            elif num <= 33:
                category = "龙头进阶"
            elif num <= 37:
                category = "盘口时段"
            else:
                category = "高阶逻辑"

        # 提取标签（## 标题）
        tags = []
        for m in re.finditer(r"^##\s+(.+)", content, re.MULTILINE):
            tag = m.group(1).strip()
            tag = re.sub(r"^[\d.]+\s*", "", tag)
            if tag:
                tags.append(tag)

        # 提取关键词
        keywords = set()
        tech_patterns = [
            r"MA\d+", r"MACD", r"RSI", r"KDJ", r"BOLL", r"布林",
            r"金叉", r"死叉", r"超买", r"超卖", r"放量", r"缩量",
            r"突破", r"回踩", r"支撑", r"压力", r"均线", r"背离",
            r"涨停", r"跌停", r"封板", r"炸板", r"换手",
        ]
        for pattern in tech_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                keywords.add(pattern)

        # 提取数值条件
        numeric_conditions = re.findall(
            r"(?:MA|RSI|KDJ|MACD|BOLL|J|K|D|DIF|DEA)\s*[<>]=?\s*[\d.]+", content
        )
        keywords.update(numeric_conditions[:10])

        # 推断风险等级
        risk_level = RiskLevel.MEDIUM
        if any(kw in content for kw in ["涨停", "打板", "龙头", "高位"]):
            risk_level = RiskLevel.HIGH
        elif any(kw in content for kw in ["低吸", "支撑", "超跌"]):
            risk_level = RiskLevel.LOW

        return cls(
            name=name,
            category=category,
            tags=tags,
            description=content[:200] + "..." if len(content) > 200 else content,
            content=content,
            conditions={"keywords": list(keywords)},
            parameters={},
            risk_level=risk_level,
            filename=filename,
        )


@dataclass
class StrategyVersion:
    """策略版本"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str = ""
    version: int = 1
    content: str = ""
    conditions: dict = field(default_factory=dict)
    parameters: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    comment: str = ""  # 版本说明

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BacktestResult:
    """回测结果"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str = ""
    strategy_name: str = ""
    stock_code: str = ""
    stock_name: str = ""
    start_date: str = ""
    end_date: str = ""
    initial_capital: float = 100000.0
    final_capital: float = 100000.0
    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_hold_days: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    expectancy: float = 0.0
    parameters: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # 退出策略统计
    exit_strategy_type: str = ""  # 退出策略类型 (conservative/balanced/aggressive/custom)
    exit_statistics: dict = field(default_factory=dict)  # 退出原因统计
    exit_analysis: dict = field(default_factory=dict)  # 退出原因分析

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("equity_curve", None)
        return d

    def summary(self) -> str:
        """生成可读的回测摘要"""
        lines = [
            f"=== 回测报告: {self.strategy_name} ===",
            f"股票: {self.stock_code} {self.stock_name}",
            f"回测区间: {self.start_date} ~ {self.end_date}",
            f"初始资金: {self.initial_capital:,.0f}",
            f"最终资金: {self.final_capital:,.0f}",
            f"总收益率: {self.total_return:.2%}",
            f"年化收益: {self.annual_return:.2%}",
            f"最大回撤: {self.max_drawdown:.2%}",
            "",
            f"总交易次数: {self.total_trades}",
            f"胜率: {self.win_rate:.1%}",
            f"盈利因子: {self.profit_factor:.2f}",
            f"夏普比率: {self.sharpe_ratio:.2f}",
            f"期望收益: {self.expectancy:.2%}",
            "",
            f"盈利笔数: {self.winning_trades} | 平均盈利: {self.avg_win:.2%}",
            f"亏损笔数: {self.losing_trades} | 平均亏损: {self.avg_loss:.2%}",
            f"平均持仓: {self.avg_hold_days:.1f} 天",
            f"最大连赢: {self.max_consecutive_wins} | 最大连亏: {self.max_consecutive_losses}",
        ]

        # 添加退出统计
        if self.exit_statistics:
            lines.append("")
            lines.append("=== 退出统计 ===")
            for reason, count in self.exit_statistics.items():
                pct = count / self.total_trades * 100 if self.total_trades > 0 else 0
                lines.append(f"{reason}: {count}次 ({pct:.1f}%)")

        # 添加退出原因分析
        if self.exit_analysis:
            lines.append("")
            lines.append("=== 退出原因分析 ===")
            for reason, analysis in self.exit_analysis.items():
                avg_return = analysis.get("avg_return", 0)
                count = analysis.get("count", 0)
                lines.append(f"{reason}: 平均收益 {avg_return:.2%} ({count}次)")

        return "\n".join(lines)


@dataclass
class Signal:
    """策略信号"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    strategy_id: str = ""
    strategy_name: str = ""
    stock_code: str = ""
    stock_name: str = ""
    signal_type: str = SignalType.BUY
    signal_price: float = 0.0
    signal_time: str = field(default_factory=lambda: datetime.now().isoformat())
    confidence: float = 0.0  # 0-1
    matched_conditions: list = field(default_factory=list)
    status: str = SignalStatus.PENDING
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Portfolio:
    """策略组合"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    strategies: list = field(default_factory=list)  # [{strategy_id, weight, params}]
    total_capital: float = 1000000.0
    risk_budget: float = 0.02  # 风险预算
    rebalance_frequency: str = "monthly"  # 再平衡频率
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Trade:
    """交易记录"""

    code: str = ""
    name: str = ""
    entry_date: str = ""
    entry_price: float = 0.0
    exit_date: str = ""
    exit_price: float = 0.0
    direction: str = "long"
    shares: int = 0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0
    signal: str = ""
    exit_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PerformanceMetrics:
    """绩效指标"""

    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    avg_hold_days: float = 0.0
    expectancy: float = 0.0
    calmar_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    def to_dict(self) -> dict:
        return asdict(self)
