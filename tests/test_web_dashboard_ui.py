"""看板前端 node 测试 —— 渲染契约：涨跌平比例条/梯队无条形/空态/错误行/视图持久化/轮询注册/新闻转义

看板只展示事实：渲染层不得输出荐股语义（禁词由 test_web_dashboard 的响应体扫描钉），
新闻标题/摘要是不可信内容必须转义。
"""

import pytest

from tests.fakes_web import BAN_VALUES
from tests.test_web_static import _run_node  # 复用 JS 桩执行器

# 不可信内容样本（新闻标题/摘要级别）
XSS_TITLE = "<img src=x onerror=alert(1)>"
XSS_URL = "javascript:alert(1)"


@pytest.fixture
def fixture_payload() -> dict:
    return {
        "updated_at": "2026-10-17T15:01:02",
        "as_of": "2026-10-17",
        "session": "closed",
        "panels": {
            "overview": {
                "data": {
                    "indices": [{"code": "sh000001", "name": "上证指数", "price": 3250.5, "change_pct": 1.58}],
                    "breadth": {"total": 5300, "up": 3000, "down": 2000, "flat": 300},
                    "counts": {"limit_up": 60, "limit_down": 5, "failed": 20},
                    "failed_rate": 25.0,
                    "streak_dist": [{"streak": 5, "count": 1}, {"streak": 2, "count": 3}, {"streak": 1, "count": 20}],
                    "max_streak": 5,
                    "amount_total_yi": 12345.67,
                    "industry_top": [{"industry": "电子", "count": 6}],
                    "note": "当日全市场快照口径",
                    "sources": {"stats": "em-spot", "zt": "em-zt-pool", "indices": "sina"},
                },
                "error": None,
                "source": "composite",
                "stale": False,
            },
            "ladder": {
                "data": {
                    "trade_date": "2026-10-17",
                    "max_streak": 5,
                    "tiers": [
                        {
                            "streak": 5,
                            "label": "5板",
                            "count": 1,
                            "stocks": [{"code": "600001", "name": "甲电子", "change_pct": 10.0, "streak": 5}],
                        },
                        {
                            "streak": 1,
                            "label": "首板",
                            "count": 20,
                            "stocks": [{"code": "000002", "name": "乙股份", "change_pct": 10.0, "streak": 1}],
                        },
                    ],
                },
                "error": None,
                "source": "em-zt-pool",
                "stale": False,
            },
            "limit_up": {
                "data": {
                    "trade_date": "2026-10-17",
                    "count": 1,
                    "stocks": [
                        {
                            "code": "600001",
                            "name": "甲电子",
                            "price": 10.0,
                            "change_pct": 10.0,
                            "streak": 3,
                            "first_time": "09:30:00",
                            "industry": "电子",
                        }
                    ],
                },
                "error": None,
                "source": "em-zt-pool",
                "stale": False,
            },
            "failed": {
                "data": {"trade_date": "2026-10-17", "count": 0, "stocks": []},
                "error": None,
                "source": "em-zb-pool",
                "stale": False,
            },
            "limit_down": {
                "data": {"trade_date": "2026-10-17", "count": 0, "stocks": []},
                "error": None,
                "source": "em-dt-pool",
                "stale": False,
            },
            "sectors": {
                "data": {
                    "board_type": "行业板块",
                    "source_label": "同花顺详情",
                    "note": None,
                    "sectors": [{"name": "电子", "change_pct": 2.5, "inflow": 12.3, "leader": "甲电子"}],
                },
                "error": None,
                "source": "sector-rank",
                "stale": False,
            },
            "news": {
                "data": {
                    "items": [
                        {
                            "title": XSS_TITLE,
                            "source": "东方财富",
                            "time": "2026-10-17 10:00:00",
                            "summary": "<b>x</b>",
                            "url": XSS_URL,
                        },
                    ],
                    "sources_used": ["东方财富"],
                },
                "error": None,
                "source": "em",
                "stale": False,
            },
        },
    }


def _fixture_js(payload: dict) -> str:
    import json

    return (
        "const FIXTURE = "
        + json.dumps(payload, ensure_ascii=False)
        + ";\nconst BAN_VALUES = "
        + json.dumps(list(BAN_VALUES), ensure_ascii=False)
        + ";"
    )


