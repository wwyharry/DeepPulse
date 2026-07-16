# 更新日志

## v0.3.0 (2026-07-15)

### 🌐 Web 平台（重大更新）

- **新增** FastAPI 后端 + 纯 HTML/CSS/JS 前端，无需 Node.js
- **新增** 一键启动：`python -m webapp`
- **新增** AI 对话页面：流式输出、推理过程展示、工具调用可视化
- **新增** 行情中心：K 线图、板块动态、涨停池、市场情绪
- **新增** 分析工具：趋势强度、背离检测、支撑压力、量价分析、多周期共振
- **新增** 条件选股：DuckDB 高性能选股，支持组合条件
- **新增** 新闻资讯：多源聚合（百度/东方财富/新浪），涨停快讯，板块动态
- **新增** 战法库：40 个内置战法，可视化编辑
- **新增** 自选股管理：搜索添加、移除、个股详情页快捷操作
- **新增** 会话管理：历史会话恢复、会话标题自动生成
- **新增** 评测系统：自适应评分（1-10 分）、根据问题类型调整评价标准

### 🔬 深度分析模块（6 个新工具）

- **新增** `indicators.py`：统一技术指标引擎（14 个指标：MA/EMA/RSI/MACD/KDJ/BOLL/ATR/OBV/ADX/WilliamsR/CCI/MFI/VWAP/一目均衡表）
- **新增** `divergence.py`：背离检测（RSI/MACD/KDJ 底背离/顶背离）
- **新增** `support_resistance.py`：支撑压力位检测（前高前低、均线、布林带、整数关口）
- **新增** `trend.py`：趋势强度评估（0-100 分，吸筹/拉升/派发/下跌阶段判断）
- **新增** `volume_analysis.py`：深度量价分析（量能趋势、量价同步、异常量能、量价背离）
- **新增** `screener_v2.py`：高性能选股器（DuckDB 批量计算，替代逐只遍历）
- **新增** `backtest_optimizer.py`：参数优化（网格搜索）+ Monte Carlo 模拟
- **新增** `timeframe_confluence.py`：多周期共振分析（日线+60分钟+15分钟评分）
- **新增** `fund_flow.py`：多数据源资金流向（自动降级：东方财富→龙虎榜→量价分析）

### 📊 板块数据增强

- **新增** 同花顺行业板块数据源（涨跌幅、成交量、成交额、净流入、领涨股）
- **新增** 同花顺概念板块数据源（概念名称、驱动事件、龙头股、成分股数）
- **新增** 申万一级行业分类（含 PE、PB、股息率估值数据）
- **新增** 涨停股行业分布推断热门板块
- **移除** 北向资金（交易所已停止公布）

### ⚖️ 评测 Agent 升级

- **重构** 自适应评分（1-10 分制），根据问题类型调整评价标准
- **简化** 移除工具调用验证，快速输出评测结果
- **新增** 评测历史保存与统计

### 🏗️ 代码重组

- **重构** `deeppulse/` Python package（三端共享核心代码）
- **新增** `cli/`、`tui/`、`web/` 三个独立入口
- **重构** `agent/` → `deeppulse/agent/`，`src/` → `deeppulse/src/`
- **新增** `deeppulse/config.py`（向后兼容的 config shim）

### 🔧 技术改进

- **新增** 统一指标引擎，消除 backtest.py 和 market.py 的重复实现
- **新增** 多数据源板块数据（同花顺 + 东方财富 + 申万）
- **新增** 多数据源资金流向（自动降级策略）
- **修复** 行尾符统一为 LF（.gitattributes）
- **更新** README.md、CHANGELOG.md、CONFIG_GUIDE.md

---

## v0.2.2 (2026-06-25)

### 📊 数据准确性

- **修复** 成交量单位：所有工具输出从"股"统一转换为"手"（1手=100股），符合A股市场惯例
- **新增** 工具返回中标注 `volume_unit: "手"` 字段，消除LLM单位混淆
- **影响** `query_kline`、`latest_price`、`realtime_price`、`realtime_prices`、`calc_technical`、`query_timeframe_kline`、`multi_timeframe_analysis`

### 🛡️ 数据真实性防护

- **新增** System Prompt "数据真实性铁律"：严禁编造数据、工具失败禁止编造、截断数据必须声明
- **新增** Agent 工具调用去重检测：相同参数的重复调用自动提醒LLM停止重试
- **新增** 工具 error 结果自动注入"禁止编造"系统提示
- **新增** 连续3次工具异常自动注入"停止尝试"保护提示
- **新增** 工具截断智能处理：JSON感知截断保留合法结构，添加 `_truncated` 元数据标记
- **新增** `TOOL_REPEAT_DETECTION_WINDOW` 配置项（默认5）

### 🎨 TUI 稳定性修复

