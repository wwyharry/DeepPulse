"""动态退出策略模块

核心理念: "没有问题就不止盈"
- ATR止损: 基于市场波动的动态止损
- 移动止损: 保护利润，跟随最高价
- 技术退出: 当出现技术问题时才考虑止盈
- 分批退出: 降低单次决策风险
"""

from dataclasses import asdict, dataclass
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    """安全获取浮点数值，处理None和非数值类型"""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_get(row: dict, key: str, default: float = 0.0) -> float:
    """安全从row获取浮点数值，处理None和非数值类型"""
    return _safe_float(row.get(key), default)


@dataclass
class ExitStrategy:
    """退出策略配置"""

    # 止损配置（ATR止损为主）
    stop_loss_mode: str = "atr"  # fixed/atr
    stop_loss_fixed: float = 0.05  # 固定止损比例（备用）
    stop_loss_atr_multiple: float = 2.0  # ATR倍数（核心参数）

    # 移动止损配置（保护利润）
    trailing_stop_enabled: bool = True
    trailing_stop_atr_multiple: float = 3.0  # 移动止损ATR倍数
    trailing_stop_activation_pct: float = 0.05  # 激活移动止损的盈利幅度

    # 保本止损配置
    breakeven_enabled: bool = True
    breakeven_activation_pct: float = 0.03  # 激活保本止损的盈利幅度

    # 技术退出配置（"没有问题就不止盈"）
    technical_exit_enabled: bool = True
    exit_score_threshold_partial: int = 3  # 部分退出分数阈值
    exit_score_threshold_full: int = 5  # 全部退出分数阈值

    # 时间退出配置
    max_hold_days: int = 30  # 最大持仓天数

    # 分批退出配置
    partial_exit_ratio: float = 0.5  # 部分退出比例

    def to_dict(self) -> dict:
        """转换为字典"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExitStrategy":
        """从字典创建"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# 预设退出策略
PRESET_EXIT_STRATEGIES = {
    "conservative": ExitStrategy(
        stop_loss_mode="atr",
        stop_loss_atr_multiple=2.0,  # 宽止损
        trailing_stop_enabled=True,
        trailing_stop_atr_multiple=2.5,
        trailing_stop_activation_pct=0.05,
        breakeven_enabled=True,
        breakeven_activation_pct=0.03,
        technical_exit_enabled=True,
        exit_score_threshold_partial=3,
        exit_score_threshold_full=5,
        max_hold_days=30,
    ),
    "balanced": ExitStrategy(
        stop_loss_mode="atr",
        stop_loss_atr_multiple=1.5,  # 中等止损
        trailing_stop_enabled=True,
        trailing_stop_atr_multiple=2.0,
        trailing_stop_activation_pct=0.03,
        breakeven_enabled=True,
        breakeven_activation_pct=0.02,
        technical_exit_enabled=True,
        exit_score_threshold_partial=3,
        exit_score_threshold_full=5,
        max_hold_days=25,
    ),
    "aggressive": ExitStrategy(
        stop_loss_mode="atr",
        stop_loss_atr_multiple=1.0,  # 窄止损
        trailing_stop_enabled=True,
        trailing_stop_atr_multiple=1.5,
        trailing_stop_activation_pct=0.02,
        breakeven_enabled=True,
        breakeven_activation_pct=0.015,
        technical_exit_enabled=True,
        exit_score_threshold_partial=2,
        exit_score_threshold_full=4,
        max_hold_days=20,
    ),
}


