# 配置 API Key

DeepPulse 需要配置 LLM API Key 才能运行。有两种方式：

## 方式 1: 直接配置（推荐）

编辑 `setting.json`，将 API Key 直接填入：

```json
{
  "llm": {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "api_key": "sk-your-actual-api-key-here",  // 直接填入你的API Key
    "model": "deepseek-v4-pro",
    "protocol": "openai",
    "max_tokens": 16384,
    "temperature": 0.3
  }
}
```

**注意**：
- ✅ 正确格式：`"api_key": "sk-xxx"`（直接填入）
- ❌ 错误格式：`"api_key": "${sk-xxx}"`（这表示引用环境变量，不是直接使用）
- ❌ 错误格式：`"api_key": "${}"`（空的环境变量引用）

## 方式 2: 环境变量

1. 编辑 `setting.json`，使用环境变量引用：

```json
{
  "llm": {
    "api_key": "${DEEPSEEK_API_KEY}"  // 引用环境变量
  }
}
```

2. 设置环境变量：

**Windows:**
```bash
set DEEPSEEK_API_KEY=sk-your-actual-api-key-here
```

**Linux/Mac:**
```bash
export DEEPSEEK_API_KEY=sk-your-actual-api-key-here
```

## 快速配置脚本

运行配置助手（交互式）：

```bash
python setup_config.py
```

按照提示选择配置方式即可。

## 验证配置

运行验证脚本检查配置是否正确：

```bash
python verify_upgrade.py
```

如果看到 `✅ 配置文件: ✅ 通过`，说明配置成功。

## 常见问题

### Q: 显示 "API Key 未配置"
A: 请确保 `setting.json` 中的 `api_key` 字段不是 `${}`，而是实际的 API Key 或有效的环境变量引用。

### Q: 显示 "环境变量 XXX 未设置"
A: 如果使用环境变量方式，需要先设置环境变量，然后在同一终端会话中启动 DeepPulse。

### Q: 如何获取 API Key？
A: 访问你选择的 LLM 服务商官网：
- DeepSeek: https://platform.deepseek.com/
- OpenAI: https://platform.openai.com/
- Claude: https://console.anthropic.com/

## 完整配置示例

```json
{
  "llm": {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "api_key": "sk-1234567890abcdef",  // 直接填入你的 API Key
    "model": "deepseek-v4-pro",
    "protocol": "openai",
    "max_tokens": 16384,
    "temperature": 0.3
  },
  "embedding": {
    "provider": "local",
    "model": "shibing624/text2vec-base-chinese"
  },
  "agent": {
    "max_rounds": 10,
    "verbose": true,
    "enable_judge": true
  }
}
```

**重要提示**：
- API Key 格式：直接填入 `"sk-xxx"`，不要用 `"${sk-xxx}"` 包裹
- `${}` 语法仅用于引用环境变量：`"${ENV_VAR_NAME}"`

配置完成后，即可启动 DeepPulse：

```bash
# TUI 模式（推荐）
python -m agent.tui_cli

# CLI 模式
python -m agent.cli
```
