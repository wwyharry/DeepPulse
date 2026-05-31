# A股短线分析 AI Agent

基于 LLM 的 A股沪深主板短线交易分析系统。通过自然语言对话，自动完成技术指标计算、K线走势分析、财经新闻搜索，并结合内置短线战法库给出操作建议。

## 核心特性

- **自然语言交互**: 用中文提问，Agent 自动调用工具完成分析
- **技术指标计算**: MA均线、MACD、RSI、KDJ、布林带、ATR、OBV，自动判断金叉/死叉/超买/超卖
- **多周期K线**: 1/5/15/30/60分钟和周线数据，支持多周期共振分析（日线看方向、60分钟找买点、15分钟精确入场）
- **量价关系分析**: 放量/缩量、量价背离等短线信号识别
- **K线形态识别**: 自动识别十字星、锤子线、吞没、早晨之星等经典形态
- **市场情绪分析**: 涨停/跌停/炸板统计、连板高度、情绪评级（高潮/发酵/启动/低迷/冰点）
- **板块排行**: 行业板块和概念板块涨跌排行，发现热点方向
- **资金流向**: 个股近5日主力净流入/流出趋势
- **龙虎榜**: 查看当日上榜个股和个股历史龙虎榜，分析游资和机构动向
- **北向资金**: 沪深港通资金流向，判断外资态度和市场风向
- **板块资金流**: 行业和概念板块主力净流入排行
- **股票筛选**: 按技术条件从全市场筛选股票（MA、RSI、MACD、成交量等）
- **财经新闻搜索**: 综合百度新闻、东方财富、新浪财经多源聚合
- **实时行情**: 新浪财经 + 东方财富双源冗余，自动故障切换与熔断保护
- **策略回测**: 用历史数据验证策略的胜率、盈亏比、最大回撤、夏普比率
- **K线图生成**: 生成专业K线图（带均线/MACD叠加），浏览器自动打开
- **涨幅对比图**: 多只股票归一化涨幅对比，横向比较强弱
- **短线战法库**: 内置 40 个完整短线战法（Markdown），Agent 根据技术面自动匹配
- **自选股管理**: 分组管理自选股，设置目标价和止损价，实时查看状态
- **盯盘告警**: 设置价格/涨跌幅/放量/RSI告警规则，触发时桌面通知
- **交易日志**: 记录买卖操作，自动更新持仓，查看交易历史
- **复盘系统**: 自动生成周/月/季度复盘报告，统计战法使用和情绪分布
- **动态推理链**: 根据市场状态（高潮/发酵/启动/低迷/冰点）自动调整分析策略
- **长期记忆系统**: 跨会话记住分析结论、用户偏好和教学纠错，支持向量语义搜索（sentence-transformers）和 BM25 关键词搜索双模式
- **学习能力**: 自动检测用户教学/纠错，保存为结构化学习记忆，在后续分析中应用
- **预测跟踪**: 保存投资预测并自动验证结果，从对错中持续学习，提升分析准确率
- **用户画像**: 自动从对话中提取交易风格、风险偏好、关注板块，个性化调整分析建议
- **知识图谱**: 结构化存储指标、策略、规则之间的关系，支持关联推理
- **LLM 记忆整合**: 会话结束时自动用 LLM 语义合并相似记忆，避免信息冗余
- **流式输出**: 实时展示思考过程、工具调用和分析结果
- **多 LLM 支持**: 兼容 OpenAI 和 Anthropic 协议（DeepSeek、GPT、Claude 等）

## 技术栈

| 组件 | 技术 |
|------|------|
| LLM | DeepSeek / OpenAI / Anthropic（通过 OpenAI 或 Anthropic SDK） |
| 数据库 | DuckDB（嵌入式列式分析引擎） |
| 数据源 | AkShare + BaoStock（免费） |
| 实时行情 | 新浪财经 + 东方财富（双源冗余，自动故障切换） |
| 新闻爬虫 | curl_cffi + BeautifulSoup |
| 语义搜索 | sentence-transformers（本地 768 维向量）+ BM25 关键词搜索 |
| 语言 | Python 3.10+ |

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 LLM

