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

# logic_json 统一格式提示：解析/校验失败时一次性给出，防止模型逐字段试错烧轮
LOGIC_JSON_HINT = (
    'logic_json 形状: {"name": "...", "factors": [{"factor_id": "ma_trend", "params": {"fast": 5, "slow": 20}, '
    '"weight": 1.0, "direction": 1}], "entry_threshold": 0.6}。direction 只能是 1（正向）或 -1（反向），'
    "weight 为正数，factor_id 须来自 list_factors。请按此一次性修正后重试（最多 1 次），不要逐字段试错。"
)

_DIRECTION_POSITIVE = {"1", "1.0", "+1", "正向", "做多", "多", "long", "+"}
_DIRECTION_NEGATIVE = {"-1", "-1.0", "反向", "做空", "空", "short", "-"}


def _norm_direction(raw):
    """→ (值 | None, 自动修正说明 | None, 硬错误 | None)。

    数值型非 ±1（如 5）原样放行给 validate()——保持既有校验错误清单行为；
    非法字符串/None/复合类型才是硬错误。
    """
    if isinstance(raw, bool):
        val = 1 if raw else -1
        return val, f"direction: {raw} → {val}", None
    if isinstance(raw, int):
        return raw, None, None  # ±1 或越界数值都放行给 validate 判（保持既有错误清单）
    if isinstance(raw, float):
        if raw.is_integer() and int(raw) in (1, -1):
            return int(raw), f"direction: {raw} → {int(raw)}", None
        return raw, None, None  # 同上：越界数值交给 validate
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in _DIRECTION_POSITIVE:
            return 1, f'direction: "{raw}" → 1', None
        if s in _DIRECTION_NEGATIVE:
            return -1, f'direction: "{raw}" → -1', None
        return None, None, f"direction 非法: {raw!r}（只能是 1 或 -1）"
    return None, None, f"direction 非法: {raw!r}（只能是 1 或 -1）"


def _norm_weight(raw):
    if isinstance(raw, bool):
        return None, None, f"weight 非法: {raw!r}（必须是正数）"
    if isinstance(raw, (int, float)):
        return float(raw), None, None
    if isinstance(raw, str):
        try:
            return float(raw.strip()), f'weight: "{raw}" → {float(raw.strip())}', None
        except ValueError:
            return None, None, f"weight 非法: {raw!r}（必须是正数）"
    return None, None, f"weight 非法: {raw!r}（必须是正数）"


def normalize_logic_dict(logic) -> tuple:
    """归一化 logic_json（容错修复常见格式错误）→ (normalized, auto_fixed, hard_errors)。

    - 自动修正（进 auto_fixed，透明可查）：direction "1"/1.0/True/"正向"→1（负向同理）；
      weight 数字串→float；单 dict factors→[dict]；params 非 dict→{}；缺 name 补默认。
    - 硬错误（进 hard_errors，一次性列全）：缺 factor_id、direction/weight 不可修复、factors 形状非法。
    - 数值型非法 direction（如 5）与越界数值原样放行给 StrategySpec.validate()（保持既有错误清单）。
    """
    auto_fixed: list = []
    hard_errors: list = []
    if not isinstance(logic, dict):
        return {}, auto_fixed, [f"logic_json 必须是 JSON 对象，当前: {type(logic).__name__}"]

    out: dict = {}
    out["name"] = logic.get("name") or "未命名策略"
    out["entry_threshold"] = logic.get("entry_threshold", 0.6)
    out["exit_preset"] = logic.get("exit_preset", "balanced")
    out["exit_params"] = logic.get("exit_params") if isinstance(logic.get("exit_params"), dict) else {}
    out["llm_rationale"] = logic.get("llm_rationale") or ""

    factors = logic.get("factors", [])
    if isinstance(factors, dict):
        factors = [factors]
        auto_fixed.append("factors: 单对象已包装为列表")
    if not isinstance(factors, list):
        hard_errors.append(f"factors 必须是列表，当前: {type(factors).__name__}")
        factors = []

    norm_factors = []
    for i, f in enumerate(factors):
        if not isinstance(f, dict):
            hard_errors.append(f"factors[{i}] 必须是 JSON 对象")
            continue
        fid = f.get("factor_id")
        if not fid or not isinstance(fid, str) or not fid.strip():
            hard_errors.append(f"factors[{i}] 缺少 factor_id")
            continue
        nf = {"factor_id": fid.strip()}
        params = f.get("params")
        if params is None:
            nf["params"] = {}
        elif isinstance(params, dict):
            nf["params"] = params
        else:
            nf["params"] = {}
            auto_fixed.append(f"factors[{i}].params 非对象，已置空")
        if "weight" in f:
            w, fixed, err = _norm_weight(f.get("weight"))
            if err:
                hard_errors.append(f"factors[{i}].{err}")
            else:
                nf["weight"] = w
                if fixed:
                    auto_fixed.append(f"factors[{i}].{fixed}")
        if "direction" in f:
            d, fixed, err = _norm_direction(f.get("direction"))
            if err:
                hard_errors.append(f"factors[{i}].{err}")
            else:
                nf["direction"] = d
                if fixed:
                    auto_fixed.append(f"factors[{i}].{fixed}")
        norm_factors.append(nf)
    out["factors"] = norm_factors
    return out, auto_fixed, hard_errors