class ExitScoreCalculator:
    """退出分数计算器

    根据技术指标计算退出分数，用于判断是否应该退出
    """

    def calculate_exit_score(
        self,
        row: dict,
        df_history: Any,
        entry_price: float,
        highest_price: float,
    ) -> dict:
        """
        计算退出分数

        Args:
            row: 当前行数据
            df_history: 历史数据DataFrame
            entry_price: 入场价格
            highest_price: 持仓期间最高价

        Returns:
            {
                "total_score": int,
                "technical_score": int,
                "trend_score": int,
                "momentum_score": int,
                "details": list[str]
            }
        """
        technical_score = 0
        trend_score = 0
        momentum_score = 0
        details = []

        current_price = _safe_get(row, "close", 0)

        # 1. 技术信号
        # MACD死叉
        dif = _safe_get(row, "dif", 0)
        dea = _safe_get(row, "dea", 0)
        dif_prev = _safe_get(row, "dif_prev", 0)
        dea_prev = _safe_get(row, "dea_prev", 0)
        if dif < dea and dif_prev >= dea_prev:
            technical_score += 2
            details.append("MACD死叉")

        # RSI超买回落
        rsi = _safe_get(row, "rsi6", 50)
        rsi_prev = _safe_get(row, "rsi6_prev", 50)
        if rsi_prev > 70 and rsi < 60:
            technical_score += 2
            details.append("RSI超买回落")

        # 跌破MA20
        ma20 = _safe_get(row, "ma20", 0)
        ma20_prev = _safe_get(row, "ma20_prev", 0)
        close_prev = _safe_get(row, "close_prev", 0)
        if ma20 > 0 and current_price < ma20 and ma20_prev > 0 and close_prev >= ma20_prev:
            technical_score += 3
            details.append("跌破MA20")

        # 布林带上轨回落
        boll_upper = _safe_get(row, "boll_upper", 0)
        high = _safe_get(row, "high", 0)
        if boll_upper > 0 and high >= boll_upper and current_price < boll_upper:
            technical_score += 1
            details.append("布林带上轨回落")

        # 2. 趋势信号
        # 高点不再创新高
        if highest_price > 0 and current_price < highest_price * 0.95:
            trend_score += 2
            details.append("高点回落超过5%")

        # 低点创新低
        if df_history is not None and len(df_history) >= 3:
            recent_lows = df_history["low"].tail(3)
            if recent_lows.iloc[-1] < recent_lows.iloc[-2] < recent_lows.iloc[-3]:
                trend_score += 3
                details.append("连续创新低")

        # 均线空头排列
        ma5 = _safe_get(row, "ma5", 0)
        ma10 = _safe_get(row, "ma10", 0)
        if ma5 > 0 and ma10 > 0 and ma20 > 0:
            if ma5 < ma10 < ma20:
                trend_score += 3
                details.append("均线空头排列")

        # 3. 动量信号
        # 成交量萎缩
        vol_ratio = _safe_get(row, "vol_ratio", 1)
        if vol_ratio < 0.7:
            momentum_score += 1
            details.append("成交量萎缩")

        # 上涨乏力（涨幅小）
        pct_change = _safe_get(row, "pct_change", 0)
        if 0 < pct_change < 0.5:
            momentum_score += 1
            details.append("上涨乏力")

        # 波动率放大
        atr = _safe_get(row, "atr", 0)
        atr_prev = _safe_get(row, "atr_prev", 0)
        if atr > 0 and atr_prev > 0 and atr > atr_prev * 1.5:
            momentum_score += 1
            details.append("波动率放大")

        total_score = technical_score + trend_score + momentum_score

        return {
            "total_score": total_score,
            "technical_score": technical_score,
            "trend_score": trend_score,
            "momentum_score": momentum_score,
            "details": details,
        }


