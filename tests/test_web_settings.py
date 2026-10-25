"""Web 设置 API 测试：api_key 掩码、深合并写回、校验文案、settings/test 探活。

全部离线：SETTING_PATH 注入 tmp 文件；LLMClient 用 FakeLLMClient 桩。
"""

import json
import threading

import pytest

from tests.fakes_web import (
    FakeLLMClient,
    fake_setting,
    make_test_client,
    patch_llmclient,
    patch_setting_path,
    read_setting_file,
    write_setting_file,
)

MASKED = "sk-***abcd"  # FAKE_SETTING api_key "sk-testkey0123456789abcd" 的约定掩码


def assert_rejected(client, setting_path, payload, *keywords):
    """PUT 必须 4xx + 中文 detail + 盘上文件一字不改（校验失败绝不落盘）"""
    before = setting_path.read_text(encoding="utf-8")
    r = client.put("/api/settings", json=payload)
    assert 400 <= r.status_code < 500, f"应 4xx，实际 {r.status_code}: {r.text}"
    detail = r.json().get("detail", "")
    assert isinstance(detail, str) and detail.strip(), "detail 必须是非空中文原因"
    assert any(kw in detail for kw in keywords), f"detail 应含 {keywords} 之一，实际: {detail!r}"
    # 校验失败不得落盘
    assert setting_path.read_text(encoding="utf-8") == before


@pytest.fixture
def setting_path(tmp_path, monkeypatch):
    """注入 settings_store.SETTING_PATH 并写入初始配置（含未知键，供深合并断言）"""
    path = tmp_path / "setting.json"
    initial = fake_setting()
    # 未知键/未知段：深合并必须原样保留（gotcha 33 反面——绝不整段覆盖丢键）
    initial["ui_theme"] = "dark"
    initial["llm"]["custom_llm_note"] = "keep-me"
    initial["agent"]["history_keep_last_turns"] = 2
    write_setting_file(path, initial)
    patch_setting_path(monkeypatch, path)
    FakeLLMClient.reset()
    patch_llmclient(monkeypatch, FakeLLMClient)
    yield path
    FakeLLMClient.reset()


@pytest.fixture
def client(tmp_path, setting_path):
    with make_test_client(tmp_path) as c:
        yield c


# ── GET：掩码与形状 ────────────────────────────────────────────────


class TestGetSettings:
    def test_masked_key_never_leaks(self, client, setting_path):
        r = client.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert body["llm"]["api_key_masked"] == MASKED
        # 明文 key 绝不出现在任何响应体里
        assert "sk-testkey0123456789abcd" not in r.text
        assert body["llm"]["key_form"] == "raw"

    def test_shape_llm_agent_embedding_data(self, client):
        body = client.get("/api/settings").json()
        for field in (
            "provider",
            "base_url",
            "api_key_masked",
            "key_form",
            "model",
            "protocol",
            "max_tokens",
            "temperature",
            "timeout",
        ):
            assert field in body["llm"], f"llm 缺字段 {field}"
        assert body["llm"]["protocol"] == "openai"
        assert body["llm"]["model"] == "deepseek-v4-pro"
        for field in ("provider", "model", "api_key_masked", "base_url"):
            assert field in body["embedding"], f"embedding 缺字段 {field}"
        assert body["agent"]["max_rounds"] == 8
        assert body["agent"]["max_rounds_extension"] is None  # 未配置：view 吐 null，前端 fill 显示空（缺省由代码默认）
        assert body["agent"]["verbose"] is False
        assert body["agent"]["enable_judge"] is False
        assert body["data"]["online_fallback"] is True
        assert body["data"]["cache_to_db"] is False
        assert body["data"]["default_source"] == "eastmoney"
        assert "db_path" in body["data"]

    def test_env_ref_key_form_and_embedding_mask(self, tmp_path, monkeypatch):
        setting = fake_setting()
        setting["llm"]["api_key"] = "${MY_DEEPSEEK_KEY}"
        setting["embedding"]["api_key"] = "sk-embedkey1234mbed"
        path = write_setting_file(tmp_path / "setting.json", setting)
        patch_setting_path(monkeypatch, path)
        with make_test_client(tmp_path) as client:
            r = client.get("/api/settings")
            body = r.json()
        assert body["llm"]["key_form"] == "env_ref"
        assert body["embedding"]["api_key_masked"] == "sk-***mbed"
        # 明文 embedding key 绝不泄漏；llm 段不得回吐 api_key 明文/原文字段
        assert "sk-embedkey1234mbed" not in r.text
        assert "api_key" not in body["llm"] or not body["llm"]["api_key"]


