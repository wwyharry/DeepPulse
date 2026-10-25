"""Web UI 浅色/深色主题测试

钉子：浅色令牌覆盖全、A 股涨红跌绿锁定（两主题红涨绿跌不互换）、
防 FOUC 引导脚本先于样式表、切换持久化（localStorage 键与取值钉死）、
主题块外零硬编码色（防 hover/遮罩/阴影再漂移回字面色）。
"""

import colorsys
import re
from pathlib import Path

import pytest

from tests.fakes_web import make_test_client
from tests.test_web_static import _run_node  # 复用 JS 桩执行器

STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


def _read(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


def _theme_block(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, f"缺少主题块 {selector}"
    return m.group(1)


def _block_tokens(body: str) -> dict:
    return dict(re.findall(r"--([a-z0-9-]+)\s*:\s*([^;]+);", body))


_COLOR_LITERAL = re.compile(r"^(#[0-9a-fA-F]{3,8}|rgba?\(|hsl\()")
# 允许两主题同值/不覆写的令牌（图片衬底刻意双主题留白）与纯引用派生令牌
_THEME_SAME_OK = {"chart-matte"}
_THEME_DERIVED_OK = {"stop-fg", "ok-fg"}  # 必须是 var(--up)/var(--down) 引用，见锁定测试


def _color_tokens(tokens: dict) -> dict:
    """:root 中值为颜色字面量的令牌（布局/时长/字体等非颜色令牌不计）"""
    return {k: v for k, v in tokens.items() if _COLOR_LITERAL.match(v.strip())}


def _hex_hue(value: str) -> float | None:
    """#rgb/#rrggbb → 色相 [0,360)；非 hex 返回 None"""
    v = value.strip()
    m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", v)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    hue, _, _ = colorsys.rgb_to_hls(r, g, b)
    return hue * 360


def _hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


@pytest.fixture
def client(tmp_path):
    with make_test_client(tmp_path) as c:
        yield c


class TestThemeTokens:
    def test_no_self_referential_tokens(self):
        """守卫：禁止 `--x: var(--x)` 自引用（值解析为无效，令牌整条作废）"""
        css = _read("app.css")
        bad = re.findall(r"--([a-z0-9-]+)\s*:\s*var\(--\1\s*\)", css)
        assert not bad, f"发现自引用令牌（解析必失效）: {bad}"

    def test_light_block_covers_all_color_tokens(self):
        """浅色块须覆写 :root 全部颜色字面量令牌（动态抽取，含透明变体），漏一个就有一块区域还是暗色"""
        css = _read("app.css")
        dark = _color_tokens(_block_tokens(_theme_block(css, ":root")))
        light = _block_tokens(_theme_block(css, ':root[data-theme="light"]'))
        assert len(dark) >= 30, f"颜色令牌抽取异常（仅 {len(dark)} 个）"
        missing = [t for t in dark if t not in light and t not in _THEME_SAME_OK]
        assert not missing, f"浅色主题块缺少令牌: {missing}"
        # 派生引用令牌（--stop-fg/--ok-fg）跟随 --up/--down，浅色不得双源覆写
        assert "stop-fg" not in light and "ok-fg" not in light
        # 浅色值不得与深色雷同（复制粘贴假覆盖）
        same = [t for t in dark if t not in _THEME_SAME_OK and light.get(t) == dark[t]]
        assert not same, f"浅色令牌与深色同值（疑似假覆写）: {same}"

    def test_up_down_semantic_lock(self):
        """A 股涨红跌绿锁定：.up 恒 --up、.down 恒 --down；--stop-fg/--ok-fg 恒引用 --up/--down；
        两主题 --up 红相、--down 绿相且跨主题不换相，绝不对调"""
        css = _read("app.css")
        assert re.search(r"\.up\s*\{\s*color:\s*var\(--up\)", css)
        assert re.search(r"\.down\s*\{\s*color:\s*var\(--down\)", css)
        # 比例条段同锁涨红跌绿（背景色路径，防对调/防漂中性色）
        assert re.search(r"\.ratio-seg\.up\s*\{\s*background:\s*var\(--up\)", css), "比例条上涨段须恒 --up"
        assert re.search(r"\.ratio-seg\.down\s*\{\s*background:\s*var\(--down\)", css), "比例条下跌段须恒 --down"
        assert re.search(r"\.ratio-seg\.flat\s*\{\s*background:\s*var\(--text-dim\)", css), "比例条平盘段须中性 --text-dim"
        dark = _block_tokens(_theme_block(css, ":root"))
        assert dark.get("stop-fg", "").strip() == "var(--up)", f"--stop-fg 须恒为 var(--up)，实际 {dark.get('stop-fg')!r}"
        assert dark.get("ok-fg", "").strip() == "var(--down)", f"--ok-fg 须恒为 var(--down)，实际 {dark.get('ok-fg')!r}"
        hues = {}
        for sel in (":root", ':root[data-theme="light"]'):
            tokens = _block_tokens(_theme_block(css, sel))
            up_hue = _hex_hue(tokens["up"])
            down_hue = _hex_hue(tokens["down"])
            assert up_hue is not None and down_hue is not None, f"{sel} 的 --up/--down 须为 hex 色值"
            assert (up_hue >= 345 or up_hue <= 15), f"{sel} --up 不是红相（hue={up_hue:.0f}）"
            assert 135 <= down_hue <= 165, f"{sel} --down 不是绿相（hue={down_hue:.0f}）"
            hues[sel] = (up_hue, down_hue)
        u0, d0 = hues[":root"]
        u1, d1 = hues[':root[data-theme="light"]']
        assert _hue_distance(u0, u1) < 15, "跨主题 --up 换相（涨红锁定破坏）"
        assert _hue_distance(d0, d1) < 15, "跨主题 --down 换相（跌绿锁定破坏）"

    def test_meta_color_scheme_supports_both(self):
        html = _read("index.html")
        m = re.search(r'<meta name="color-scheme" content="([^"]+)"', html)
        assert m and "light" in m.group(1) and "dark" in m.group(1), m.group(0) if m else "缺少 color-scheme meta"


class TestThemeBootstrap:
    def test_bootstrap_script_runs_before_stylesheet(self):
        """防 FOUC：主题引导内联脚本必须排在样式表之前；断言用精确形态（子串充数/注释糊弄不算）"""
        html = _read("index.html")
        i_script = html.index("<script>")
        i_css = html.index('<link rel="stylesheet"')
        assert i_script < i_css, "主题引导脚本必须在 stylesheet link 之前"
        head = html[:i_css]
        # 脚本体内嵌字面 </script> 会把标签提前截断（浏览器双炸）：head 段收尾标签恰好一次
        assert head.count("</script>") == 1, "样式表之前应恰好一个完整脚本块"
        body = html[i_script : html.index("</script>", i_script)]
        assert re.search(r"localStorage\.getItem\(['\"]deeppulse\.theme['\"]\)", body), "须精确读取 deeppulse.theme"
        assert re.search(r"documentElement\.setAttribute\(['\"]data-theme['\"]", body), "须精确写 data-theme"
        assert re.search(r"matchMedia\(['\"]\(prefers-color-scheme", body), "首访须读系统偏好"

    def test_bootstrap_uses_same_storage_key_as_app(self):
        html, js = _read("index.html"), _read("app.js")
        assert "deeppulse.theme" in html and "deeppulse.theme" in js


class TestThemeNoHardcodedColors:
    def test_app_css_literals_only_in_theme_blocks(self):
        """守卫：颜色字面量只允许出现在 :root / [data-theme] 主题块内，其余一律走 var/color-mix"""
        css = _read("app.css")
        stripped = re.sub(r':root(\[data-theme="light"\])?\s*\{[^}]*\}', "", css)
        hits = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", stripped)
        assert not hits, f"主题块外出现硬编码色 {len(hits)} 处（应 token 化或 color-mix 跟随主题）"

    def test_js_files_carry_no_colors(self):
        for name in ("app.js", "markdown.js", "icons.js"):
            text = _read(name)
            hits = re.findall(r"rgba?\(|hsl\(", text)
            assert not hits, f"{name} 含颜色函数调用: {hits[:5]}"


class TestThemeToggleControl:
    def test_theme_button_present_and_plain_icon_btn(self):
        html = _read("index.html")
        m = re.search(r'<button class="([^"]*)" id="themeBtn"[^>]*>', html)
        assert m, "index.html 缺少 #themeBtn"
        cls = m.group(1)
        assert "switch" not in cls and "primary" not in cls  # 不占 .switch 闭包名额、不冒充主按钮
        assert 'data-icon="sun"' in m.group(0)  # 默认深色激活 → 目标浅色显示太阳
        # 位于设置按钮之前（顶栏右侧区）
        assert html.index('id="themeBtn"') < html.index('id="settingsBtn"')
        js = _read("app.js")
        assert "#themeBtn" in js and "themeUI" in js


class TestThemeToggle:
    def test_toggle_persists_and_flips(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const t = mod.themeUI;
ok(t.STORAGE_KEY === 'deeppulse.theme', 'storage key 钉死');
// 未锁定：matchMedia 桩 matches=false → 系统偏暗
ok(t.resolve(t.load()) === 'dark', '无存储时跟随系统（桩为暗）');
let applied = t.toggle();
ok(applied === 'light', 'toggle 从暗翻到浅');
ok(localStorage.getItem('deeppulse.theme') === 'light', '切换后落盘');
ok(__doc.documentElement.getAttribute('data-theme') === 'light', 'data-theme=light');
applied = t.toggle();
ok(applied === 'dark', '再切回暗');
ok(localStorage.getItem('deeppulse.theme') === 'dark', '存储跟随翻转');
console.log('OK');
process.exit(0);
""",
        )

    def test_apply_and_invalid_stored_value(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const t = mod.themeUI;
t.apply('light');
ok(__doc.documentElement.getAttribute('data-theme') === 'light', 'apply 写 data-theme');
ok(__doc.documentElement.style.colorScheme === 'light', 'apply 同步 colorScheme');
// 非法存储值视作未锁定 → 回落系统（桩为暗）
localStorage.setItem('deeppulse.theme', 'blue');
ok(t.load() === null, '非法值 load 得 null');
ok(t.resolve(t.load()) === 'dark', '非法值回落系统主题');
// 按钮装饰：目标主题图标/文案（暗激活显示太阳→浅色）
t.apply('dark');
const btn = __doc.querySelector('#themeBtn');
ok(btn.getAttribute('data-icon') === 'sun', '暗激活按钮显示 sun');
ok(btn.title.indexOf('浅色') >= 0, '暗激活标题指向浅色');
t.apply('light');
ok(btn.getAttribute('data-icon') === 'moon', '浅激活按钮显示 moon');
ok(btn.title.indexOf('深色') >= 0, '浅激活标题指向深色');
console.log('OK');
process.exit(0);
""",
        )

    def test_system_change_follows_when_unlocked(self, tmp_path):
        """三段式：未锁定时双向实时跟随系统；锁定后系统变化不再覆盖"""
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const t = mod.themeUI;
ok(__mqListeners.length >= 1, 'change 监听已注册');
// ① 未锁定：双向实时跟随（harness 桩 __mqLight 可翻转）
__mqLight = true; __mqListeners.forEach(function (f) { f(); });
ok(__doc.documentElement.getAttribute('data-theme') === 'light', '未锁定跟随系统=浅');
__mqLight = false; __mqListeners.forEach(function (f) { f(); });
ok(__doc.documentElement.getAttribute('data-theme') === 'dark', '系统切暗→跟随');
__mqLight = true; __mqListeners.forEach(function (f) { f(); });
ok(__doc.documentElement.getAttribute('data-theme') === 'light', '系统切浅→跟随');
// ② 锁定为暗后，系统再变浅不得覆盖
t.toggle();
ok(localStorage.getItem('deeppulse.theme') === 'dark', '锁定为暗');
__mqLight = true; __mqListeners.forEach(function (f) { f(); });
ok(__doc.documentElement.getAttribute('data-theme') === 'dark', '锁定后不受系统变化影响');
console.log('OK');
process.exit(0);
""",
        )

    def test_toggle_survives_setitem_failure(self, tmp_path):
        """隐私模式 setItem 抛错：toggle 不得锁死（内存态兜底），锁定判定亦不得被系统覆盖"""
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const t = mod.themeUI;
localStorage.setItem = function () { throw new Error('QuotaExceededError'); };
// 连点三次必须一直翻转（旧 bug：以 load() 为基准会卡死在系统反面）
ok(t.toggle() === 'light', 'setItem 抛错仍翻到浅');
ok(t.toggle() === 'dark', '继续翻到暗');
ok(t.toggle() === 'light', '继续翻回浅');
ok(__doc.documentElement.getAttribute('data-theme') === 'light', 'DOM 跟随内存态');
ok(t.isLocked() === true, '内存态视为已锁定');
// 二次 init 同步 current()（内存态优先），不得退回系统主题
t.init();
ok(__doc.documentElement.getAttribute('data-theme') === 'light', 'init 不覆盖内存态');
// 系统变化不得覆盖内存锁定
__mqLight = false; __mqListeners.forEach(function (f) { f(); });
ok(__doc.documentElement.getAttribute('data-theme') === 'light', '系统变暗不覆盖内存锁定');
console.log('OK');
process.exit(0);
""",
        )

    def test_init_idempotent_and_click_wired(self, tmp_path):
        """init 幂等：重复调用不得叠加 click 监听；点击监听真翻转主题"""
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const t = mod.themeUI;
const btn = __doc.querySelector('#themeBtn');
ok(btn._listeners.click && btn._listeners.click.length === 1, 'boot 后恰好一个 click 监听');
t.init();
t.init();
ok(btn._listeners.click.length === 1, '重复 init 不叠加监听');
const before = __doc.documentElement.getAttribute('data-theme');
btn._listeners.click[0]();
ok(__doc.documentElement.getAttribute('data-theme') !== before, '点击真实翻转主题');
console.log('OK');
process.exit(0);
""",
        )


class TestStaticCachePolicy:
    def test_static_and_index_are_no_cache(self, client):
        """主题/样式大改后用户必须拿到新 app.css：静态资产 no-cache（仍可 ETag 304）"""
        for url in ("/", "/index.html", "/static/app.css", "/static/app.js", "/static/index.html"):
            r = client.get(url)
            assert r.status_code == 200, url
            assert r.headers.get("cache-control") == "no-cache", f"{url} 缺 Cache-Control: no-cache"

    def test_api_responses_untouched(self, client):
        """反向：API 语义不动，不得被静态缓存策略波及"""
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.headers.get("cache-control") != "no-cache", "API 响应不得贴静态 no-cache 策略"
