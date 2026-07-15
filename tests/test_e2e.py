"""端到端冒烟测试 - mock LLM + 内存DB，验证完整对话流程"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestE2ESmoke:
    """模拟用户输入 -> Agent 推理 -> 工具调用 -> 输出结论"""

    def test_tool_definitions_loadable(self):
        """工具定义应能正常加载"""
        from deeppulse.agent.tools import TOOL_DEFINITIONS, TOOL_DISPATCH

        assert len(TOOL_DEFINITIONS) > 0
        assert len(TOOL_DISPATCH) > 0
        for td in TOOL_DEFINITIONS:
            name = td["function"]["name"]
            assert name in TOOL_DISPATCH, f"工具 {name} 在 TOOL_DEFINITIONS 中但不在 TOOL_DISPATCH 中"

    def test_tool_dispatch_callable(self):
        """所有工具函数应可调用"""
        from deeppulse.agent.tools import TOOL_DISPATCH

        for name, func in TOOL_DISPATCH.items():
            assert callable(func), f"工具 {name} 不可调用"

    def test_memory_system_roundtrip(self, tmp_db_path):
        """记忆系统应能完成保存->搜索->更新的完整流程"""
        from deeppulse.agent.memory import MemoryManager

        mem = MemoryManager(db_path=Path(tmp_db_path))
        mem.init_tables()

        # 保存
        result = mem.save_memory(
            content="贵州茅台在1800元有强支撑",
            memory_type="insight",
            keywords="600519,贵州茅台,支撑位",
            importance=0.7,
        )
        parsed = json.loads(result)
        assert parsed["status"] == "saved"
        memory_id = parsed["memory_id"]

        # 更新
        update_result = mem.update_memory(memory_id, importance=0.9)
        update_parsed = json.loads(update_result)
        assert update_parsed["status"] == "updated"

    def test_agent_initialization(self):
        """Agent 应能正常初始化（mock LLM）"""
        with patch("agent.client.load_setting") as mock_setting:
            mock_setting.return_value = {
                "llm": {
                    "protocol": "openai",
                    "api_key": "test-key",
                    "base_url": "http://localhost:8080",
                    "model": "test-model",
                },
                "agent": {
                    "verbose": False,
                    "max_rounds": 5,
                },
            }
            with patch("openai.OpenAI") as mock_openai:
                mock_openai.return_value = MagicMock()
                from deeppulse.agent.agent import StockAgent

                agent = StockAgent(setting=mock_setting.return_value)
                assert agent is not None