class DynamicExitEngine:
    """动态退出引擎 - "没有问题就不止盈"

    核心逻辑:
    1. ATR止损: 根据市场波动动态调整止损位
    2. 移动止损: 保护利润，跟随最高价
    3. 保本止损: 盈利达到阈值后保护本金
    4. 技术退出: 当出现技术问题时才考虑止盈
    5. 时间退出: 超过最大持仓时间
    """

    def __init__(self, exit_strategy: ExitStrategy):
        self.exit_strategy = exit_strategy
        self.highest_price = 0.0  # 持仓期间最高价
        self.breakeven_activated = False  # 保本止损是否激活
        self.trailing_stop_price = 0.0  # 移动止损价
        self.partial_taken = False  # 是否已部分止盈
        self.entry_atr = 0.0  # 入场时的ATR
        self.exit_calculator = ExitScoreCalculator()

    def should_exit(
        self,
        row: dict,
        entry_price: float,
        hold_days: int,
        df_history: Any = None,
    ) -> tuple[bool, str, float]:
        """
        判断是否应该退出

        Args:
            row: 当前行数据
            entry_price: 入场价格
            hold_days: 持仓天数
            df_history: 历史数据DataFrame

        Returns:
            (should_exit, reason, exit_ratio)
            - should_exit: 是否应该退出
            - reason: 退出原因
            - exit_ratio: 退出比例（用于分批止盈）
        """
        current_price = _safe_get(row, "close", 0)
        if current_price <= 0 or entry_price <= 0:
            return False, "", 0.0

        pnl_pct = (current_price - entry_price) / entry_price

        # 更新最高价
        high = _safe_get(row, "high", current_price)
        self.highest_price = max(self.highest_price, high)

        # 初始化入场ATR (支持atr或atr14列名)
        if self.entry_atr == 0:
            self.entry_atr = _safe_get(row, "atr", _safe_get(row, "atr14", 0))

        # 1. 检查ATR止损（核心止损逻辑）
        atr = _safe_get(row, "atr", _safe_get(row, "atr14", self.entry_atr))
        if atr > 0:
            stop_loss_distance = atr * self.exit_strategy.stop_loss_atr_multiple
            stop_loss_price = entry_price - stop_loss_distance
            if current_price <= stop_loss_price:
                return True, "ATR止损", 1.0

        # 2. 检查移动止损（保护利润）
        if self.exit_strategy.trailing_stop_enabled:
            # 激活条件：盈利达到一定幅度
            if pnl_pct >= self.exit_strategy.trailing_stop_activation_pct:
                # 计算移动止损价
                trailing_distance = atr * self.exit_strategy.trailing_stop_atr_multiple
                new_trailing_stop = self.highest_price - trailing_distance

                # 移动止损价只能上移，不能下移
                if new_trailing_stop > self.trailing_stop_price:
                    self.trailing_stop_price = new_trailing_stop

                if self.trailing_stop_price > 0 and current_price <= self.trailing_stop_price:
                    return True, "移动止损", 1.0

        # 3. 检查保本止损
        if self.exit_strategy.breakeven_enabled:
            if pnl_pct >= self.exit_strategy.breakeven_activation_pct:
                self.breakeven_activated = True

            if self.breakeven_activated and pnl_pct <= 0:
                return True, "保本止损", 1.0

        # 4. 检查技术退出（"没有问题就不止盈"）
        if self.exit_strategy.technical_exit_enabled:
            exit_score = self.exit_calculator.calculate_exit_score(row, df_history, entry_price, self.highest_price)

            total_score = exit_score["total_score"]

            # 达到全部退出阈值
            if total_score >= self.exit_strategy.exit_score_threshold_full:
                return True, f"技术退出(分数:{total_score})", 1.0

            # 达到部分退出阈值（且尚未部分止盈）
            if total_score >= self.exit_strategy.exit_score_threshold_partial and not self.partial_taken:
                self.partial_taken = True
                return True, f"部分止盈(分数:{total_score})", self.exit_strategy.partial_exit_ratio

        # 5. 检查时间退出
        if hold_days >= self.exit_strategy.max_hold_days:
            return True, "超时退出", 1.0

        return False, "", 0.0

    def reset(self):
        """重置引擎状态（用于新的交易）"""
        self.highest_price = 0.0
        self.breakeven_activated = False
        self.trailing_stop_price = 0.0
        self.partial_taken = False
        self.entry_atr = 0.0


def get_preset_strategy(preset_name: str) -> ExitStrategy:
    """获取预设退出策略

    Args:
        preset_name: 预设名称 (conservative/balanced/aggressive)

    Returns:
        ExitStrategy 实例
    """
    if preset_name not in PRESET_EXIT_STRATEGIES:
        raise ValueError(f"未知的预设策略: {preset_name}，可选: {list(PRESET_EXIT_STRATEGIES.keys())}")
    return PRESET_EXIT_STRATEGIES[preset_name]


def list_preset_strategies() -> dict:
    """列出所有预设策略

    Returns:
        预设策略字典
    """
    return {
        name: {
            "name": name,
            "description": _get_preset_description(name),
            "params": strategy.to_dict(),
        }
        for name, strategy in PRESET_EXIT_STRATEGIES.items()
    }


def _get_preset_description(preset_name: str) -> str:
    """获取预设策略描述"""
    descriptions = {
        "conservative": "保守型: 宽ATR止损(2倍) + 宽移动止损(2.5倍ATR)，适合稳健投资者",
        "balanced": "平衡型: 中ATR止损(1.5倍) + 中移动止损(2倍ATR)，适合大多数场景",
        "aggressive": "激进型: 窄ATR止损(1倍) + 窄移动止损(1.5倍ATR)，适合短线交易",
    }
    return descriptions.get(preset_name, "")
