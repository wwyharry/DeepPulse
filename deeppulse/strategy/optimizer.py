"""自动策略优化循环 - LLM 动态调优

优化流程：
  前3轮：基础训练（回测 + 简单调参）
  第4-N轮：LLM动态调优（回测 + LLM分析 + 参数优化）
  最终：LLM综合分析所有轮次，选出最优策略 + 原因

设计理念：
1. 前3轮快速探索参数空间，建立基线
2. 第4轮起LLM介入，基于历史数据智能调优
3. 最终由LLM综合评判，而非单纯看评分
"""

from dataclasses import dataclass, field

from deeppulse.strategy.backtest import backtest_stock
from deeppulse.strategy.factor_library import get_factor
from deeppulse.strategy.llm_generator import LLMStrategyGenerator, apply_actions
from deeppulse.strategy.models import BacktestResult
from deeppulse.strategy.spec import StrategySpec, compile_signal_func, default_spec

# 基础训练轮数：前N轮不使用LLM调优（动态计算，最少1轮）
# 收敛阈值：综合评分提升小于该比例视为无提升
CONVERGENCE_TOLERANCE = 0.005
# 滑动窗口大小：用于检测趋势
TREND_WINDOW = 3
# 每轮回测次数上限：避免参数组合爆炸
MAX_BACKTESTS_PER_ROUND = 50
# 负增长容忍度：允许的最大回撤比例（相对于历史最佳）
MAX_REGRESSION_TOLERANCE = 0.05


@dataclass
class OptimizeReport:
    """自动优化结果报告"""

    best_spec: StrategySpec
    best_result: dict
    best_score: float | None  # None = 从未回测成功，不是 0.0
    history: list[dict] = field(default_factory=list)
    converged: bool = False
    code: str = ""
    days: int = 750
    llm_final_reasoning: str = ""  # LLM 最终选择原因
    untested: bool = False  # True = best_spec 为未验证的兜底默认策略

    def to_dict(self) -> dict:
        d = {
            "code": self.code,
            "days": self.days,
            "best_spec": self.best_spec.to_dict(),
            "best_result": self.best_result,
            "best_score": round(self.best_score, 4) if self.best_score is not None else None,
            "untested": self.untested,
            "converged": self.converged,
            "iterations": len(self.history),
            "history": self.history,
            "llm_final_reasoning": self.llm_final_reasoning,
        }
        if self.untested:
            d["note"] = "优化失败，返回未验证的默认策略（best_score 非实测成绩）"
        return d


def compute_score(result: BacktestResult) -> float:
    """综合评分：收益×0.4 + 夏普×0.03 + 胜率×0.2 − 回撤×0.1"""
    return result.total_return * 0.4 + result.sharpe_ratio * 0.03 + result.win_rate * 0.2 - result.max_drawdown * 0.1