编辑 `setting.json`，填入你的 API Key：

```json
{
  "llm": {
    "provider": "deepseek",
    "base_url": "https://api.deepseek.com",
    "api_key": "sk-your-api-key",
    "model": "deepseek-v4-pro",
    "protocol": "openai",
    "max_tokens": 16384,
    "temperature": 0.3
  },
  "embedding": {
    "provider": "local",
    "model": "shibing624/text2vec-base-chinese",
    "api_key": "",
    "base_url": ""
  },
  "agent": {
    "max_rounds": 10,
    "verbose": true
  }
}
```

支持的配置方式：
- **DeepSeek**: `protocol: "openai"`, `base_url: "https://api.deepseek.com"`
- **OpenAI**: `protocol: "openai"`, `base_url: "https://api.openai.com/v1"`
- **Claude**: `protocol: "anthropic"`, 无需 `base_url`

API Key 支持环境变量引用：`"${ENV_VAR_NAME}"`

### 3. 首次初始化数据库并采集数据

首次使用需要全量采集历史数据（约3500只股票 x 5年日K，耗时较长）：

```bash
# 一键全量采集（初始化数据库 + 股票列表 + 日K数据）
python scripts/fetch_all.py

# 或分步执行
python scripts/init_db.py          # 初始化数据库
python scripts/fetch_stocks.py     # 采集股票列表
python scripts/fetch_kline.py      # 采集日K数据（默认过去5年）
```

### 4. 启动 Agent

```bash
# 交互模式
python -m agent.cli

# 单次提问
python -m agent.cli "分析一下贵州茅台的短线走势"

# 带参数启动
python -m agent.cli --model deepseek-v4-pro --no-thinking "600519技术面怎么样"
```

Agent 启动后，数据更新是按需进行的：
- **分析单只股票时**: Agent 会自动先更新该股票的K线数据（只需几秒）
- **更新全部数据**: 对 Agent 说"更新数据库"即可全量更新（约10-15分钟）

也可以手动运行增量更新脚本：
```bash
python scripts/update_data.py              # 默认更新最近5天
python scripts/update_data.py --max-days 10  # 回溯10天
python scripts/update_data.py --skip-stocks  # 只更新K线，不更新股票列表
```

## Agent 使用指南

### 交互模式命令

启动交互模式后，可用以下命令：

| 命令 | 说明 |
|------|------|
| `quit` / `exit` / `q` | 退出 |
| `reset` | 重置对话（自动保存会话摘要） |
| `/thinking` | 切换思考过程显示 |
| `/memory` | 列出所有长期记忆 |
| `/memory search <关键词>` | 搜索记忆 |
| `/memory save <内容>` | 手动保存记忆 |
| `/memory predictions` | 查看预测准确率统计 |
| `/memory profile` | 查看用户交易画像 |
| `/memory knowledge` | 查看知识图谱 |
| `/memory clear` | 清除所有记忆 |
| `/strategy` | 列出所有短线战法 |
| `/strategy search <关键词>` | 搜索匹配的战法 |
| `/strategy <战法名>` | 查看指定战法详情 |
| `/strategy reload` | 重新加载战法文件 |
| `/watchlist` | 查看自选股实时状态 |
| `/watchlist add <代码>` | 添加自选股 |
| `/watchlist remove <代码>` | 移除自选股 |
| `/watchlist groups` | 查看自选股分组 |
| `/portfolio` | 查看当前持仓和盈亏 |
| `/portfolio history` | 查看交易历史 |
| `/portfolio review` | 自动生成复盘报告 |

### CLI 参数

| 参数 | 说明 |
|------|------|
| `question` | 直接提问（不进入交互模式） |
| `--model <model>` | 覆盖模型名称 |
| `--no-verbose` | 隐藏工具调用过程 |
| `--no-thinking` | 隐藏思考过程 |
| `--no-stream` | 禁用流式输出 |

