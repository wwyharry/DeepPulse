# Contributing to DeepPulse

感谢你对 DeepPulse 的关注！以下是参与贡献的指南。

## 开发环境搭建

```bash
# 1. Fork 并克隆仓库
git clone https://github.com/你的用户名/DeepPulse.git
cd DeepPulse

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 安装依赖（含开发工具）
pip install -e ".[dev]"

# 4. 安装 pre-commit hooks
pre-commit install

# 5. 复制配置文件
cp setting.example.json setting.json
# 编辑 setting.json 填入你的 API Key
```

## 代码规范

项目使用 [Ruff](https://docs.astral.sh/ruff/) 进行代码检查和格式化：

```bash
# 检查
ruff check .

# 自动修复
ruff check --fix .

# 格式化
ruff format .
```

代码规范要点：
- 行宽 120 字符
- Python 3.10+ 语法（使用 `X | Y` 而非 `Union[X, Y]`）
- 中文注释和文档字符串（项目面向 A 股市场）
- 函数和变量使用 snake_case，类使用 PascalCase

## 提交规范

提交信息格式：

```
<类型>: <简短描述>

<可选的详细说明>
```

类型：
- `feat`: 新功能
- `fix`: 修复 bug
- `test`: 添加或修改测试
- `docs`: 文档更新
- `refactor`: 重构（不改变功能）
- `chore`: 构建/工具/配置变更

示例：
```
feat: 添加板块资金流向查询工具
fix: RSI 在全涨/全跌数据下返回 NaN
test: 添加 market.py 模块单元测试
```

## 提交 Pull Request

1. 从 `master` 创建分支：`git checkout -b feat/你的功能`
2. 编写代码并确保通过检查：`ruff check . && ruff format --check .`
3. 编写或更新测试：`python -m pytest tests/ -m "not integration" -q`
4. 提交并推送到你的 Fork
5. 在 GitHub 上创建 Pull Request

PR 描述中请说明：
- 做了什么改动
- 为什么要做这个改动
- 如何验证（测试步骤或截图）

## 添加新工具

DeepPulse 的工具定义在 `deeppulse/agent/tools/` 目录下，每个工具需要：

1. **工具定义** — 在对应模块的 `TOOL_DEFINITIONS` 列表中添加 JSON Schema
2. **工具函数** — 在 `TOOL_DISPATCH` 字典中注册处理函数
3. **测试** — 在 `tests/` 中添加对应的单元测试

工具函数示例：

```python
def my_new_tool(param1: str, param2: int = 10) -> str:
    """工具说明（会被 LLM 读取）"""
    # 实现逻辑
    return json.dumps({"result": "..."}, ensure_ascii=False)
```

## 添加新战法

在 `agent/strategies/` 下创建 `.md` 文件，格式参考 `agent/strategies/README.md`。

战法文件会被 Agent 自动加载，无需修改代码。

## 添加新数据源

### 历史数据源

在 `src/sources/` 下创建新文件，继承 `DataSourceBase`：

```python
from src.sources.base import DataSourceBase

class MySource(DataSourceBase):
    def fetch_stock_list(self) -> list[dict]:
        ...

    def fetch_daily_kline(self, code: str, start_date: str, end_date: str) -> list[dict]:
        ...
```

### 实时行情源

在 `src/realtime/` 下创建新文件，继承 `RealtimeQuoteSource`：

```python
from src.realtime.base import RealtimeQuoteSource, RealtimeQuote

class MyRealtimeSource(RealtimeQuoteSource):
    def fetch_quote(self, code: str) -> RealtimeQuote | None:
        ...

    def fetch_quotes(self, codes: list[str]) -> dict[str, RealtimeQuote]:
        ...
```

然后在 `src/realtime/manager.py` 的 `SOURCE_REGISTRY` 中注册。

## 运行测试

```bash
# 运行所有单元测试
python -m pytest tests/ -q

# 运行指定文件
python -m pytest tests/test_patterns.py -v

# 查看覆盖率
python -m pytest tests/ --cov --cov-report=term-missing
```

## 架构概览

```
用户输入
  ↓
CLI (cli.py) / TUI (tui/app.py)
  ↓
StockAgent (agent.py) — ReAct 推理循环
  ├── LLMClient (client.py) — OpenAI/Anthropic 双协议
  ├── 59 个工具 (tools.py)
  │   ├── 数据层 (src/) — DuckDB + BaoStock/AkShare + 新浪/东财
  │   ├── 技术分析 (patterns.py, screener.py, backtest.py)
  │   ├── 市场分析 (market.py, news.py, datalink.py)
  │   └── 记忆系统 (memory.py) — 向量/BM25/关键词三级检索
  └── 评判Agent (judge_agent.py) — 全流程质量评测
```

## 问题反馈

- Bug 报告：使用 GitHub Issues，附上复现步骤和错误日志
- 功能建议：使用 GitHub Issues，说明使用场景
- 安全问题：请通过邮件私下报告，不要公开 issue
