"""Unit tests for agent/tools.py — tool definitions integrity and dispatch mapping."""

from deeppulse.agent.tools import TOOL_DEFINITIONS, TOOL_DISPATCH


class TestToolDefinitionsIntegrity:
    """Validate TOOL_DEFINITIONS schema without calling any tools."""

    def test_all_definitions_have_required_keys(self):
        for tool in TOOL_DEFINITIONS:
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

    def test_all_parameters_are_object_type(self):
        for tool in TOOL_DEFINITIONS:
            params = tool["function"]["parameters"]
            assert params["type"] == "object"
            assert "properties" in params
            assert "required" in params

    def test_required_fields_are_list(self):
        for tool in TOOL_DEFINITIONS:
            required = tool["function"]["parameters"]["required"]
            assert isinstance(required, list)

    def test_required_fields_exist_in_properties(self):
        for tool in TOOL_DEFINITIONS:
            props = tool["function"]["parameters"]["properties"]
            required = tool["function"]["parameters"]["required"]
            for field in required:
                assert field in props, f"Tool '{tool['function']['name']}': required field '{field}' not in properties"

    def test_no_duplicate_names(self):
        names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
        assert len(names) == len(set(names)), "Duplicate tool names found"

    def test_descriptions_are_nonempty(self):
        for tool in TOOL_DEFINITIONS:
            desc = tool["function"]["description"]
            assert isinstance(desc, str) and len(desc) > 0


class TestToolDispatchIntegrity:
    """Validate TOOL_DISPATCH mapping matches TOOL_DEFINITIONS."""

    def test_every_definition_has_dispatch(self):
        def_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        dispatch_names = set(TOOL_DISPATCH.keys())
        missing = def_names - dispatch_names
        assert not missing, f"Tools defined but not in dispatch: {missing}"

    def test_every_dispatch_has_definition(self):
        def_names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
        dispatch_names = set(TOOL_DISPATCH.keys())
        extra = dispatch_names - def_names
        assert not extra, f"In dispatch but not in definitions: {extra}"

    def test_all_dispatch_values_are_callable(self):
        for name, func in TOOL_DISPATCH.items():
            assert callable(func), f"TOOL_DISPATCH['{name}'] is not callable"

    def test_tool_count(self):
        assert len(TOOL_DEFINITIONS) >= 50, f"Expected 50+ tools, got {len(TOOL_DEFINITIONS)}"
        assert len(TOOL_DISPATCH) == len(TOOL_DEFINITIONS)
