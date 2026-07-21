"""WebSocket 策略处理 — 流式输出 AI 策略生成和分析进度"""

import asyncio
import json
import time
import traceback
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


def _now_ms() -> int:
    return int(time.time() * 1000)


class StreamBuffer:
    """流式输出缓冲区 — 捕获 LLM 输出并实时发送"""

    def __init__(self, websocket: WebSocket, step: str, iteration: int = 0):
        self.websocket = websocket
        self.step = step
        self.iteration = iteration
        self.buffer = ""
        self._lock = asyncio.Lock()

    async def write(self, text: str):
        """写入文本并立即发送"""
        async with self._lock:
            self.buffer += text
            await self.websocket.send_json({
                "type": "stream",
                "step": self.step,
                "iteration": self.iteration,
                "text": text,
                "ts": _now_ms(),
            })

    async def flush(self):
        """刷新缓冲区"""
        pass  # 已经实时发送了


# ============================================================
#  策略优化 — 流式进度
# ============================================================

@router.websocket("/strategy/optimize")
async def strategy_optimize_ws(websocket: WebSocket):
    """WebSocket 策略优化端点 — 流式输出优化进度

    通过 URL query 参数传入:
        ws://host/ws/strategy/optimize?code=000001&days=750&max_iterations=5&hint=偏好短线

    Server → Client 事件协议:
        {"type":"start","code":"000001","stock_name":"平安银行","max_iterations":5,"ts":...}
        {"type":"step","step":"load_data","status":"running","message":"...","ts":...}
        {"type":"step","step":"load_data","status":"done","message":"...","elapsed_ms":312,"ts":...}
        {"type":"step","step":"llm_generate","status":"running","ts":...}
        {"type":"substep","step":"llm_generate","sub":"构建 prompt","ts":...}
        {"type":"substep","step":"llm_generate","sub":"调用 LLM","ts":...}
        {"type":"step","step":"llm_generate","status":"done","message":"策略: xxx","elapsed_ms":2300,"ts":...}
        {"type":"step","step":"backtest","iteration":1,"status":"running","ts":...}
        {"type":"substep","step":"backtest","sub":"编译信号函数","ts":...}
        {"type":"substep","step":"backtest","sub":"执行回测 750 天","ts":...}
        {"type":"substep","step":"backtest","sub":"计算评分","ts":...}
        {"type":"step","step":"backtest","status":"done","message":"评分 0.4567","elapsed_ms":1800,"ts":...}
        {"type":"metrics","iteration":1,"score":0.4567,"total_return":0.123,"win_rate":0.65,"max_drawdown":-0.05,"sharpe":1.23,"trades":15,"ts":...}
        {"type":"step","step":"grid_search","iteration":1,"status":"running","ts":...}
        {"type":"tune","param":"entry_threshold","old":0.6,"new":0.55,"ts":...}
        {"type":"tune","param":"exit_preset","old":"balanced","new":"aggressive","ts":...}
        {"type":"step","step":"grid_search","status":"done","message":"评分提升至 0.5123","elapsed_ms":900,"ts":...}
        {"type":"step","step":"llm_analyze","iteration":1,"status":"running","ts":...}
        {"type":"llm_token","step":"llm_analyze","text":"建议增加RSI权重","ts":...}
        {"type":"step","step":"llm_analyze","status":"done","elapsed_ms":3100,"ts":...}
        {"type":"iteration_done","iteration":1,"max_iterations":5,"score":0.5123,"improvement":0.0556,"comment":"...","ts":...}
        {"type":"best","score":0.5123,"spec":{...},"ts":...}
        {"type":"done","best_spec":{...},"best_result":{...},"converged":false,"total_iterations":3,"ts":...}
        {"type":"error","message":"...","ts":...}
    """
    code = websocket.query_params.get("code", "")
    days = int(websocket.query_params.get("days", "750"))
    max_iterations = int(websocket.query_params.get("max_iterations", "5"))
    hint = websocket.query_params.get("hint") or None

    await websocket.accept()

    try:
        import re
        if not re.match(r"^\d{6}$", code):
            await _ws_send(websocket, "error", message="请输入6位股票代码")
            return

        stock_name = _get_stock_name(code)
        await _ws_send(websocket, "start", code=code, stock_name=stock_name,
                       max_iterations=max_iterations, days=days)

        # ---- 加载数据 ----
        # 总步骤：load_data(1) + llm_generate(1) + 每轮(backtest + grid_search + llm_analyze) * max_iterations
        total_steps = 2 + max_iterations * 3
        t0 = _now_ms()
        await _ws_send(websocket, "step", step="load_data", status="running",
                       message="加载K线数据和技术指标...",
                       progress={"current": 0, "total": total_steps})
        from deeppulse.strategy.llm_generator import build_kline_stats
        from web.app.api.strategy_llm import _load_kline_with_indicators

        df = _load_kline_with_indicators(code, days)
        kline_stats = build_kline_stats(df)
        await _ws_send(websocket, "step", step="load_data", status="done",
                       message=f"加载完成，共 {len(df)} 条K线数据",
                       elapsed_ms=_now_ms() - t0,
                       progress={"current": 1, "total": total_steps})

        # ---- 运行优化 ----
        await _run_optimize_stream(websocket, code, days, max_iterations, hint, kline_stats)

    except WebSocketDisconnect:
        pass
    except json.JSONDecodeError:
        try:
            await _ws_send(websocket, "error", message="无效的 JSON 格式")
        except Exception:
            pass
    except Exception as e:
        try:
            await _ws_send(websocket, "error", message=str(e))
        except Exception:
            pass


