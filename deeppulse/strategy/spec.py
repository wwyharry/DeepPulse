"""策略规格（StrategySpec）- LLM 生成的结构化多因子策略定义

StrategySpec 是可序列化的 JSON 策略描述：因子组合 + 权重 + 入场阈值 +
退出策略预设。compile_signal_func() 将其编译为与现有 BacktestEngine
兼容的信号函数。
"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from deeppulse.strategy.factor_library import get_factor


@dataclass
class FactorSpec:
    """单个因子在策略中的配置"""

    factor_id: str
    params: dict = field(default_factory=dict)
    weight: float = 1.0
    direction: int = 1  # 1 正向 / -1 反向

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "FactorSpec":
        return FactorSpec(
            factor_id=d["factor_id"],
            params=d.get("params") or {},
            weight=float(d.get("weight", 1.0)),
            direction=int(d.get("direction", 1)),
        )


@dataclass
class StrategySpec:
    """结构化多因子策略规格"""

    name: str
    factors: list[FactorSpec] = field(default_factory=list)
    entry_threshold: float = 0.6
    exit_preset: str = "balanced"
    exit_params: dict = field(default_factory=dict)
    llm_rationale: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "factors": [f.to_dict() for f in self.factors],
            "entry_threshold": self.entry_threshold,
            "exit_preset": self.exit_preset,
            "exit_params": self.exit_params,
            "llm_rationale": self.llm_rationale,
        }

    @staticmethod
    def from_dict(d: dict) -> "StrategySpec":
        return StrategySpec(
            name=d.get("name") or "未命名策略",
            factors=[FactorSpec.from_dict(f) for f in d.get("factors", [])],
            entry_threshold=float(d.get("entry_threshold", 0.6)),
            exit_preset=d.get("exit_preset", "balanced"),
            exit_params=d.get("exit_params") or {},
            llm_rationale=d.get("llm_rationale", ""),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(s: str) -> "StrategySpec":
        return StrategySpec.from_dict(json.loads(s))

    def validate(self) -> list[str]:
        """校验规格合法性，返回错误列表（空列表 = 合法）"""
        errors = []
        if not self.factors:
            errors.append("策略至少需要一个因子")
        total_weight = 0.0
        for f in self.factors:
            try:
                factor = get_factor(f.factor_id)
            except KeyError as e:
                errors.append(str(e))
                continue
            if f.weight <= 0:
                errors.append(f"因子 {f.factor_id} 权重必须为正数，当前: {f.weight}")
            if f.direction not in (1, -1):
                errors.append(f"因子 {f.factor_id} direction 只能是 1 或 -1，当前: {f.direction}")
            total_weight += f.weight
            for pname, pvalue in f.params.items():
                if pname not in factor.params:
                    errors.append(f"因子 {f.factor_id} 无参数 {pname}")
                    continue
                fp = factor.params[pname]
                if not (fp.min - 1e-9 <= float(pvalue) <= fp.max + 1e-9):
                    errors.append(
                        f"因子 {f.factor_id} 参数 {pname}={pvalue} 越界 [{fp.min}, {fp.max}]"
                    )
        if self.factors and total_weight <= 0:
            errors.append("因子权重总和必须大于 0")
        if not (0.0 < self.entry_threshold <= 1.0):
            errors.append(f"entry_threshold 必须在 (0, 1] 区间，当前: {self.entry_threshold}")
        return errors


def compile_signal_func(spec: StrategySpec) -> Callable[[pd.DataFrame], pd.Series]:
    """将策略规格编译为回测引擎兼容的信号函数

    返回的函数接收带技术指标列的 K线 DataFrame，输出 int Series：
    加权因子总分 >= entry_threshold 的 bar 为 1（买入信号），否则为 0。
    """
    errors = spec.validate()
    if errors:
        raise ValueError("策略规格非法: " + "; ".join(errors))

    factors = [(get_factor(f.factor_id), f) for f in spec.factors]
    total_weight = sum(f.weight for f in spec.factors)

    def signal_func(df: pd.DataFrame) -> pd.Series:
        score = pd.Series(0.0, index=df.index)
        for factor, fspec in factors:
            s = factor.score(df, fspec.params)
            score = score + s * (fspec.weight * fspec.direction)
        normalized = (score / total_weight).clip(0.0, 1.0)
        return pd.Series(
            np.where(normalized >= spec.entry_threshold, 1, 0), index=df.index, dtype=int
        )

    return signal_func


def factor_scores_detail(spec: StrategySpec, df: pd.DataFrame) -> dict:
    """计算最新 bar 的各因子得分明细（供 evaluate_spec 使用）"""
    total_weight = sum(f.weight for f in spec.factors) or 1.0
    details = []
    total = 0.0
    for fspec in spec.factors:
        factor = get_factor(fspec.factor_id)
        s = factor.score(df, fspec.params)
        latest = float(s.iloc[-1]) if len(s) else 0.0
        weighted = latest * fspec.weight * fspec.direction
        total += weighted
        details.append({
            "factor_id": fspec.factor_id,
            "factor_name": factor.name,
            "score": round(latest, 4),
            "weight": fspec.weight,
            "direction": fspec.direction,
            "weighted_score": round(weighted, 4),
        })
    normalized = max(0.0, min(1.0, total / total_weight))
    return {
        "factors": details,
        "total_score": round(normalized, 4),
        "entry_threshold": spec.entry_threshold,
        "triggered": normalized >= spec.entry_threshold,
    }


def default_spec() -> StrategySpec:
    """内置默认多因子组合（LLM 生成失败时的兜底策略）"""
    return StrategySpec(
        name="默认多因子策略（均线趋势+MACD金叉+放量）",
        factors=[
            FactorSpec(factor_id="ma_trend", params={"fast": 5, "slow": 20}, weight=1.0),
            FactorSpec(factor_id="macd_trend", weight=0.8),
            FactorSpec(factor_id="volume_breakout", params={"vol_ratio": 2.0, "pct": 3.0}, weight=0.6),
        ],
        entry_threshold=0.6,
        exit_preset="balanced",
        llm_rationale="内置兜底策略：趋势确认为主，MACD 与量能确认辅助",
    )
