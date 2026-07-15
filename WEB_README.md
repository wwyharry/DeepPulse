# DeepPulse Web 智能分析平台

## 快速启动

```bash
# 一键启动
python -m webapp

# 自定义端口
python -m webapp --port 8080

# 不自动打开浏览器
python -m webapp --no-open
```

启动后访问：
- 🌐 Web 界面: http://localhost:8000
- 📖 API 文档: http://localhost:8000/docs

## 三端使用

```bash
# CLI 模式（命令行对话）
python -m cli "分析一下贵州茅台"

# TUI 模式（终端界面）
python -m tui

# Web 模式（浏览器界面）
python -m webapp
```

## 依赖说明

仅需 Python，无需 Node.js / npm。

```bash
# 安装 Python 依赖
pip install -r requirements.txt
```

## 功能特性

### AI 对话分析
- 流式输出，支持推理过程展示
- 工具调用可视化
- 会话历史保存

### 行情数据
- 实时行情查询
- K 线图表（支持多周期）
- 技术指标计算
- 板块排名、涨停池

### 分析工具
- K 线形态识别
- 条件选股器
- 策略回测（7 种策略）

### 投资组合
- 自选股管理
- 交易记录
- 持仓分析
- 预警系统

### 记忆系统
- 长期记忆存储
- 预测追踪与验证
- 知识图谱
- 用户画像

### 战法库
- 40 个内置短线战法
- 关键词搜索
- AI 自动匹配

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/market/stocks/search | 搜索股票 |
| GET | /api/market/stocks/{code} | 股票信息 |
| GET | /api/market/stocks/{code}/kline | K 线数据 |
| GET | /api/market/stocks/{code}/realtime | 实时行情 |
| GET | /api/market/stocks/{code}/technical | 技术指标 |
| GET | /api/market/overview | 大盘概览 |
| GET | /api/market/sentiment | 市场情绪 |
| GET | /api/market/sectors | 板块排名 |
| GET | /api/market/limit-up | 涨停池 |
| POST | /api/analysis/patterns | 形态识别 |
| POST | /api/analysis/screener | 条件选股 |
| POST | /api/analysis/backtest | 策略回测 |
| GET | /api/analysis/strategies | 战法列表 |
| GET | /api/portfolio/watchlist | 自选股 |
| POST | /api/portfolio/watchlist | 添加自选 |
| DELETE | /api/portfolio/watchlist/{code} | 删除自选 |
| GET | /api/portfolio/portfolio | 持仓 |
| GET | /api/portfolio/trades | 交易记录 |
| GET | /api/memory/memories | 记忆列表 |
| POST | /api/memory/memories/search | 搜索记忆 |
| GET | /api/memory/predictions | 预测记录 |
| GET | /api/memory/profile | 用户画像 |
| GET | /api/memory/knowledge | 知识图谱 |
| GET | /api/system/status | 系统状态 |

### WebSocket

| 端点 | 说明 |
|------|------|
| ws://localhost:8000/ws/chat | AI 对话（流式） |
| ws://localhost:8000/ws/realtime | 实时行情推送 |

## 项目结构

```
stock_agent-opensource/
├── deeppulse/              # Python 核心包（三端共享）
│   ├── agent/              # Agent 核心
│   ├── src/                # 数据层
│   └── config.py           # 配置
├── cli/                    # CLI 入口
├── tui/                    # TUI 入口
├── web/                    # Web 后端（FastAPI）
│   ├── app/
│   │   ├── api/            # REST API
│   │   └── ws/             # WebSocket
│   └── static/
│       └── index.html      # 前端页面
└── webapp/                 # 一键启动脚本
```