# ── PUT：深合并 / api_key 语义 / 原子写 ─────────────────────────────


class TestPutSettings:
    def test_deep_merge_preserves_other_sections_and_unknown_keys(self, client, setting_path):
        r = client.put("/api/settings", json={"llm": {"model": "deepseek-v5"}})
        assert r.status_code == 200
        on_disk = read_setting_file(setting_path)
        # 只改了 llm.model，其余段与未知键原样保留
        assert on_disk["llm"]["model"] == "deepseek-v5"
        assert on_disk["llm"]["api_key"] == "sk-testkey0123456789abcd"
        assert on_disk["llm"]["custom_llm_note"] == "keep-me"
        assert on_disk["embedding"] == fake_setting()["embedding"]
        assert on_disk["agent"]["max_rounds"] == 8
        assert on_disk["agent"]["verbose"] is False
        assert on_disk["agent"]["history_keep_last_turns"] == 2
        assert on_disk["data"]["default_source"] == "eastmoney"
        assert on_disk["ui_theme"] == "dark"
        # 响应同 GET 形状且反映新值
        body = r.json()
        assert body["llm"]["model"] == "deepseek-v5"
        assert body["llm"]["api_key_masked"] == MASKED
        assert "sk-testkey0123456789abcd" not in r.text

    def test_empty_or_missing_api_key_keeps_old(self, client, setting_path):
        # 空串 = 不变（防掩码回写污染）
        r = client.put("/api/settings", json={"llm": {"api_key": "", "temperature": 0.5}})
        assert r.status_code == 200
        assert read_setting_file(setting_path)["llm"]["api_key"] == "sk-testkey0123456789abcd"
        # 不传 api_key = 不变
        r = client.put("/api/settings", json={"llm": {"max_tokens": 4096}})
        assert r.status_code == 200
        assert read_setting_file(setting_path)["llm"]["api_key"] == "sk-testkey0123456789abcd"
        assert read_setting_file(setting_path)["llm"]["max_tokens"] == 4096

    def test_new_key_written_raw_without_dollar_braces(self, client, setting_path):
        r = client.put("/api/settings", json={"llm": {"api_key": "sk-newrealkey9999wxyz"}})
        assert r.status_code == 200
        on_disk = read_setting_file(setting_path)
        # 写回真实 key 不包 ${}（gotcha 34）
        assert on_disk["llm"]["api_key"] == "sk-newrealkey9999wxyz"
        assert r.json()["llm"]["api_key_masked"] == "sk-***wxyz"

    def test_env_ref_key_not_disturbed(self, client, setting_path):
        write_setting_file(
            setting_path,
            {**fake_setting(), "llm": {**fake_setting()["llm"], "api_key": "${MY_DEEPSEEK_KEY}"}},
        )
        r = client.put("/api/settings", json={"llm": {"temperature": 0.1}})
        assert r.status_code == 200
        assert read_setting_file(setting_path)["llm"]["api_key"] == "${MY_DEEPSEEK_KEY}"

    def test_atomic_write_valid_json(self, client, setting_path):
        for payload in (
            {"agent": {"max_rounds": 3}},
            {"data": {"online_fallback": False}},
            {"llm": {"model": "m-again"}},
        ):
            r = client.put("/api/settings", json=payload)
            assert r.status_code == 200
            # 原子写：写完的文件始终是合法 JSON（半截文件会让 load_setting 起不来）
            on_disk = read_setting_file(setting_path)
            assert isinstance(on_disk["llm"]["max_tokens"], int)
        assert json.loads(setting_path.read_text(encoding="utf-8"))["llm"]["model"] == "m-again"

    def test_explicit_null_clears_optional_llm_numbers(self, client, setting_path):
        """设置页「留空保存」契约：max_tokens/temperature/timeout 显式 null = 清除该项
        （回落默认），深合并 null 覆盖旧值，GET/PUT 响应双向往返 null。"""
        r = client.put(
            "/api/settings",
            json={"llm": {"max_tokens": None, "temperature": None, "timeout": None}},
        )
        assert r.status_code == 200, r.text
        on_disk = read_setting_file(setting_path)
        assert on_disk["llm"]["max_tokens"] is None
        assert on_disk["llm"]["temperature"] is None
        assert on_disk["llm"]["timeout"] is None
        body = r.json()
        assert body["llm"]["max_tokens"] is None
        assert body["llm"]["timeout"] is None
        # GET 同口径：null 原样吐回（前端 fill 据此显示空输入）
        body = client.get("/api/settings").json()
        assert body["llm"]["max_tokens"] is None
        assert body["llm"]["timeout"] is None

    def test_max_rounds_extension_round_trip(self, client, setting_path):
        """附加轮次额度：0 合法（关闭附加）；正整数往返；缺省键不落盘（代码默认生效）"""
        r = client.put("/api/settings", json={"agent": {"max_rounds_extension": 0}})
        assert r.status_code == 200, r.text
        assert read_setting_file(setting_path)["agent"]["max_rounds_extension"] == 0
        assert r.json()["agent"]["max_rounds_extension"] == 0
        r = client.put("/api/settings", json={"agent": {"max_rounds_extension": 7}})
        assert r.status_code == 200
        assert read_setting_file(setting_path)["agent"]["max_rounds_extension"] == 7