### 对话示例

```
你: 贵州茅台短线怎么样
分析师: [自动调用 update_stock → realtime_price → calc_technical → recognize_kline_patterns → query_kline → stock_fund_flow → stock_news → search_strategy]

你: 今天市场热点是什么
分析师: [自动调用 market_hot_news → market_overview → market_sentiment → sector_ranking]

你: 帮我找RSI超卖的股票
分析师: [自动调用 screen_stocks("RSI6<20")]

你: 对比一下茅台和五粮液
分析师: [自动调用多只股票的分析工具]

你: 你分析错了，RSI超卖要结合成交量看
分析师: [自动保存为 learning 记忆，后续分析中应用]
```

## Agent 工具一览

Agent 可调用以下工具，均由系统自动调度，无需手动调用：

### 数据更新
| 工具 | 说明 |
|------|------|
| `update_stock` | 检查并更新单只股票K线到最新（分析前自动调用） |
| `update_all_stocks` | 更新全部股票K线数据（用户要求时调用） |
| `realtime_price` | 获取单只股票实时行情（盘中实时价/盘后收盘价，新浪+东财双源） |
| `realtime_prices` | 批量获取多只股票实时行情（比逐个调用更高效） |
| `update_timeframe_data` | 更新多周期K线数据（1/5/15/30/60分钟、周线） |

### 数据查询
| 工具 | 说明 |
|------|------|
| `search_stock` | 按名称或代码搜索股票 |
| `query_stock_info` | 查询股票基本信息 |
| `query_kline` | 查询日K线数据（参数 days 指交易日，非自然日） |
| `query_timeframe_kline` | 查询多周期K线数据（分钟级/周线） |
| `multi_timeframe_analysis` | 获取多周期数据（日线+60分钟+15分钟）用于共振分析 |
| `latest_price` | 获取最新价格（支持批量） |
| `market_overview` | 市场概览（涨跌统计） |

### 技术分析
| 工具 | 说明 |
|------|------|
| `calc_technical` | 计算技术指标（MA/MACD/RSI/KDJ/布林带）+ 量价分析 |

### 新闻搜索
| 工具 | 说明 |
|------|------|
| `search_news` | 搜索财经新闻 |
| `stock_news` | 搜索个股新闻（多源聚合） |
| `market_hot_news` | 今日市场热点新闻 |

### 市场情绪
| 工具 | 说明 |
|------|------|
| `market_sentiment` | 市场情绪综合分析（涨停/跌停/炸板/连板/情绪评级） |
| `limit_up_pool` | 涨停股池（封板时间、连板数、所属行业） |
| `sector_ranking` | 板块涨跌排行（行业板块/概念板块） |
| `stock_fund_flow` | 个股近5日主力净流入/流出趋势 |
| `dragon_tiger_list` | 龙虎榜数据（上榜个股、净买入额、上榜原因） |
| `northbound_flow` | 北向资金（沪深港通）近5日净流入和趋势 |
| `sector_fund_flow` | 板块资金流向（行业+概念板块主力净流入排行） |
| `stock_dragon_tiger` | 个股龙虎榜历史明细（游资/机构席位动向） |

### K线形态与选股
| 工具 | 说明 |
|------|------|
| `recognize_kline_patterns` | K线形态识别（十字星、锤子线、吞没、早晨之星等） |
| `screen_stocks` | 按技术条件从全市场筛选股票（MA、RSI、MACD、成交量等） |

### 回测验证
| 工具 | 说明 |
|------|------|
| `backtest_stock` | 单只股票策略回测（胜率/盈亏比/最大回撤/夏普比率） |
| `backtest_multi_stock` | 多只股票批量回测，评估策略普适性 |

### K线图与可视化
| 工具 | 说明 |
|------|------|
| `generate_chart` | 生成K线图（带均线/MACD叠加），浏览器自动打开 |
| `compare_stocks_chart` | 多只股票涨幅对比图（归一化） |

