#!/usr/bin/env python3
"""配置助手 - 帮助用户设置API Key"""

import io
import json
import sys
from pathlib import Path

# 设置stdout为UTF-8编码
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def main():
    print("=" * 60)
    print("DeepPulse 配置助手")
    print("=" * 60)

    setting_path = Path("setting.json")

    if not setting_path.exists():
        print("\n❌ setting.json 不存在")
        print("💡 正在从 setting.example.json 复制...")

        example_path = Path("setting.example.json")
        if example_path.exists():
            import shutil

            shutil.copy(example_path, setting_path)
            print("✅ 已创建 setting.json")
        else:
            print("❌ setting.example.json 也不存在，请检查项目完整性")
            return 1

    # 读取配置
    with open(setting_path, encoding="utf-8") as f:
        setting = json.load(f)

    print("\n📋 当前配置:")
    print(f"  LLM Provider: {setting['llm']['provider']}")
    print(f"  Base URL: {setting['llm']['base_url']}")
    print(f"  Model: {setting['llm']['model']}")
    print(f"  API Key: {setting['llm']['api_key']}")

    # 检查API Key
    api_key = setting["llm"]["api_key"]

    if api_key == "${}":
        print("\n⚠️ API Key 未配置")
        print("\n请选择配置方式:")
        print("  1. 直接输入 API Key (推荐)")
        print("  2. 使用环境变量")
        print("  3. 退出")

        choice = input("\n请选择 (1-3): ").strip()

        if choice == "1":
            print("\n请输入你的 API Key:")
            new_key = input("API Key: ").strip()

            if not new_key:
                print("❌ API Key 不能为空")
                return 1

            # 更新配置
            setting["llm"]["api_key"] = new_key

            # 保存
            with open(setting_path, "w", encoding="utf-8") as f:
                json.dump(setting, f, indent=2, ensure_ascii=False)

            print("\n✅ API Key 已保存到 setting.json")
            print("\n🎉 配置完成！现在可以启动 DeepPulse:")
            print("  TUI模式: python -m agent.tui_cli")
            print("  CLI模式: python -m agent.cli")

        elif choice == "2":
            print("\n请输入环境变量名称 (例如: DEEPSEEK_API_KEY):")
            env_name = input("环境变量名: ").strip()

            if not env_name:
                print("❌ 环境变量名不能为空")
                return 1

            # 更新配置
            setting["llm"]["api_key"] = f"${{{env_name}}}"

            # 保存
            with open(setting_path, "w", encoding="utf-8") as f:
                json.dump(setting, f, indent=2, ensure_ascii=False)

            print(f"\n✅ 已配置使用环境变量: {env_name}")
            print("\n⚠️ 请在启动前设置环境变量:")
            if sys.platform == "win32":
                print(f"  Windows: set {env_name}=your-api-key")
            else:
                print(f"  Linux/Mac: export {env_name}=your-api-key")

        else:
            print("\n👋 已退出")
            return 0

    else:
        print("\n✅ API Key 已配置")

        # 验证配置
        print("\n🔍 验证配置...")
        try:
            from deeppulse.agent.client import load_setting

            load_setting()
            print("✅ 配置验证通过")
            print("\n🎉 现在可以启动 DeepPulse:")
            print("  TUI模式: python -m agent.tui_cli")
            print("  CLI模式: python -m agent.cli")
        except ValueError as e:
            print(f"❌ 配置验证失败: {e}")
            return 1

    print("\n" + "=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