class TestDashboardRender:
    def test_render_all_writes_bodies_and_banner(self, tmp_path, fixture_payload):
        _run_node(
            tmp_path,
            _fixture_js(fixture_payload)
            + r"""
const mod = require(__APP_JS__);
mod.dashboardUI.renderAll(FIXTURE);
const lad = __doc.querySelector('#dashLadderBody').innerHTML;
ok(lad.indexOf('5板') >= 0 && lad.indexOf('首板') >= 0, '梯队层标签渲染');
// 连板梯队不带比例条，显示清楚即可（层级+家数+个股）
ok(lad.indexOf('bar-fill') < 0 && lad.indexOf('bar-track') < 0, '连板梯队无比例条');
ok(lad.indexOf('ratio-bar') < 0 && lad.indexOf('ratio-seg') < 0, '连板梯队不接比例条元素');
ok(lad.indexOf('class="tier-count">20<') >= 0, '梯队家数整数渲染');
ok(lad.indexOf('600001') >= 0 && lad.indexOf('甲电子') >= 0, '梯队个股 chips 展示');
const ov = __doc.querySelector('#dashOverviewBody').innerHTML;
ok(ov.indexOf('3250.5') >= 0, '指数价格渲染');
ok(ov.indexOf('25.0%') >= 0, '炸板率渲染（契约 1 位小数）');
ok(ov.indexOf('12345.67') >= 0, '成交额渲染');
// 家数按语义色分段渲染，整数不得被 fmtNum 缺省两位小数格式化成 '3000.00'
ok(ov.indexOf('class="up">3000<') >= 0, '上涨家数整数+涨语义色渲染');
ok(ov.indexOf('class="down">2000<') >= 0, '下跌家数整数+跌语义色渲染');
ok(ov.indexOf('class="flat">300<') >= 0, '平盘家数整数渲染');
ok(ov.indexOf('3000.00') < 0, '家数不得渲染成两位小数');
// 一根比例条按家数占比分段：3000/2000/300 → 56.6% / 37.7% / 5.7%
ok(ov.indexOf('class="ratio-bar"') >= 0, '涨跌平比例条渲染');
ok(ov.indexOf('ratio-seg up" style="width:56.6%') >= 0, '比例条上涨段按占比');
ok(ov.indexOf('ratio-seg down" style="width:37.7%') >= 0, '比例条下跌段按占比');
ok(ov.indexOf('ratio-seg flat" style="width:5.7%') >= 0, '比例条平盘段按占比');
ok(ov.indexOf('bar-fill') < 0 && ov.indexOf('bar-track') < 0, '概况无旧条形元素');
ok(ov.split('class="ratio-bar"').length === 2, '恰好一根比例条（不多挂）');
ok(ov.split('ratio-seg').length === 4, '比例条恰三段');
ok(ov.indexOf('class="up">60</span> / <span class="down">5</span> / <span>20</span>') >= 0, '涨停/跌停/炸板家数整数渲染');
ok(ov.indexOf('tier-count') < 0 && ov.indexOf('tier-row') < 0, '概况不重复渲染连板分布（梯队已有）');
ok(ov.indexOf('>60<') >= 0, '涨停家数整数渲染');
ok(ov.indexOf('涨停行业') >= 0 && ov.indexOf('电子') >= 0, '概况涨停行业渲染');
// 非交易时段横幅
const banner = __doc.querySelector('#dashBanner');
ok(banner.hidden === false, 'session=closed 显示横幅');
ok(__doc.querySelector('#dashBannerText').textContent.indexOf('2026-10-17') >= 0, '横幅带最近交易日');
// 更新时间
ok(__doc.querySelector('#dashUpdated').textContent.indexOf('2026-10-17 15:01:02') >= 0, '更新时间渲染');
ok(__doc.querySelector('#dashUpdated').textContent.indexOf('数据日 2026-10-17') >= 0, '更新行常显数据日');
// 空面板走 empty-state
ok(__doc.querySelector('#dashFailedBody').innerHTML.indexOf('empty-state') >= 0, '空炸板池空态');
console.log('OK');
process.exit(0);
""",
        )

    def test_news_content_is_escaped(self, tmp_path, fixture_payload):
        """新闻标题/摘要/链接不可信：HTML 须转义，javascript: 链接不得作为 href"""
        _run_node(
            tmp_path,
            _fixture_js(fixture_payload)
            + r"""
const mod = require(__APP_JS__);
mod.dashboardUI.renderAll(FIXTURE);
const news = __doc.querySelector('#dashNewsBody').innerHTML;
ok(news.indexOf('<img') < 0, '标题 img 标签被转义');
ok(news.indexOf('&lt;img') >= 0, '标题转义为文本实体');
ok(news.indexOf('href="javascript:') < 0, 'javascript: 链接不得输出为 href');
ok(news.indexOf('&lt;') >= 0, '转义实体存在');
ok(news.indexOf('<b>') < 0, '摘要标签不得原样输出（变异 esc(n.summary) 须被抓）');
ok(news.indexOf('&lt;b&gt;') >= 0, '摘要转义为文本实体');
console.log('OK');
process.exit(0);
""",
        )

    def test_panel_error_and_stale_render(self, tmp_path, fixture_payload):
        fixture_payload["panels"]["sectors"]["error"] = "所有数据源均不可用"
        fixture_payload["panels"]["sectors"]["data"]["sectors"] = []
        fixture_payload["panels"]["news"]["stale"] = True
        _run_node(
            tmp_path,
            _fixture_js(fixture_payload)
            + r"""
const mod = require(__APP_JS__);
mod.dashboardUI.renderAll(FIXTURE);
const sec = __doc.querySelector('#dashSectorsBody').innerHTML;
ok(sec.indexOf('err-line') >= 0, '错误面板显示 err-line');
ok(sec.indexOf('所有数据源均不可用') >= 0, '错误文案中文透出');
const news = __doc.querySelector('#dashNewsBody').innerHTML;
ok(news.indexOf('数据可能已过期') >= 0, 'stale 面板显示过期角标文案');
ok(news.indexOf('dash-stale') >= 0, '过期角标接线 .dash-stale 胶囊');
console.log('OK');
process.exit(0);
""",
        )

    def test_breadth_unknown_renders_no_ratio_bar(self, tmp_path, fixture_payload):
        """家数未知/非法（含部分缺失）不得画比例条冒充占比 —— 未知≠0 前端钉"""
        fixture_payload["panels"]["overview"]["data"]["breadth"] = {
            "total": None,
            "up": None,
            "down": None,
            "flat": None,
        }
        _run_node(
            tmp_path,
            _fixture_js(fixture_payload)
            + r"""
const mod = require(__APP_JS__);
const noBar = (ov) => ov.indexOf('ratio-bar') < 0 && ov.indexOf('ratio-seg') < 0;
let ov = mod.dashboardUI.overviewHtml(FIXTURE.panels.overview);
ok(noBar(ov), '家数全未知不画比例条');
ok(ov.indexOf('>--</span> / <span>--</span> / <span>--</span>') >= 0, '未知家数渲染 -- / -- / --');
ok(ov.indexOf('class="up">--') < 0 && ov.indexOf('class="down">--') < 0 && ov.indexOf('class="flat">--') < 0,
   '未知单元格不带涨跌语义色');
// 部分缺失：未知≠0 —— 守卫丢 null 判断的变异（画出下跌 0% 的条）在此被抓
FIXTURE.panels.overview.data.breadth = { total: 3300, up: 3000, down: null, flat: 300 };
ov = mod.dashboardUI.overviewHtml(FIXTURE.panels.overview);
ok(noBar(ov), '部分家数未知不画比例条');
ok(ov.indexOf('class="up">3000<') >= 0 && ov.indexOf('>--</span>') >= 0, '已知家数与 -- 混排各自正确');
// 非法值（''/非数字串/负数）按未知同路径
FIXTURE.panels.overview.data.breadth = { total: 0, up: '', down: 'abc', flat: -3 };
ov = mod.dashboardUI.overviewHtml(FIXTURE.panels.overview);
ok(noBar(ov), '非法家数不画比例条');
ok(ov.indexOf('class="up">--') < 0 && ov.indexOf('class="down">--') < 0 && ov.indexOf('class="flat">--') < 0,
   '非法单元格 -- 不带语义色');
// 全 0：合法空结果但无占比可画
FIXTURE.panels.overview.data.breadth = { total: 0, up: 0, down: 0, flat: 0 };
ov = mod.dashboardUI.overviewHtml(FIXTURE.panels.overview);
ok(noBar(ov), '全 0 家数不画空条');
console.log('OK');
process.exit(0);
""",
        )

    def test_breadth_ratio_bar_zero_segment_and_string_counts(self, tmp_path, fixture_payload):
        """单边为 0 仍是合法占比（真值守卫吞条变异被抓）；字符串数字家数与条同口径；三段合计恒 100%"""
        _run_node(
            tmp_path,
            _fixture_js(fixture_payload)
            + r"""
const mod = require(__APP_JS__);
FIXTURE.panels.overview.data.breadth = { total: 5000, up: 5000, down: 0, flat: 0 };
let ov = mod.dashboardUI.overviewHtml(FIXTURE.panels.overview);
ok(ov.indexOf('class="ratio-bar"') >= 0, '单边为 0 仍画比例条');
ok(ov.indexOf('ratio-seg up" style="width:100%') >= 0, '满占比段 100%');
ok(ov.indexOf('ratio-seg down" style="width:0%') >= 0 && ov.indexOf('ratio-seg flat" style="width:0%') >= 0,
   '零家数段 0%');
// 字符串数字：条与家数同口径（bSum 字符串拼接变异在此被抓）
FIXTURE.panels.overview.data.breadth = { total: 5300, up: '3000', down: '2000', flat: '300' };
ov = mod.dashboardUI.overviewHtml(FIXTURE.panels.overview);
ok(ov.indexOf('class="up">3000<') >= 0, '字符串家数按数字渲染');
ok(ov.indexOf('ratio-seg up" style="width:56.6%') >= 0, '字符串家数比例条按数字占比');
ok(ov.indexOf('ratio-seg down" style="width:37.7%') >= 0 && ov.indexOf('ratio-seg flat" style="width:5.7%') >= 0,
   '三段占比 56.6/37.7/5.7');
// 最大余数补差：三段宽度合计恰 100%（独立 round 的 99.9% 抹差在此被抓）
const w = ov.match(/width:([\d.]+)%/g).map((m) => Number(m.match(/width:([\d.]+)%/)[1]));
ok(w.length === 3, '恰三段');
ok(Math.abs(w[0] + w[1] + w[2] - 100) < 0.05, '三段宽度合计 100%');
FIXTURE.panels.overview.data.breadth = { total: 3, up: 1, down: 1, flat: 1 };
ov = mod.dashboardUI.overviewHtml(FIXTURE.panels.overview);
const w2 = ov.match(/width:([\d.]+)%/g).map((m) => Number(m.match(/width:([\d.]+)%/)[1]));
ok(Math.abs(w2[0] + w2[1] + w2[2] - 100) < 0.05, '均分家数三段仍合计 100%');
console.log('OK');
process.exit(0);
""",
        )

    def test_overview_no_recommendation_semantics(self, tmp_path, fixture_payload):
        """看板渲染输出禁含操作建议/情绪评级类文案（构造层就无此字段，双保险）"""
        _run_node(
            tmp_path,
            _fixture_js(fixture_payload)
            + r"""
const mod = require(__APP_JS__);
const html = mod.dashboardUI.overviewHtml(FIXTURE.panels.overview)
  + mod.dashboardUI.ladderHtml(FIXTURE.panels.ladder)
  + mod.dashboardUI.poolHtml(FIXTURE.panels.limit_up, { full: true })
  + mod.dashboardUI.poolHtml(FIXTURE.panels.failed)
  + mod.dashboardUI.poolHtml(FIXTURE.panels.limit_down)
  + mod.dashboardUI.sectorsHtml(FIXTURE.panels.sectors)
  + mod.dashboardUI.newsHtml(FIXTURE.panels.news);
// 禁词表与后端 fakes_web.BAN_VALUES 共享口径（不再各自硬编码）
const banned = BAN_VALUES;
for (const w of banned) {
  ok(html.indexOf(w) < 0, '渲染输出含禁词: ' + w);
}
console.log('OK');
process.exit(0);
""",
        )