### 自选股与告警
| 工具 | 说明 |
|------|------|
| `add_to_watchlist` | 添加自选股（支持分组、目标价、止损价） |
| `remove_from_watchlist` | 移除自选股 |
| `list_watchlist` | 查看自选股实时状态 |
| `set_alert_rule` | 设置告警规则（价格/涨跌幅/放量/RSI） |
| `check_alerts` | 立即检查告警条件 |
| `get_alert_history` | 查看告警历史 |

### 交易日志
| 工具 | 说明 |
|------|------|
| `record_trade` | 记录买卖操作（含理由、战法、情绪） |
| `view_portfolio` | 查看当前持仓和盈亏 |
| `view_trade_history` | 查看交易历史 |
| `generate_review` | 自动生成复盘报告 |
| `save_review` | 保存复盘笔记 |

### 记忆系统
| 工具 | 说明 |
|------|------|
| `save_memory` | 保存长期记忆（自动生成 embedding 向量，支持 learning 类型） |
| `search_memory` | 搜索历史记忆（自动选择向量语义搜索/BM25/关键词回退） |
| `update_memory` | 更新记忆 |
| `delete_memory` | 删除记忆（软删除） |
| `list_memories` | 列出记忆 |
| `save_session_context` | 保存会话上下文 |
| `record_learning` | 显式记录学习知识（检测到教学/纠错时使用） |
| `save_prediction` | 保存投资预测（方向、目标价、止损位、理由） |
| `check_predictions` | 检查某只股票的待验证预测 |
| `verify_prediction` | 验证预测结果，自动调整关联记忆重要度 |
| `prediction_stats` | 查看预测准确率统计 |
| `get_user_profile` | 获取用户交易画像 |
| `update_user_profile` | 更新用户画像 |
| `query_knowledge` | 查询知识图谱（实体关系） |
| `add_knowledge` | 添加知识关系到图谱 |

### 战法系统
| 工具 | 说明 |
|------|------|
| `search_strategy` | 根据技术面搜索匹配战法 |
| `list_strategies` | 列出所有战法 |
| `get_strategy` | 获取战法详情 |

## 短线战法库

战法以 Markdown 文件形式存放在 `agent/strategies/` 目录下，共 40 个完整战法，Agent 会根据当前技术面自动搜索匹配的战法并融入分析结论。

### 战法分类

| 分类 | 战法 |
|------|------|
| **三大基础** | 首板挖掘、接力、低吸 |
| **首板细分** | 低位首板、首板回封、题材首板、日内首板、换手首板、一字首板 |
| **接力细分** | 二板接力、换手二板、加速二板、三板接力、高位接力、高位缩量加速、高位T字板 |
| **低吸细分** | 5日线低吸、10日线低吸、平台支撑、龙头首阴反包、分歧低吸、翘板、反核、超跌低吸 |
| **半路细分** | 点火半路、板后半路、超跌反弹半路、弱转强半路 |
| **龙头进阶** | 龙头战法、龙头二波、卡位、补涨龙、龙头情绪周期 |
| **盘口时段** | 集合竞价、尾盘套利、分时攻击波、分时背离 |
| **高阶逻辑** | 分歧转一致、换手板、空仓战法 |

### 添加自定义战法

在 `agent/strategies/` 下创建 `.md` 文件，格式参考 `agent/strategies/README.md`。

## 记忆系统

Agent 拥有跨会话的长期记忆能力，支持从对话中学习和成长。记忆系统包含 6 大子模块：

### 架构概览

```
┌──────────────────────────────────────────────────────┐
│                   MemoryManager                       │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │ EmbeddingIndex│  │  BM25Index   │  │ 三级回退搜索 │ │
│  │ (768维向量)   │  │ (关键词搜索)  │  │ API→本地→BM25│ │
│  └──────────────┘  └──────────────┘  └─────────────┘ │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │PredictionTrack│  │  UserProfile │  │KnowledgeGraph│ │
│  │ (预测跟踪验证) │  │ (交易画像)    │  │ (知识图谱)   │ │
│  └──────────────┘  └──────────────┘  └─────────────┘ │
│  ┌──────────────────────────────────────────────────┐ │
│  │          智能上下文注入 (多策略预算分配)            │ │
│  │  30%相关记忆 | 25%学习记忆 | 20%画像 | 15%会话    │ │
│  └──────────────────────────────────────────────────┘ │
│  ┌──────────────────────────────────────────────────┐ │
│  │          LLM 记忆整合 (语义合并去重)               │ │
│  └──────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────┘
```