class AutoOptimizer:
    """LLM + 网格搜索的自动策略优化器"""

    def __init__(self, llm_client=None):
        self.generator = LLMStrategyGenerator(llm_client=llm_client)

    def _run_backtest(self, code: str, spec: StrategySpec, days: int) -> tuple[BacktestResult, float]:
        """回测一个策略规格，返回 (结果, 综合评分)"""
        signal_func = compile_signal_func(spec)
        result = backtest_stock(
            code,
            signal_func=signal_func,
            days=days,
            exit_strategy_preset=spec.exit_preset,
            exit_strategy_params=spec.exit_params or None,
        )
        result.strategy_name = spec.name
        return result, compute_score(result)

    def _grid_search_params(
        self, code: str, spec: StrategySpec, days: int, base_score: float
    ) -> tuple[StrategySpec, BacktestResult | None, float]:
        """单参数扰动式网格搜索：一次只动一个参数，贪心接受更优解

        回测次数上限 MAX_BACKTESTS_PER_ROUND，避免组合爆炸。
        """
        best_spec = StrategySpec.from_dict(spec.to_dict())
        best_result: BacktestResult | None = None
        best_score = base_score
        budget = MAX_BACKTESTS_PER_ROUND

        for factor_idx, fspec in enumerate(best_spec.factors):
            try:
                factor = get_factor(fspec.factor_id)
            except KeyError:
                continue
            for pname, fparam in factor.params.items():
                current = float(fspec.params.get(pname, fparam.default))
                candidates = [v for v in fparam.grid() if abs(v - current) > 1e-9]
                for value in candidates:
                    if budget <= 0:
                        return best_spec, best_result, best_score
                    trial = StrategySpec.from_dict(best_spec.to_dict())
                    trial.factors[factor_idx].params[pname] = value
                    if trial.validate():
                        continue
                    budget -= 1
                    try:
                        result, score = self._run_backtest(code, trial, days)
                    except Exception:
                        continue
                    if score > best_score:
                        best_spec, best_result, best_score = trial, result, score
        return best_spec, best_result, best_score

    def optimize(
        self,
        code: str,
        days: int = 750,
        max_iterations: int = 7,
        hint: str | None = None,
        kline_stats: dict | None = None,
    ) -> OptimizeReport:
        """自动优化主循环

        流程：
        1. 首轮由 LLM 生成初始策略（失败则用 default_spec() 兜底）
        2. 前N轮：基础训练，回测 + 简单参数微调
        3. 后续轮次：LLM 动态调优，回测 + LLM 分析 + 参数优化
        4. 最终：LLM 综合分析所有轮次，选出最优策略 + 原因

        Args:
            code: 股票代码
            days: 回测天数
            max_iterations: 最大迭代轮数（用户自定义，1-20）
            hint: 用户偏好提示
            kline_stats: K线统计信息
        """
        if not (1 <= max_iterations <= 20):
            raise ValueError(f"max_iterations 必须在 1-20 之间，收到: {max_iterations}")

        # 动态计算基础训练轮数和最小迭代轮数
        # 基础训练：总轮数的40%（至少1轮，最多3轮）
        base_training_rounds = max(1, min(3, max_iterations * 2 // 5))
        # 最小迭代：总轮数的60%（至少2轮，不超过max_iterations）
        min_iterations = min(max_iterations, max(2, max_iterations * 3 // 5))

        # ---- 初始策略 ----
        spec = default_spec()
        if kline_stats is not None:
            try:
                spec = self.generator.generate(code, kline_stats, hint=hint)
            except Exception:
                pass  # LLM 失败 → 兜底默认策略

        history: list[dict] = []
        best_spec: StrategySpec | None = None
        best_result: BacktestResult | None = None
        best_score = float("-inf")
        score_history: list[float] = []
        converged = False
        llm_final_reasoning = ""

        for iteration in range(1, max_iterations + 1):
            # ============================================================
            #  a) 回测当前策略
            # ============================================================
            try:
                result, score = self._run_backtest(code, spec, days)
            except Exception as e:
                history.append(
                    {
                        "iteration": iteration,
                        "spec": spec.to_dict(),
                        "score": None,
                        "metrics": {},
                        "llm_comment": f"回测失败: {e}",
                        "phase": "base" if iteration <= base_training_rounds else "llm",
                    }
                )
                break

            # ============================================================
            #  b) 根据阶段选择调优方式
            # ============================================================
            phase = "base" if iteration <= base_training_rounds else "llm"
            llm_comment = ""
            next_spec = spec

            if phase == "base":
                # ---- 基础训练阶段：简单参数微调 ----
                tuned_spec, tuned_result, tuned_score = self._grid_search_params(code, spec, days, score)
                if tuned_result is not None and tuned_score > score:
                    spec, result, score = tuned_spec, tuned_result, tuned_score
                    llm_comment = f"基础调参：评分提升至 {score:.4f}"
                else:
                    llm_comment = "基础调参：无提升"
                next_spec = spec  # 基础阶段保持当前结构

            else:
                # ---- LLM 动态调优阶段 ----
                try:
                    analysis = self.generator.analyze_backtest(spec, result.to_dict(), history)
                    if analysis.get("comment"):
                        llm_comment = analysis["comment"]
                    next_spec = apply_actions(spec, analysis.get("actions", []))

                    # LLM 调优后回测验证
                    try:
                        new_result, new_score = self._run_backtest(code, next_spec, days)
                        if new_score > score:
                            spec, result, score = next_spec, new_result, new_score
                            llm_comment += f" [调优成功] 评分提升至 {score:.4f}"
                        else:
                            llm_comment += " [调优无效] 保持当前策略"
                            next_spec = spec
                    except Exception:
                        llm_comment += " [调优回测失败] 保持当前策略"
                        next_spec = spec

                except Exception as e:
                    llm_comment = f"LLM 分析失败: {e}"
                    next_spec = spec

            # ============================================================
            #  c) 负增长检测与回滚
            # ============================================================
            rolled_back = False
            if best_score != float("-inf") and score < best_score:
                denom = abs(best_score) if best_score != 0 else 1.0
                regression = (best_score - score) / denom
                if regression > MAX_REGRESSION_TOLERANCE:
                    llm_comment += f" [回滚] 得分下降 {regression:.1%}，回滚至最佳策略"
                    spec = best_spec
                    result = best_result
                    score = best_score
                    rolled_back = True

            # ============================================================
            #  d) 记录历史
            # ============================================================
            history.append(
                {
                    "iteration": iteration,
                    "phase": phase,
                    "spec": spec.to_dict(),
                    "score": round(score, 4),
                    "metrics": {
                        "total_return": result.total_return,
                        "annual_return": result.annual_return,
                        "max_drawdown": result.max_drawdown,
                        "win_rate": result.win_rate,
                        "sharpe_ratio": result.sharpe_ratio,
                        "total_trades": result.total_trades,
                    },
                    "llm_comment": llm_comment,
                    "rolled_back": rolled_back,
                }
            )

            # 更新最佳策略
            if score > best_score:
                best_spec, best_result, best_score = spec, result, score

            # ============================================================
            #  e) 收敛判断
            # ============================================================
            score_history.append(score)

            if len(score_history) >= TREND_WINDOW:
                recent_avg = sum(score_history[-TREND_WINDOW:]) / TREND_WINDOW
                if len(score_history) > TREND_WINDOW:
                    prev_avg = sum(score_history[-(TREND_WINDOW + 1) : -1]) / TREND_WINDOW
                    if prev_avg == 0:
                        # 从 0 起跳视为无穷大改善（否则大提升会被误判为已收敛）
                        improvement = float("inf") if recent_avg > 0 else 0.0
                    else:
                        improvement = (recent_avg - prev_avg) / abs(prev_avg)
                else:
                    improvement = float("inf")

                # 至少执行 min_iterations 轮，且最近 TREND_WINDOW 轮平均得分无提升
                if iteration >= min_iterations and improvement < CONVERGENCE_TOLERANCE:
                    converged = True

            if converged:
                break

            if not rolled_back:
                spec = next_spec

        # ============================================================
        #  最终：LLM 综合分析，选出最优策略
        # ============================================================
        if best_spec is None or best_result is None:
            # 全部轮回失败：返回未验证的兜底默认策略，不伪造 0.0 实测成绩
            best_spec = default_spec()
            best_result_dict: dict = {}
            best_score = None
            untested = True
        else:
            best_result_dict = best_result.to_dict()
            untested = False

        # LLM 综合分析（如果有足够历史）
        if len(history) >= 2:
            try:
                final_analysis = self.generator.analyze_final_strategy(
                    code, history, best_spec.to_dict(), best_result_dict
                )
                llm_final_reasoning = final_analysis.get("reasoning", "")
                # 如果 LLM 推荐了不同的策略，更新 best_spec
                if final_analysis.get("recommended_spec"):
                    recommended_spec = StrategySpec.from_dict(final_analysis["recommended_spec"])
                    try:
                        rec_result, rec_score = self._run_backtest(code, recommended_spec, days)
                        if best_score is None or rec_score > best_score:
                            best_spec = recommended_spec
                            best_result_dict = rec_result.to_dict()
                            best_score = rec_score
                            untested = False
                            llm_final_reasoning += f"\n\n[最终优化] LLM 推荐策略评分更高: {rec_score:.4f}"
                    except Exception:
                        pass
            except Exception as e:
                llm_final_reasoning = f"最终分析失败: {e}"

        return OptimizeReport(
            best_spec=best_spec,
            best_result=best_result_dict,
            best_score=None if best_score == float("-inf") else best_score,
            history=history,
            converged=converged,
            code=code,
            days=days,
            llm_final_reasoning=llm_final_reasoning,
            untested=untested,
        )