# ── PUT：校验（4xx + 中文 detail） ─────────────────────────────────


class TestPutValidation:
    def _assert_rejected(self, client, setting_path, payload, *keywords):
        assert_rejected(client, setting_path, payload, *keywords)

    def test_illegal_protocol(self, client, setting_path):
        self._assert_rejected(client, setting_path, {"llm": {"protocol": "gemini"}}, "协议", "protocol")

    def test_illegal_timeout(self, client, setting_path):
        self._assert_rejected(client, setting_path, {"llm": {"timeout": "abc"}}, "数字", "timeout", "秒")

    def test_placeholder_api_key_rejected(self, client, setting_path):
        self._assert_rejected(
            client,
            setting_path,
            {"llm": {"api_key": "sk-your-key-here"}},
            "占位",
            "API Key",
            "api_key",
            "示例",
            "未配置",
        )
        self._assert_rejected(
            client,
            setting_path,
            {"llm": {"api_key": "${}"}},
            "占位",
            "API Key",
            "api_key",
            "示例",
            "未配置",
        )

    def test_illegal_max_rounds_type(self, client, setting_path):
        self._assert_rejected(client, setting_path, {"agent": {"max_rounds": "many"}}, "整数", "数字", "max_rounds")

    def test_illegal_default_source(self, client, setting_path):
        self._assert_rejected(client, setting_path, {"data": {"default_source": "yahoo"}}, "default_source", "数据源", "来源")

    def test_anthropic_with_base_url_rejected(self, client, setting_path):
        """anthropic + base_url 校验：官方 SDK 不收 base_url，带 base_url 切换必须拒绝（中文说明）"""
        before = setting_path.read_text(encoding="utf-8")
        r = client.put(
            "/api/settings",
            json={"llm": {"protocol": "anthropic", "base_url": "https://anthropic.example.com"}},
        )
        assert 400 <= r.status_code < 500
        detail = r.json().get("detail", "")
        assert "base_url" in detail or "Anthropic" in detail or "官方" in detail
        assert setting_path.read_text(encoding="utf-8") == before
        # 清空 base_url 后可切换 anthropic
        r2 = client.put("/api/settings", json={"llm": {"protocol": "anthropic", "base_url": ""}})
        assert r2.status_code == 200
        assert r2.json()["llm"]["protocol"] == "anthropic"
        assert read_setting_file(setting_path)["llm"]["protocol"] == "anthropic"
        assert read_setting_file(setting_path)["llm"]["base_url"] == ""


