"""Web 静态资源测试：入口页/资产可达、全 SVG 零 emoji、图标名闭环、markdown.js 双环境导出。

前端文件属 impl:frontend 所有权——本文件只做验收断言，不修改 web/static。
另含前端缺陷回归用例（node 子进程直测 app.js / markdown.js 纯逻辑与关键 UI 行为）：
M5 历史回灌工具映射、M6 anthropic 保存死锁、M7 延迟 abort 误杀、m5 嵌套占位符、
m6 删除会话 busy 防护、m7 首条消息连发竞态、m8 流结束强制滚底、m9 展开态丢失、
m10 会话列表消息数、m11 顶栏丢弃顺序与 eye 图标。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fakes_web import make_test_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "web" / "static"

# 契约 §4.4 必备用名（全 SVG，零 emoji）
REQUIRED_ICONS = [
    "logo",
    "plus",
    "send",
    "stop",
    "gear",
    "trash",
    "edit",
    "chat",
    "clock",
    "refresh",
    "chart-line",
    "wallet",
    "star",
    "book",
    "search",
    "eye",
    "check",
    "x",
    "chevron-down",
    "chevron-right",
    "warning",
    "sparkle",
    "brain",
    "wrench",
    "database",
    "shield",
    "layers",
    "menu",
    "user",
    "zap",
    "sun",
    "moon",
]

# emoji 码点兜底：图标必须全 SVG，静态目录不得出现 emoji
EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff"  # 象形文字/交通/补充-A
    "\U0001f1e6-\U0001f1ff"  # 区域指示符（旗帜）
    "☀-➿"  # 杂项符号/装饰符号（含 U+2705/U+270F 等）
    "⯀-⯿"  # 杂项符号与箭头（含 U+2B50 等）
    "️"  # 变体选择符
    "]"
)

TEXT_SUFFIXES = {".html", ".css", ".js", ".svg", ".json", ".map", ".txt", ".md"}


@pytest.fixture
def client(tmp_path):
    with make_test_client(tmp_path) as c:
        yield c


def _static_files() -> list:
    if not STATIC_DIR.is_dir():
        return []
    return sorted(p for p in STATIC_DIR.rglob("*") if p.is_file())


def _read_static(name: str) -> str:
    path = STATIC_DIR / name
    assert path.is_file(), f"缺少静态文件 web/static/{name}"
    return path.read_text(encoding="utf-8")


# ── 页面与资产可达 ────────────────────────────────────────────────


class TestStaticServing:
    def test_index_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        text = r.text
        assert "<html" in text.lower()
        # 入口页应挂上无构建产物的原生资源
        assert "app.js" in text or "app.css" in text

    def test_static_assets_reachable(self, client):
        names = [p.name for p in _static_files() if p.suffix in TEXT_SUFFIXES]
        assert names, f"web/static 下没有静态资产（目录: {STATIC_DIR}）"
        for name in names:
            r = client.get(f"/static/{name}")
            assert r.status_code == 200 and r.content, f"/static/{name} 不可达：{r.status_code}"
            # 防 SPA 回退假阳性：js/css 响应不得是 html 外壳
            if not name.endswith(".html"):
                assert "<html" not in r.text[:200].lower(), f"/static/{name} 被 SPA 回退顶包"

    def test_index_referenced_assets_served(self, client):
        """index.html 引用的每个 /static/* 资源必须真实可达（浏览器首屏样式与脚本依赖）"""
        import re

        html = client.get("/").text
        urls = sorted(set(re.findall(r'(?:href|src)="(/static/[^"]+)"', html)))
        assert urls, "index.html 未引用任何 /static/ 资源"
        for url in urls:
            r = client.get(url)
            assert r.status_code == 200 and r.content, f"index.html 引用的 {url} 不可达（{r.status_code}）"


# ── 零 emoji / 图标闭环 ───────────────────────────────────────────


class TestIcons:
    def test_static_dir_contains_no_emoji(self):
        files = _static_files()
        assert files, f"web/static 为空（目录: {STATIC_DIR}）"
        offenders = []
        for path in files:
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for m in EMOJI_RE.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                offenders.append(f"{path.name}:{line} U+{ord(m.group()):04X} {m.group()!r}")
        assert not offenders, "static 目录出现 emoji 码点（图标必须全 SVG）：" + "; ".join(offenders[:10])

    def test_required_icon_names_defined(self):
        icons_js = _read_static("icons.js")
        missing = [n for n in REQUIRED_ICONS if f"i-{n}" not in icons_js]
        assert not missing, f"icons.js 缺少必备图标定义: {missing}"

    def test_index_icon_refs_all_defined(self):
        icons_js = _read_static("icons.js")
        refs = set()
        for path in _static_files():
            if path.suffix.lower() not in {".html", ".js", ".css"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            refs.update(re.findall(r"icon\(\s*[\"']([a-z0-9-]+)[\"']", text))
            refs.update(re.findall(r"#i-([a-z0-9-]+)", text))
            refs.update(re.findall(r"data-icon=[\"']([a-z0-9-]+)[\"']", text))
        assert refs, "未从 index.html/app.js 提取到任何 icon 引用"
        missing = sorted(n for n in refs if f"i-{n}" not in icons_js)
        assert not missing, f"引用了未定义的图标名: {missing}"

    def test_eye_icon_defined_and_used_for_key_toggle(self):
        """m11(3)：密钥显隐开关用专用 eye 图标，不再拿 search 代用"""
        icons_js = _read_static("icons.js")
        assert "i-eye" in icons_js, "icons.js 应定义 i-eye symbol id"
        assert re.search(r"eye:\s*'<path", icons_js), "eye 需要专用 SVG 图形（不是复用 search）"
        html = _read_static("index.html")
        m = re.search(r'id="toggleKey"[^>]*data-icon="([a-z0-9-]+)"', html)
        assert m, "index.html 缺少 #toggleKey 显隐开关"
        assert m.group(1) == "eye", f"密钥显隐开关应使用 eye 图标，实际：{m.group(1)}"


# ── markdown.js：双环境导出 + XSS 转义 ────────────────────────────


class TestMarkdownModule:
    def test_dual_env_export_declared(self):
        source = _read_static("markdown.js")
        assert "DeepPulseMarkdown" in source, "应导出 window.DeepPulseMarkdown 供单测"
        assert "module.exports" in source, "应兼容 node require（双环境导出）"

    def test_node_render_escapes_script_tags(self, tmp_path):
        if not shutil.which("node"):
            pytest.skip("环境无 node，跳过 markdown.js 纯函数单测")
        md_path = (STATIC_DIR / "markdown.js").resolve()
        assert md_path.is_file(), "缺少 web/static/markdown.js"
        script = f"""
const mod = require({json.dumps(str(md_path))});
if (!mod || typeof mod.render !== "function") {{
    console.error("FAIL: module.exports.render 不是函数");
    process.exit(2);
}}
if (typeof mod.escapeHtml !== "function") {{
    console.error("FAIL: module.exports.escapeHtml 不是函数");
    process.exit(3);
}}
const out = mod.render("<script>alert(1)</script>\\n\\n**bold**\\n");
if (out.includes("<script")) {{
    console.error("FAIL: XSS 未转义: " + out);
    process.exit(4);
}}
if (!out.includes("<strong>bold</strong>")) {{
    console.error("FAIL: **bold** 未渲染为 strong: " + out);
    process.exit(5);
}}
const esc = mod.escapeHtml("<img src=x onerror=alert(1)>");
if (esc.includes("<img")) {{
    console.error("FAIL: escapeHtml 未转义: " + esc);
    process.exit(6);
}}
console.log("OK");
"""
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, f"markdown.js 校验失败: {proc.stdout}{proc.stderr}"
        assert "OK" in proc.stdout


# ══════════════════════════════════════════════════════════════════
# 前端缺陷回归（node 子进程：真实消息形状 / 真实 UI 对象行为）
# ══════════════════════════════════════════════════════════════════

# 极简 DOM 桩：按 selector 记忆化假元素，够 app.js 启动与 UI 对象单测。
# body 里通过 __els / __makeEl 取用；断言失败 process.exit(1)，成功打印 OK。
HARNESS_JS = r"""
function __makeClassList() {
  var s = Object.create(null);
  return {
    add: function () { for (var i = 0; i < arguments.length; i++) s[arguments[i]] = 1; },
    remove: function () { for (var i = 0; i < arguments.length; i++) delete s[arguments[i]]; },
    toggle: function (c, force) {
      var on = force === undefined ? !s[c] : !!force;
      if (on) s[c] = 1; else delete s[c];
      return on;
    },
    contains: function (c) { return !!s[c]; }
  };
}
function __makeEl() {
  var el = {
    style: {}, children: [], _listeners: {}, _attrs: {}, dataset: {},
    classList: __makeClassList(),
    className: '', innerHTML: '', textContent: '', value: '', checked: false,
    disabled: false, title: '', placeholder: '', type: 'text',
    scrollTop: 0, scrollHeight: 1000, clientHeight: 400,
    addEventListener: function (t, fn) { (el._listeners[t] = el._listeners[t] || []).push(fn); },
    removeEventListener: function () {},
    appendChild: function (c) { el.children.push(c); return c; },
    removeChild: function (c) { return c; },
    replaceWith: function () {},
    focus: function () {}, select: function () {},
    setAttribute: function (k, v) { el._attrs[k] = String(v); },
    getAttribute: function (k) { return k in el._attrs ? el._attrs[k] : null; },
    closest: function () { return null; },
    getBoundingClientRect: function () { return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0 }; },
    querySelector: function (sel) { return __doc.querySelector(sel); },
    querySelectorAll: function () { return []; }
  };
  return el;
}
var __els = Object.create(null);
var __doc = {
  readyState: 'complete',
  hidden: false,
  documentElement: null, // 下方回填（须先有 __makeEl）
  createElement: function () { return __makeEl(); },
  querySelector: function (sel) { if (!__els[sel]) __els[sel] = __makeEl(); return __els[sel]; },
  querySelectorAll: function () { return []; },
  addEventListener: function () {}
};
__doc.documentElement = __makeEl();
global.document = __doc;
var __store = Object.create(null);
global.window = {
  DeepPulseMarkdown: require(__MARKDOWN_JS__),
  DeepPulseIcons: require(__ICONS_JS__),
  confirm: function () { return true; },
  localStorage: {
    getItem: function (k) { return k in __store ? __store[k] : null; },
    setItem: function (k, v) { __store[k] = String(v); },
    removeItem: function (k) { delete __store[k]; }
  },
  matchMedia: function (q) {
    return {
      media: q,
      get matches() { return !!global.__mqLight; },
      addEventListener: function (ev, cb) { if (ev === 'change') __mqListeners.push(cb); },
      addListener: function (cb) { __mqListeners.push(cb); }
    };
  }
};
global.localStorage = global.window.localStorage; // 浏览器里 localStorage 亦是全局
var __mqListeners = []; // 捕获 matchMedia change 监听；__mqLight 翻转后逐个触发模拟系统换肤
global.fetch = async function () {
  return { ok: true, status: 200, text: async function () { return '{}'; } };
};
"""

JS_ASSERT = r"""
function ok(cond, name) {
  if (!cond) { console.error("FAIL: " + name); process.exit(1); }
}
"""


def _run_node(tmp_path: Path, body: str) -> None:
    """组装并执行 node 脚本（统一挂 DOM 桩）；body 成功路径须打印 OK。"""
    if not shutil.which("node"):
        pytest.skip("环境无 node，跳过前端纯逻辑单测")
    script = JS_ASSERT + HARNESS_JS + "\n" + body
    script = script.replace("__MARKDOWN_JS__", json.dumps(str((STATIC_DIR / "markdown.js").resolve())))
    script = script.replace("__ICONS_JS__", json.dumps(str((STATIC_DIR / "icons.js").resolve())))
    script = script.replace("__APP_JS__", json.dumps(str((STATIC_DIR / "app.js").resolve())))
    path = tmp_path / "case.js"
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, f"node 用例失败:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "OK" in proc.stdout, f"node 用例未输出 OK: {proc.stdout}{proc.stderr}"


class TestStoredMessageMapping:
    """M5：历史回灌工具结果映射（anthropic 全丢 / openai 并行错位）"""

    def test_anthropic_tool_result_maps_by_tool_use_id(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
// 真实 anthropic 形状：tool_use 在 assistant，tool_result 包在 role=user 里
const out = mod.normalizeStoredMessages([
  { role: "system", content: "sys" },
  { role: "user", content: "帮我看看600519" },
  { role: "assistant", content: [
    { type: "thinking", thinking: "想一想" },
    { type: "text", text: "调用工具" },
    { type: "tool_use", id: "tu_1", name: "query_stock", input: { code: "600519" } }
  ]},
  { role: "user", content: [
    { type: "tool_result", tool_use_id: "tu_1", content: "{\"price\": 1700}" }
  ]},
  { role: "assistant", content: [{ type: "text", text: "结论" }] }
]);
ok(out.length === 3, "展示 3 条（user + 2 assistant），工具结果包裹的 user 不落气泡，实际 " + out.length);
ok(out[0].role === "user" && out[0].text === "帮我看看600519", "用户消息保留");
ok(out[1].role === "assistant" && out[1].thinking === "想一想", "thinking 保留");
ok(out[1].text === "调用工具", "assistant 正文保留");
ok(out[1].tools.length === 1 && out[1].tools[0].name === "query_stock", "tool_use 落成工具卡");
ok(out[1].tools[0].result === "{\"price\": 1700}", "tool_result 回填进工具卡（不得显示无结果），实际 " + JSON.stringify(out[1].tools[0].result));
ok(out[2].text === "结论", "后续 assistant 正常");
console.log("OK");
process.exit(0);
""",
        )

    def test_anthropic_tool_result_out_of_order_matches_id(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
// 乱序回灌 + content 为块数组：必须按 tool_use_id 对号入座
const out = mod.normalizeStoredMessages([
  { role: "assistant", content: [
    { type: "tool_use", id: "tu_A", name: "a", input: {} },
    { type: "tool_use", id: "tu_B", name: "b", input: {} }
  ]},
  { role: "user", content: [
    { type: "tool_result", tool_use_id: "tu_B", content: [{ type: "text", text: "B结果" }] }
  ]},
  { role: "user", content: [
    { type: "tool_result", tool_use_id: "tu_A", content: "A结果" }
  ]}
]);
ok(out.length === 1 && out[0].tools.length === 2, "一条 assistant 两张工具卡");
ok(out[0].tools[0].result === "A结果", "tu_A 结果对号，实际 " + out[0].tools[0].result);
ok(out[0].tools[1].result === "B结果", "tu_B 结果对号，实际 " + out[0].tools[1].result);
console.log("OK");
process.exit(0);
""",
        )

    def test_openai_parallel_tool_calls_map_by_tool_call_id(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
// 真实 openai 形状：并行 tool_calls + 逐条 role=tool（旧实现 A→B 错位、B 被丢）
const out = mod.normalizeStoredMessages([
  { role: "user", content: "分析" },
  { role: "assistant", content: "", tool_calls: [
    { id: "call_A", type: "function", function: { name: "query_stock", arguments: "{\"code\":\"600519\"}" } },
    { id: "call_B", type: "function", function: { name: "query_index", arguments: "{\"code\":\"000001\"}" } }
  ]},
  { role: "tool", tool_call_id: "call_A", content: "结果A" },
  { role: "tool", tool_call_id: "call_B", content: "结果B" }
]);
ok(out.length === 2, "user + assistant 两条");
const tools = out[1].tools;
ok(tools.length === 2, "两张工具卡，实际 " + tools.length);
ok(tools[0].name === "query_stock" && tools[0].result === "结果A", "A 的结果进 A（不得串到 B），实际 " + JSON.stringify(tools[0]));
ok(tools[1].name === "query_index" && tools[1].result === "结果B", "B 的结果不得丢弃，实际 " + JSON.stringify(tools[1]));
console.log("OK");
process.exit(0);
""",
        )

    def test_tool_result_without_id_fills_first_unfilled(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
// 拿不到 tool_call_id 时按顺序填第一个未填结果的工具
const out = mod.normalizeStoredMessages([
  { role: "assistant", content: "", tool_calls: [
    { id: "call_A", function: { name: "a", arguments: "{}" } },
    { id: "call_B", function: { name: "b", arguments: "{}" } }
  ]},
  { role: "tool", content: "第一结果" },
  { role: "tool", content: "第二结果" }
]);
const tools = out[0].tools;
ok(tools[0].result === "第一结果" && tools[1].result === "第二结果",
   "无 id 顺序填充不得错位，实际 " + tools.map(t => t.result).join(" / "));
console.log("OK");
process.exit(0);
""",
        )


class TestMarkdownNestedPlaceholders:
    """m5：链接标签内含行内代码时嵌套占位符不二次还原 → 渲染损坏"""

    def test_code_inside_link_label_renders(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const md = require(__MARKDOWN_JS__);
const out = md.render("[`600519`](https://x.example/a)");
ok(!/[\uE000-\uE001]/.test(out), "不得残留 PUA 占位符: " + JSON.stringify(out));
ok(out.indexOf('<a href="https://x.example/a"') >= 0, "链接渲染: " + out);
ok(out.indexOf("<code>600519</code>") >= 0, "行内代码保留在链接标签内: " + out);
ok(!/>\s*0\s*</.test(out), "不得渲染成 <a>0</a>: " + out);
console.log("OK");
process.exit(0);
""",
        )

    def test_nested_combination_renders(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const md = require(__MARKDOWN_JS__);
const out = md.render("看 [`600519`](https://a.example) 和 [`000001`](https://b.example) **加粗** `裸代码`");
ok(!/[\uE000-\uE001]/.test(out), "组合嵌套不得残留 PUA: " + JSON.stringify(out));
ok(out.indexOf("<code>600519</code>") >= 0 && out.indexOf("<code>000001</code>") >= 0, "两处代码完整: " + out);
ok(out.indexOf("<strong>加粗</strong>") >= 0, "粗体正常: " + out);
ok(out.indexOf("<code>裸代码</code>") >= 0, "独立行内代码正常: " + out);
console.log("OK");
process.exit(0);
""",
        )


class TestToolCardDisplay:
    """工具卡展示截断契约：超 2000 才截+提示，短结果不得挂假提示（SSE/回灌同口径）"""

    def test_truncate_note_only_when_cut(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
let html = mod.chatUI.toolCardHtml({ name: "list_factors", args: { category: "x" }, result: "短结果" }, true);
ok(html.indexOf('展示为截断') < 0, '短结果不得挂截断提示');
ok(html.indexOf('短结果') >= 0, '短结果原样展示');
const exact = new Array(2001).join('长');
html = mod.chatUI.toolCardHtml({ name: "t", args: {}, result: exact }, true);
ok(html.indexOf('展示为截断') < 0, '恰 2000 字不截不提示');
const long = new Array(2501).join('长');
html = mod.chatUI.toolCardHtml({ name: "t", args: {}, result: long }, true);
ok(html.indexOf('展示为截断 2000 字') >= 0, '超限挂 2000 字提示');
const shown = html.split('长').length - 1;
ok(shown === 2000, '展示恰 2000 字（回灌路径与 SSE 同口径），实际 ' + shown);
console.log("OK");
process.exit(0);
""",
        )


class TestSettingsProtocolSave:
    """M6：切 anthropic 后保存死锁——base_url 被禁用却仍回传旧值"""

    def test_switch_anthropic_clears_base_url(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
__els["#llmBaseUrl"] = __makeEl();
__els["#llmBaseUrl"].value = "https://old.example/v1";
mod.settingsUI.setProtocol("anthropic");
ok(__els["#llmBaseUrl"].value === "", "选 anthropic 时清空 base_url，实际 " + JSON.stringify(__els["#llmBaseUrl"].value));
ok(__els["#llmBaseUrl"].disabled === true, "同时保持禁用 + 提示");
ok(String(__els["#baseUrlHint"].textContent).indexOf("Anthropic") >= 0, "提示文案同步切换");
console.log("OK");
process.exit(0);
""",
        )

    def test_collect_anthropic_never_sends_base_url(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const fields = {
  "#llmProvider": { value: "anthropic" }, "#llmModel": { value: "claude-sonnet-4-5" },
  "#llmApiKey": { value: "" }, "#llmMaxTokens": { value: "" },
  "#llmTemperature": { value: "" }, "#llmTimeout": { value: "" },
  "#embProvider": { value: "none" }, "#embModel": { value: "" },
  "#embBaseUrl": { value: "" }, "#embApiKey": { value: "" },
  "#agentMaxRounds": { value: "" }, "#agentMaxRoundsExtension": { value: "" },
  "#agentVerbose": { checked: false },
  "#agentJudge": { checked: false }, "#dataOnlineFallback": { checked: true },
  "#dataCacheToDb": { checked: false }, "#dataSource": { value: "baostock" }
};
for (const k in fields) { __els[k] = __makeEl(); Object.assign(__els[k], fields[k]); }
// 禁用输入框仍可能带回旧值——collect 必须与后端校验口径对齐
__els["#llmBaseUrl"] = __els["#llmBaseUrl"] || __makeEl();
__els["#llmBaseUrl"].value = "https://old.example/v1";
__els["#protocolGroup input:checked"] = __makeEl();
__els["#protocolGroup input:checked"].value = "anthropic";
const body = mod.settingsUI.collect();
ok(body.llm.protocol === "anthropic", "协议 anthropic");
ok(body.llm.base_url === "", "anthropic 时 base_url 必须置空（否则 400 保存死锁），实际 " + JSON.stringify(body.llm.base_url));
console.log("OK");
process.exit(0);
""",
        )


class TestStopAnalysisAbort:
    """M7：stopAnalysis 延迟 abort 误杀下一轮对话流 + m6：删除会话后停止按钮失效"""

    def test_delayed_abort_is_identity_guarded(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const timers = [];
const origSet = global.setTimeout, origClear = global.clearTimeout;
global.setTimeout = function (fn, ms) { const h = { fn: fn, ms: ms, cleared: false }; timers.push(h); return h; };
global.clearTimeout = function (h) { if (h) h.cleared = true; };
(async function () {
  mod.api.post = async function () { return {}; };
  const a = { aborted: false, abort() { this.aborted = true; } };
  const b = { aborted: false, abort() { this.aborted = true; } };
  mod.state.sessionId = "s1";
  mod.state.abortCtrl = a;
  await mod.stopAnalysis();
  const live = timers.filter(t => t.ms === 2500 && !t.cleared);
  ok(live.length === 1, "延迟 abort 定时器存在，实际 " + live.length);
  // 正常停止 → finish() 清空 abortCtrl → 用户 2.5s 内发新问题
  mod.state.abortCtrl = b;
  live.forEach(t => t.fn());
  ok(!a.aborted, "旧流已收束，兜底 abort 不再补刀");
  ok(!b.aborted, "新流 controllerB 不得被旧定时器误杀");
  global.setTimeout = origSet; global.clearTimeout = origClear;
  console.log("OK");
  process.exit(0);
})();
""",
        )

    def test_repeated_stop_keeps_single_timer(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const timers = [];
const origSet = global.setTimeout, origClear = global.clearTimeout;
global.setTimeout = function (fn, ms) { const h = { fn: fn, ms: ms, cleared: false }; timers.push(h); return h; };
global.clearTimeout = function (h) { if (h) h.cleared = true; };
(async function () {
  mod.api.post = async function () { return {}; };
  mod.state.sessionId = "s1";
  mod.state.abortCtrl = { aborted: false, abort() { this.aborted = true; } };
  await mod.stopAnalysis();
  await mod.stopAnalysis();
  await mod.stopAnalysis();
  const live = timers.filter(t => t.ms === 2500 && !t.cleared);
  ok(live.length === 1, "连按停止只挂一个兜底定时器，实际 " + live.length);
  global.setTimeout = origSet; global.clearTimeout = origClear;
  console.log("OK");
  process.exit(0);
})();
""",
        )

    def test_abort_without_session_id(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const timers = [];
const origSet = global.setTimeout, origClear = global.clearTimeout;
global.setTimeout = function (fn, ms) { const h = { fn: fn, ms: ms, cleared: false }; timers.push(h); return h; };
global.clearTimeout = function (h) { if (h) h.cleared = true; };
(async function () {
  mod.api.post = async function () { return {}; };
  // 会话已被删除（sessionId=null）——停止按钮仍须 abort 本地流
  mod.state.sessionId = null;
  const ctrl = { aborted: false, abort() { this.aborted = true; } };
  mod.state.abortCtrl = ctrl;
  await mod.stopAnalysis();
  const live = timers.filter(t => t.ms === 2500 && !t.cleared);
  ok(live.length === 1, "无会话也安排本地 abort，实际 " + live.length);
  live.forEach(t => t.fn());
  ok(ctrl.aborted, "无会话时依然 abort state.abortCtrl");
  global.setTimeout = origSet; global.clearTimeout = origClear;
  console.log("OK");
  process.exit(0);
})();
""",
        )


class TestRemoveSessionBusyGuard:
    """m6：删除当前会话无 busy 防护，且删除后停止按钮失效"""

    def test_remove_blocked_while_busy(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
let delCalls = 0;
mod.api.del = async function () { delCalls++; return {}; };
mod.state.busy = true;
mod.state.sessionId = "s1";
mod.sessionsUI.remove({ id: "s1", title: "当前会话" });
setTimeout(function () {
  ok(delCalls === 0, "busy 时删除必须拦截（不得摘掉渲染节点），实际 del=" + delCalls);
  console.log("OK");
  process.exit(0);
}, 30);
""",
        )


class TestSendMessageRace:
    """m7：首条消息双击/Enter 连发竞态——busy 标志置位太晚"""

    def test_double_submit_creates_single_session(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const input = __els["#chatInput"] = __makeEl();
input.value = "第一问";
let createCalls = 0;
let seen = null;
mod.sessionsUI.createSession = async function () {
  createCalls++;
  seen = { busy: mod.state.busy, input: input.value };
  await new Promise(function (r) { setTimeout(r, 20); });
  mod.state.sessionId = "s1";
  return { id: "s1" };
};
(async function () {
  const p1 = mod.sendMessage();
  const p2 = mod.sendMessage();
  await Promise.all([p1, p2]);
  ok(createCalls === 1, "双击/Enter 连发只允许一次建会话，实际 " + createCalls);
  ok(seen && seen.busy === true, "进入 sendMessage 后同步置忙（须在 createSession await 之前）");
  ok(seen && seen.input === "", "先清空输入框再建会话（二次提交拿不到文本）");
  console.log("OK");
  process.exit(0);
})();
""",
        )


class TestStreamRenderBehavior:
    """m8 流结束强制滚底 / m9 展开态丢失 / m10 会话列表消息数"""

    def test_flush_render_respects_stick_bottom(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const scroll = __els["#chatScroll"];
mod.chatUI.scrollEl = scroll;
mod.chatUI.innerEl = __els["#chatInner"];
const msg = { role: "assistant", text: "hi", thinking: "", tools: [], streaming: true, ts: Date.now() };
msg.body = __makeEl();
mod.chatUI.turn = msg;
scroll.scrollTop = 10;
scroll.scrollHeight = 5000;
mod.state.stickBottom = false;
mod.chatUI.flushRender();
ok(scroll.scrollTop === 10, "上翻（stickBottom=false）时 finish 收束不得强制拽底，实际 " + scroll.scrollTop);
mod.state.stickBottom = true;
mod.chatUI.flushRender();
ok(scroll.scrollTop === 5000, "stickBottom 时仍须贴底");
console.log("OK");
process.exit(0);
""",
        )

    def test_open_state_survives_rerender(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const msg = {
  role: "assistant",
  text: "正文",
  thinking: "这是一个足够长的思考内容超过二十个字用来显示折叠框",
  tools: [{ name: "t1", args: {}, result: "" }, { name: "t2", args: {}, result: "" }],
  streaming: true,
  ts: Date.now()
};
msg.body = __makeEl();
msg.openState = { think: true, tools: [false, true] };
mod.chatUI.renderBody(msg);
let html = msg.body.innerHTML;
ok(html.indexOf("think-box open") >= 0, "think 展开态回放");
const cards = html.match(/<div class="tool-card( open)?">/g) || [];
ok(cards.length === 2, "两张工具卡，实际 " + cards.length);
ok(cards[0] === '<div class="tool-card">', "第 1 张工具卡保持收起，实际 " + cards[0]);
ok(cards[1] === '<div class="tool-card open">', "第 2 张工具卡展开（索引对齐），实际 " + cards[1]);
// 流式整块重渲（80ms 节流路径）后仍保持
mod.chatUI.renderBody(msg);
html = msg.body.innerHTML;
ok(html.indexOf("think-box open") >= 0, "重渲后 think 仍展开（不得收回）");
const cards2 = html.match(/<div class="tool-card( open)?">/g) || [];
ok(cards2.length === 2 && cards2[0] === '<div class="tool-card">' && cards2[1] === '<div class="tool-card open">',
   "重渲后工具卡展开态稳定");
console.log("OK");
process.exit(0);
""",
        )

    def test_done_refreshes_session_list(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const input = __els["#chatInput"] = __makeEl();
let refreshed = 0;
mod.sessionsUI.refresh = async function () { refreshed++; };
mod.state.sessionId = "s1";
mod.state.busy = false;
input.value = "第二问";
global.fetch = async function (url) {
  if (url === "/api/chat") {
    const enc = new TextEncoder();
    const chunks = [enc.encode('event: content\ndata: {"text":"你好"}\n\nevent: done\ndata: {}\n\n')];
    let i = 0;
    return {
      status: 200, ok: true,
      body: {
        getReader() {
          return {
            async read() {
              if (i < chunks.length) return { done: false, value: chunks[i++] };
              return { done: true, value: undefined };
            },
            releaseLock() {}
          };
        }
      }
    };
  }
  return { ok: true, status: 200, text: async function () { return '{}'; } };
};
(async function () {
  await mod.sendMessage();
  ok(refreshed >= 1, "done/error 收束后刷新会话列表（message_count 不得停在 0），实际 " + refreshed);
  ok(mod.state.busy === false, "收束后解除 busy");
  ok(mod.state.abortCtrl === null, "收束后 abortCtrl 清空");
  console.log("OK");
  process.exit(0);
})();
""",
        )


class TestTopbarHideOrder:
    """m11(2)：顶栏窄屏丢弃顺序须为 工具数→耗时→会话→模型，R{n} 比模型保留更久"""

    def test_hide_tiers_follow_spec_discard_order(self):
        html = _read_static("index.html")

        def chip_classes(eid: str) -> set:
            m = re.search(r'<span class="([^"]*)" id="' + eid + '"', html)
            assert m, f"index.html 缺少 #{eid}"
            return set(m.group(1).split())

        tools = chip_classes("statTools")
        elapsed = chip_classes("statTime")
        sess = chip_classes("statSession")
        model = chip_classes("statModel")
        rnd = chip_classes("statRound")

        # hide-sm 断点更宽 → 先丢；hide-xs 更窄 → 后丢
        assert "hide-sm" in tools and "hide-xs" not in tools, f"工具数应在 hide-sm 档，实际 {tools}"
        assert "hide-sm" in elapsed and "hide-xs" not in elapsed, f"耗时应在 hide-sm 档，实际 {elapsed}"
        assert "hide-xs" in sess and "hide-sm" not in sess, f"会话应在 hide-xs 档，实际 {sess}"
        assert "hide-xs" in model and "hide-sm" not in model, f"模型应在 hide-xs 档，实际 {model}"
        # R{n} 常驻：比模型保留更久
        assert "hide-sm" not in rnd and "hide-xs" not in rnd, f"轮次徽标不得提前丢弃，实际 {rnd}"

    def test_hide_sm_breakpoint_wider_than_hide_xs(self):
        css = _read_static("app.css")
        i_sm = css.find("hide-sm")
        i_xs = css.find("hide-xs")
        assert i_sm > 0 and i_xs > 0, "app.css 应含 hide-sm / hide-xs 分档"
        m_sm = list(re.finditer(r"@media \(max-width: (\d+)px\)", css[:i_sm]))
        m_xs = list(re.finditer(r"@media \(max-width: (\d+)px\)", css[:i_xs]))
        assert m_sm and m_xs, "hide 规则应位于响应式 @media 分档内"
        w_sm = int(m_sm[-1].group(1))
        w_xs = int(m_xs[-1].group(1))
        assert w_sm > w_xs, f"hide-sm 断点({w_sm}px)须宽于 hide-xs({w_xs}px)——先丢工具数/耗时，后丢会话/模型"


# ── 设置模态布局与功能闭包（回归：内容被裁剪不可滚 / 开关点不动） ──


class TestSettingsModalLayout:
    def _srcs(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
        js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
        return html, css, js

    def test_modal_body_flex_scroll_not_clip(self):
        """回归：列 flex + max-height 容器里 modal-body 缺 min-height:0 会裁掉底部内容且不可滚"""
        _, css, _ = self._srcs()
        m = re.search(r"\.modal-body\s*\{([^}]*)\}", css)
        assert m, "缺少 .modal-body 规则"
        body = m.group(1)
        assert "min-height: 0" in body, "modal-body 必须 min-height:0（flex 子项才可收缩、滚动生效）"
        assert re.search(r"flex:\s*1", body), "modal-body 必须 flex:1 撑满剩余空间"
        assert "overflow-y: auto" in body

    def test_settings_controls_all_present_and_wired(self):
        """功能闭包：设置区每个控件都要在 HTML 定义且被 app.js 读写，不许「存在但没接线」"""
        html, _, js = self._srcs()
        ids = [
            "llmProvider",
            "llmBaseUrl",
            "llmApiKey",
            "llmModel",
            "llmMaxTokens",
            "llmTemperature",
            "llmTimeout",
            "embProvider",
            "embModel",
            "embApiKey",
            "embBaseUrl",
            "agentMaxRounds",
            "agentMaxRoundsExtension",
            "agentVerbose",
            "agentJudge",
            "dataOnlineFallback",
            "dataCacheToDb",
            "dataSource",
            "dataDbPath",
        ]
        for cid in ids:
            assert f'id="{cid}"' in html, f"index.html 缺少控件 {cid}"
            assert f"#{cid}" in js, f"app.js 未引用控件 {cid}"

    def test_switches_are_label_clickable(self):
        """回归：checkbox 藏在 display:none 的 input 里、外面套 span 就永远点不动——必须 label 紧凑包裹

        断言整块结构（含 track/knob 自闭合），防止标签嵌套写坏——曾出现 track 的 </span>
        被吞导致 <label><span> 交错，正则宽松匹配会假阳性。
        """
        html, css, _ = self._srcs()
        block = re.compile(
            r'<label class="switch">\s*'
            r'<input type="checkbox" id="\w+">\s*'
            r'<span class="track"></span>\s*'
            r'<span class="knob"></span>\s*'
            r"</label>"
        )
        blocks = block.findall(html)
        assert len(blocks) == 4, f"应有 4 个结构完好的开关 label 块，实际 {len(blocks)}"
        # 坏嵌套兜底：<label class="switch"> 的块内不得再出现交错（label 收尾必须在 track/knob 之后）
        for m in re.finditer(r'<label class="switch">([\s\S]{0,200}?)</label>', html):
            assert '<input type="checkbox"' in m.group(1)
            assert m.group(1).rstrip().endswith(('<span class="knob"></span>', "</span>")), m.group(1)[-60:]
        assert re.search(r"\.switch input\s*\{[^}]*display:\s*none", css)

    def test_section_nav_targets_exist(self):
        """分区导航的 data-target 必须指向真实 section，且点击/滚动高亮已接线"""
        html, _, js = self._srcs()
        targets = re.findall(r'class="modal-tab[^"]*" data-target="([^"]+)"', html)
        assert targets, "缺少分区导航（modal-tabs）"
        for t in targets:
            assert f'id="{t}"' in html, f"导航目标 #{t} 不存在"
        assert "settingsTabs" in js and "scrollIntoView" in js, "app.js 需实现导航跳转"

    def test_settings_modal_wider_than_default(self):
        """设置内容多，默认 720px 双列过挤——须为设置模态放宽宽度"""
        _, css, _ = self._srcs()
        m = re.search(r"#settingsMask \.modal\s*\{([^}]*)\}", css)
        assert m, "缺少 #settingsMask .modal 宽度覆盖"
        wm = re.search(r"width:\s*min\((\d+)px", m.group(1))
        assert wm and int(wm.group(1)) >= 800, f"设置模态宽度应 ≥800px，实际 {wm.group(1) if wm else '未设置'}"

    def test_index_html_tags_balanced(self):
        """回归：脚本化改 HTML 曾把 <label>/<span> 嵌套改交错（浏览器容错但 label 包不住 input）"""
        from html.parser import HTMLParser

        VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

        class Checker(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stack, self.errors = [], []

            def handle_starttag(self, tag, attrs):
                if tag not in VOID:
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if tag in VOID:
                    return
                if not self.stack or self.stack.pop() != tag:
                    self.errors.append(tag)

        c = Checker()
        c.feed((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
        c.close()
        assert not c.errors, f"标签交错/未闭合：{c.errors}"
        assert not c.stack, f"未闭合标签：{c.stack}"


# ── 设置模态对抗审查回归（7 确认缺陷）：高亮抢锁/末段够不着/吸顶偏移/页签压扁/
# 数字字段空值语义/fill-collect 闭包钉死 ──────────────────────────────────


class TestSettingsNavHighlight:
    """缺陷 1/5 回归：pickActiveTab——点击设的 active 不被滚动高亮抢走、末段永远能选中"""

    def test_pick_active_tab_threshold_rules(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
ok(typeof mod.pickActiveTab === "function", "pickActiveTab 须导出为纯函数");
// 取「最后一个越过 64px 阈值线」的段
ok(mod.pickActiveTab([0, 200, 500], false) === 0, "只有首段越过阈值");
ok(mod.pickActiveTab([-10, 30, 500], false) === 1, "取最后一个越过阈值的段，实际 " + mod.pickActiveTab([-10, 30, 500], false));
ok(mod.pickActiveTab([-10, 30, 64], false) === 2, "恰好越过阈值(<=64)算越过");
ok(mod.pickActiveTab([-10, 30, 65], false) === 1, "阈值是 64px，65 不算越过，实际 " + mod.pickActiveTab([-10, 30, 65], false));
ok(mod.pickActiveTab([100, 200, 500], false) === 0, "都没越过取首段兜底");
ok(mod.pickActiveTab([], true) === 0, "空列表兜底 0");
ok(mod.pickActiveTab(null, false) === 0, "null 兜底 0");
console.log("OK");
process.exit(0);
""",
        )

    def test_pick_active_tab_at_bottom_forces_last(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
// 到底特判：末段尾部不足一屏时段顶永远爬不到阈值线，不强制末段则最后一段页签永远选不中
ok(mod.pickActiveTab([100, 200, 500], true) === 2, "到底强制末段，实际 " + mod.pickActiveTab([100, 200, 500], true));
ok(mod.pickActiveTab([100, 200], true) === 1, "两段同理");
console.log("OK");
process.exit(0);
""",
        )

    def test_highlight_needs_lock_and_at_bottom_guard_in_source(self):
        """行为面接线：点击须有导航锁（否则点击设定的 active 被 debounce 滚动高亮抢走）；
        到底特判须有 maxScroll>0 守卫（否则内容一屏放得下恒「到底」错亮末段）"""
        js = _read_static("app.js")
        assert "navLockUntil" in js, "缺少点击导航锁（缺陷 1）"
        assert re.search(r"maxScroll > 0 && .*scrollTop >= maxScroll", js), "到底特判缺少 maxScroll>0 守卫（缺陷 5）"


class TestSettingsScrollSpacer:
    """缺陷 2 回归：末段尾部不足一屏时 scrollIntoView 被 maxScroll 钳住，须伸缩垫撑出滚动余量"""

    def test_spacer_need_px_formula(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
ok(typeof mod.spacerNeedPx === "function", "spacerNeedPx 须导出为纯函数");
// need = 段顶相对 body 顶偏移 + scrollTop - 8 - maxScroll（8 = .form-section 的 scroll-margin-top）
ok(mod.spacerNeedPx(306, 0, 0) === 298, "无滚动余量补段顶-8，实际 " + mod.spacerNeedPx(306, 0, 0));
ok(mod.spacerNeedPx(300, 100, 100) === 292, "含 scrollTop 公式，实际 " + mod.spacerNeedPx(300, 100, 100));
ok(mod.spacerNeedPx(108, 0, 100) === 0, "恰好可达不补垫");
ok(mod.spacerNeedPx(100, 0, 100) === 0, "已可达不补垫");
ok(mod.spacerNeedPx(109, 0, 100) === 1, "差 1px 补 1px，实际 " + mod.spacerNeedPx(109, 0, 100));
ok(mod.spacerNeedPx(108.5, 0, 100) === 1, "小数向上取整");
console.log("OK");
process.exit(0);
""",
        )

    def test_size_scroll_spacer_writes_height(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const body = __els["#settingsMask .modal-body"] = __makeEl();
const spacer = __els["#settingsScrollSpacer"] = __makeEl();
const sec = __makeEl();
body.scrollTop = 100;
body.clientHeight = 400;
// scrollHeight 随垫高增长（真实布局耦合）：不先归零重测的实现 need 会被旧垫高吃掉
Object.defineProperty(body, "scrollHeight", {
  get: function () { return 1000 + parseFloat(spacer.style.height || "0"); }
});
body.getBoundingClientRect = function () { return { top: 100 }; };
var sawZero = false;
sec.getBoundingClientRect = function () {
  sawZero = sawZero || spacer.style.height === "0px" || spacer.style.height === "";
  return { top: 900 };  // 段顶相对 body 顶偏移 800
};
__doc.querySelectorAll = function (sel) {
  return sel === "#settingsMask .form-section" ? [sec] : [];
};
// need = 800 + 100 - 8 - 600 = 292
mod.settingsUI.sizeScrollSpacer();
ok(sawZero, "测量前必须先把垫高归零（缺陷 2：旧垫高混进 scrollHeight 会把 need 算成 0）");
ok(spacer.style.height === "292px", "垫高 = 段顶偏移+scrollTop-8-maxScroll，实际 " + spacer.style.height);
mod.settingsUI.sizeScrollSpacer();
ok(spacer.style.height === "292px", "重复调用幂等，实际 " + spacer.style.height);
sec.getBoundingClientRect = function () { return { top: 100 }; };  // 偏移 0，无需垫
mod.settingsUI.sizeScrollSpacer();
ok(spacer.style.height === "0px", "可直达时垫高归零，实际 " + spacer.style.height);
__doc.querySelectorAll = function () { return []; };
spacer.style.height = "50px";
mod.settingsUI.sizeScrollSpacer();
ok(spacer.style.height === "0px", "无段可测时垫高归零，实际 " + spacer.style.height);
console.log("OK");
process.exit(0);
""",
        )

    def test_spacer_and_sticky_css_contract(self):
        """HTML/CSS/JS 三处接线：伸缩垫元素 + 吸顶负偏移 + 垫高重算触点"""
        html = _read_static("index.html")
        css = _read_static("app.css")
        js = _read_static("app.js")
        assert 'id="settingsScrollSpacer"' in html, "缺伸缩垫元素（缺陷 2）"
        m = re.search(r"\.modal-scroll-spacer\s*\{([^}]*)\}", css)
        assert m and re.search(r"flex:\s*none", m.group(1)), "伸缩垫须 flex:none（不许被压缩吸收）"
        # 缺陷 3：sticky top 被 modal-body padding-top 顶偏 16px，负偏移抵掉
        body_m = re.search(r"\.modal-body\s*\{([^}]*)\}", css)
        assert body_m and re.search(r"--body-pad-top:\s*16px", body_m.group(1)), (
            "modal-body 须声明 --body-pad-top: 16px"
        )
        head_m = re.search(r"\.form-section-head\s*\{([^}]*)\}", css)
        assert head_m, "缺少 .form-section-head 吸顶规则"
        head_norm = re.sub(r"\s+", "", head_m.group(1))
        assert "calc(0px-var(--body-pad-top" in head_norm, "吸顶 top 须抵掉 body 内边距（缺陷 3）"
        assert re.search(r"\.form-section\s*\{[^}]*scroll-margin-top:\s*8px", css), (
            "scroll-margin-top:8px 是 spacerNeedPx 公式的落点契约"
        )
        # 缺陷 4：列 flex 收缩把页签行压到 23px——头/页签/底栏必须锁高
        for sel in (".modal-head", ".modal-tabs", ".modal-foot"):
            m2 = re.search(re.escape(sel) + r"\s*\{([^}]*)\}", css)
            assert m2, f"缺少 {sel} 规则"
            assert re.search(r"flex:\s*none", m2.group(1)), f"{sel} 须 flex:none 锁高（缺陷 4）"
        # 重算触点：打开/测试连接/窗口缩放都要重算垫高
        assert js.count("sizeScrollSpacer") >= 4, "sizeScrollSpacer 须有定义 + open/test/resize 三个触点"
        assert "this.sizeScrollSpacer()" in js and "settingsUI.sizeScrollSpacer()" in js


class TestSettingsFieldClosure:
    """缺陷 6/7 回归：fill/collect 双向闭包钉死 + 数字字段空值语义"""

    def test_fill_collect_closure_all_controls(self):
        """每个设置控件都要 fill 回填且 collect 采集；dataDbPath 只读白名单除外（只展示不提交）"""
        js = _read_static("app.js")
        i_fill = js.index("fill(data) {")
        i_collect = js.index("collect() {")
        i_end = js.index("emptyValueNote() {")
        assert i_fill < i_collect < i_end, "fill/collect/emptyValueNote 方法顺序变化，闭包切片需同步"
        fill_body = js[i_fill:i_collect]
        collect_body = js[i_collect:i_end]
        ids = [
            "llmProvider",
            "llmBaseUrl",
            "llmApiKey",
            "llmModel",
            "llmMaxTokens",
            "llmTemperature",
            "llmTimeout",
            "embProvider",
            "embModel",
            "embApiKey",
            "embBaseUrl",
            "agentMaxRounds",
            "agentMaxRoundsExtension",
            "agentVerbose",
            "agentJudge",
            "dataOnlineFallback",
            "dataCacheToDb",
            "dataSource",
            "dataDbPath",
        ]
        for cid in ids:
            assert f"#{cid}" in fill_body, f"fill() 未回填控件 {cid}（打开设置静默丢字段）"
            if cid == "dataDbPath":
                assert f"#{cid}" not in collect_body, "dataDbPath 只读展示，collect 不得提交（防写回 setting.json）"
            else:
                assert f"#{cid}" in collect_body, f"collect() 未采集控件 {cid}（保存静默丢字段）"

    def test_number_field_empty_semantics(self, tmp_path):
        """清空语义：max_tokens/timeout → 显式 null（清除该项）；max_rounds → 键缺席（保持原值）"""
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const fields = {
  "#llmProvider": { value: "deepseek" }, "#llmModel": { value: "m" },
  "#llmApiKey": { value: "" }, "#llmMaxTokens": { value: "" },
  "#llmTemperature": { value: "" }, "#llmTimeout": { value: "" },
  "#embProvider": { value: "none" }, "#embModel": { value: "" },
  "#embBaseUrl": { value: "" }, "#embApiKey": { value: "" },
  "#agentMaxRounds": { value: "" }, "#agentMaxRoundsExtension": { value: "" },
  "#agentVerbose": { checked: false },
  "#agentJudge": { checked: false }, "#dataOnlineFallback": { checked: true },
  "#dataCacheToDb": { checked: false }, "#dataSource": { value: "baostock" }
};
for (const k in fields) { __els[k] = __makeEl(); Object.assign(__els[k], fields[k]); }
__els["#llmBaseUrl"] = __makeEl(); __els["#llmBaseUrl"].value = "";
__els["#protocolGroup input:checked"] = __makeEl();
__els["#protocolGroup input:checked"].value = "openai";
const body = mod.settingsUI.collect();
ok(body.llm.max_tokens === null, "清空 max_tokens → 显式 null（清除该项），实际 " + JSON.stringify(body.llm.max_tokens));
ok(body.llm.timeout === null, "清空 timeout → 显式 null（清除该项），实际 " + JSON.stringify(body.llm.timeout));
ok(body.agent.max_rounds === undefined, "清空 max_rounds → 键缺席（后端拒 null，保持原值），实际 " + JSON.stringify(body.agent.max_rounds));
ok(body.agent.max_rounds_extension === undefined, "清空 max_rounds_extension → 键缺席（保持原值），实际 " + JSON.stringify(body.agent.max_rounds_extension));
// 双向往返：盘上 null 回填为空输入
mod.settingsUI.fill({ llm: { max_tokens: null, timeout: null }, agent: { max_rounds: null, max_rounds_extension: null } });
ok(__els["#llmMaxTokens"].value === "", "盘上 null 回填为空输入");
ok(__els["#llmTimeout"].value === "", "盘上 null 回填为空输入（timeout）");
ok(__els["#agentMaxRounds"].value === "", "盘上 null 回填为空输入（max_rounds）");
ok(__els["#agentMaxRoundsExtension"].value === "", "盘上 null 回填为空输入（max_rounds_extension）");
// 有值时正常提交（0 是合法值——关闭附加额度）
__els["#llmMaxTokens"].value = "2048"; __els["#llmTimeout"].value = "60"; __els["#agentMaxRounds"].value = "9";
__els["#agentMaxRoundsExtension"].value = "0";
const body2 = mod.settingsUI.collect();
ok(body2.llm.max_tokens === 2048, "有值 max_tokens 正常提交，实际 " + JSON.stringify(body2.llm.max_tokens));
ok(body2.llm.timeout === 60, "有值 timeout 正常提交");
ok(body2.agent.max_rounds === 9, "有值 max_rounds 正常提交");
ok(body2.agent.max_rounds_extension === 0, "0 合法提交（关闭附加额度），实际 " + JSON.stringify(body2.agent.max_rounds_extension));
console.log("OK");
process.exit(0);
""",
        )

    def test_empty_value_note_names_cleared_fields(self, tmp_path):
        """保存提示点名被清空/留空的数字字段——杜绝「提示成功但值纹丝不动」"""
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
__els["#llmMaxTokens"] = __makeEl();
__els["#llmTimeout"] = __makeEl();
__els["#agentMaxRounds"] = __makeEl();
__els["#agentMaxRoundsExtension"] = __makeEl();
mod.settingsUI.loaded = { llm: { max_tokens: 4096, timeout: 120 }, agent: { max_rounds: 12, max_rounds_extension: 10 } };
const note = mod.settingsUI.emptyValueNote();
ok(note.indexOf("max_tokens 已清除") >= 0, "点名 max_tokens 清除，实际 " + note);
ok(note.indexOf("timeout 已清除") >= 0, "点名 timeout 清除，实际 " + note);
ok(note.indexOf("max_rounds 留空保持原值") >= 0, "点名 max_rounds 保持原值，实际 " + note);
ok(note.indexOf("max_rounds_extension 留空保持原值") >= 0, "点名 max_rounds_extension 保持原值，实际 " + note);
__els["#llmMaxTokens"].value = "4096";
__els["#llmTimeout"].value = "120";
__els["#agentMaxRounds"].value = "12";
__els["#agentMaxRoundsExtension"].value = "10";
ok(mod.settingsUI.emptyValueNote() === "", "无清空动作不加回执");
mod.settingsUI.loaded = { llm: { max_tokens: null, timeout: null }, agent: {} };
__els["#llmMaxTokens"].value = "";
__els["#llmTimeout"].value = "";
__els["#agentMaxRounds"].value = "";
__els["#agentMaxRoundsExtension"].value = "";
ok(mod.settingsUI.emptyValueNote() === "", "盘上本来就是 null/缺省时清空无需回执");
console.log("OK");
process.exit(0);
""",
        )

    def test_number_field_hints_declare_empty_semantics(self):
        """清空语义必须在 UI 里可见声明（用户不看文档也知道留空会怎样）"""
        html = _read_static("index.html")
        assert "留空保存 = 清除该项" in html, "max_tokens/timeout 须声明清空语义"
        assert "留空保存 = 保持原值不变" in html, "max_rounds 须声明保持原值语义"