- **修复** Windows 终端编码乱码：`tui_cli.py` 添加 UTF-8 编码保护（`chcp 65001` + `TextIOWrapper`）
- **修复** 流式输出 O(n²) 卡顿：`AgentMessageContainer` 渲染节流（200ms 间隔），消除每token全量Markdown重解析
- **修复** 不完整Markdown语法导致渲染崩溃：`Markdown()` 添加 try/except，失败时降级为纯文本
- **修复** 7处裸 `asyncio.create_task` 泄漏：统一使用 `_create_task()` 封装，Task引用被持有防止GC回收
- **修复** 后台任务异常静默丢失：`_on_task_done()` 回调在verbose模式下显示到ChatLog
- **修复** `asyncio.get_event_loop()` 弃用警告：全部替换为 `asyncio.get_running_loop()`
- **修复** 面板刷新 `except Exception: pass` 完全静默：添加连续失败计数，3次后verbose模式显示错误
- **新增** Agent 初始化自动重试（3次，3s/6s/12s 指数退避），网络抖动不再永久卡在"初始化失败"
- **新增** `deeppulse-tui` 命令行入口（`pip install` 后直接运行）

### 🔧 配置

- **新增** `config.py` 中 `TOOL_REPEAT_DETECTION_WINDOW = 5`

---

## v0.2.1 (2026-06-14)

### 🎨 TUI 全面升级

- **新增** 暗色金融主题（GitHub Dark 风格，涨红跌绿专业配色）
- **新增** 顶部状态栏：实时显示模型名、会话ID、推理轮次、耗时、工具调用数
- **新增** 三栏布局全面可滚动，自适应终端大小
- **新增** 命令系统：`/help` `/memory` `/strategy` `/watchlist` `/portfolio` `/judge` `/stats` `/clear`
- **新增** 快捷键：Ctrl+H 帮助、Ctrl+J 手动评判、Ctrl+↑/↓ 历史命令翻页
- **新增** 会话统计：分析完成后自动显示轮次/工具数/总耗时
- **修复** 自选股/持仓面板不显示股票名称的问题
- **修复** 市场概况面板卡在"加载中"的问题
- **修复** ChatLog 缺少 `clear()` 方法导致重置报错

### 📈 市场数据

- **新增** 市场概况显示三大指数（上证指数、深证成指、创业板指）
- **数据源** 新浪 API 实时行情，启动即加载
- **移除** 旧的数据库涨跌统计（不准确）

### 📊 自选股 & 持仓实时刷新

- **新增** 自选股面板每 60 秒自动刷新，显示股票名称+实时价格+涨跌幅
- **新增** 持仓面板每 60 秒自动刷新，显示股票名称+盈亏+盈亏比例
- **数据源** `WatchlistManager` + `RealtimeQuoteManager`（新浪/东财双源）
- **修复** `_load_watchlist` 调用不存在的 `view_watchlist` 函数

### ⚖️ 评判Agent 全流程评测

- **重写** 评判Agent 可见主Agent完整对话流（工具调用+工具返回数据+推理过程）
- **新增** 5 维评测：数据核实、推理链完整性、工具使用合理性、风险覆盖、操作建议合理性
- **新增** 综合评分 X/10，实时推送到状态栏
- **新增** 数据核实统计：引用 X 条数据，Y 条准确，Z 条存疑
- **接口变更** `judge_stream_async(messages)` 直接接收完整 messages 列表
- **新增** `score` 事件类型

### 🔧 技术改进

- **新增** `StatusBar` 组件（`agent/tui/widgets.py`）
- **改进** 所有面板组件 `__init__` 透传 `*args, **kwargs`（支持 Textual `id=` 参数）
- **改进** 左右栏从 `Vertical` 改为 `ScrollableContainer`
- **改进** 面板 CSS 去掉固定高度限制，使用 `height: auto`

---

## v0.2.0 (2026-06-14)

### 🎉 重大更新

#### 🎨 TUI 全屏终端界面

- **新增** 基于 `textual` 的现代化全屏终端界面
- **新增** 三栏布局：左侧自选股/持仓、中间对话流、右侧工具/数据
- **新增** 实时流式输出，Agent 思考过程完全透明
- **新增** 快捷键：Ctrl+Q 退出、Ctrl+R 重置、Ctrl+L 清屏

#### ⚖️ 评判Agent

- **新增** 主分析完成后可手动触发质量检查
- **新增** 4 维检查：逻辑、数据、风险、引导
- **新增** 纠错学习闭环

#### 异步支持

- **升级** `LLMClient` 支持异步流式调用
- **升级** `StockAgent` 支持 `chat_stream_async()`
- **新增** `_run_tool_async()` 在线程池中异步执行工具

### 📚 文档

- **新增** `TUI_GUIDE.md`、`CHANGELOG.md`、`CONFIG_GUIDE.md`、`QUICK_START.md`

---

## v0.1.0 (2026-06-05)

### 🎉 首次发布

- 59 个工具覆盖全链路分析
- ReAct 推理引擎
- 长期记忆系统（向量语义搜索 + BM25）
- 40 个短线战法库
- 数据源韧性保护（重试 + 熔断）
- CLI 交互模式

详见 README.md
