#!/usr/bin/env python3
"""升级验证脚本 - 检查TUI和评判Agent是否正确安装"""

import io
import sys
from pathlib import Path

# 设置stdout为UTF-8编码
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))


def check_dependencies():
    """检查依赖是否安装"""
    print("🔍 检查依赖...")

    missing = []

    # 检查textual
    try:
        import textual

        print(f"  ✅ textual {textual.__version__}")
    except ImportError:
        print("  ❌ textual 未安装")
        missing.append("textual>=0.47.0")

    # 检查asyncio（Python内置）
    from importlib.util import find_spec

    if find_spec("asyncio"):
        print("  ✅ asyncio (内置)")
    else:
        print("  ❌ asyncio 不可用")
        missing.append("asyncio")

    # 检查其他核心依赖
    deps = [
        ("openai", "openai"),
        ("anthropic", "anthropic"),
        ("duckdb", "duckdb"),
    ]

    for module_name, package_name in deps:
        try:
            __import__(module_name)
            print(f"  ✅ {package_name}")
        except ImportError:
            print(f"  ❌ {package_name} 未安装")
            missing.append(package_name)

    if missing:
        print(f"\n❌ 缺少依赖: {', '.join(missing)}")
        print(f"💡 运行: pip install {' '.join(missing)}")
        return False
    else:
        print("\n✅ 所有依赖已安装")
        return True


def check_files():
    """检查新增文件是否存在"""
    print("\n🔍 检查新增文件...")

    files = [
        "agent/judge_agent.py",
        "agent/tui_cli.py",
        "agent/tui/__init__.py",
        "agent/tui/app.py",
        "agent/tui/widgets.py",
        "agent/tui/styles.tcss",
        "TUI_GUIDE.md",
        "CHANGELOG.md",
    ]

    all_exist = True
    for file in files:
        path = Path(file)
        if path.exists():
            print(f"  ✅ {file}")
        else:
            print(f"  ❌ {file} 不存在")
            all_exist = False

    if all_exist:
        print("\n✅ 所有文件完整")
        return True
    else:
        print("\n❌ 部分文件缺失")
        return False


def check_imports():
    """检查导入是否正常"""
    print("\n🔍 检查模块导入...")

    imports = [
        ("agent.judge_agent", "JudgeAgent"),
        ("agent.tui.app", "DeepPulseTUI"),
        ("agent.tui.widgets", "ChatLog"),
        ("agent.client", "LLMClient"),
        ("agent.agent", "StockAgent"),
    ]

    all_ok = True
    for module, cls in imports:
        try:
            mod = __import__(module, fromlist=[cls])
            getattr(mod, cls)
            print(f"  ✅ {module}.{cls}")
        except Exception as e:
            print(f"  ❌ {module}.{cls}: {e}")
            all_ok = False

    if all_ok:
        print("\n✅ 所有模块导入正常")
        return True
    else:
        print("\n❌ 部分模块导入失败")
        return False


def check_async_support():
    """检查异步支持"""
    print("\n🔍 检查异步支持...")

    try:
        from deeppulse.agent.client import LLMClient

        # 检查是否有chat_stream_async方法
        if hasattr(LLMClient, "chat_stream_async"):
            print("  ✅ LLMClient.chat_stream_async")
        else:
            print("  ❌ LLMClient.chat_stream_async 不存在")
            return False

        from deeppulse.agent.agent import StockAgent

        # 检查是否有chat_stream_async方法
        if hasattr(StockAgent, "chat_stream_async"):
            print("  ✅ StockAgent.chat_stream_async")
        else:
            print("  ❌ StockAgent.chat_stream_async 不存在")
            return False

        print("\n✅ 异步支持完整")
        return True
    except Exception as e:
        print(f"\n❌ 异步支持检查失败: {e}")
        return False


def check_setting():
    """检查配置文件"""
    print("\n🔍 检查配置文件...")

    setting_path = Path("setting.json")

    if not setting_path.exists():
        print("  ⚠️ setting.json 不存在")
        print("  💡 复制 setting.example.json 为 setting.json 并配置 API Key")
        return False
    else:
        print("  ✅ setting.json 存在")

        try:
            import json

            with open(setting_path, encoding="utf-8") as f:
                setting = json.load(f)

            if "llm" in setting and "api_key" in setting["llm"]:
                print("  ✅ LLM配置完整")
                return True
            else:
                print("  ❌ LLM配置不完整")
                return False
        except Exception as e:
            print(f"  ❌ 配置文件解析失败: {e}")
            return False


def main():
    """主函数"""
    print("=" * 60)
    print("DeepPulse v0.2.0 升级验证")
    print("=" * 60)

    results = {
        "依赖检查": check_dependencies(),
        "文件检查": check_files(),
        "模块导入": check_imports(),
        "异步支持": check_async_support(),
        "配置文件": check_setting(),
    }

    print("\n" + "=" * 60)
    print("验证结果汇总")
    print("=" * 60)

    for name, result in results.items():
        status = "✅ 通过" if result else "❌ 失败"
        print(f"{name}: {status}")

    all_passed = all(results.values())

    print("\n" + "=" * 60)
    if all_passed:
        print("🎉 升级验证通过！")
        print("\n下一步:")
        print("  1. 启动TUI模式: python -m agent.tui_cli")
        print("  2. 查看快速入门: cat TUI_GUIDE.md")
        print("  3. 查看更新日志: cat CHANGELOG.md")
    else:
        print("❌ 升级验证未通过，请检查上述错误")
        print("\n常见解决方案:")
        print("  1. 安装依赖: pip install -r requirements.txt")
        print("  2. 检查文件完整性")
        print("  3. 配置setting.json")
        return 1

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
