#!/usr/bin/env python3
"""DeepPulse TUI模式启动入口"""

import argparse
import io
import os
import sys
from pathlib import Path

# Windows 终端 UTF-8 支持（必须在所有 print 之前）
if sys.platform == "win32":
    # 设置控制台代码页为 UTF-8
    try:
        os.system("chcp 65001 >nul 2>&1")
    except Exception:
        pass
    # 包装 stdout/stderr 为 UTF-8 模式，防止 emoji/中文乱码
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from deeppulse.agent.client import load_setting
from deeppulse.agent.tui.app import run_tui


def _safe_print(msg: str):
    """编码安全的 print，防止 Windows GBK 终端崩溃"""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", errors="replace").decode("utf-8", errors="replace"))


def main():
    """TUI模式主函数"""
    parser = argparse.ArgumentParser(description="DeepPulse - TUI模式")
    parser.add_argument("--model", type=str, help="覆盖配置中的模型名称")
    parser.add_argument("--verbose", action="store_true", help="显示详细日志")
    parser.add_argument("--no-judge", action="store_true", help="禁用评判Agent")

    args = parser.parse_args()

    # 加载配置
    try:
        setting = load_setting()
    except FileNotFoundError:
        _safe_print("❌ 未找到 setting.json，请先配置 LLM")
        _safe_print("💡 提示: 复制 setting.example.json 为 setting.json 并填入 API Key")
        sys.exit(1)
    except Exception as e:
        _safe_print(f"❌ 配置加载失败: {e}")
        sys.exit(1)

    # 覆盖模型
    if args.model:
        setting["llm"]["model"] = args.model

    # 禁用评判Agent（通过环境标记）
    if args.no_judge:
        setting["agent"]["enable_judge"] = False
    else:
        setting["agent"]["enable_judge"] = True

    _safe_print("✨ 正在启动 DeepPulse TUI...")
    _safe_print("💡 提示: Ctrl+Q 退出, Ctrl+R 重置对话, Ctrl+L 清屏")
    _safe_print("")

    # 启动TUI
    try:
        run_tui(setting=setting, verbose=args.verbose)
    except KeyboardInterrupt:
        _safe_print("\n👋 再见！")
    except Exception as e:
        _safe_print(f"\n❌ 运行错误: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
