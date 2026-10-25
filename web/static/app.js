/**
 * DeepPulse Web UI 主逻辑：SSE 流式对话 / 会话管理 / 数据面板 / 设置 / 斜杠命令。
 * 模块 IIFE：api · sse · state · chatUI · sessionsUI · panelsUI · settingsUI · commandsUI
 */
(function () {
  'use strict';

  // node 单测环境无 window：渲染器/图标库缺失时仍可加载纯函数（浏览器侧两者均已注入）
  var root = typeof window !== 'undefined' ? window : {};
  var MD = root.DeepPulseMarkdown || {};
  var ICONS = root.DeepPulseIcons || {};
  var icon = function (name, size) { return ICONS.icon(name, size); };

  // ---------- DOM 工具 ----------
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function esc(s) { return MD.escapeHtml(s); }
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html != null) n.innerHTML = html;
    return n;
  }
  function fmtNum(v, digits) {
    if (v == null || v === '' || isNaN(Number(v))) return '--';
    return Number(v).toFixed(digits == null ? 2 : digits);
  }
  function fmtSigned(v, digits, suffix) {
    if (v == null || v === '' || isNaN(Number(v))) return '--';
    var n = Number(v);
    var s = (n > 0 ? '+' : '') + n.toFixed(digits == null ? 2 : digits);
    return suffix ? s + suffix : s;
  }
  function chgClass(v) {
    if (v == null || v === '' || isNaN(Number(v))) return 'flat';
    return Number(v) > 0 ? 'up' : (Number(v) < 0 ? 'down' : 'flat');
  }
  function relTime(ts) {
    if (!ts) return '';
    var d = new Date(ts);
    if (isNaN(d.getTime())) return String(ts);
    var diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + ' 分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + ' 小时前';
    if (diff < 172800) return '昨天';
    if (diff < 86400 * 30) return Math.floor(diff / 86400) + ' 天前';
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }
  function debounce(fn, ms) {
    var t = null;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, ms);
    };
  }
  // 设置分区高亮判定（纯函数，供 node 单测）：
  // tops = 各段顶相对吸顶线的偏移 px（负=已越过吸顶线）；atBottom = 是否已滚到底。
  // 取「最后一个越过阈值的段」；滚到底时强制末段——末段尾部不足一屏时段顶永远爬不到阈值线，
  // 没有到底特判的话最后一段的页签永远选不中（对抗审查缺陷 1/5）。
  function pickActiveTab(tops, atBottom) {
    if (!tops || !tops.length) return 0;
    if (atBottom) return tops.length - 1;
    var idx = 0;
    for (var i = 0; i < tops.length; i++) {
      if (tops[i] <= 64) idx = i;
    }
    return idx;
  }
  // 末段滚动伸缩垫高度（纯函数，供 node 单测）：让 last 段顶能滚到吸顶落点
  // （body 顶 + 8px，即 .form-section 的 scroll-margin-top:8px 落点）所需补出的额外滚动高度。
  // need = (段顶相对 body 顶的偏移 + 当前 scrollTop) - 8 - (scrollHeight - clientHeight)
  function spacerNeedPx(lastOffsetTop, scrollTop, maxScroll) {
    var need = lastOffsetTop + scrollTop - 8 - maxScroll;
    return need > 0 ? Math.ceil(need) : 0;
  }

  // ---------- Toast ----------
  function toast(msg, kind, ms) {
    var wrap = $('#toastWrap');
    if (!wrap) return;
    var ic = kind === 'err' ? 'warning' : (kind === 'ok' ? 'check' : 'sparkle');
    var t = el('div', 'toast ' + (kind || 'info'), icon(ic, 15) + '<span>' + esc(msg) + '</span>');
    wrap.appendChild(t);
    setTimeout(function () {
      t.style.transition = 'opacity 200ms';
      t.style.opacity = '0';
      setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 220);
    }, ms || 3000);
  }

  // ==================== api ====================
  var api = {
    async request(method, path, body) {
      var opt = {
        method: method,
        headers: { 'Content-Type': 'application/json' }
      };
      if (body !== undefined) opt.body = JSON.stringify(body);
      var res;
      try {
        res = await fetch(path, opt);
      } catch (e) {
        toast('网络请求失败', 'err');
        throw e;
      }
      var data = null;
      var text = await res.text();
      if (text) {
        try { data = JSON.parse(text); } catch (e) { data = { detail: text }; }
      }
      if (!res.ok) {
        var detail = (data && (data.detail || data.message)) || ('请求失败 HTTP ' + res.status);
        var err = new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        err.data = data;
        err.status = res.status;
        throw err;
      }
      return data;
    },
    get(path) { return this.request('GET', path); },
    post(path, body) { return this.request('POST', path, body); },
    patch(path, body) { return this.request('PATCH', path, body); },
    put(path, body) { return this.request('PUT', path, body); },
    del(path) { return this.request('DELETE', path); }
  };

  // ==================== sse ====================
  // 手动解析 text/event-stream：event: / data: 行、空行分帧
  var sse = {
    parseChunk(buf) {
      // 兼容 CRLF 分帧
      buf = String(buf).replace(/\r\n/g, '\n');
      var frames = [];
      var idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        var raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        var f = sse.parseFrame(raw);
        if (f) frames.push(f);
      }
      return { frames: frames, rest: buf };
    },
    parseFrame(raw) {
      if (!raw || !raw.trim()) return null;
      var event = 'message';
      var data = '';
      var lines = raw.split('\n');
      for (var i = 0; i < lines.length; i++) {
        var line = lines[i];
        if (line.indexOf('event:') === 0) event = line.slice(6).trim();
        else if (line.indexOf('data:') === 0) data += line.slice(5).replace(/^ /, '');
      }
      if (event === 'message' && !data) return null;
      var parsed;
      try { parsed = JSON.parse(data || '{}'); } catch (e) { parsed = { text: data }; }
      return { event: event, data: parsed };
    },
    async *read(res) {
      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buf = '';
      try {
        while (true) {
          var r = await reader.read();
          if (r.done) break;
          buf += decoder.decode(r.value, { stream: true });
          var p = sse.parseChunk(buf);
          buf = p.rest;
          for (var i = 0; i < p.frames.length; i++) yield p.frames[i];
        }
        buf += decoder.decode();
        if (buf.trim()) {
          var f = sse.parseFrame(buf);
          if (f) yield f;
        }
      } finally {
        try { reader.releaseLock(); } catch (e) { /* 忽略 */ }
      }
    }
  };

  // ==================== state ====================
  var state = {
    sessionId: null,
    sessions: [],
    busy: false,
    rounds: 0,
    toolCount: 0,
    startedAt: 0,
    elapsedTimer: null,
    abortCtrl: null,
    abortTimer: null,
    streamTask: null,
    stickBottom: true,
    pollers: [],
    view: 'dashboard',
    model: '--',
    summary: {},
    turnBuf: null
  };

  function setBusy(v) {
    state.busy = v;
    var btn = $('#sendBtn');
    var ic = $('#sendIcon');
    if (v) {
      btn.classList.add('stop');
      btn.title = '停止';
      ic.innerHTML = icon('stop', 17);
      $('#statStatus').className = 'stat-chip is-live';
      $('#statStatus').innerHTML = icon('zap', 12) + '<b>分析中</b>';
    } else {
      btn.classList.remove('stop');
      btn.title = '发送';
      ic.innerHTML = icon('send', 17);
      $('#statStatus').className = 'stat-chip';
      $('#statStatus').innerHTML = icon('check', 12) + '<b>就绪</b>';
      stopElapsed();
    }
    updateTopbar();
  }

  function startElapsed() {
    state.startedAt = Date.now();
    stopElapsed();
    state.elapsedTimer = setInterval(updateElapsed, 1000);
    updateElapsed();
  }
  function stopElapsed() {
    if (state.elapsedTimer) { clearInterval(state.elapsedTimer); state.elapsedTimer = null; }
  }
  function updateElapsed() {
    var sec = state.startedAt ? Math.round((Date.now() - state.startedAt) / 1000) : 0;
    var b = $('#statTime');
    if (b) b.innerHTML = icon('clock', 12) + '<b>' + sec + 's</b>';
  }

  function updateTopbar() {
    var m = $('#statModel');
    if (m) {
      var name = state.model || '--';
      var seg = String(name).split('/').pop();
      m.innerHTML = icon('zap', 12) + '<b>' + esc(seg) + '</b>';
    }
    var s = $('#statSession');
    if (s) s.innerHTML = icon('chat', 12) + '<b>' + esc(state.sessionId ? state.sessionId.slice(0, 8) : '--') + '</b>';
    var r = $('#statRound');
    if (r) r.innerHTML = '<b>R' + state.rounds + '</b>';
    var t = $('#statTools');
    if (t) t.innerHTML = icon('wrench', 12) + '<b>' + state.toolCount + '</b>';
  }

  // ==================== chatUI ====================
  var chatUI = {
    /** 当前流式回合缓冲 */
    turn: null,
    throttleTimer: null,

    scrollEl: null,
    innerEl: null,

    init() {
      this.scrollEl = $('#chatScroll');
      this.innerEl = $('#chatInner');
      var self = this;
      this.scrollEl.addEventListener('scroll', function () {
        var atBottom = self.isAtBottom();
        state.stickBottom = atBottom;
        $('#backToLatest').classList.toggle('show', !atBottom);
      });
      $('#backToLatest').addEventListener('click', function () {
        self.scrollToBottom(true);
        state.stickBottom = true;
        $('#backToLatest').classList.remove('show');
      });
    },

    isAtBottom() {
      var e = this.scrollEl;
      return e.scrollHeight - e.scrollTop - e.clientHeight < 64;
    },

    scrollToBottom(force) {
      if (force || state.stickBottom) {
        this.scrollEl.scrollTop = this.scrollEl.scrollHeight;
      }
    },

    clear() {
      this.innerEl.innerHTML = '';
      this.renderEmpty();
    },

    renderEmpty() {
      this.innerEl.innerHTML =
        '<div class="chat-empty">' +
        icon('sparkle', 42) +
        '<h2>开始一段投研对话</h2>' +
        '<p>向 DeepPulse 提问行情、财报、策略与持仓；输入 / 可唤起命令菜单。</p>' +
        '<div class="suggest-grid">' +
        '<button class="suggest-btn" data-suggest="帮我看看贵州茅台最近的走势">' + icon('chart-line', 16) + '<span>看看个股走势</span><span style="margin-left:auto;opacity:.5">' + icon('chevron-right', 13) + '</span></button>' +
        '<button class="suggest-btn" data-suggest="今天市场情绪怎么样？">' + icon('zap', 16) + '<span>今日市场情绪</span><span style="margin-left:auto;opacity:.5">' + icon('chevron-right', 13) + '</span></button>' +
        '<button class="suggest-btn" data-suggest="帮我分析一下自选股的表现">' + icon('star', 16) + '<span>自选股表现</span><span style="margin-left:auto;opacity:.5">' + icon('chevron-right', 13) + '</span></button>' +
        '<button class="suggest-btn" data-suggest="给我一些当前行情下的策略建议">' + icon('layers', 16) + '<span>策略建议</span><span style="margin-left:auto;opacity:.5">' + icon('chevron-right', 13) + '</span></button>' +
        '</div></div>';
      var self = this;
      $$('.suggest-btn', this.innerEl).forEach(function (b) {
        b.addEventListener('click', function () {
          $('#chatInput').value = b.getAttribute('data-suggest');
          self.autoGrow($('#chatInput'));
          sendMessage();
        });
      });
    },

    autoGrow(ta) {
      ta.style.height = 'auto';
      ta.style.height = Math.min(180, ta.scrollHeight) + 'px';
    },

    /* ---------- 消息块 ---------- */
    addMessage(role, text, opts) {
      opts = opts || {};
      if ($('.chat-empty', this.innerEl)) this.innerEl.innerHTML = '';
      var msg = {
        role: role,
        text: text || '',
        thinking: opts.thinking || '',
        tools: opts.tools || [],
        streaming: !!opts.streaming,
        ts: opts.ts || Date.now()
      };
      var node = el('div', 'msg ' + role);
      node.setAttribute('data-role', role);
      var roleName = role === 'user' ? '你' : (role === 'assistant' ? 'DeepPulse' : (role === 'error' ? '错误' : '命令'));
      var roleIcon = role === 'user' ? 'user' : (role === 'assistant' ? 'sparkle' : (role === 'error' ? 'warning' : 'chat'));
      node.innerHTML =
        '<div class="msg-head">' +
        '<span class="msg-role">' + icon(roleIcon, 13) + '<span>' + esc(roleName) + '</span></span>' +
        '<span class="msg-time">' + esc(relTime(msg.ts)) + '</span>' +
        '</div>' +
        '<div class="msg-body"></div>';
      this.innerEl.appendChild(node);
      msg.node = node;
      msg.body = $('.msg-body', node);
      this.renderBody(msg);
      this.scrollToBottom();
      return msg;
    },

    renderBody(msg) {
      var body = msg.body;
      if (!body) return;
      // m9：展开态记在 msg 上，流式整块重渲后原样回放（thinking / 工具卡不收回）
      var open = msg.openState || (msg.openState = { think: false, tools: [] });
      var html = '';
      // thinking 折叠：<20 字不显示；>2000 字中间截断
      if (msg.thinking && msg.thinking.length >= 20) {
        var th = msg.thinking;
        var truncated = false;
        if (th.length > 2000) {
          th = th.slice(0, 1200) + '\n……（思考内容过长，中间已省略）……\n' + th.slice(-600);
          truncated = true;
        }
        html +=
          '<div class="think-box' + (open.think ? ' open' : '') + '">' +
          '<button type="button" class="think-toggle">' +
          icon('brain', 14) + '<span>思考过程</span>' +
          '<span style="flex:1"></span>' +
          '<span class="chev">' + icon('chevron-down', 13) + '</span>' +
          '</button>' +
          '<div class="think-body">' + esc(th) + (truncated ? '<div class="tool-truncate-note">内容已截断展示</div>' : '') + '</div>' +
          '</div>';
      }
      // markdown 正文
      var contentHtml = MD.render(msg.text || '');
      if (msg.streaming) contentHtml += '<span class="stream-cursor">▍</span>';
      html += '<div class="md">' + contentHtml + '</div>';
      // 工具卡片
      for (var i = 0; i < msg.tools.length; i++) html += this.toolCardHtml(msg.tools[i], !!open.tools[i]);
      body.innerHTML = html;

      // 绑定折叠（同步写回 msg.openState，供下一次重渲回放）
      var think = $('.think-toggle', body);
      if (think) {
        think.addEventListener('click', function () {
          open.think = !open.think;
          think.parentNode.classList.toggle('open', open.think);
        });
      }
      $$('.tool-card-head', body).forEach(function (h, idx) {
        h.addEventListener('click', function () {
          open.tools[idx] = !open.tools[idx];
          h.parentNode.classList.toggle('open', !!open.tools[idx]);
        });
      });
    },

    toolCardHtml(tool, open) {
      var argsStr = '';
      try { argsStr = JSON.stringify(tool.args == null ? {} : tool.args); } catch (e) { argsStr = String(tool.args); }
      if (argsStr.length > 80) argsStr = argsStr.slice(0, 80) + '…';
      var result = tool.result || '';
      // 展示截断契约：超 2000 字只展示前 2000（SSE/TUI/回灌三径同口径），截了才挂提示
      var shown = result;
      var truncateNote = '';
      if (shown.length > 2000) {
        shown = shown.slice(0, 2000);
        truncateNote = '<div class="tool-truncate-note">展示为截断 2000 字；完整结果保存在会话记录中。</div>';
      }
      return (
        '<div class="tool-card' + (open ? ' open' : '') + '">' +
        '<button type="button" class="tool-card-head">' +
        icon('wrench', 14) +
        '<span class="tool-name">' + esc(tool.name || 'tool') + '</span>' +
        '<span class="tool-args-summary">' + esc(argsStr) + '</span>' +
        '<span class="chev">' + icon('chevron-down', 13) + '</span>' +
        '</button>' +
        '<div class="tool-card-body">' +
        '<div class="tool-block-label">参数</div>' +
        '<pre class="tool-pre">' + esc(this.prettyJson(tool.args)) + '</pre>' +
        '<div class="tool-block-label">结果</div>' +
        '<pre class="tool-pre">' + esc(shown || '（无结果）') + '</pre>' +
        truncateNote +
        '</div></div>'
      );
    },

    prettyJson(obj) {
      try { return JSON.stringify(obj == null ? {} : obj, null, 2); } catch (e) { return String(obj); }
    },

    /* ---------- 流式更新（80ms 节流整块重渲） ---------- */
    scheduleRender() {
      if (this.throttleTimer) return;
      var self = this;
      this.throttleTimer = setTimeout(function () {
        self.throttleTimer = null;
        if (self.turn) {
          self.renderBody(self.turn);
          self.scrollToBottom();
        }
      }, 80);
    },

    flushRender() {
      if (this.throttleTimer) {
        clearTimeout(this.throttleTimer);
        this.throttleTimer = null;
      }
      if (this.turn) {
        this.renderBody(this.turn);
        // m8：收束也走非强制贴底——用户上翻看前文时不被拽回底部
        this.scrollToBottom();
      }
    },

    /* ---------- 历史消息回灌 ---------- */
    loadMessages(payload) {
      this.innerEl.innerHTML = '';
      var msgs = normalizeStoredMessages(payload.messages || []);
      if (!msgs.length) {
        this.renderEmpty();
        return;
      }
      for (var i = 0; i < msgs.length; i++) {
        this.addMessage(msgs[i].role, msgs[i].text, {
          thinking: msgs[i].thinking,
          tools: msgs[i].tools,
          ts: msgs[i].ts
        });
      }
      this.scrollToBottom(true);
    }
  };

  // 存量消息（openai/anthropic 形）→ 展示模型
  function extractText(content) {
    if (content == null) return '';
    if (typeof content === 'string') return content;
    if (Array.isArray(content)) {
      var parts = [];
      for (var i = 0; i < content.length; i++) {
        var b = content[i];
        if (!b) continue;
        if (typeof b === 'string') parts.push(b);
        else if (b.type === 'text' && typeof b.text === 'string') parts.push(b.text);
        else if (typeof b.text === 'string' && !b.type) parts.push(b.text);
      }
      return parts.join('\n');
    }
    return '';
  }

  /**
   * 把工具结果回填到工具卡（M5）。
   * 优先按 tool_call_id / tool_use_id 精确匹配；拿不到 id（或未命中）时，
   * 按顺序填第一个未填结果的工具——覆盖 openai 并行 tool_calls 的错位问题。
   */
  function attachToolResult(tools, toolId, text) {
    var target = null;
    if (toolId != null && toolId !== '') {
      for (var i = 0; i < tools.length; i++) {
        if (tools[i].id === toolId) { target = tools[i]; break; }
      }
    }
    if (!target) {
      for (var j = 0; j < tools.length; j++) {
        if (!tools[j].result) { target = tools[j]; break; }
      }
    }
    if (target && !target.result) target.result = text;
    return !!target;
  }

  /** 跨消息回填：先全局按 id 精确找（不误填别的工具），找不到再退回顺序填充。 */
  function fillToolResult(out, toolId, text) {
    if (toolId != null && toolId !== '') {
      for (var i = out.length - 1; i >= 0; i--) {
        var ts = out[i].tools || [];
        for (var j = 0; j < ts.length; j++) {
          if (ts[j].id === toolId) {
            if (!ts[j].result) ts[j].result = text;
            return;
          }
        }
      }
    }
    for (var k = out.length - 1; k >= 0; k--) {
      if (out[k].role === 'assistant' && out[k].tools.length) {
        attachToolResult(out[k].tools, '', text);
        return;
      }
    }
  }

  function normalizeStoredMessages(messages) {
    var out = [];
    for (var i = 0; i < messages.length; i++) {
      var m = messages[i];
      if (!m || typeof m !== 'object') continue;
      var role = m.role;
      if (role === 'system') continue;
      if (role === 'user') {
        // anthropic 的工具结果包在 role=user 的 tool_result 块里（M5：不能整条丢弃）
        if (Array.isArray(m.content)) {
          for (var u = 0; u < m.content.length; u++) {
            var ub = m.content[u];
            if (ub && typeof ub === 'object' && ub.type === 'tool_result') {
              fillToolResult(out, ub.tool_use_id, extractText(ub.content));
            }
          }
        }
        var ut = extractText(m.content);
        if (ut) out.push({ role: 'user', text: ut, thinking: '', tools: [], ts: null });
        continue;
      }
      if (role === 'assistant') {
        var item = { role: 'assistant', text: extractText(m.content), thinking: '', tools: [], ts: null };
        // openai tool_calls（保留 id，供后续 role=tool 按 tool_call_id 回填）
        if (Array.isArray(m.tool_calls)) {
          for (var t = 0; t < m.tool_calls.length; t++) {
            var tc = m.tool_calls[t] || {};
            var fn = tc.function || tc;
            var args = fn.arguments != null ? fn.arguments : '{}';
            if (typeof args === 'string') {
              try { args = JSON.parse(args); } catch (e) { args = { raw: args }; }
            }
            item.tools.push({ id: tc.id != null ? tc.id : null, name: fn.name || 'tool', args: args, result: '' });
          }
        }
        // anthropic 块
        if (Array.isArray(m.content)) {
          for (var c = 0; c < m.content.length; c++) {
            var blk = m.content[c];
            if (!blk || typeof blk !== 'object') continue;
            if (blk.type === 'thinking' && typeof blk.thinking === 'string') item.thinking += blk.thinking;
            if (blk.type === 'tool_use') item.tools.push({ id: blk.id != null ? blk.id : null, name: blk.name || 'tool', args: blk.input || {}, result: '' });
            if (blk.type === 'tool_result') {
              attachToolResult(item.tools, blk.tool_use_id, extractText(blk.content));
            }
          }
        }
        if (item.text || item.thinking || item.tools.length) out.push(item);
        continue;
      }
      if (role === 'tool') {
        // openai 的 tool 结果消息 → 按 tool_call_id 回填到对应工具卡（M5：并行调用不得错位）
        fillToolResult(out, m.tool_call_id, extractText(m.content));
      }
    }
    return out;
  }

  // ==================== 发送 / 流式消费 ====================
  async function sendMessage() {
    if (state.busy) {
      // m7：重复提交在建会话/开流窗口内只忽略；已有流才走停止
      if (state.abortCtrl) await stopAnalysis();
      return;
    }
    var input = $('#chatInput');
    var text = input.value.trim();
    if (!text) return;

    // 斜杠命令
    if (text.charAt(0) === '/' && !text.startsWith('//')) {
      input.value = '';
      chatUI.autoGrow(input);
      commandsUI.closeMenu();
      await commandsUI.execute(text);
      return;
    }

    // m7：立刻同步置忙 + 清空输入，堵住首条消息双击/Enter 连发竞态窗口
    setBusy(true);
    input.value = '';
    chatUI.autoGrow(input);
    commandsUI.closeMenu();
    var controller = new AbortController();
    state.abortCtrl = controller;
    var terminated = false;

    if (!state.sessionId) {
      try {
        await sessionsUI.createSession();
      } catch (e) {
        if (state.abortCtrl === controller) state.abortCtrl = null;
        if (state.abortTimer) { clearTimeout(state.abortTimer); state.abortTimer = null; }
        setBusy(false);
        input.value = text; // 归还输入，避免消息丢失
        chatUI.autoGrow(input);
        toast('创建会话失败', 'err');
        return;
      }
    }

    chatUI.addMessage('user', text);
    state.rounds = 0;
    state.toolCount = 0;
    updateTopbar();
    startElapsed();

    chatUI.turn = chatUI.addMessage('assistant', '', { streaming: true, tools: [], thinking: '' });

    function finish(kind, message) {
      if (terminated) return;
      terminated = true;
      // M7：挂起的延迟 abort 一并清掉，旧定时器不得误杀下一轮对话流
      if (state.abortTimer) { clearTimeout(state.abortTimer); state.abortTimer = null; }
      if (state.abortCtrl === controller) state.abortCtrl = null;
      if (chatUI.turn) {
        chatUI.turn.streaming = false;
        chatUI.flushRender();
        chatUI.turn = null;
      }
      // error 帧红色提示；无论 turn 是否还在都要给用户一个交代
      if (kind === 'error' && message) {
        chatUI.addMessage('error', message);
      }
      setBusy(false);
      // m10：收束后回取会话列表（message_count 等以服务端为准）
      sessionsUI.refresh();
    }

    try {
      var res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: state.sessionId, message: text }),
        signal: controller.signal
      });

      if (res.status === 409) {
        finish('error', '会话正在分析中');
        toast('会话正在分析中', 'err');
        return;
      }
      if (!res.ok) {
        var data = null;
        try { data = await res.json(); } catch (e) { /* 忽略 */ }
        var detail = (data && data.detail) || ('请求失败 HTTP ' + res.status);
        finish('error', typeof detail === 'string' ? detail : JSON.stringify(detail));
        if (data && data.candidates && data.candidates.length) renderCandidates(data.candidates);
        toast(typeof detail === 'string' ? detail : '请求失败', 'err');
        return;
      }
      if (!res.body) {
        finish('error', '响应无数据流');
        return;
      }

      // 手动解析 SSE 帧
      var reader = res.body.getReader();
      var decoder = new TextDecoder();
      var buf = '';
      while (true) {
        var r = await reader.read();
        if (r.done) break;
        buf += decoder.decode(r.value, { stream: true });
        var p = sse.parseChunk(buf);
        buf = p.rest;
        for (var i = 0; i < p.frames.length; i++) {
          handleFrame(p.frames[i], finish);
        }
      }
      buf += decoder.decode();
      if (buf.trim()) {
        var f = sse.parseFrame(buf);
        if (f) handleFrame(f, finish);
      }
      // 流结束但没收到 done/error —— 兜底收束，绝不悬挂
      finish('done');
    } catch (e) {
      if (e && e.name === 'AbortError') {
        finish('error', '已停止');
      } else {
        finish('error', (e && e.message) || '连接中断');
      }
    }
  }

  function handleFrame(frame, finish) {
    if (!frame || !frame.event) return;
    var d = frame.data || {};
    switch (frame.event) {
      case 'session': {
        if (d.session_id) state.sessionId = d.session_id;
        sessionsUI.upsertSession({ id: d.session_id, title: d.title });
        updateTopbar();
        break;
      }
      case 'round': {
        state.rounds = typeof d.n === 'number' ? d.n : (parseInt(d.n, 10) || state.rounds);
        updateTopbar();
        break;
      }
      case 'thinking': {
        if (chatUI.turn) {
          chatUI.turn.thinking += (d.text || '');
          chatUI.scheduleRender();
        }
        break;
      }
      case 'content': {
        if (chatUI.turn) {
          chatUI.turn.text += (d.text || '');
          chatUI.scheduleRender();
        }
        break;
      }
      case 'tool_start': {
        if (chatUI.turn) {
          var tool = parseToolStart(d);
          chatUI.turn.tools.push(tool);
          state.toolCount = chatUI.turn.tools.length;
          updateTopbar();
          chatUI.scheduleRender();
        }
        break;
      }
      case 'tool_result': {
        if (chatUI.turn && chatUI.turn.tools.length) {
          var last = chatUI.turn.tools[chatUI.turn.tools.length - 1];
          last.result = d.text || '';
          panelsUI.absorbToolResult(last.name, last.result);
          chatUI.scheduleRender();
        } else {
          panelsUI.absorbToolResult('', d.text || '');
        }
        break;
      }
      case 'done': {
        finish('done');
        break;
      }
      case 'error': {
        finish('error', d.message || '分析出错');
        break;
      }
      default:
        break;
    }
  }

  function parseToolStart(d) {
    // 契约形：{name, args}；兼容 map 形：{text:"name({json})"}
    if (d && d.name) {
      return { name: d.name, args: d.args == null ? {} : d.args, result: '' };
    }
    var raw = (d && (d.text || d.tool)) || '';
    var p = raw.indexOf('(');
    if (p > 0) {
      var name = raw.slice(0, p).trim();
      var argStr = raw.slice(p + 1).replace(/\)\s*$/, '');
      var args = {};
      try { args = JSON.parse(argStr); } catch (e) { args = { raw: argStr }; }
      return { name: name, args: args, result: '' };
    }
    return { name: raw || 'tool', args: {}, result: '' };
  }

  function renderCandidates(candidates) {
    if (!candidates || !candidates.length) return;
    var box = el('div', 'msg assistant');
    var items = candidates.map(function (c) {
      return '<button class="btn small suggest-cand" data-code="' + esc(c.code || '') + '">' +
        esc(c.code || '') + ' ' + esc(c.name || '') + '</button>';
    }).join(' ');
    box.innerHTML =
      '<div class="msg-head"><span class="msg-role">' + icon('search', 13) + '<span>你是不是要找</span></span></div>' +
      '<div class="msg-body"><div style="display:flex;flex-wrap:wrap;gap:8px">' + items + '</div></div>';
    chatUI.innerEl.appendChild(box);
    $$('.suggest-cand', box).forEach(function (b) {
      b.addEventListener('click', function () {
        $('#chatInput').value = '分析 ' + b.getAttribute('data-code');
        chatUI.autoGrow($('#chatInput'));
      });
    });
    chatUI.scrollToBottom(true);
  }

  async function stopAnalysis() {
    // M7：捕获触发时刻的 controller，且身份守卫——finish 清空 abortCtrl 后，
    // 兜底定时器不得误杀用户 2.5s 内发起的下一轮对话流
    var ctrl = state.abortCtrl;
    // 连按停止只保留一个挂起的兜底定时器
    if (state.abortTimer) { clearTimeout(state.abortTimer); state.abortTimer = null; }
    // m6：不依赖 sessionId——会话被删后也要能收束本地流
    if (state.sessionId) {
      try {
        await api.post('/api/chat/stop', { session_id: state.sessionId });
      } catch (e) {
        // 服务端停止失败时也要尽量本地收束
      }
    }
    if (!ctrl) return;
    state.abortTimer = setTimeout(function () {
      state.abortTimer = null;
      if (state.abortCtrl === ctrl) {
        try { ctrl.abort(); } catch (e) { /* 忽略 */ }
      }
    }, 2500);
  }

  // ==================== sessionsUI ====================
  var sessionsUI = {
    filter: '',

    init() {
      var self = this;
      $('#newSessionBtn').addEventListener('click', function () {
        if (state.busy) { toast('请先停止当前分析', 'err'); return; }
        self.createSession(true);
      });
      $('#sessionSearch').addEventListener('input', debounce(function (e) {
        self.filter = e.target.value.trim().toLowerCase();
        self.render();
      }, 160));
      $('#menuToggle').addEventListener('click', function () {
        $('#colLeft').classList.toggle('open');
        $('#drawerMask').classList.toggle('show');
      });
      $('#drawerMask').addEventListener('click', function () {
        $('#colLeft').classList.remove('open');
        $('#drawerMask').classList.remove('show');
      });
    },

    async refresh() {
      try {
        var data = await api.get('/api/sessions');
        state.sessions = data.sessions || [];
        this.render();
      } catch (e) {
        // toast 已由 api 层处理
      }
    },

    upsertSession(partial) {
      if (!partial || !partial.id) return;
      var found = null;
      for (var i = 0; i < state.sessions.length; i++) {
        if (state.sessions[i].id === partial.id) { found = state.sessions[i]; break; }
      }
      if (found) {
        if (partial.title) found.title = partial.title;
        found.updated_at = new Date().toISOString();
      } else {
        state.sessions.unshift({
          id: partial.id,
          title: partial.title || '未命名会话',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          message_count: 0
        });
      }
      this.render();
    },

    render() {
      var list = $('#sessionList');
      var self = this;
      list.innerHTML = '';
      var items = state.sessions;
      if (this.filter) {
        items = items.filter(function (s) {
          return (s.title || '').toLowerCase().indexOf(self.filter) >= 0 ||
            (s.id || '').toLowerCase().indexOf(self.filter) >= 0;
        });
      }
      if (!items.length) {
        list.innerHTML =
          '<div class="empty-state">' + icon('chat', 30) +
          '<div>' + (this.filter ? '没有匹配的会话' : '还没有历史会话') + '</div></div>';
        return;
      }
      for (var i = 0; i < items.length; i++) {
        list.appendChild(this.itemNode(items[i]));
      }
    },

    itemNode(s) {
      var self = this;
      var node = el('div', 'session-item' + (s.id === state.sessionId ? ' active' : ''));
      node.setAttribute('data-id', s.id);
      node.innerHTML =
        '<div class="session-title">' + esc(s.title || '未命名会话') + '</div>' +
        '<div class="session-meta">' +
        '<span>' + esc(relTime(s.updated_at || s.created_at)) + '</span>' +
        '<span>' + (s.message_count || 0) + ' 条</span>' +
        '</div>' +
        '<div class="session-actions">' +
        '<button class="icon-btn act-rename" title="重命名">' + icon('edit', 13) + '</button>' +
        '<button class="icon-btn danger act-del" title="删除">' + icon('trash', 13) + '</button>' +
        '</div>';
      node.addEventListener('click', function (e) {
        if (e.target.closest('.session-actions') || e.target.closest('.session-rename-input')) return;
        if (state.busy) { toast('请先停止当前分析', 'err'); return; }
        self.switchTo(s.id);
      });
      $('.act-rename', node).addEventListener('click', function (e) {
        e.stopPropagation();
        self.beginRename(node, s);
      });
      $('.act-del', node).addEventListener('click', function (e) {
        e.stopPropagation();
        self.remove(s);
      });
      return node;
    },

    beginRename(node, s) {
      var titleEl = $('.session-title', node);
      var input = el('input', 'session-rename-input');
      input.value = s.title || '';
      titleEl.replaceWith(input);
      input.focus();
      input.select();
      var done = async function (save) {
        var val = input.value.trim();
        if (save && val && val !== s.title) {
          try {
            var data = await api.patch('/api/sessions/' + encodeURIComponent(s.id), { title: val });
            s.title = (data.session && data.session.title) || val;
            toast('已重命名', 'ok');
          } catch (e) {
            toast(e.message || '重命名失败', 'err');
          }
        }
        self.render();
      };
      input.addEventListener('keydown', function (e) {
        if (e.key === 'Enter') { e.preventDefault(); done(true); }
        if (e.key === 'Escape') { e.preventDefault(); done(false); }
      });
      input.addEventListener('blur', function () { done(true); });
    },

    async remove(s) {
      // m6：流式中删除会话会摘掉渲染节点、卡死停止按钮，与新建/切换同样先拦
      if (state.busy) { toast('请先停止当前分析', 'err'); return; }
      if (!window.confirm('删除会话「' + (s.title || s.id) + '」？此操作不可恢复。')) return;
      try {
        await api.del('/api/sessions/' + encodeURIComponent(s.id));
        toast('会话已删除', 'ok');
        if (state.sessionId === s.id) {
          state.sessionId = null;
          chatUI.clear();
          updateTopbar();
        }
        await this.refresh();
      } catch (e) {
        toast(e.message || '删除失败', 'err');
      }
    },

    async createSession(silent) {
      var data = await api.post('/api/sessions', {});
      var s = data.session;
      state.sessionId = s.id;
      state.rounds = 0;
      state.toolCount = 0;
      updateTopbar();
      await this.refresh();
      chatUI.clear();
      if (!silent) toast('已创建新会话', 'ok');
      $('#colLeft').classList.remove('open');
      $('#drawerMask').classList.remove('show');
      return s;
    },

    async switchTo(id) {
      if (!id || id === state.sessionId) {
        $('#colLeft').classList.remove('open');
        $('#drawerMask').classList.remove('show');
        return;
      }
      state.sessionId = id;
      state.rounds = 0;
      state.toolCount = 0;
      updateTopbar();
      this.render();
      try {
        var data = await api.get('/api/sessions/' + encodeURIComponent(id) + '/messages');
        chatUI.loadMessages(data);
      } catch (e) {
        chatUI.clear();
        toast(e.message || '加载会话失败', 'err');
      }
      $('#colLeft').classList.remove('open');
      $('#drawerMask').classList.remove('show');
    }
  };

  // ==================== panelsUI ====================
  var panelsUI = {
    init() {
      var self = this;
      $('#refreshWatchlist').addEventListener('click', function () { self.loadWatchlist(); });
      $('#refreshPortfolio').addEventListener('click', function () { self.loadPortfolio(); });
      $('#refreshIndices').addEventListener('click', function () { self.loadIndices(); });
      $('#clearSummary').addEventListener('click', function () {
        state.summary = {};
        self.renderSummary();
      });
      this.loadWatchlist();
      this.loadPortfolio();
      this.loadIndices();
      this.renderSummary();
      // 60s 轮询
      var t = setInterval(function () {
        if (document.hidden) return;
        self.loadWatchlist();
        self.loadPortfolio();
        self.loadIndices();
      }, 60000);
      state.pollers.push(t);
    },

    async loadWatchlist() {
      var body = $('#watchlistBody');
      try {
        var data = await api.get('/api/watchlist');
        var items = data.items || [];
        if (!items.length) {
          body.innerHTML = '<div class="empty-state">' + icon('star', 26) + '<div>暂无自选股</div></div>';
          return;
        }
        body.innerHTML = items.map(function (it) {
          var price = it.price;
          var chg = it.change_pct;
          return (
            '<div class="row-item">' +
            '<span class="code">' + esc(it.code || '') + '</span>' +
            '<span class="name">' + esc(it.name || it.code || '--') + '</span>' +
            '<span class="price">' + esc(price == null ? '--' : fmtNum(price)) + '</span>' +
            '<span class="chg ' + chgClass(chg) + '">' + esc(chg == null ? '--' : fmtSigned(chg, 2, '%')) + '</span>' +
            '</div>'
          );
        }).join('');
      } catch (e) {
        body.innerHTML = '<div class="err-line">' + esc(e.message || '加载失败') + '</div>';
      }
    },

    async loadPortfolio() {
      var body = $('#portfolioBody');
      try {
        var data = await api.get('/api/portfolio');
        var items = data.items || [];
        if (!items.length) {
          body.innerHTML = '<div class="empty-state">' + icon('wallet', 26) + '<div>暂无持仓</div></div>';
          return;
        }
        body.innerHTML = items.map(function (it) {
          var profit = it.profit;
          var pct = it.profit_pct;
          return (
            '<div class="row-item">' +
            '<span class="code">' + esc(it.code || '') + '</span>' +
            '<span class="name">' + esc(it.name || it.code || '--') + '</span>' +
            '<span class="price ' + chgClass(profit) + '">' + esc(profit == null ? '--' : fmtSigned(profit, 2)) + '</span>' +
            '<span class="chg ' + chgClass(pct) + '">' + esc(pct == null ? '--' : fmtSigned(pct, 2, '%')) + '</span>' +
            '</div>'
          );
        }).join('');
      } catch (e) {
        body.innerHTML = '<div class="err-line">' + esc(e.message || '加载失败') + '</div>';
      }
    },

    async loadIndices() {
      var body = $('#indicesBody');
      try {
        var data = await api.get('/api/market/indices');
        var items = data.indices || [];
        if (!items.length) {
          body.innerHTML =
            '<div class="err-line">' + esc(data.error || '暂无指数数据') + '</div>';
          return;
        }
        body.innerHTML = items.map(function (it) {
          var chg = it.change_pct;
          return (
            '<div class="row-item">' +
            '<span class="name">' + esc(it.name || it.code || '--') + '</span>' +
            '<span class="price">' + esc(it.price == null ? '--' : fmtNum(it.price)) + '</span>' +
            '<span class="chg ' + chgClass(chg) + '">' + esc(chg == null ? '--' : fmtSigned(chg, 2, '%')) + '</span>' +
            '</div>'
          );
        }).join('');
      } catch (e) {
        body.innerHTML = '<div class="err-line">' + esc(e.message || '加载失败') + '</div>';
      }
    },

    /** 从 tool_result 抽 price/change_pct/rsi（JSON 优先，正则兜底） */
    absorbToolResult(toolName, text) {
      if (!text) return;
      var found = extractDataFields(text);
      var keys = Object.keys(found);
      if (!keys.length) return;
      var changed = false;
      for (var i = 0; i < keys.length; i++) {
        if (state.summary[keys[i]] !== found[keys[i]]) {
          state.summary[keys[i]] = found[keys[i]];
          changed = true;
        }
      }
      if (changed) this.renderSummary();
    },

    renderSummary() {
      var body = $('#summaryBody');
      var s = state.summary;
      var rows = [];
      function push(key, label, fmt, cls) {
        if (s[key] == null) return;
        rows.push(
          '<div class="kv-row"><span class="k">' + esc(label) + '</span>' +
          '<span class="v ' + (cls || '') + '">' + esc(fmt(s[key])) + '</span></div>'
        );
      }
      push('price', '当前价', function (v) { return fmtNum(v); });
      push('change_pct', '涨跌幅', function (v) { return fmtSigned(v, 2, '%'); }, chgClass(s.change_pct));
      push('change', '涨跌额', function (v) { return fmtSigned(v, 2); }, chgClass(s.change));
      push('rsi', 'RSI', function (v) { return fmtNum(v, 1); });
      push('volume', '成交量(股)', function (v) {
        var n = Number(v);
        if (isNaN(n)) return String(v);
        return n >= 1e8 ? (n / 1e8).toFixed(2) + ' 亿' : (n >= 1e4 ? (n / 1e4).toFixed(2) + ' 万' : String(n));
      });
      if (!rows.length) {
        body.innerHTML =
          '<div class="empty-state">' + icon('database', 26) +
          '<div>工具返回的现价 / 涨跌幅 / RSI 会汇总在这里</div></div>';
        return;
      }
      body.innerHTML = rows.join('');
    }
  };

  function extractDataFields(text) {
    var out = {};
    // JSON 解析
    try {
      var obj = JSON.parse(text);
      walkCollect(obj, out, 0);
    } catch (e) { /* 非 JSON 或被截断，走正则 */ }
    // 正则兜底（覆盖截断 JSON）
    if (out.price == null) {
      var m = /"?price"?\s*[:=]\s*"?(-?\d+(?:\.\d+)?)/i.exec(text) ||
              /(?:现价|最新价|收盘价)[^\d\-]{0,8}(-?\d+(?:\.\d+)?)/.exec(text);
      if (m) out.price = Number(m[1]);
    }
    if (out.change_pct == null) {
      var m2 = /"?(?:change_pct|changePct)"?\s*[:=]\s*"?(-?\d+(?:\.\d+)?)/i.exec(text) ||
               /(?:涨跌幅|涨幅)[^\d\-]{0,8}(-?\d+(?:\.\d+)?)%?/.exec(text);
      if (m2) out.change_pct = Number(m2[1]);
    }
    if (out.rsi == null) {
      var m3 = /"?rsi"?\s*[:=]\s*"?(-?\d+(?:\.\d+)?)/i.exec(text);
      if (m3) out.rsi = Number(m3[1]);
    }
    return out;
  }

  function walkCollect(node, out, depth) {
    if (depth > 4 || node == null) return;
    if (Array.isArray(node)) {
      for (var i = 0; i < node.length && i < 8; i++) walkCollect(node[i], out, depth + 1);
      return;
    }
    if (typeof node !== 'object') return;
    var keys = Object.keys(node);
    for (var k = 0; k < keys.length; k++) {
      var key = keys[k];
      var val = node[key];
      var lk = key.toLowerCase();
      if (out.price == null && (lk === 'price' || lk === 'current' || lk === 'close') && isFiniteNum(val)) out.price = Number(val);
      else if (out.change_pct == null && (lk === 'change_pct' || lk === 'changepct' || lk === 'pct_chg') && isFiniteNum(val)) out.change_pct = Number(val);
      else if (out.change == null && (lk === 'change' || lk === 'change_amt' || lk === 'changeamount') && isFiniteNum(val)) out.change = Number(val);
      else if (out.rsi == null && lk === 'rsi' && isFiniteNum(val)) out.rsi = Number(val);
      else if (out.volume == null && lk === 'volume' && isFiniteNum(val)) out.volume = Number(val);
      else if (val && typeof val === 'object') walkCollect(val, out, depth + 1);
    }
  }

  function isFiniteNum(v) {
    return typeof v === 'number' && isFinite(v) || (typeof v === 'string' && v !== '' && isFinite(Number(v)));
  }

  // ==================== commandsUI ====================
  var COMMANDS = [
    { name: 'help', desc: '显示命令帮助', icon: 'book' },
    { name: 'stats', desc: '会话统计（消息数 / 工具数 / 记忆）', icon: 'chart-line' },
    { name: 'memory', desc: '记忆 list / search X / predictions / profile', icon: 'brain' },
    { name: 'strategy', desc: '战法：空=前15 / search X / 名称', icon: 'layers' },
    { name: 'watchlist', desc: '自选股 list / add X / remove X', icon: 'star' },
    { name: 'portfolio', desc: '持仓 list / history / review', icon: 'wallet' },
    { name: 'chart', desc: 'K 线图：chart <代码> [天数]', icon: 'chart-line' },
    { name: 'fetch', desc: '数据采集任务（后台执行）', icon: 'database' },
    { name: 'stop', desc: '停止当前分析', icon: 'stop' },
    { name: 'clear', desc: '清空当前显示（本地）', icon: 'x' },
    { name: 'reset', desc: '重开新对话（本地）', icon: 'refresh' }
  ];

  var commandsUI = {
    open: false,
    active: 0,
    filtered: [],

    init() {
      var self = this;
      var input = $('#chatInput');
      input.addEventListener('input', function () {
        chatUI.autoGrow(input);
        self.updateMenu(input.value);
      });
      input.addEventListener('keydown', function (e) {
        return self.onKeydown(e, input);
      });
      document.addEventListener('click', function (e) {
        if (!e.target.closest('#cmdMenu') && !e.target.closest('#chatInput')) self.closeMenu();
      });
    },

    updateMenu(value) {
      var v = String(value || '');
      if (v.charAt(0) !== '/' || v.indexOf('\n') >= 0 || v.startsWith('//')) {
        this.closeMenu();
        return;
      }
      var q = v.slice(1).split(/\s/)[0].toLowerCase();
      this.filtered = COMMANDS.filter(function (c) {
        return !q || c.name.indexOf(q) === 0;
      });
      if (!this.filtered.length) {
        this.closeMenu();
        return;
      }
      this.active = 0;
      this.render();
      this.open = true;
      $('#cmdMenu').classList.add('show');
    },

    render() {
      var menu = $('#cmdMenu');
      var self = this;
      menu.innerHTML = this.filtered.map(function (c, i) {
        return (
          '<button type="button" class="cmd-menu-item' + (i === self.active ? ' active' : '') + '" data-name="' + esc(c.name) + '">' +
          icon(c.icon, 15) +
          '<span class="cmd-name">/' + esc(c.name) + '</span>' +
          '<span class="cmd-desc">' + esc(c.desc) + '</span>' +
          '</button>'
        );
      }).join('');
      $$('.cmd-menu-item', menu).forEach(function (b) {
        b.addEventListener('click', function () {
          self.complete(b.getAttribute('data-name'), $('#chatInput'));
        });
      });
    },

    complete(name, input) {
      input.value = '/' + name + ' ';
      this.closeMenu();
      input.focus();
    },

    closeMenu() {
      this.open = false;
      var m = $('#cmdMenu');
      if (m) m.classList.remove('show');
    },

    onKeydown(e, input) {
      if (this.open) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          this.active = (this.active + 1) % this.filtered.length;
          this.render();
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          this.active = (this.active - 1 + this.filtered.length) % this.filtered.length;
          this.render();
          return;
        }
        if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey && this.filtered.length)) {
          e.preventDefault();
          this.complete(this.filtered[this.active].name, input);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          this.closeMenu();
          return;
        }
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    },

    async execute(raw) {
      var line = String(raw || '').trim();
      if (!line || line.charAt(0) !== '/') return;
      var parts = line.slice(1).split(/\s+/);
      var name = (parts[0] || '').toLowerCase();
      var arg = line.slice(1 + name.length).trim();

      if (name === 'clear') {
        chatUI.clear();
        toast('已清空显示', 'ok');
        return;
      }
      if (name === 'reset') {
        await sessionsUI.createSession(true);
        toast('已重开新对话', 'ok');
        return;
      }
      if (name === 'stop') {
        await stopAnalysis();
        return;
      }

      var known = COMMANDS.some(function (c) { return c.name === name; });
      if (!known) {
        toast('未知命令 /' + name, 'err');
        return;
      }

      var msg = chatUI.addMessage('command', '', { tools: [] });
      msg.text = '/' + name + (arg ? ' ' + arg : '');
      chatUI.renderBody(msg);

      // 挂载一个结果块
      var resultBox = el('div', 'cmd-block');
      resultBox.innerHTML =
        '<button type="button" class="tool-card-head">' +
        icon('wrench', 14) +
        '<span class="tool-name">/' + esc(name) + '</span>' +
        '<span class="tool-args-summary">' + esc(arg || '') + '</span>' +
        '</button>' +
        '<div class="tool-card-body" style="display:block"><div class="cmd-result-body"><span class="spinner"></span> 执行中…</div></div>';
      msg.body.appendChild(resultBox);

      try {
        var data = await api.post('/api/commands/' + encodeURIComponent(name), {
          arg: arg,
          session_id: state.sessionId
        });
        var resultBody = $('.cmd-result-body', resultBox);
        if (data.image_base64) {
          resultBody.innerHTML =
            '<img class="cmd-img" alt="K 线图" src="data:' + esc(data.mime || 'image/png') + ';base64,' + esc(data.image_base64) + '">' +
            (data.path ? '<div class="tool-truncate-note">' + esc(data.path) + '</div>' : '');
        } else if (data.job_id) {
          resultBody.innerHTML = '<div class="tool-truncate-note">任务已启动（' + esc(data.job_id) + '），日志：</div><pre class="tool-pre fetch-log"></pre>';
          this.pollFetchLogs(data.job_id, $('.fetch-log', resultBody));
        } else if (data.display != null) {
          resultBody.innerHTML = '<div class="md">' + MD.render(String(data.display)) + '</div>';
        } else {
          resultBody.innerHTML = '<pre class="tool-pre">' + esc(JSON.stringify(data, null, 2)) + '</pre>';
        }
        if (data.refresh_watchlist) panelsUI.loadWatchlist();
        if (data.refresh_portfolio) panelsUI.loadPortfolio();
      } catch (e) {
        var rb = $('.cmd-result-body', resultBox);
        if (rb) rb.innerHTML = '<div class="err-line">' + esc(e.message || '命令执行失败') + '</div>';
      }
      chatUI.scrollToBottom(true);
    },

    async pollFetchLogs(jobId, preEl) {
      var maxTries = 300;
      for (var i = 0; i < maxTries; i++) {
        await new Promise(function (r) { setTimeout(r, 1000); });
        try {
          var data = await api.get('/api/commands/fetch/logs?job_id=' + encodeURIComponent(jobId));
          if (preEl) preEl.textContent = (data.lines || []).join('\n');
          if (data.done) return;
        } catch (e) {
          return;
        }
      }
    }
  };

  // ==================== settingsUI ====================
  var PRESETS = {
    deepseek: {
      provider: 'deepseek',
      base_url: 'https://api.deepseek.com',
      model: 'deepseek-chat',
      protocol: 'openai'
    },
    openai: {
      provider: 'openai',
      base_url: 'https://api.openai.com/v1',
      model: 'gpt-4o',
      protocol: 'openai'
    },
    anthropic: {
      provider: 'anthropic',
      base_url: '',
      model: 'claude-sonnet-4-5',
      protocol: 'anthropic'
    },
    custom: {
      provider: '',
      base_url: '',
      model: '',
      protocol: 'openai'
    }
  };

  var settingsUI = {
    loaded: null,
    dirty: false,

    init() {
      var self = this;
      $('#settingsBtn').addEventListener('click', function () { self.open(); });
      $('#settingsClose').addEventListener('click', function () { self.tryClose(); });
      $('#settingsCancel').addEventListener('click', function () { self.tryClose(); });
      $('#settingsMask').addEventListener('click', function (e) {
        if (e.target === $('#settingsMask')) self.tryClose();
      });
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && $('#settingsMask').classList.contains('show')) self.tryClose();
      });
      $('#settingsSave').addEventListener('click', function () { self.save(); });
      $('#testConnectionBtn').addEventListener('click', function () { self.test(); });

      $('#llmTemperature').addEventListener('input', function (e) {
        $('#llmTemperatureVal').textContent = Number(e.target.value).toFixed(1);
        self.dirty = true;
      });
      $('#toggleKey').addEventListener('click', function () {
        var inp = $('#llmApiKey');
        inp.type = inp.type === 'password' ? 'text' : 'password';
      });
      $$('#llmPresets .preset-btn').forEach(function (b) {
        b.addEventListener('click', function () {
          $$('#llmPresets .preset-btn').forEach(function (x) { x.classList.remove('active'); });
          b.classList.add('active');
          var p = PRESETS[b.getAttribute('data-preset')];
          if (!p) return;
          if (p.provider != null) $('#llmProvider').value = p.provider;
          if (p.base_url != null) $('#llmBaseUrl').value = p.base_url;
          if (p.model) $('#llmModel').value = p.model;
          self.setProtocol(p.protocol || 'openai');
          self.dirty = true;
        });
      });
      $$('#protocolGroup .radio-chip').forEach(function (chip) {
        chip.addEventListener('click', function () {
          var input = $('input', chip);
          if (input) {
            self.setProtocol(input.value);
            self.dirty = true;
          }
        });
      });
      $$('#settingsMask input, #settingsMask select').forEach(function (inp) {
        inp.addEventListener('change', function () { self.dirty = true; });
        inp.addEventListener('input', function () { self.dirty = true; });
      });

      // 分区导航：点击跳段 + 滚动高亮（长表单始终可定位、可完整到达）
      var tabs = $$('#settingsTabs .modal-tab');
      var body = $('#settingsMask .modal-body');
      var navLockUntil = 0; // 点击跳转后的短暂锁：smooth 滚动自身触发的 scroll 不得回写 active（缺陷 1）
      function highlightFromScroll() {
        if (!body || !tabs.length) return;
        if (Date.now() < navLockUntil) return;
        var bTop = body.getBoundingClientRect().top;
        var tops = [];
        for (var i = 0; i < tabs.length; i++) {
          var el = document.getElementById(tabs[i].getAttribute('data-target'));
          tops.push(el ? el.getBoundingClientRect().top - bTop : 1e9);
        }
        var maxScroll = body.scrollHeight - body.clientHeight;
        // 到底特判只在真有滚动余量时生效——内容一屏放得下时恒满足"到底"会错亮末段
        var atBottom = maxScroll > 0 && body.scrollTop >= maxScroll - 2;
        var idx = pickActiveTab(tops, atBottom);
        for (var j = 0; j < tabs.length; j++) tabs[j].classList.toggle('active', j === idx);
      }
      tabs.forEach(function (t) {
        t.addEventListener('click', function () {
          var el = document.getElementById(t.getAttribute('data-target'));
          if (el && el.scrollIntoView) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
          tabs.forEach(function (x) { x.classList.toggle('active', x === t); });
          navLockUntil = Date.now() + 600; // smooth 滚动约数百 ms，锁过其时长后恢复滚动高亮
        });
      });
      if (body) {
        body.addEventListener('scroll', debounce(highlightFromScroll, 100));
      }
      if (window.addEventListener) {
        window.addEventListener('resize', debounce(function () {
          settingsUI.sizeScrollSpacer();
        }, 150));
      }
    },

    setProtocol(val) {
      $$('#protocolGroup .radio-chip').forEach(function (chip) {
        var input = $('input', chip);
        var on = input && input.value === val;
        chip.classList.toggle('active', !!on);
        if (input) input.checked = !!on;
      });
      var isAnthropic = val === 'anthropic';
      var baseInput = $('#llmBaseUrl');
      baseInput.disabled = isAnthropic;
      // M6：anthropic 下输入框被禁用却回传旧 base_url 会 400「请清空后再切」——模态框内无解，直接清空
      if (isAnthropic) baseInput.value = '';
      $('#baseUrlHint').textContent = isAnthropic
        ? 'Anthropic 官方不支持自定义 base_url'
        : 'openai 协议下生效；anthropic 协议使用官方固定地址。';
    },

    getProtocol() {
      var checked = $('#protocolGroup input:checked');
      return checked ? checked.value : 'openai';
    },

    async open() {
      $('#settingsMask').classList.add('show');
      $('#testResult').classList.remove('show');
      var body = $('#settingsMask .modal-body');
      if (body) body.scrollTop = 0;
      $$('#settingsTabs .modal-tab').forEach(function (x, i) { x.classList.toggle('active', i === 0); });
      this.dirty = false;
      try {
        var data = await api.get('/api/settings');
        this.loaded = data;
        this.fill(data);
      } catch (e) {
        toast(e.message || '读取设置失败', 'err');
      }
      this.sizeScrollSpacer();
    },

    // 末段滚动伸缩垫（缺陷 2）：modal-body 尾部撑出余量，让任意段顶都能滚到吸顶落点
    // （scroll-margin-top:8px 处）；否则 scrollIntoView 被 maxScroll 钳住，末段永远距顶几十~几百 px
    sizeScrollSpacer() {
      var body = $('#settingsMask .modal-body');
      var spacer = $('#settingsScrollSpacer');
      var secs = $$('#settingsMask .form-section');
      if (!body || !spacer) return;
      spacer.style.height = '0px'; // 先归零再测量，否则旧垫高混进 scrollHeight 把 need 算成 0
      if (!secs.length) return;
      var last = secs[secs.length - 1];
      var bTop = body.getBoundingClientRect().top;
      var lTop = last.getBoundingClientRect().top;
      var need = spacerNeedPx(lTop - bTop, body.scrollTop, body.scrollHeight - body.clientHeight);
      if (need > 0) spacer.style.height = need + 'px';
    },

    fill(data) {
      var llm = data.llm || {};
      var emb = data.embedding || {};
      var ag = data.agent || {};
      var dt = data.data || {};
      $('#llmProvider').value = llm.provider || '';
      $('#llmBaseUrl').value = llm.base_url || '';
      $('#llmApiKey').value = '';
      $('#llmApiKey').placeholder = llm.api_key_masked || '留空保持不变';
      $('#apiKeyHint').textContent = llm.api_key_masked
        ? ('当前密钥：' + llm.api_key_masked + '（留空保持不变）')
        : '尚未配置密钥；留空保持不变。';
      $('#llmModel').value = llm.model || '';
      $('#llmMaxTokens').value = llm.max_tokens != null ? llm.max_tokens : '';
      $('#llmTemperature').value = llm.temperature != null ? llm.temperature : 0.7;
      $('#llmTemperatureVal').textContent = Number(llm.temperature != null ? llm.temperature : 0.7).toFixed(1);
      $('#llmTimeout').value = llm.timeout != null ? llm.timeout : '';
      this.setProtocol(llm.protocol || 'openai');

      $('#embProvider').value = emb.provider || 'none';
      $('#embModel').value = emb.model || '';
      $('#embApiKey').value = '';
      $('#embApiKey').placeholder = emb.api_key_masked || '留空保持不变';
      $('#embBaseUrl').value = emb.base_url || '';

      $('#agentMaxRounds').value = ag.max_rounds != null ? ag.max_rounds : '';
      $('#agentMaxRoundsExtension').value = ag.max_rounds_extension != null ? ag.max_rounds_extension : '';
      $('#agentVerbose').checked = !!ag.verbose;
      $('#agentJudge').checked = !!ag.enable_judge;

      $('#dataOnlineFallback').checked = dt.online_fallback != null ? !!dt.online_fallback : true;
      $('#dataCacheToDb').checked = !!dt.cache_to_db;
      $('#dataSource').value = dt.default_source || 'baostock';
      $('#dataDbPath').value = dt.db_path || '';
      this.dirty = false;
    },

    collect() {
      var protocol = this.getProtocol();
      var llm = {
        provider: $('#llmProvider').value.trim(),
        // M6：与后端校验口径对齐——anthropic 协议必须回传空 base_url
        base_url: protocol === 'anthropic' ? '' : $('#llmBaseUrl').value.trim(),
        model: $('#llmModel').value.trim(),
        protocol: protocol
      };
      var key = $('#llmApiKey').value;
      if (key && key.trim()) llm.api_key = key.trim();
      // 数字字段空值语义（fill/collect 对称，缺陷 6）：
      // max_tokens/timeout 清空 = 显式 null 清除该项（回落默认），与盘上 null 双向往返；
      // max_rounds 是必填硬键（后端拒绝 null），留空 = 不提交该项（保持原值）。
      llm.max_tokens = $('#llmMaxTokens').value !== '' ? Number($('#llmMaxTokens').value) : null;
      if ($('#llmTemperature').value !== '') llm.temperature = Number($('#llmTemperature').value);
      llm.timeout = $('#llmTimeout').value !== '' ? Number($('#llmTimeout').value) : null;

      var emb = {
        provider: $('#embProvider').value,
        model: $('#embModel').value.trim(),
        base_url: $('#embBaseUrl').value.trim()
      };
      var embKey = $('#embApiKey').value;
      if (embKey && embKey.trim()) emb.api_key = embKey.trim();

      return {
        llm: llm,
        embedding: emb,
        agent: {
          max_rounds: $('#agentMaxRounds').value !== '' ? Number($('#agentMaxRounds').value) : undefined,
          max_rounds_extension: $('#agentMaxRoundsExtension').value !== '' ? Number($('#agentMaxRoundsExtension').value) : undefined,
          verbose: $('#agentVerbose').checked,
          enable_judge: $('#agentJudge').checked
        },
        data: {
          online_fallback: $('#dataOnlineFallback').checked,
          cache_to_db: $('#dataCacheToDb').checked,
          default_source: $('#dataSource').value
        }
      };
    },

    // 空值动作回执（缺陷 6）：被清空/留空的数字字段在保存提示里点名，杜绝「提示成功但值纹丝不动」
    emptyValueNote() {
      var loaded = this.loaded || {};
      var llm = loaded.llm || {};
      var ag = loaded.agent || {};
      var notes = [];
      if ($('#llmMaxTokens').value === '' && llm.max_tokens != null) notes.push('max_tokens 已清除');
      if ($('#llmTimeout').value === '' && llm.timeout != null) notes.push('timeout 已清除');
      if ($('#agentMaxRounds').value === '' && ag.max_rounds != null) notes.push('max_rounds 留空保持原值');
      if ($('#agentMaxRoundsExtension').value === '' && ag.max_rounds_extension != null) notes.push('max_rounds_extension 留空保持原值');
      return notes.length ? '（' + notes.join('，') + '）' : '';
    },

    async save() {
      var body = this.collect();
      // 清理 undefined
      if (body.agent.max_rounds == null) delete body.agent.max_rounds;
      if (body.agent.max_rounds_extension == null) delete body.agent.max_rounds_extension;
      try {
        var data = await api.put('/api/settings', body);
        this.loaded = data;
        this.dirty = false;
        toast('设置已保存' + this.emptyValueNote(), 'ok');
        $('#settingsMask').classList.remove('show');
        loadHealth();
      } catch (e) {
        toast(e.message || '保存失败', 'err');
      }
    },

    async test() {
      var box = $('#testResult');
      var btn = $('#testConnectionBtn');
      var body = this.collect();
      if (body.agent.max_rounds == null) delete body.agent.max_rounds;
      if (body.agent.max_rounds_extension == null) delete body.agent.max_rounds_extension;
      box.className = 'test-result show';
      box.innerHTML = '<span class="spinner"></span> 测试连接中…';
      btn.disabled = true;
      try {
        var data = await api.post('/api/settings/test', body);
        if (data && data.status === 'ok') {
          box.className = 'test-result show ok';
          box.innerHTML = icon('check', 14) + ' 连接成功，延迟 ' + (data.latency_ms != null ? data.latency_ms : '--') + ' ms';
        } else {
          box.className = 'test-result show err';
          box.innerHTML = icon('warning', 14) + ' ' + esc((data && data.message) || '连接失败');
        }
      } catch (e) {
        box.className = 'test-result show err';
        box.innerHTML = icon('warning', 14) + ' ' + esc(e.message || '连接失败');
      }
      btn.disabled = false;
      this.sizeScrollSpacer(); // 测试结果块出现/消失会改变正文高度，垫高需重算
    },

    tryClose() {
      if (this.dirty) {
        if (!window.confirm('有未保存的改动，确定关闭吗？')) return;
      }
      $('#settingsMask').classList.remove('show');
      this.dirty = false;
    }
  };

  // ==================== 启动 ====================
  async function loadHealth() {
    try {
      var data = await api.get('/api/health');
      state.model = data.model || '--';
      updateTopbar();
    } catch (e) {
      $('#statStatus').className = 'stat-chip is-err';
      $('#statStatus').innerHTML = icon('warning', 12) + '<b>服务异常</b>';
    }
  }

  // ---------- 主题切换（浅色/深色） ----------
  // 存储键/取值与 index.html 头部防 FOUC 引导脚本一致；首访跟随系统，手动切换后锁定并持久化。
  // 全部环境访问带守卫：node 无 localStorage/matchMedia 时静默降级，不得炸 require。
  var THEME_KEY = 'deeppulse.theme';
  var sessionTheme = null; // 会话内存态：setItem 被拒（隐私模式）时仍能翻转/锁定，不锁死

  var themeUI = {
    STORAGE_KEY: THEME_KEY,
    load: function () {
      try {
        if (typeof localStorage === 'undefined') return null;
        var v = localStorage.getItem(THEME_KEY);
        return v === 'light' || v === 'dark' ? v : null; // 非法值视作未选择
      } catch (e) { return null; }
    },
    systemPrefers: function () {
      try {
        if (typeof window === 'undefined' || !window.matchMedia) return 'dark';
        return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
      } catch (e) { return 'dark'; }
    },
    resolve: function (stored) {
      return stored === 'light' || stored === 'dark' ? stored : this.systemPrefers();
    },
    current: function () {
      // 当前生效主题：会话内存态 > DOM 现值 > 存储/系统解析
      if (sessionTheme === 'light' || sessionTheme === 'dark') return sessionTheme;
      try {
        var dom = typeof document !== 'undefined' && document.documentElement
          ? document.documentElement.getAttribute('data-theme') : null;
        if (dom === 'light' || dom === 'dark') return dom;
      } catch (e) { /* 读 DOM 失败走解析 */ }
      return this.resolve(this.load());
    },
    isLocked: function () {
      return (sessionTheme === 'light' || sessionTheme === 'dark') || this.load() !== null;
    },
    apply: function (theme) {
      var t = theme === 'light' ? 'light' : 'dark';
      try {
        var doc = typeof document !== 'undefined' ? document.documentElement : null;
        if (doc) {
          doc.setAttribute('data-theme', t);
          if (doc.style) doc.style.colorScheme = t;
          // 切换瞬间禁过渡，防 150ms 扫色
          if (doc.classList) {
            doc.classList.add('theme-swapping');
            setTimeout(function () { doc.classList.remove('theme-swapping'); }, 0);
          }
        }
        var meta = typeof document !== 'undefined' ? $('meta[name="color-scheme"]') : null;
        if (meta && meta.setAttribute) meta.setAttribute('content', 'light dark');
        var btn = typeof document !== 'undefined' ? $('#themeBtn') : null;
        if (btn) {
          // 图标表示"点击后的目标主题"：深色激活显示太阳（→浅色），浅色激活显示月亮（→深色）
          var next = t === 'dark' ? 'light' : 'dark';
          if (btn.setAttribute) btn.setAttribute('data-icon', next === 'light' ? 'sun' : 'moon');
          btn.innerHTML = icon(next === 'light' ? 'sun' : 'moon', 18);
          btn.title = next === 'light' ? '切换到浅色模式' : '切换到深色模式';
        }
      } catch (e) { /* 主题装饰失败不影响主流程 */ }
      return t;
    },
    toggle: function () {
      var next = this.current() === 'dark' ? 'light' : 'dark';
      sessionTheme = next; // 先立内存态：即使 setItem 被拒也能继续翻转/锁定
      try {
        if (typeof localStorage !== 'undefined') localStorage.setItem(THEME_KEY, next);
      } catch (e) { /* 隐私模式禁存储：本次会话内仍生效 */ }
      return this.apply(next);
    },
    init: function () {
      var self = this;
      this.apply(this.current()); // 同步当前生效主题（内存态 > DOM > 存储/系统），幂等安全
      if (this._bound) return; // 幂等：重复 init 不得叠加 click 监听
      this._bound = true;
      var btn = typeof document !== 'undefined' ? $('#themeBtn') : null;
      if (btn && btn.addEventListener) {
        btn.addEventListener('click', function () { self.toggle(); });
      }
      // 未锁定（无内存态且无存储）时跟随系统实时切换
      try {
        if (typeof window !== 'undefined' && window.matchMedia) {
          var mq = window.matchMedia('(prefers-color-scheme: light)');
          var onChange = function () { if (!self.isLocked()) self.apply(self.systemPrefers()); };
          if (mq && mq.addEventListener) mq.addEventListener('change', onChange);
          else if (mq && mq.addListener) mq.addListener(onChange);
        }
      } catch (e) { /* matchMedia 缺失退化为静态主题 */ }
    }
  };

  // ---------- 看板视图（纯数据整理：只展示公开事实，绝不掺任何算法推荐/评分/建议） ----------
  var VIEW_KEY = 'deeppulse.view';

  function normalizeView(name) { return name === 'chat' ? 'chat' : 'dashboard'; }

  function loadViewPref() {
    try {
      if (typeof localStorage !== 'undefined') return normalizeView(localStorage.getItem(VIEW_KEY));
    } catch (e) { /* 隐私模式：回落默认 */ }
    return 'dashboard';
  }

  function setView(name) {
    var target = normalizeView(name);
    try { if (typeof localStorage !== 'undefined') localStorage.setItem(VIEW_KEY, target); } catch (e) { /* 内存态兜底 */ }
    state.view = target;
    var dash = $('#colDashboard');
    var chat = $('#colChat');
    if (dash) dash.hidden = target !== 'dashboard';
    if (chat) chat.hidden = target !== 'chat';
    var bd = $('#viewDashboard');
    var bc = $('#viewChat');
    if (bd && bd.classList) bd.classList.toggle('is-active', target === 'dashboard');
    if (bc && bc.classList) bc.classList.toggle('is-active', target === 'chat');
    if (target === 'dashboard') dashboardUI.ensureFresh();
    return target;
  }

  function _dashSrc(panel) {
    var bits = [];
    if (panel && panel.source) bits.push('数据源 ' + panel.source);
    var html = '';
    if (panel && panel.stale) html += '<span class="dash-stale">数据可能已过期</span> ';
    if (bits.length) html += '<div class="dash-src">' + esc(bits.join(' · ')) + '</div>';
    return html;
  }

  function _dashBody(panel, bodyHtml, emptyText) {
    if (panel && panel.error && !bodyHtml) {
      return '<div class="err-line">' + esc(panel.error) + '</div>' + _dashSrc(panel);
    }
    var html = bodyHtml || '<div class="empty-state">' + icon('sparkle', 26) + '<div>' + esc(emptyText) + '</div></div>';
    var err = panel && panel.error ? '<div class="err-line">' + esc(panel.error) + '</div>' : '';
    return err + html + _dashSrc(panel);
  }

  var dashboardUI = {
    lastFetch: 0,
    loading: false,
    _bound: false,

    init() {
      if (this._bound) return;  // 幂等：重复 init 不叠加监听/轮询
      this._bound = true;
      var self = this;
      var ref = $('#dashRefresh');
      if (ref) ref.addEventListener('click', function () { self.load(true); });
      var vd = $('#viewDashboard');
      var vc = $('#viewChat');
      if (vd) vd.addEventListener('click', function () { setView('dashboard'); });
      if (vc) vc.addEventListener('click', function () { setView('chat'); });
      setView(loadViewPref());
      this.load();
      var t = setInterval(function () {
        if (document.hidden) return;
        if (state.view !== 'dashboard') return;
        self.load();
      }, 60000);
      state.pollers.push(t);
      if (typeof document.addEventListener === 'function') {
        document.addEventListener('visibilitychange', function () {
          if (!document.hidden && state.view === 'dashboard') self.ensureFresh();
        });
      }
    },

    ensureFresh() {
      if (!this.lastFetch || Date.now() - this.lastFetch > 60000) this.load();
    },

    async load(force) {
      if (this.loading) {
        if (force) this._pendingForce = true;  // 手动刷新排队补拉，不得静默吞掉
        return;
      }
      this.loading = true;
      try {
        var p = await api.get(force ? '/api/dashboard?refresh=1' : '/api/dashboard');
        this.lastFetch = Date.now();
        this.renderAll(p);
      } catch (e) {
        this.renderError(String((e && e.message) || e));
      } finally {
        this.loading = false;
        if (this._pendingForce) { this._pendingForce = false; this.load(true); }
      }
    },

    renderError(msg) {
      ['dashOverviewBody', 'dashLadderBody', 'dashLimitUpBody', 'dashFailedBody', 'dashLimitDownBody', 'dashSectorsBody', 'dashNewsBody'].forEach(function (id) {
        var node = $('#' + id);
        if (node) node.innerHTML = '<div class="err-line">' + esc(msg) + '</div>';
      });
    },

    renderAll(p) {
      p = p || {};
      var panels = p.panels || {};
      var upd = $('#dashUpdated');
      if (upd) {
        var updText = p.updated_at ? String(p.updated_at).replace('T', ' ') : '--';
        upd.textContent = p.as_of ? ('更新 ' + updText + ' · 数据日 ' + p.as_of) : ('更新 ' + updText);
      }
      var banner = $('#dashBanner');
      var bannerText = $('#dashBannerText');
      if (banner) {
        banner.hidden = p.session !== 'closed';
        if (bannerText && p.session === 'closed') {
          bannerText.textContent = '非交易时段 · 显示最近交易日 ' + (p.as_of || '--') + ' 的数据';
        }
      }
      var map = {
        dashOverviewBody: this.overviewHtml(panels.overview),
        dashLadderBody: this.ladderHtml(panels.ladder),
        dashLimitUpBody: this.poolHtml(panels.limit_up, { full: true }),
        dashFailedBody: this.poolHtml(panels.failed),
        dashLimitDownBody: this.poolHtml(panels.limit_down),
        dashSectorsBody: this.sectorsHtml(panels.sectors),
        dashNewsBody: this.newsHtml(panels.news)
      };
      for (var id in map) {
        var node = $('#' + id);
        if (node) node.innerHTML = map[id];
      }
    },

    // —— 纯渲染函数（返回 HTML 串，node 单测直调） ——

    overviewHtml(panel) {
      var d = (panel && panel.data) || {};
      var rows = [];
      (d.indices || []).forEach(function (i) {
        rows.push(
          '<div class="row-item">' +
          '<span class="name">' + esc(i.name || i.code || '--') + '</span>' +
          '<span class="price">' + esc(i.price == null ? '--' : fmtNum(i.price)) + '</span>' +
          '<span class="chg ' + chgClass(i.change_pct) + '">' + esc(i.change_pct == null ? '--' : fmtSigned(i.change_pct, 2, '%')) + '</span>' +
          '</div>'
        );
      });
      var b = d.breadth || {};
      // 家数可用值 = 有限非负数字（数字字符串同样入账）；null/''/NaN/非数字串/负数一律按未知
      var bNum = function (v) {
        if (v == null || v === '') return null;
        var n = Number(v);
        return isFinite(n) && n >= 0 ? n : null;
      };
      var countCell = function (v, cls) {
        var n = bNum(v);
        return '<span' + (n == null ? '' : ' class="' + cls + '"') + '>' + esc(n == null ? '--' : fmtNum(n, 0)) + '</span>';
      };
      rows.push(
        '<div class="kv-row"><span class="k">上涨 / 下跌 / 平盘</span><span class="v">' +
        countCell(b.up, 'up') + ' / ' + countCell(b.down, 'down') + ' / ' + countCell(b.flat, 'flat') +
        '</span></div>'
      );
      // 一根比例条按上涨/下跌/平盘家数占比分段；三值须皆已知（含 0）且合计>0 才画，未知不冒充 0
      var bVals = [b.up, b.down, b.flat].map(bNum);
      var bSum = 0;
      var bKnown = bVals.every(function (n) { if (n == null) return false; bSum += n; return true; });
      if (bKnown && bSum > 0) {
        // 宽度与文案同一百分比：最大余数法补差，三段合计恒 100%，极小段不被抹成 0
        var raws = bVals.map(function (n) { return (n / bSum) * 1000; });
        var cents = raws.map(function (x) { return Math.floor(x); });
        var byRem = [0, 1, 2].sort(function (x, y) { return (raws[y] - cents[y]) - (raws[x] - cents[x]); });
        for (var gi = 0; gi < 1000 - cents[0] - cents[1] - cents[2]; gi++) cents[byRem[gi]] += 1;
        var segPct = function (i) { return cents[i] / 10; };
        rows.push(
          '<div class="ratio-bar" title="' +
          esc('上涨 ' + segPct(0) + '% · 下跌 ' + segPct(1) + '% · 平盘 ' + segPct(2) + '%') +
          '">' +
          '<span class="ratio-seg up" style="width:' + segPct(0) + '%"></span>' +
          '<span class="ratio-seg down" style="width:' + segPct(1) + '%"></span>' +
          '<span class="ratio-seg flat" style="width:' + segPct(2) + '%"></span>' +
          '</div>'
        );
      }
      var c = d.counts || {};
      rows.push(
        '<div class="kv-row"><span class="k">涨停 / 跌停 / 炸板</span><span class="v">' +
        '<span class="up">' + esc(c.limit_up == null ? '--' : fmtNum(c.limit_up, 0)) + '</span> / ' +
        '<span class="down">' + esc(c.limit_down == null ? '--' : fmtNum(c.limit_down, 0)) + '</span> / ' +
        '<span>' + esc(c.failed == null ? '--' : fmtNum(c.failed, 0)) + '</span>' +
        '</span></div>'
      );
      rows.push(
        '<div class="kv-row"><span class="k">炸板率</span><span class="v">' +
        esc(d.failed_rate == null ? '--' : fmtNum(d.failed_rate, 1) + '%') + '</span></div>'
      );
      rows.push(
        '<div class="kv-row"><span class="k">两市成交额</span><span class="v">' +
        esc(d.amount_total_yi == null ? '--' : fmtNum(d.amount_total_yi) + ' 亿') + '</span></div>'
      );
      // 连板分布不在此重复渲染：连板梯队面板已有同一批层级家数（含个股），避免写两遍
      var top = d.industry_top || [];
      if (top.length) {
        rows.push(
          '<div class="kv-row"><span class="k">涨停行业</span><span class="v">' +
          esc(top.map(function (t) { return t.industry + '×' + t.count; }).join('、')) +
          '</span></div>'
        );
      }
      if (d.note) rows.push('<div class="dash-src">' + esc(d.note) + '</div>');
      return _dashBody(panel, rows.join(''), '暂无概况数据');
    },

    ladderHtml(panel) {
      var d = (panel && panel.data) || {};
      var tiers = d.tiers || [];
      if (!tiers.length) return _dashBody(panel, '', '暂无连板数据');
      var html = tiers.map(function (t) {
        var chips = (t.stocks || []).map(function (s) {
          return (
            '<span class="stock-chip">' +
            '<span class="chip-code">' + esc(s.code || '') + '</span>' +
            '<span>' + esc(s.name || '--') + '</span>' +
            '<span class="' + chgClass(s.change_pct) + '">' + esc(s.change_pct == null ? '--' : fmtSigned(s.change_pct, 2, '%')) + '</span>' +
            '</span>'
          );
        }).join('');
        return (
          '<div class="tier-row">' +
          '<span class="tier-label">' + esc(t.label || '--') + '</span>' +
          '<span class="tier-count">' + esc(fmtNum(t.count, 0)) + '</span>' +
          '<span class="tier-stocks">' + chips + '</span>' +
          '</div>'
        );
      }).join('');
      return _dashBody(panel, html, '暂无连板数据');
    },

    poolHtml(panel, opts) {
      var d = (panel && panel.data) || {};
      var stocks = d.stocks || [];
      if (!stocks.length) return _dashBody(panel, '', '暂无数据');
      var full = !!(opts && opts.full);
      var html = stocks.map(function (s) {
        var extra = '';
        if (full) {
          extra =
            '<span class="chg sm">' + esc(s.streak == null ? '--' : (s.streak === 1 ? '首板' : s.streak + '板')) + '</span>' +
            '<span class="chg time">' + esc(s.first_time || '--') + '</span>';
        }
        return (
          '<div class="row-item">' +
          '<span class="code">' + esc(s.code || '') + '</span>' +
          '<span class="name">' + esc(s.name || '--') + '</span>' +
          '<span class="price">' + esc(s.price == null ? '--' : fmtNum(s.price)) + '</span>' +
          '<span class="chg ' + chgClass(s.change_pct) + '">' + esc(s.change_pct == null ? '--' : fmtSigned(s.change_pct, 2, '%')) + '</span>' +
          extra +
          '</div>'
        );
      }).join('');
      return _dashBody(panel, html, '暂无数据');
    },

    sectorsHtml(panel) {
      var d = (panel && panel.data) || {};
      var sectors = d.sectors || [];
      if (!sectors.length) return _dashBody(panel, '', '暂无板块数据');
      var html = sectors.map(function (s) {
        return (
          '<div class="row-item">' +
          '<span class="name">' + esc(s.name || '--') + '</span>' +
          '<span class="chg ' + chgClass(s.change_pct) + '">' + esc(s.change_pct == null ? '--' : fmtSigned(s.change_pct, 2, '%')) + '</span>' +
          '<span class="chg">' + esc(s.inflow == null ? '--' : fmtNum(s.inflow)) + '</span>' +
          '<span class="chg">' + esc(s.leader || '--') + '</span>' +
          '</div>'
        );
      }).join('');
      if (d.note) html += '<div class="dash-src">' + esc(d.note) + '</div>';
      return _dashBody(panel, html, '暂无板块数据');
    },

    newsHtml(panel) {
      var d = (panel && panel.data) || {};
      var items = d.items || [];
      if (!items.length) return _dashBody(panel, '', '暂无资讯');
      var html = items.map(function (n) {
        var title = esc(n.title || '');
        // 链接白名单：仅 http(s) 可点击（防 javascript: 等伪协议注入）
        var url = /^https?:\/\//i.test(String(n.url || '')) ? String(n.url) : '';
        var inner = url
          ? '<a class="news-title" href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + title + '</a>'
          : '<span class="news-title">' + title + '</span>';
        return (
          '<div class="news-item">' +
          '<div class="news-meta"><span>' + esc(n.time || '--') + '</span><span>' + esc(n.source || '--') + '</span></div>' +
          inner +
          (n.summary ? '<div class="news-summary">' + esc(n.summary) + '</div>' : '') +
          '</div>'
        );
      }).join('');
      return _dashBody(panel, html, '暂无资讯');
    }
  };

  function hydrateIcons() {
    $$('[data-icon]').forEach(function (node) {
      if (node.getAttribute('data-icon-done')) return;
      var name = node.getAttribute('data-icon');
      var size = node.getAttribute('data-size');
      node.innerHTML = icon(name, size ? Number(size) : undefined);
      node.setAttribute('data-icon-done', '1');
    });
  }

  function boot() {
    // sprite 注入（供 CSS/外部 <use> 复用）
    var host = $('#icon-sprite-host');
    if (host) host.innerHTML = ICONS.spriteHtml();

    hydrateIcons();
    themeUI.init();
    chatUI.init();
    sessionsUI.init();
    panelsUI.init();
    dashboardUI.init();
    settingsUI.init();
    commandsUI.init();

    $('#sendBtn').addEventListener('click', function () {
      if (state.busy) stopAnalysis();
      else sendMessage();
    });
    chatUI.renderEmpty();
    updateTopbar();
    loadHealth();
    sessionsUI.refresh();
  }

  // 双环境导出：node 单测直接 require 本文件拿纯函数与关键 UI 对象（无 document 时跳过 boot）
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      extractText: extractText,
      normalizeStoredMessages: normalizeStoredMessages,
      parseToolStart: parseToolStart,
      pickActiveTab: pickActiveTab,
      spacerNeedPx: spacerNeedPx,
      state: state,
      api: api,
      chatUI: chatUI,
      sessionsUI: sessionsUI,
      settingsUI: settingsUI,
      themeUI: themeUI,
      dashboardUI: dashboardUI,
      setView: setView,
      sendMessage: sendMessage,
      stopAnalysis: stopAnalysis
    };
  } else if (typeof window !== 'undefined') {
    window.DeepPulseApp = {
      normalizeStoredMessages: normalizeStoredMessages
    };
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', boot);
    } else {
      boot();
    }
  }
})();
