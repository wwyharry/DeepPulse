# 🚀 快速开始

## 安装

```bash
# 1. 克隆仓库
git clone https://github.com/wwyharry/DeepPulse.git
cd DeepPulse

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 LLM
cp setting.example.json setting.json
# 编辑 setting.json 填入你的 API Key
```

详细配置说明: [CONFIG_GUIDE.md](CONFIG_GUIDE.md)

## 初始化数据

```bash
# 一键全量采集（约 30-60 分钟，仅首次）
python scripts/fetch_all.py
```

## 启动

```bash
# TUI 模式（推荐）
python -m agent.tui_cli

# CLI 模式
python -m agent.cli
```

## TUI 界面

```
┌──────────────────────────────────────────────────────────────────────┐
│  ⚡ DeepPulse │ 模型: deepseek-reasoner │ 会话: abc123 │ R3 │ ⏱ 12s  │
├────────────┬──────────────────────────────────┬──────────────────────┤
│ 📊 自选股   │  👤 你: 贵州茅台短线怎么样         │ 🔧 工具面板         │
│ 贵州茅台    │                                  │   ✅ calc_technical  │
│ ▲ +2.3%   │  🤔 主分析 [Round 3] ⏱8s          │   ✅ query_kline     │
│            │  分析内容流式输出...                 │                     │
│ 💼 持仓     │                                  │ 📊 数据摘要          │
│ 贵州茅台    │  ⚖️ 评判Agent [评分: 7.5/10]       │   RSI(6): 32.5     │
│ ▲ +2300元  │  评测报告...                       │                     │
│            │                                  │ 📈 市场概况         │
│ 📈 市场概况 │  [✅ 认可] [❌ 不认可]               │   上证指数           │
│ 上证指数    │                                  │   3367 ▲+0.82%     │
│ 3367 ▲0.82 │  > 输入框                         │                     │
└────────────┴──────────────────────────────────┴──────────────────────┘
```

## 快捷键

| 快捷键 | 说明 |
|--------|------|
| `Ctrl+Q` | 退出 |
| `Ctrl+R` | 重置对话 |
| `Ctrl+L` | 清屏 |
| `Ctrl+H` | 帮助 |
| `Ctrl+J` | 手动评判 |
| `Ctrl+↑/↓` | 历史命令 |

## 常用命令

| 命令 | 说明 |
|------|------|
| `/help` | 帮助 |
| `/memory` | 记忆管理 |
| `/strategy` | 战法库 |
| `/watchlist` | 自选股 |
| `/portfolio` | 持仓 |
| `/judge` | 手动评判 |
| `/stats` | 会话统计 |

## 使用示例

```
你: 贵州茅台短线怎么样
🤔 主分析: [自动调用工具分析...]
⚖️ 评判Agent: 评分 7.5/10，发现 2 个问题...
[✅ 认可] [❌ 不认可]
```

## 常见问题

**Q: 启动需要约 2 分钟？**
A: 首次加载语义模型到内存，后续秒级响应。可设置 `"provider": "none"` 跳过。

**Q: TUI 界面乱码？**
A: Windows 用户执行 `chcp 65001`，或使用 Windows Terminal。

**Q: 如何禁用评判Agent？**
A: `python -m agent.tui_cli --no-judge`