### 语义搜索

支持三种搜索模式，自动选择最佳方案：
- **向量语义搜索**: 使用 sentence-transformers 本地模型（768 维），余弦相似度排序
- **BM25 关键词搜索**: 无 embedding 时的回退方案，比 LIKE 匹配准确率提升 50%+
- **Embedding 配置**: `provider: "local"` 本地模型（零费用）/ `"openai"` API / `"none"` 纯 BM25

### 预测跟踪

Agent 给出分析结论时自动保存预测，下次分析同只股票时自动验证：
- 记录预测方向、目标价、止损位、理由
- 自动对比实际走势，判定正确/错误/部分正确
- 正确的预测 → 提升关联记忆重要度
- 错误的预测 → 降低关联记忆重要度，生成教训记忆
- 支持 `/memory predictions` 查看准确率统计

### 用户画像

自动从对话中提取用户交易特征：
- 交易风格（低吸型/追高型/打板型）
- 风险偏好（稳健型/激进型）
- 止损习惯、目标收益
- 关注板块和股票
- 支持 `/memory profile` 查看画像

### 知识图谱

结构化存储交易知识之间的关系：
- 实体类型：股票、指标、形态、策略、概念、规则
- 关系类型：触发、需要、矛盾、支持、适用、用户偏好
- 分析时自动查询关联实体辅助推理

### 记忆类型

| 类型 | 说明 | 半衰期 |
|------|------|--------|
| `preference` | 用户偏好（分析风格、输出格式等） | 90 天 |
| `insight` | 分析结论（技术面判断、关键价位等） | 30 天 |
| `fact` | 重要事实（市场规律、行业信息等） | 60 天 |
| `context` | 上下文线索（会话延续信息） | 14 天 |
| `summary` | 会话摘要（自动保存） | 45 天 |
| `learning` | 用户教学/纠错（最宝贵的记忆） | 180 天 |

### 学习能力

Agent 能够自动检测用户的教学和纠错，并保存为结构化学习记忆：

- **纠错检测**: "你分析错了"、"应该看XXX而不是YYY"、"这个判断有问题"
- **教学检测**: "记住：..."、"以后要注意..."、"我教你一个技巧"
- **结构化存储**: 学到了什么 / 为什么重要 / 什么时候应用
- **自动应用**: 后续分析中主动引用已学到的知识

### 记忆管理

- **自动保存**: 完成股票分析后自动保存结论并提取知识实体，检测到教学时自动保存学习记忆
- **语义搜索**: 新对话开始时自动加载相关历史记忆（向量/BM25/关键词三级回退）
- **记忆衰减**: 低重要度且长期未访问的记忆会被自动遗忘（指数衰减）
- **LLM 整合**: 会话结束时用 LLM 语义合并相似记忆，避免信息冗余
- **智能注入**: 上下文预算分配（相关 30% / 学习 25% / 画像 20% / 会话 15% / 预测 10%）
- **自动维护**: 会话结束时自动运行衰减遗忘和压缩合并
- **会话管理**: 退出时自动生成会话摘要并保存

## 项目结构