async def _run_optimize_stream(
    websocket: WebSocket,
    code: str,
    days: int,
    max_iterations: int,
    hint: str | None,
    kline_stats: dict,
):
    """流式运行优化循环 — LLM 动态调优

    优化流程：
    1. 前3轮：基础训练（回测 + 简单调参）
    2. 第4-N轮：LLM 动态调优（回测 + LLM 分析 + 参数优化）
    3. 最终：LLM 综合分析所有轮次，选出最优策略 + 原因

    流式输出：
    - 每个步骤都有实时状态更新
    - LLM 输出通过 stream 事件逐 token 发送
    """
    from deeppulse.strategy.backtest import backtest_stock
    from deeppulse.strategy.llm_generator import apply_actions
    from deeppulse.strategy.optimizer import (
        AutoOptimizer,
        BASE_TRAINING_ROUNDS,
        CONVERGENCE_TOLERANCE,
        MAX_REGRESSION_TOLERANCE,
        MIN_ITERATIONS,
        TREND_WINDOW,
        compute_score,
    )
    from deeppulse.strategy.spec import compile_signal_func, default_spec

    optimizer = AutoOptimizer()
    generator = optimizer.generator

    # 创建流式输出缓冲区
    stream_buf = StreamBuffer(websocket, "llm")

    # 进度追踪
    total_steps = 2 + max_iterations * 2 + 1
    current_step = 1
    start_time = _now_ms()

    # ---- 初始策略生成 ----
    t0 = _now_ms()
    await _ws_send(websocket, "step", step="llm_generate", status="running",
                   message="LLM 生成初始策略...",
                   progress={"current": current_step, "total": total_steps})

    spec = default_spec()
    init_comment = "使用内置默认策略"
    try:
        await _ws_send(websocket, "log", step="llm_generate",
                       message=">>> 构建 prompt，分析 K线数据...")
        spec = generator.generate(code, kline_stats, hint=hint)
        init_comment = spec.llm_rationale or "LLM 生成初始策略"
        current_step += 1
        await _ws_send(websocket, "log", step="llm_generate",
                       message=f">>> 生成策略: {spec.name}")
        await _ws_send(websocket, "log", step="llm_generate",
                       message=f">>> 因子: {', '.join(f.factor_id for f in spec.factors)}")
        await _ws_send(websocket, "log", step="llm_generate",
                       message=f">>> 入场阈值: {spec.entry_threshold}")
        await _ws_send(websocket, "step", step="llm_generate", status="done",
                       message=f"策略: {spec.name}", elapsed_ms=_now_ms() - t0,
                       detail={"name": spec.name, "factors": len(spec.factors),
                               "threshold": spec.entry_threshold},
                       progress={"current": current_step, "total": total_steps})
    except Exception as e:
        current_step += 1
        await _ws_send(websocket, "log", step="llm_generate",
                       message=f">>> LLM 生成失败: {e}")
        await _ws_send(websocket, "log", step="llm_generate",
                       message=">>> 使用默认策略兜底")
        await _ws_send(websocket, "step", step="llm_generate", status="warning",
                       message=f"LLM 生成失败，使用默认策略: {e}",
                       elapsed_ms=_now_ms() - t0,
                       progress={"current": current_step, "total": total_steps})

    history = []
    best_spec = None
    best_result = None
    best_result_dict = {}
    best_score = float("-inf")
    score_history = []
    converged = False
    llm_final_reasoning = ""

    for iteration in range(1, max_iterations + 1):
        # ============================================================
        #  a) 回测
        # ============================================================
        current_step += 1
        phase = "base" if iteration <= BASE_TRAINING_ROUNDS else "llm"
        phase_label = "基础训练" if phase == "base" else "LLM调优"

        await _ws_send(websocket, "log", step="backtest",
                       message=f"\n{'='*60}")
        await _ws_send(websocket, "log", step="backtest",
                       message=f"第 {iteration} 轮 [{phase_label}]")
        await _ws_send(websocket, "log", step="backtest",
                       message=f"{'='*60}")

        t_bt = _now_ms()
        await _ws_send(websocket, "step", step="backtest", iteration=iteration,
                       status="running",
                       message=f"第 {iteration} 轮 [{phase_label}]：回测 {spec.name}",
                       progress={"current": current_step, "total": total_steps,
                                 "elapsed_ms": _now_ms() - start_time})

        try:
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 策略: {spec.name}")
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 因子: {', '.join(f.factor_id for f in spec.factors)}")
            signal_func = compile_signal_func(spec)

            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 执行回测 ({days} 天)...")
            result = backtest_stock(
                code, signal_func=signal_func, days=days,
                exit_strategy_preset=spec.exit_preset,
                exit_strategy_params=spec.exit_params or None,
            )
            result.strategy_name = spec.name

            score = compute_score(result)
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 回测完成!")
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 综合评分: {score:.4f}")
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 总收益: {result.total_return:.2%}")
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 年化收益: {result.annual_return:.2%}")
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 胜率: {result.win_rate:.2%}")
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 最大回撤: {result.max_drawdown:.2%}")
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 夏普比率: {result.sharpe_ratio:.2f}")
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 交易次数: {result.total_trades}")

            await _ws_send(websocket, "step", step="backtest", iteration=iteration,
                           status="done", message=f"评分 {score:.4f}",
                           elapsed_ms=_now_ms() - t_bt)
        except Exception as e:
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 回测失败: {e}")
            await _ws_send(websocket, "step", step="backtest", iteration=iteration,
                           status="error", message=f"回测失败: {e}",
                           elapsed_ms=_now_ms() - t_bt)
            break

        # 发送实时指标
        await _ws_send(websocket, "metrics", iteration=iteration,
                       score=round(score, 4),
                       total_return=round(result.total_return, 4),
                       annual_return=round(result.annual_return, 4),
                       win_rate=round(result.win_rate, 4),
                       max_drawdown=round(result.max_drawdown, 4),
                       sharpe=round(result.sharpe_ratio, 4),
                       trades=result.total_trades,
                       final_capital=round(result.final_capital, 2),
                       phase=phase,
                       progress={"current": current_step, "total": total_steps})

        # ============================================================
        #  b) 根据阶段选择调优方式
        # ============================================================
        current_step += 1
        llm_comment = init_comment if iteration == 1 else ""
        next_spec = spec

        if phase == "base":
            # ---- 基础训练阶段：简单参数微调 ----
            t_tune = _now_ms()
            await _ws_send(websocket, "log", step="tune",
                           message=f"\n--- 基础调参 ---")
            await _ws_send(websocket, "step", step="tune", iteration=iteration,
                           status="running", message=f"第 {iteration} 轮：基础调参...",
                           progress={"current": current_step, "total": total_steps})

            await _ws_send(websocket, "log", step="tune",
                           message=">>> 扫描参数空间...")
            tuned_spec, tuned_result, tuned_score = optimizer._grid_search_params(
                code, spec, days, score
            )

            if tuned_result is not None and tuned_score > score:
                # 发送参数调整详情
                await _ws_send(websocket, "log", step="tune",
                               message=f">>> 发现更优参数!")
                if tuned_spec.entry_threshold != spec.entry_threshold:
                    await _ws_send(websocket, "log", step="tune",
                                   message=f">>> 入场阈值: {spec.entry_threshold} -> {tuned_spec.entry_threshold}")
                    await _ws_send(websocket, "tune", param="entry_threshold",
                                   old=spec.entry_threshold, new=tuned_spec.entry_threshold)
                if tuned_spec.exit_preset != spec.exit_preset:
                    await _ws_send(websocket, "log", step="tune",
                                   message=f">>> 退出预设: {spec.exit_preset} -> {tuned_spec.exit_preset}")
                    await _ws_send(websocket, "tune", param="exit_preset",
                                   old=spec.exit_preset, new=tuned_spec.exit_preset)
                for i, (old_f, new_f) in enumerate(zip(spec.factors, tuned_spec.factors)):
                    if old_f.weight != new_f.weight:
                        await _ws_send(websocket, "log", step="tune",
                                       message=f">>> {old_f.factor_id} 权重: {old_f.weight} -> {new_f.weight}")
                        await _ws_send(websocket, "tune",
                                       param=f"{old_f.factor_id}.weight",
                                       old=old_f.weight, new=new_f.weight)

                spec, result, score = tuned_spec, tuned_result, tuned_score
                llm_comment = f"基础调参：评分提升至 {score:.4f}"
                await _ws_send(websocket, "log", step="tune",
                               message=f">>> 评分提升至 {score:.4f}")
                await _ws_send(websocket, "step", step="tune", iteration=iteration,
                               status="improved", message=f"评分提升至 {score:.4f}",
                               elapsed_ms=_now_ms() - t_tune)
            else:
                llm_comment = "基础调参：无提升"
                await _ws_send(websocket, "log", step="tune",
                               message=">>> 未找到更优参数")
                await _ws_send(websocket, "step", step="tune", iteration=iteration,
                               status="done", message="无提升",
                               elapsed_ms=_now_ms() - t_tune)

        else:
            # ---- LLM 动态调优阶段 ----
            t_llm = _now_ms()
            await _ws_send(websocket, "log", step="llm_tune",
                           message=f"\n--- LLM 动态调优 ---")
            await _ws_send(websocket, "step", step="llm_tune", iteration=iteration,
                           status="running", message=f"第 {iteration} 轮：LLM 动态调优...",
                           progress={"current": current_step, "total": total_steps})

            try:
                await _ws_send(websocket, "log", step="llm_tune",
                               message=">>> 分析历史数据，生成调优建议...")

                # 使用流式输出
                analysis = await _llm_analyze_with_stream(
                    websocket, generator, spec, result.to_dict(), history
                )

                if analysis.get("comment"):
                    llm_comment = analysis["comment"]
                    await _ws_send(websocket, "log", step="llm_tune",
                                   message=f">>> LLM 分析: {llm_comment[:200]}...")

                next_spec = apply_actions(spec, analysis.get("actions", []))

                await _ws_send(websocket, "log", step="llm_tune",
                               message=">>> 验证调优结果...")
                # LLM 调优后回测验证
                try:
                    new_signal_func = compile_signal_func(next_spec)
                    new_result = backtest_stock(
                        code, signal_func=new_signal_func, days=days,
                        exit_strategy_preset=next_spec.exit_preset,
                        exit_strategy_params=next_spec.exit_params or None,
                    )
                    new_score = compute_score(new_result)

                    if new_score > score:
                        spec, result, score = next_spec, new_result, new_score
                        llm_comment += f" [调优成功] 评分提升至 {score:.4f}"
                        await _ws_send(websocket, "log", step="llm_tune",
                                       message=f">>> 调优成功! 评分: {score:.4f}")
                        await _ws_send(websocket, "step", step="llm_tune", iteration=iteration,
                                       status="improved", message=f"LLM 调优成功，评分 {score:.4f}",
                                       elapsed_ms=_now_ms() - t_llm)
                    else:
                        llm_comment += f" [调优无效] 保持当前策略"
                        next_spec = spec
                        await _ws_send(websocket, "log", step="llm_tune",
                                       message=f">>> 调优无效，保持当前策略")
                        await _ws_send(websocket, "step", step="llm_tune", iteration=iteration,
                                       status="done", message="调优无效，保持当前",
                                       elapsed_ms=_now_ms() - t_llm)
                except Exception as e:
                    llm_comment += " [调优回测失败] 保持当前策略"
                    next_spec = spec
                    await _ws_send(websocket, "log", step="llm_tune",
                                   message=f">>> 调优回测失败: {e}")
                    await _ws_send(websocket, "step", step="llm_tune", iteration=iteration,
                                   status="warning", message="调优回测失败",
                                   elapsed_ms=_now_ms() - t_llm)

            except Exception as e:
                llm_comment = f"LLM 分析失败: {e}"
                next_spec = spec
                await _ws_send(websocket, "log", step="llm_tune",
                               message=f">>> LLM 分析失败: {e}")
                await _ws_send(websocket, "step", step="llm_tune", iteration=iteration,
                               status="warning", message=f"LLM 分析失败: {e}",
                               elapsed_ms=_now_ms() - t_llm)

        # ============================================================
        #  c) 负增长检测与回滚
        # ============================================================
        rolled_back = False
        if best_score != float("-inf") and score < best_score:
            regression = (best_score - score) / abs(best_score) if best_score != 0 else 0
            if regression > MAX_REGRESSION_TOLERANCE:
                llm_comment += f" [回滚] 得分下降 {regression:.1%}，回滚至最佳策略"
                spec = best_spec
                result = best_result
                score = best_score
                rolled_back = True
                await _ws_send(websocket, "log", step="backtest",
                               message=f">>> 得分下降 {regression:.1%}，回滚至最佳策略")
                await _ws_send(websocket, "rollback",
                               iteration=iteration,
                               message=f"得分下降 {regression:.1%}，回滚至最佳策略",
                               best_score=round(best_score, 4))

        # ============================================================
        #  d) 记录历史
        # ============================================================
        iteration_data = {
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
        history.append(iteration_data)

        # 更新最优
        if score > best_score:
            best_spec = spec
            best_result = result
            best_result_dict = result.to_dict()
            best_score = score
            await _ws_send(websocket, "log", step="backtest",
                           message=f">>> 新最优策略! 评分: {best_score:.4f}")
            await _ws_send(websocket, "best",
                           score=round(best_score, 4),
                           spec=best_spec.to_dict(),
                           metrics=iteration_data["metrics"])

        # 轮次完成
        await _ws_send(websocket, "iteration_done",
                       iteration=iteration, max_iterations=max_iterations,
                       score=round(score, 4),
                       comment=llm_comment, spec_name=spec.name,
                       rolled_back=rolled_back, phase=phase,
                       progress={"current": current_step, "total": total_steps})

        # ============================================================
        #  e) 收敛判断
        # ============================================================
        score_history.append(score)

        if len(score_history) >= TREND_WINDOW:
            recent_avg = sum(score_history[-TREND_WINDOW:]) / TREND_WINDOW
            if len(score_history) > TREND_WINDOW:
                prev_avg = sum(score_history[-(TREND_WINDOW+1):-1]) / TREND_WINDOW
                improvement = (recent_avg - prev_avg) / abs(prev_avg) if prev_avg != 0 else 0
            else:
                improvement = float("inf")

            if iteration >= MIN_ITERATIONS and improvement < CONVERGENCE_TOLERANCE:
                converged = True

        if converged:
            await _ws_send(websocket, "log", step="converged",
                           message=f"\n>>> 收敛! 最近 {TREND_WINDOW} 轮平均得分无提升")
            await _ws_send(websocket, "step", step="converged",
                           status="done",
                           message=f"最近 {TREND_WINDOW} 轮平均得分无提升，收敛退出")
            break

        if not rolled_back:
            spec = next_spec

    # ============================================================
    #  最终：LLM 综合分析
    # ============================================================
    current_step += 1
    if best_spec is None:
        best_spec = default_spec()
        best_score = 0.0

    # LLM 综合分析
    if len(history) >= 2:
        t_final = _now_ms()
        await _ws_send(websocket, "log", step="final_analysis",
                       message=f"\n{'='*60}")
        await _ws_send(websocket, "log", step="final_analysis",
                       message="LLM 综合分析所有轮次...")
        await _ws_send(websocket, "log", step="final_analysis",
                       message=f"{'='*60}")
        await _ws_send(websocket, "step", step="final_analysis", status="running",
                       message="LLM 综合分析所有轮次...",
                       progress={"current": current_step, "total": total_steps})

        try:
            # 使用流式输出
            final_analysis = await _llm_final_analysis_with_stream(
                websocket, generator, code, history, best_spec.to_dict(), best_result_dict
            )
            llm_final_reasoning = final_analysis.get("reasoning", "")

            # 如果 LLM 推荐了不同的策略
            if final_analysis.get("recommended_spec"):
                await _ws_send(websocket, "log", step="final_analysis",
                               message=">>> LLM 推荐了优化策略，验证中...")
                try:
                    from deeppulse.strategy.spec import StrategySpec
                    recommended_spec = StrategySpec.from_dict(final_analysis["recommended_spec"])
                    rec_signal_func = compile_signal_func(recommended_spec)
                    rec_result = backtest_stock(
                        code, signal_func=rec_signal_func, days=days,
                        exit_strategy_preset=recommended_spec.exit_preset,
                        exit_strategy_params=recommended_spec.exit_params or None,
                    )
                    rec_score = compute_score(rec_result)

                    if rec_score > best_score:
                        best_spec = recommended_spec
                        best_result_dict = rec_result.to_dict()
                        best_score = rec_score
                        llm_final_reasoning += f"\n\n[最终优化] LLM 推荐策略评分更高: {rec_score:.4f}"
                        await _ws_send(websocket, "log", step="final_analysis",
                                       message=f">>> 推荐策略更优! 评分: {rec_score:.4f}")
                    else:
                        await _ws_send(websocket, "log", step="final_analysis",
                                       message=f">>> 当前策略已是最优")
                except Exception as e:
                    await _ws_send(websocket, "log", step="final_analysis",
                                   message=f">>> 验证推荐策略失败: {e}")

            await _ws_send(websocket, "step", step="final_analysis", status="done",
                           message="综合分析完成",
                           elapsed_ms=_now_ms() - t_final)
        except Exception as e:
            llm_final_reasoning = f"最终分析失败: {e}"
            await _ws_send(websocket, "log", step="final_analysis",
                           message=f">>> 综合分析失败: {e}")
            await _ws_send(websocket, "step", step="final_analysis", status="warning",
                           message=f"综合分析失败: {e}",
                           elapsed_ms=_now_ms() - t_final)

    # 发送最终结果
    await _ws_send(websocket, "log", step="done",
                   message=f"\n{'='*60}")
    await _ws_send(websocket, "log", step="done",
                   message=f"优化完成! 最优评分: {best_score:.4f}")
    await _ws_send(websocket, "log", step="done",
                   message=f"{'='*60}")

    from deeppulse.strategy.optimizer import OptimizeReport
    report = OptimizeReport(
        best_spec=best_spec,
        best_result=best_result_dict,
        best_score=best_score if best_score != float("-inf") else 0.0,
        history=history,
        converged=converged,
        code=code,
        days=days,
        llm_final_reasoning=llm_final_reasoning,
    )
    result_dict = report.to_dict()
    await _ws_send(websocket, "done",
                   best_spec=best_spec.to_dict(),
                   best_result=best_result_dict,
                   converged=report.converged,
                   total_iterations=len(history),
                   llm_final_reasoning=llm_final_reasoning,
                   result=result_dict,
                   progress={"current": total_steps, "total": total_steps})


async def _llm_analyze_with_stream(
    websocket: WebSocket,
    generator: Any,
    spec: Any,
    result: dict,
    history: list,
) -> dict:
    """带流式输出的 LLM 分析"""
    import json as _json
    import re

    try:
        from deeppulse.agent.client import LLMClient, load_setting
        client = LLMClient(load_setting())
    except Exception as e:
        return {"comment": f"LLM 客户端初始化失败: {e}", "actions": []}

    metrics = {
        k: result.get(k)
        for k in ["total_return", "annual_return", "max_drawdown", "win_rate",
                  "sharpe_ratio", "profit_factor", "total_trades", "avg_hold_days"]
    }
    history_brief = [
        {"iteration": h.get("iteration"), "score": h.get("score"),
         "total_return": (h.get("metrics") or {}).get("total_return")}
        for h in history[-5:]
    ]

    from deeppulse.strategy.llm_generator import _factor_catalog_prompt

    prompt = f"""你是一位资深量化策略调优专家。以下是一个多因子策略的回测结果，请分析并给出调整动作。

【当前策略】
{spec.to_json()}

【回测指标】
{_json.dumps(metrics, ensure_ascii=False, indent=2)}

【退出统计】
{_json.dumps(result.get("exit_statistics", {}), ensure_ascii=False)}

【近期迭代历史】
{_json.dumps(history_brief, ensure_ascii=False)}

【可用因子库】
{_factor_catalog_prompt()}

请判断问题所在（如交易过少=阈值过高、回撤过大=缺少防守因子、胜率低=因子冲突等），
严格只输出如下 JSON：
{{
  "comment": "一段中文分析评语",
  "actions": [
    {{"type": "adjust_param", "factor_id": "因子id", "param": "参数名", "value": 数值}},
    {{"type": "adjust_weight", "factor_id": "因子id", "weight": 数值}},
    {{"type": "adjust_threshold", "value": 0.3到0.8之间的数值}},
    {{"type": "add_factor", "factor_id": "因子id", "params": {{}}, "weight": 1.0, "direction": 1}},
    {{"type": "remove_factor", "factor_id": "因子id"}}
  ]
}}
actions 可以为空数组（表示当前结构合理，仅依赖参数网格搜索）。"""

    full_text = ""
    try:
        await websocket.send_json({
            "type": "stream_start",
            "step": "llm_tune",
            "message": "LLM 分析中...",
            "ts": _now_ms(),
        })

        async for chunk in client.chat_stream_async([{"role": "user", "content": prompt}]):
            if hasattr(chunk, 'type') and chunk.type == "content" and chunk.text:
                full_text += chunk.text
                await websocket.send_json({
                    "type": "stream",
                    "step": "llm_tune",
                    "text": chunk.text,
                    "ts": _now_ms(),
                })

        await websocket.send_json({
            "type": "stream_end",
            "step": "llm_tune",
            "ts": _now_ms(),
        })

        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", full_text, re.DOTALL)
        candidate = m.group(1) if m else None
        if candidate is None:
            start, end = full_text.find("{"), full_text.rfind("}")
            if start != -1 and end > start:
                candidate = full_text[start:end + 1]
            else:
                return {"comment": "LLM 输出格式异常", "actions": []}
        return _json.loads(candidate)
    except Exception as e:
        return {"comment": f"LLM 分析失败: {e}", "actions": []}


async def _llm_final_analysis_with_stream(
    websocket: WebSocket,
    generator: Any,
    code: str,
    history: list,
    best_spec: dict,
    best_result: dict,
) -> dict:
    """带流式输出的 LLM 最终分析"""
    import json as _json
    import re

    try:
        from deeppulse.agent.client import LLMClient, load_setting
        client = LLMClient(load_setting())
    except Exception as e:
        return {"reasoning": f"LLM 客户端初始化失败: {e}", "recommended_spec": None}

    history_summary = []
    for h in history:
        m = h.get("metrics", {})
        history_summary.append({
            "iteration": h.get("iteration"),
            "phase": h.get("phase", "unknown"),
            "score": h.get("score"),
            "total_return": m.get("total_return"),
            "win_rate": m.get("win_rate"),
            "max_drawdown": m.get("max_drawdown"),
            "sharpe_ratio": m.get("sharpe_ratio"),
            "total_trades": m.get("total_trades"),
            "comment": (h.get("llm_comment") or "")[:100],
        })

    best_metrics = {
        k: best_result.get(k)
        for k in ["total_return", "annual_return", "max_drawdown", "win_rate",
                  "sharpe_ratio", "total_trades"]
    }

    prompt = f"""你是一位资深量化策略评审专家。以下是股票 {code} 的策略优化全过程，请综合分析并给出最终建议。

【优化历史】
{_json.dumps(history_summary, ensure_ascii=False, indent=2)}

【当前最佳策略】
{_json.dumps(best_spec, ensure_ascii=False, indent=2)}

【最佳策略指标】
{_json.dumps(best_metrics, ensure_ascii=False, indent=2)}

请综合分析：
1. 各轮策略的表现对比（不只看评分，还要看收益、风险、交易频率等）
2. 策略的稳定性（是否有大起大落）
3. 风险收益比（夏普比率、最大回撤）
4. 是否有改进空间

严格只输出如下 JSON：
{{
  "reasoning": "详细的分析推理过程，说明为什么选择这个策略",
  "recommended_spec": null 或 策略规格JSON（如果你认为可以进一步优化）
}}

如果当前最佳策略已经足够好，recommended_spec 设为 null。"""

    full_text = ""
    try:
        await websocket.send_json({
            "type": "stream_start",
            "step": "final_analysis",
            "message": "LLM 综合分析中...",
            "ts": _now_ms(),
        })

        async for chunk in client.chat_stream_async([{"role": "user", "content": prompt}]):
            if hasattr(chunk, 'type') and chunk.type == "content" and chunk.text:
                full_text += chunk.text
                await websocket.send_json({
                    "type": "stream",
                    "step": "final_analysis",
                    "text": chunk.text,
                    "ts": _now_ms(),
                })

        await websocket.send_json({
            "type": "stream_end",
            "step": "final_analysis",
            "ts": _now_ms(),
        })

        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", full_text, re.DOTALL)
        candidate = m.group(1) if m else None
        if candidate is None:
            start, end = full_text.find("{"), full_text.rfind("}")
            if start != -1 and end > start:
                candidate = full_text[start:end + 1]
            else:
                return {"reasoning": full_text, "recommended_spec": None}
        return _json.loads(candidate)
    except Exception as e:
        return {"reasoning": f"最终分析失败: {e}", "recommended_spec": None}


# ============================================================
#  策略分析 — 流式进度
# ============================================================

@router.websocket("/strategy/analyze")
async def strategy_analyze_ws(websocket: WebSocket):
    """WebSocket AI 策略分析端点 — 流式输出分析进度

    通过 URL query 参数传入:
        ws://host/ws/strategy/analyze?strategy_id=xxx
    """
    strategy_id = websocket.query_params.get("strategy_id", "")
    await websocket.accept()

    try:
        if not strategy_id:
            await _ws_send(websocket, "error", message="请选择策略")
            return
        await _run_analyze_stream(websocket, strategy_id)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await _ws_send(websocket, "error", message=str(e))
            traceback.print_exc()
        except Exception:
            pass


async def _run_analyze_stream(websocket: WebSocket, strategy_id: str):
    """流式运行 AI 分析 — 细粒度事件"""
    from deeppulse.strategy.performance import AIAnalyzer
    from deeppulse.strategy.repository import get_repository

    repo = get_repository()

    # ---- 加载策略 ----
    t0 = _now_ms()
    await _ws_send(websocket, "step", step="load_strategy", status="running",
                   message="加载策略...")
    ss = repo.get_stock_strategy(strategy_id)
    if not ss:
        await _ws_send(websocket, "error", message="策略不存在")
        return

    spec = ss.get_spec()
    await _ws_send(websocket, "step", step="load_strategy", status="done",
                   message=f"策略: {ss.name}", elapsed_ms=_now_ms() - t0,
                   detail={"name": ss.name, "factors": len(spec.factors)})

    await _ws_send(websocket, "start",
                   stock_code=ss.stock_code, stock_name=ss.stock_name,
                   strategy_name=ss.name, strategy_id=ss.id)

    # ---- 加载K线 ----
    t0 = _now_ms()
    await _ws_send(websocket, "step", step="load_kline", status="running",
                   message=f"加载 {ss.stock_code} K线数据...")
    analyzer = AIAnalyzer()
    df = analyzer._load_kline(ss.stock_code, days=250)
    if df is None or df.empty:
        await _ws_send(websocket, "error", message=f"股票 {ss.stock_code} 无K线数据")
        return

    from deeppulse.agent.indicators import TechnicalIndicators
    df = TechnicalIndicators.add_all(df)
    await _ws_send(websocket, "step", step="load_kline", status="done",
                   message=f"共 {len(df)} 条K线", elapsed_ms=_now_ms() - t0,
                   detail={"rows": len(df), "date_range": f"{df['trade_date'].iloc[0]} ~ {df['trade_date'].iloc[-1]}"})

    # ---- 计算因子得分 ----
    t0 = _now_ms()
    await _ws_send(websocket, "step", step="calc_factors", status="running",
                   message="计算各因子得分...")

    from deeppulse.strategy.spec import factor_scores_detail
    detail = factor_scores_detail(spec, df)

    # 逐个因子发送得分
    factor_info = []
    for f in detail.get("factors", []):
        fi = {
            "factor_id": f["factor_id"],
            "factor_name": f["factor_name"],
            "score": f["score"],
            "weighted_score": f["weighted_score"],
        }
        factor_info.append(fi)
        await _ws_send(websocket, "factor_score",
                       factor_id=f["factor_id"],
                       factor_name=f["factor_name"],
                       score=round(f["score"], 4),
                       weighted_score=round(f["weighted_score"], 4),
                       direction=f.get("direction", 1))

    total_score = detail.get("total_score", 0)
    entry_threshold = detail.get("entry_threshold", 0.6)
    triggered = detail.get("triggered", False)

    await _ws_send(websocket, "step", step="calc_factors", status="done",
                   message=f"综合得分 {total_score:.4f} / 阈值 {entry_threshold}"
                           f" {'✅ 触发' if triggered else '❌ 未触发'}",
                   elapsed_ms=_now_ms() - t0,
                   detail={"total_score": round(total_score, 4),
                           "threshold": entry_threshold,
                           "triggered": triggered})

    # ---- 构建行情画像 ----
    t0 = _now_ms()
    await _ws_send(websocket, "step", step="build_stats", status="running",
                   message="构建行情画像...")
    from deeppulse.strategy.llm_generator import build_kline_stats
    kline_stats = build_kline_stats(df)
    await _ws_send(websocket, "step", step="build_stats", status="done",
                   message="行情画像构建完成", elapsed_ms=_now_ms() - t0)

    # ---- LLM 分析（流式 token） ----
    t0 = _now_ms()
    await _ws_send(websocket, "step", step="llm_analyze", status="running",
                   message="AI 综合分析中...")
    analysis = await _llm_analyze_stream(websocket, ss, spec, detail, kline_stats, df)
    await _ws_send(websocket, "step", step="llm_analyze", status="done",
                   message="AI 分析完成", elapsed_ms=_now_ms() - t0)

    # 最终结果
    await _ws_send(websocket, "done",
                   stock_code=ss.stock_code,
                   stock_name=ss.stock_name,
                   strategy_name=ss.name,
                   strategy_id=ss.id,
                   factor_scores=detail,
                   kline_stats=kline_stats,
                   llm_analysis=analysis)


async def _llm_analyze_stream(websocket, ss, spec, detail, kline_stats, df) -> dict:
    """流式调用 LLM 分析 — 逐 token 发送"""
    import json as _json
    import re

    try:
        from deeppulse.agent.client import LLMClient, load_setting
        client = LLMClient(load_setting())
    except Exception as e:
        return {"error": f"LLM 客户端初始化失败: {e}"}

    factor_desc = [{"id": f.factor_id, "weight": f.weight, "direction": f.direction,
                    "params": f.params} for f in spec.factors]
    factor_scores = [{"factor_id": f["factor_id"], "factor_name": f["factor_name"],
                      "score": f["score"], "weighted_score": f["weighted_score"]}
                     for f in detail.get("factors", [])]
    recent_kline = df.tail(5)[["trade_date", "open", "high", "low", "close", "volume"]].to_dict(orient="records")

    prompt = f"""你是一位资深 A 股量化交易分析师。请基于以下信息分析当前行情并给出操作建议。

【股票】{ss.stock_code} {ss.stock_name}
【策略】{ss.name}
【策略因子组合】
{_json.dumps(factor_desc, ensure_ascii=False, indent=2)}
入场阈值: {spec.entry_threshold}
退出预设: {spec.exit_preset}

【当前各因子得分】
{_json.dumps(factor_scores, ensure_ascii=False, indent=2)}
综合得分: {detail.get('total_score', 0)} / 阈值: {detail.get('entry_threshold', 0.6)}
是否触发: {'是' if detail.get('triggered') else '否'}

【近期行情画像】
{kline_stats}

【最近5日K线】
{_json.dumps(recent_kline, ensure_ascii=False, default=str)}

请分析：
1. 当前各因子状态解读（哪些因子强/弱，为什么）
2. 综合研判：当前是否适合买入/持有/卖出
3. 关键价位：支撑位、压力位
4. 风险提示：需要注意的风险因素
5. 操作建议：具体的操作建议（买入/观望/止损/止盈）

严格输出 JSON：
{{
  "signal": "买入/观望/卖出",
  "confidence": 0.0到1.0之间的数值,
  "factor_analysis": [
    {{"factor_id": "因子id", "factor_name": "因子名称", "status": "强/中/弱", "interpretation": "解读"}}
  ],
  "overall_assessment": "综合研判文字",
  "key_levels": {{"support": 价格数值, "resistance": 价格数值}},
  "risk_factors": ["风险1", "风险2"],
  "action_advice": "具体操作建议",
  "reasoning": "完整推理过程"
}}"""

    full_text = ""
    try:
        try:
            for chunk in client.chat_stream([{"role": "user", "content": prompt}]):
                if chunk.get("type") == "content":
                    delta = chunk.get("delta", "")
                    full_text += delta
                    await _ws_send(websocket, "llm_token",
                                   step="llm_analyze", text=delta)
        except Exception:
            resp = client.chat([{"role": "user", "content": prompt}])
            full_text = resp.get("content") or ""

        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", full_text, re.DOTALL)
        candidate = m.group(1) if m else None
        if candidate is None:
            start, end = full_text.find("{"), full_text.rfind("}")
            if start != -1 and end > start:
                candidate = full_text[start:end + 1]
            else:
                return {"signal": "观望", "confidence": 0.5,
                        "overall_assessment": full_text, "error": "LLM 输出格式异常"}
        return _json.loads(candidate)
    except Exception as e:
        return {"signal": "观望", "confidence": 0.5,
                "overall_assessment": f"LLM 分析失败: {e}", "error": str(e)}


# ============================================================
#  工具函数
# ============================================================

async def _ws_send(websocket: WebSocket, msg_type: str, **kwargs):
    """统一发送带时间戳的 JSON 消息"""
    kwargs["type"] = msg_type
    kwargs["ts"] = _now_ms()
    await websocket.send_json(kwargs)


def _get_stock_name(code: str) -> str:
    """从数据库获取股票名称"""
    try:
        import duckdb
        from deeppulse import config

        if config.DB_PATH.exists():
            conn = duckdb.connect(str(config.DB_PATH), read_only=True)
            try:
                row = conn.execute("SELECT name FROM stock_info WHERE code = ?", [code]).fetchone()
                if row and row[0]:
                    return row[0]
            finally:
                conn.close()
    except Exception:
        pass
    return code