# ── POST /api/settings/test：探活 ──────────────────────────────────


class TestSettingsTestEndpoint:
    def test_success_reports_latency(self, client, setting_path):
        FakeLLMClient.latency_s = 0.05
        r = client.post("/api/settings/test", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert isinstance(body["latency_ms"], int)
        assert 30 <= body["latency_ms"] < 10000
        # 独立构造 LLMClient，不建 StockAgent；探活消息是 ping
        assert FakeLLMClient.instances, "settings/test 应构造 LLMClient"
        msgs = FakeLLMClient.instances[-1].chat_messages
        assert msgs and msgs[0]["role"] == "user"
        assert "ping" in json.dumps(msgs, ensure_ascii=False).lower()

    def test_failure_reports_error(self, client, setting_path):
        FakeLLMClient.fail_message = "模拟连接被拒"
        r = client.post("/api/settings/test", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "error"
        assert "latency_ms" not in body or body.get("latency_ms") in (None, 0)
        message = body.get("message", "")
        assert isinstance(message, str) and message.strip(), "失败必须给非空原因"
        assert "模拟连接被拒" in message or "失败" in message or "连接" in message

    def test_override_not_persisted(self, client, setting_path):
        r = client.post("/api/settings/test", json={"llm": {"model": "other-model-probe"}})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        # 覆盖仅作用于探活，不落盘
        assert read_setting_file(setting_path)["llm"]["model"] == "deepseek-v4-pro"
        assert FakeLLMClient.instances[-1].setting is not None
        assert FakeLLMClient.instances[-1].setting["llm"]["model"] == "other-model-probe"


# ── PUT：硬段/硬键写坏必须 400（M3 回归） ──────────────────────────


class TestPutHardSections:
    """llm/embedding/agent/data 段与硬键被写坏一律 4xx 中文拒绝，盘上不变。

    反面：非 dict 段值被 deep_merge 整体覆盖写坏；字符串段让 `in` 变子串判断恒放行；
    None 段被 `or {}` 洗成空 dict；数字段在响应组装阶段英文 AttributeError 500。
    """

    def test_non_dict_section_values_rejected(self, client, setting_path):
        for payload in (
            {"agent": "x"},  # 字符串段：旧逻辑子串判断恒 False 放行 → 落盘损坏
            {"llm": None},  # null 段：旧逻辑 or {} 洗成空 dict → 落盘 llm=None
            {"data": 5},  # 数字段：旧逻辑 500 英文 AttributeError
            {"embedding": "oops"},
            {"agent": [1, 2]},
        ):
            assert_rejected(client, setting_path, payload, "JSON 对象", "对象", "段")

    def test_protocol_null_or_empty_rejected(self, client, setting_path):
        # patch 提供了 protocol 键 → null/空 一律拒绝（不能写坏 llm.protocol 硬键）
        assert_rejected(client, setting_path, {"llm": {"protocol": None}}, "protocol", "协议", "openai")
        assert_rejected(client, setting_path, {"llm": {"protocol": ""}}, "protocol", "协议", "openai")
        assert_rejected(client, setting_path, {"llm": {"protocol": "  "}}, "protocol", "协议", "openai")

    def test_max_rounds_null_bool_float_rejected(self, client, setting_path):
        for bad in (None, True, 3.5):
            assert_rejected(client, setting_path, {"agent": {"max_rounds": bad}}, "max_rounds", "整数", "正整数")

    def test_max_rounds_extension_invalid_rejected(self, client, setting_path):
        # 0 合法；null/bool/浮点/负数拒（与 max_rounds 不同的是 0 放行）
        for bad in (None, True, False, 3.5, -1, "10"):
            assert_rejected(
                client, setting_path, {"agent": {"max_rounds_extension": bad}}, "max_rounds_extension", "非负整数"
            )

    def test_verbose_enable_judge_null_rejected(self, client, setting_path):
        # 提供了该键则拒 null（构造 StockAgent 硬索引拿到 None 会炸）
        assert_rejected(client, setting_path, {"agent": {"verbose": None}}, "verbose", "布尔")
        assert_rejected(client, setting_path, {"agent": {"enable_judge": None}}, "enable_judge", "布尔")

    def test_corrupted_section_on_disk_rejected_until_repaired(self, client, setting_path):
        """盘上已有坏段：未修好不许再写回；整段替换可修复（base 非 dict 时 patch 整体覆盖）"""
        broken = fake_setting()
        broken["agent"] = "corrupt-string"
        write_setting_file(setting_path, broken)
        # 只动 llm：合并后 agent 仍是坏值 → 拒绝，不得把坏值再写回
        assert_rejected(client, setting_path, {"llm": {"temperature": 0.1}}, "agent", "JSON 对象", "对象")
        # 整段给出合法 agent → 修复成功
        r = client.put(
            "/api/settings",
            json={"agent": {"max_rounds": 5, "verbose": False, "enable_judge": False}},
        )
        assert r.status_code == 200
        on_disk = read_setting_file(setting_path)
        assert on_disk["agent"] == {"max_rounds": 5, "verbose": False, "enable_judge": False}
        assert on_disk["llm"]["temperature"] == 0.3  # 坏段修复不误伤其他段

    def test_get_tolerates_corrupted_sections(self, client, setting_path):
        """坏段/空段不得让响应组装 500（构造路径另有校验兜底）"""
        broken = fake_setting()
        broken["llm"] = None
        broken["agent"] = "oops"
        write_setting_file(setting_path, broken)
        r = client.get("/api/settings")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["llm"], dict) and isinstance(body["agent"], dict)
        assert body["llm"]["model"] is None
        assert body["agent"]["max_rounds"] is None


# ── PUT：掩码值不得当真实 api_key 写回（m3 回归） ──────────────────


class TestMaskedKeyNotWrittenBack:
    """GET 吐出的 api_key_masked 若被前端原样 PUT 回来，绝不能把真 key 覆盖成掩码字符串"""

    def test_masked_values_rejected(self, client, setting_path):
        for bad in (
            MASKED,  # GET 返回的 api_key_masked 原样
            "***abcd",
            "sk-***zzzz",
            "sk-***",  # 任意含 *** 形态
        ):
            assert_rejected(
                client,
                setting_path,
                {"llm": {"api_key": bad}},
                "请填写",
                "留空",
                "掩码",
                "完整",
            )
        # embedding 段同口径
        assert_rejected(
            client,
            setting_path,
            {"embedding": {"api_key": "sk-***mbed"}},
            "请填写",
            "留空",
            "掩码",
            "完整",
        )
        # 真 key 未被掩码覆盖
        assert read_setting_file(setting_path)["llm"]["api_key"] == "sk-testkey0123456789abcd"

    def test_full_key_still_accepted(self, client, setting_path):
        r = client.put("/api/settings", json={"llm": {"api_key": "sk-realfreshkey0001wxyz"}})
        assert r.status_code == 200
        assert read_setting_file(setting_path)["llm"]["api_key"] == "sk-realfreshkey0001wxyz"


# ── data 写后钩子：重载的是本次写入的文件（M8 回归） ───────────────


class TestDataReloadHook:
    def test_hook_reloads_written_file_not_project_root(self, tmp_path, monkeypatch):
        """钩子必须按 settings_store.SETTING_PATH 重载：PROJECT_ROOT 下放诱饵配置做反面对照"""
        import deeppulse.config as config

        # 诱饵：PROJECT_ROOT/setting.json 的 data 段是反面值（钩子读错文件就会取到它）
        decoy_root = tmp_path / "decoy_root"
        decoy_root.mkdir()
        write_setting_file(
            decoy_root / "setting.json",
            {
                "data": {
                    "online_fallback": True,
                    "cache_to_db": False,
                    "default_source": "eastmoney",
                    "db_path": None,
                }
            },
        )
        monkeypatch.setattr(config, "PROJECT_ROOT", decoy_root)

        # 本次写入的文件（SETTING_PATH）初始为默认值，PUT 后应变成 tencent/False/True
        setting = fake_setting()
        path = write_setting_file(tmp_path / "written_setting.json", setting)
        patch_setting_path(monkeypatch, path)
        # 预置模块常量为第三值：钩子若没跑/跑错文件，都到不了 tencent
        monkeypatch.setattr(config, "DATA_DEFAULT_SOURCE", "sina")
        monkeypatch.setattr(config, "DATA_ONLINE_FALLBACK", True)
        monkeypatch.setattr(config, "DATA_CACHE_TO_DB", False)

        with make_test_client(tmp_path) as client:
            r = client.put(
                "/api/settings",
                json={"data": {"default_source": "tencent", "online_fallback": False, "cache_to_db": True}},
            )
        assert r.status_code == 200
        assert config.DATA_DEFAULT_SOURCE == "tencent"
        assert config.DATA_ONLINE_FALLBACK is False
        assert config.DATA_CACHE_TO_DB is True

    def test_reload_data_settings_optional_path(self, tmp_path, monkeypatch):
        """reload_data_settings(path) 可选参数：带 path 读指定文件，缺省仍读 PROJECT_ROOT/setting.json"""
        import deeppulse.config as config

        decoy_root = tmp_path / "root"
        decoy_root.mkdir()
        write_setting_file(decoy_root / "setting.json", {"data": {"default_source": "sina", "online_fallback": True}})
        monkeypatch.setattr(config, "PROJECT_ROOT", decoy_root)
        monkeypatch.setattr(config, "DATA_DEFAULT_SOURCE", "eastmoney")

        custom = write_setting_file(
            tmp_path / "custom_setting.json",
            {"data": {"default_source": "baostock", "online_fallback": False, "cache_to_db": True}},
        )
        # 缺省：向后兼容读 PROJECT_ROOT/setting.json
        config.reload_data_settings()
        assert config.DATA_DEFAULT_SOURCE == "sina"
        assert config.DATA_ONLINE_FALLBACK is True
        # 指定 path：读的是传入文件
        config.reload_data_settings(custom)
        assert config.DATA_DEFAULT_SOURCE == "baostock"
        assert config.DATA_ONLINE_FALLBACK is False
        assert config.DATA_CACHE_TO_DB is True


# ── 并发写：进程级锁 + 唯一 tmp + fsync（M9 回归） ─────────────────


class TestConcurrentWrites:
    def test_concurrent_update_settings_no_lost_updates(self, setting_path):
        """多线程并发 update_settings：不抛异常、不丢更新、读侧始终合法 JSON"""
        from web.service import settings_store

        errors: list = []
        invalid_reads: list = []
        stop = threading.Event()
        n_threads, n_rounds = 8, 5

        def worker(i: int):
            try:
                for _ in range(n_rounds):
                    settings_store.update_settings({f"worker_{i}_key": i})
            except Exception as e:  # noqa: BLE001 —— 收集后统一断言
                errors.append(e)

        def reader():
            # 与写并发的读：replace 是原子的，路径任何时刻都必须是合法 JSON
            while not stop.is_set():
                try:
                    json.loads(setting_path.read_text(encoding="utf-8"))
                except Exception as e:  # noqa: BLE001
                    invalid_reads.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        rt = threading.Thread(target=reader)
        rt.start()
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        stop.set()
        rt.join()

        assert not errors, f"并发 update_settings 不应抛异常: {errors!r}"
        assert not invalid_reads, f"写过程中文件必须始终合法 JSON: {invalid_reads[:3]!r}"
        on_disk = read_setting_file(setting_path)
        for i in range(n_threads):
            assert on_disk.get(f"worker_{i}_key") == i, f"RMW 丢更新：worker_{i}_key 缺失"
        # 原有内容仍在（深合并不整段覆盖）
        assert on_disk["llm"]["api_key"] == "sk-testkey0123456789abcd"
        assert on_disk["ui_theme"] == "dark"

    def test_tmp_files_cleaned_up(self, setting_path):
        from web.service import settings_store

        settings_store.update_settings({"llm": {"model": "m-tmp-check"}})
        leftovers = list(setting_path.parent.glob(f"{setting_path.name}.tmp.*"))
        assert leftovers == [], f"临时文件必须清理干净: {leftovers}"