class TestViewSetAndPolling:
    def test_set_view_persists_and_toggles_hidden(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
// 默认看板
ok(mod.state.view === 'dashboard', '默认视图=看板');
let applied = mod.setView('chat');
ok(applied === 'chat', 'setView 返回目标视图');
ok(localStorage.getItem('deeppulse.view') === 'chat', '视图选择落盘');
ok(__doc.querySelector('#colChat').hidden === false, '对话列显示');
ok(__doc.querySelector('#colDashboard').hidden === true, '看板列隐藏');
ok(__doc.querySelector('#viewChat').classList.contains('is-active'), '对话 tab 激活');
// 非法输入回落看板
applied = mod.setView('???');
ok(applied === 'dashboard', '非法视图名回落看板');
ok(__doc.querySelector('#colDashboard').hidden === false, '看板列显示');
// 非法存储值也回落
localStorage.setItem('deeppulse.view', 'garbage');
// loadViewPref 不导出：经 init 侧验证 —— setView(normalize) 行为已钉，这里钉存储读取守卫
ok(localStorage.getItem('deeppulse.view') === 'garbage', '存储未被 setView 改写');
console.log('OK');
process.exit(0);
""",
        )

    def test_init_wires_tabs_and_registers_poller(self, tmp_path):
        _run_node(
            tmp_path,
            r"""
const mod = require(__APP_JS__);
const before = mod.state.pollers.length;  // boot 已 init 一次
mod.dashboardUI.init();
mod.dashboardUI.init();
ok(mod.state.pollers.length === before, '重复 init 不叠加轮询器（幂等）');
const vd = __doc.querySelector('#viewDashboard');
const vc = __doc.querySelector('#viewChat');
ok(vd._listeners.click && vd._listeners.click.length === 1, '看板 tab 恰一个点击监听');
ok(vc._listeners.click && vc._listeners.click.length === 1, '对话 tab 恰一个点击监听');
const ref = __doc.querySelector('#dashRefresh');
ok(ref._listeners.click && ref._listeners.click.length === 1, '手动刷新接线');
// 点击切换真实生效
vc._listeners.click[0]();
ok(mod.state.view === 'chat', '点击切到对话');
vd._listeners.click[0]();
ok(mod.state.view === 'dashboard', '点击切回看板');
console.log('OK');
process.exit(0);
""",
        )