# 跨参数语义约束（按参数名实际含义，作用于同一因子内合并 default 后的生效值）：
# "lt" = 必须严格小于（快/慢、短/长、区间上下界相等即语义倒置，如空强势区/自比较）；
# "le" = 必须小于等于（min/max 范围端点，相等合法，仅 min>max 才是倒置）。
# 只拒绝真正倒置的组合，不收缩合法搜索空间。
_CROSS_PARAM_RULES: tuple[tuple[str, str, str], ...] = (
    ("fast", "slow", "lt"),
    ("short", "long", "lt"),
    ("short_n", "long_n", "lt"),
    ("low", "high", "lt"),
    ("min_gap", "max_gap", "le"),
)


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
        has_positive_factor = False
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
            if f.direction == 1 and f.weight > 0:
                has_positive_factor = True
            total_weight += f.weight
            for pname, pvalue in f.params.items():
                if pname not in factor.params:
                    errors.append(f"因子 {f.factor_id} 无参数 {pname}")
                    continue
                fp = factor.params[pname]
                if not (fp.min - 1e-9 <= float(pvalue) <= fp.max + 1e-9):
                    errors.append(f"因子 {f.factor_id} 参数 {pname}={pvalue} 越界 [{fp.min}, {fp.max}]")
            # 跨参数语义约束：用合并 default 后的生效参数值判断，
            # 拒绝 fast>=slow、short>=long、low>=high 等语义倒置组合
            effective = {k: p.default for k, p in factor.params.items()}
            effective.update({k: v for k, v in f.params.items() if k in factor.params})
            for a_name, b_name, rel in _CROSS_PARAM_RULES:
                if a_name not in effective or b_name not in effective:
                    continue
                try:
                    a_val, b_val = float(effective[a_name]), float(effective[b_name])
                except (TypeError, ValueError):
                    continue  # 非数值由范围校验负责报错
                inverted = a_val >= b_val if rel == "lt" else a_val > b_val
                if inverted:
                    operator = "小于" if rel == "lt" else "小于等于"
                    errors.append(f"因子 {f.factor_id} 参数语义倒置: {a_name}={a_val} 必须{operator} {b_name}={b_val}")
        if self.factors and total_weight <= 0:
            errors.append("因子权重总和必须大于 0")
        if self.factors and not has_positive_factor:
            errors.append("策略至少需要一个 direction=1 的正向因子（反向因子仅作扣分/防守项，不能单独产生买入信号）")
        if not (0.0 < self.entry_threshold <= 1.0):
            errors.append(f"entry_threshold 必须在 (0, 1] 区间，当前: {self.entry_threshold}")
        if "partial_exit_ratio" in self.exit_params:
            try:
                r = float(self.exit_params["partial_exit_ratio"])
            except (TypeError, ValueError):
                errors.append(
                    f"exit_params.partial_exit_ratio 必须是数值，当前: {self.exit_params['partial_exit_ratio']!r}"
                )
            else:
                if not (0.0 < r <= 1.0):
                    errors.append(f"exit_params.partial_exit_ratio 必须在 (0, 1] 区间（小数，0.5=50%），当前: {r}")
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
    # 归一化分母只取正向因子权重：反向因子作扣分/防守项，不稀释最大可达得分，
    # 保证 entry_threshold 在 (0,1] 全区间可达（纯 -1 组合已在 validate 阶段拒绝）
    pos_weight = sum(f.weight for f in spec.factors if f.direction == 1)
    if pos_weight <= 0:  # 防御：validate 已保证至少一个正向因子
        pos_weight = 1.0

    def signal_func(df: pd.DataFrame) -> pd.Series:
        score = pd.Series(0.0, index=df.index)
        for factor, fspec in factors:
            s = factor.score(df, fspec.params)
            score = score + s * (fspec.weight * fspec.direction)
        normalized = (score / pos_weight).clip(0.0, 1.0)
        return pd.Series(np.where(normalized >= spec.entry_threshold, 1, 0), index=df.index, dtype=int)

    return signal_func


def factor_scores_detail(spec: StrategySpec, df: pd.DataFrame) -> dict:
    """计算最新 bar 的各因子得分明细（供 evaluate_spec 使用）"""
    total_weight = sum(f.weight for f in spec.factors if f.direction == 1) or 1.0
    details = []
    total = 0.0
    for fspec in spec.factors:
        factor = get_factor(fspec.factor_id)
        s = factor.score(df, fspec.params)
        latest = float(s.iloc[-1]) if len(s) else 0.0
        weighted = latest * fspec.weight * fspec.direction
        total += weighted
        details.append(
            {
                "factor_id": fspec.factor_id,
                "factor_name": factor.name,
                "score": round(latest, 4),
                "weight": fspec.weight,
                "direction": fspec.direction,
                "weighted_score": round(weighted, 4),
            }
        )
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
            FactorSpec(factor_id="vol_breakout", params={"vol_ratio": 2.0, "pct": 3.0}, weight=0.6),
        ],
        entry_threshold=0.6,
        exit_preset="balanced",
        llm_rationale="内置兜底策略：趋势确认为主，MACD 与量能确认辅助",
    )