```
├── config.py                  # 项目配置（数据库路径、采集范围、市场筛选、记忆参数、实时行情）
├── setting.json               # LLM 和 Agent 配置
├── requirements.txt
├── db/
│   └── astock.duckdb          # DuckDB 数据库
├── src/
│   ├── database.py            # 数据库连接与表操作（含记忆表）
│   ├── sources/
│   │   ├── base.py            # 数据源基类
│   │   ├── akshare_source.py  # AkShare 实现
│   │   └── baostock_source.py # BaoStock 实现
│   ├── realtime/              # 实时行情模块
│   │   ├── base.py            # 实时行情基类与数据结构
│   │   ├── sina_source.py     # 新浪财经实时行情
│   │   ├── eastmoney_source.py# 东方财富实时行情
│   │   └── manager.py         # 多源管理器（故障切换、熔断保护）
│   ├── collector.py           # 多源采集协调器
│   └── query.py               # 数据查询接口
├── agent/
│   ├── cli.py                 # CLI 入口（交互模式 + 单次提问）
│   ├── agent.py               # Agent 核心（ReAct 循环、流式输出、记忆刷新）
│   ├── client.py              # LLM 客户端（OpenAI / Anthropic 协议）
│   ├── tools.py               # 工具函数定义（59 个工具）
│   ├── prompts.py             # System prompt（含教学检测指令）
│   ├── news.py                # 财经新闻爬虫（百度/东方财富/新浪）
│   ├── market.py              # 市场情绪分析（涨停/跌停/板块排行/资金流向）
│   ├── patterns.py            # K线形态识别（十字星、锤子线、吞没等）
│   ├── screener.py            # 股票筛选器（技术条件选股）
│   ├── memory.py              # 长期记忆系统（向量语义搜索 + 预测跟踪 + 用户画像 + 知识图谱）
│   ├── strategy_loader.py     # 战法加载器（Markdown 解析 + 搜索）
│   ├── backtest.py            # 回测验证框架（策略回测、绩效统计）
│   ├── datalink.py            # 扩展数据源（龙虎榜、北向资金、板块资金流）
│   ├── charts.py              # K线图与可视化（mplfinance/ECharts）
│   ├── timeframes.py          # 多周期K线数据（分钟级/周线）
│   ├── watchlist.py           # 自选股管理与盯盘告警
│   ├── journal.py             # 交易日志与复盘系统
│   ├── reasoning.py           # 动态推理链（市场状态自适应）
│   └── strategies/            # 短线战法库（40 个 Markdown 战法文件）
│       ├── README.md          # 战法编写指南
│       ├── 01-放量突破战法.md
│       ├── ...
│       └── 40-空仓战法.md
├── scripts/
│   ├── init_db.py             # 初始化数据库
│   ├── fetch_stocks.py        # 采集股票列表
│   ├── fetch_kline.py         # 采集日K数据
│   ├── fetch_all.py           # 一键全部采集
│   ├── update_data.py         # 增量更新（Agent启动时自动运行）
│   └── verify_data.py         # 数据质量校验
└── tests/
```

## 数据表结构

| 表名 | 说明 |
|------|------|
| `stock_info` | 股票基本信息（代码、名称、市场、上市日期） |
| `daily_kline` | 日K线数据（开高低收、量额、换手率） |
| `fetch_log` | 采集日志（状态、时间、行数） |
| `long_term_memories` | 长期记忆（内容、类型、关键词、重要度、embedding 向量、学习字段） |
| `session_memories` | 会话临时记忆（带过期时间） |
| `memory_sessions` | 会话记录（摘要、涉及股票、消息数） |
| `predictions` | 投资预测记录（方向、目标价、验证结果、准确率） |
| `knowledge_entities` | 知识图谱实体（指标、策略、概念、规则等） |
| `knowledge_relations` | 知识图谱关系（触发、支持、矛盾等） |
| `user_profile` | 用户交易画像（风格、偏好、止损习惯等） |

## 注意事项

- 数据来源为 BaoStock/AkShare，可能存在 T+1 延迟
- 实时行情来源新浪财经 + 东方财富（双源冗余，自动故障切换）
- 新闻数据来自网络爬虫，仅供参考
- 技术分析仅供参考，不构成投资建议
- Agent 仅分析沪深主板股票（6开头沪市、0开头深市）
- `days` 参数指交易日（剔除周末和节假日），非自然日
