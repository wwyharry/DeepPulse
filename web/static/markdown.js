/**
 * DeepPulse 轻量 Markdown 渲染器（零依赖、先转义后解析、XSS 铁律）。
 * 支持：ATX 标题、围栏代码、行内代码、粗体、斜体、删除线、链接(仅 http/https)、
 *      列表(可嵌套1层)、引用、分割线、GFM 表格。不支持的语法原样文本。
 * 双环境导出：window.DeepPulseMarkdown = {render, escapeHtml} / module.exports。
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.DeepPulseMarkdown = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // 私有区占位符（正文若含 PUA 会被剥离，杜绝占位符伪造）
  var PH_OPEN = '\uE000';
  var PH_CLOSE = '\uE001';
  var PH_RE = /\uE000(\d+)\uE001/g;

  /** HTML 转义（所有文本节点必须先过此函数）。 */
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  /**
   * 实体解码到稳定态（迭代展开，防 &amp;quot; 之类多层编码绕过）。
   * 仅用于链接 URL 的协议校验与最终 href 输出。
   */
  function decodeEntities(s) {
    var cur = String(s);
    for (var i = 0; i < 5; i++) {
      var next = cur
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"')
        .replace(/&#0*39;/g, "'")
        .replace(/&#x0*27;/gi, "'")
        .replace(/&#0*34;/g, '"')
        .replace(/&#x0*22;/gi, '"')
        .replace(/&amp;/g, '&');
      if (next === cur) break;
      cur = next;
    }
    return cur;
  }

  function isSafeUrl(raw) {
    var url = decodeEntities(raw).trim();
    // 仅放行 http/https；拒绝 javascript:/data:/vbscript: 等一切其它协议
    // 解码后的 URL 不得含引号/尖括号/空白，防止属性逃逸
    return /^https?:\/\/[^\s<>"']+$/i.test(url);
  }

  function sanitizeLang(lang) {
    var m = String(lang || '').match(/^[A-Za-z0-9_+-]{0,24}/);
    return m ? m[0] : '';
  }

  // ---------- 行内解析（输入已是转义后的文本） ----------
  function inline(text) {
    var stash = [];
    function put(html) {
      stash.push(html);
      return PH_OPEN + (stash.length - 1) + PH_CLOSE;
    }

    // 铁律：先转义再解析，杜绝任何原始 HTML 注入
    var s = escapeHtml(String(text == null ? '' : text)).replace(/[\uE000\uE001]/g, '');

    // 1. 行内代码（内部不再解析）
    s = s.replace(/`([^`]+)`/g, function (_, code) {
      return put('<code>' + code + '</code>');
    });

    // 2. 链接 [text](url) —— 仅 http/https
    s = s.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, function (match, label, url) {
      if (!isSafeUrl(url)) return match; // 非法协议原样保留为文本
      var href = escapeHtml(decodeEntities(url).trim());
      return put('<a href="' + href + '" rel="noopener noreferrer" target="_blank">' + label + '</a>');
    });

    // 3. 粗斜体 / 粗体 / 斜体 / 删除线
    s = s.replace(/\*\*\*([^*]+)\*\*\*/g, '<em><strong>$1</strong></em>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    s = s.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    s = s.replace(/(^|[^_\w])_([^_]+)_(?!\w)/g, '$1<em>$2</em>');
    s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');

    // 还原占位：循环替换直至无匹配（链接标签内含行内代码时占位符会嵌套，
    // 单遍 replace 不重扫替换结果，会残留 PUA+序号，如 [`600519`](url) 渲染成 <a>0</a>）
    for (var guard = 0; guard < 8 && s.indexOf(PH_OPEN) !== -1; guard++) {
      s = s.replace(PH_RE, function (_, i) {
        return stash[Number(i)] || '';
      });
    }
    // 兜底：极端嵌套残留的占位符一律剥掉，杜绝 PUA 泄漏到输出
    s = s.replace(/\uE000\d+\uE001/g, '');
    return s;
  }

  // ---------- 表格 ----------
  function isAlignRow(line) {
    var t = line.trim();
    if (!t || t.indexOf('-') === -1) return false;
    if (!/^\|?[\s:|-]+\|?$/.test(t)) return false;
    return /\|/.test(t) && /-/.test(t);
  }

  function splitRow(line) {
    var t = line.trim().replace(/^\|/, '').replace(/\|$/, '');
    // 不支持转义竖线，按 | 切分
    return t.split('|').map(function (c) { return c.trim(); });
  }

  function parseAlign(line) {
    return splitRow(line).map(function (c) {
      var left = c.charAt(0) === ':';
      var right = c.charAt(c.length - 1) === ':';
      if (left && right) return 'center';
      if (right) return 'right';
      return 'left';
    });
  }

  function renderTable(headerLine, alignLine, bodyLines) {
    var headers = splitRow(headerLine);
    var aligns = parseAlign(alignLine);
    var out = '<div class="md-table-wrap"><table><thead><tr>';
    headers.forEach(function (h, i) {
      var a = aligns[i] || 'left';
      out += '<th style="text-align:' + a + '">' + inline(h) + '</th>';
    });
    out += '</tr></thead><tbody>';
    bodyLines.forEach(function (row) {
      var cells = splitRow(row);
      out += '<tr>';
      for (var i = 0; i < headers.length; i++) {
        var a = aligns[i] || 'left';
        out += '<td style="text-align:' + a + '">' + inline(cells[i] == null ? '' : cells[i]) + '</td>';
      }
      out += '</tr>';
    });
    out += '</tbody></table></div>';
    return out;
  }

  // ---------- 列表（可嵌套 1 层） ----------
  function listIndent(line) {
    var m = line.match(/^(\s+)/);
    return m ? m[1].length : 0;
  }

  function isListItem(line) {
    return /^(\s*)([-*+]|\d+[.)])\s+/.test(line) && !isHr(line);
  }

  function listMarker(line) {
    var m = line.match(/^(\s*)([-*+]|\d+[.)])\s+/);
    if (!m) return null;
    return {
      indent: m[1].length,
      ordered: /^\d/.test(m[2]),
      text: line.slice(m[0].length)
    };
  }

  function renderListBlock(lines) {
    var html = '';
    var i = 0;
    while (i < lines.length) {
      var mk = listMarker(lines[i]);
      if (!mk) break;
      var tag = mk.ordered ? 'ol' : 'ul';
      html += '<' + tag + '>';
      var openLi = false;
      while (i < lines.length) {
        var cur = listMarker(lines[i]);
        if (!cur) {
          // 续行并入当前 li
          if (openLi) {
            html += '<br>' + inline(lines[i]);
            i++;
            continue;
          }
          break;
        }
        if (cur.indent >= mk.indent + 2) {
          // 嵌套一层：收集缩进子项，挂到上一个 li 内部
          var sub = [];
          var base = mk.indent + 2;
          while (i < lines.length) {
            var c2 = listMarker(lines[i]);
            if (!c2 || c2.indent < base) break;
            sub.push(lines[i].slice(base));
            i++;
          }
          if (openLi) html += renderListBlock(sub).html;
          else html += '<li>' + renderListBlock(sub).html + '</li>';
          continue;
        }
        if (cur.ordered !== mk.ordered || cur.indent < mk.indent) break;
        if (openLi) html += '</li>';
        html += '<li>' + inline(cur.text);
        openLi = true;
        i++;
      }
      if (openLi) html += '</li>';
      html += '</' + tag + '>';
    }
    return { html: html, rest: i };
  }

  function isHr(line) {
    return /^(\s*)([-*_])(\s*\2){2,}\s*$/.test(line);
  }

  function isHeading(line) {
    return /^(#{1,6})\s+/.test(line);
  }

  // ---------- 块级渲染 ----------
  function renderBlocks(md) {
    var lines = String(md == null ? '' : md)
      .replace(/[\uE000\uE001]/g, '')
      .replace(/\r\n?/g, '\n')
      .split('\n');
    var out = [];
    var para = [];

    function flushPara() {
      if (!para.length) return;
      // 段内单换行按 GFM 习惯转 <br>
      out.push('<p>' + para.map(inline).join('<br>') + '</p>');
      para = [];
    }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];

      // 围栏代码
      var fence = line.match(/^(\s*)(`{3,}|~{3,})\s*([^`\n]*)$/);
      if (fence) {
        flushPara();
        var marker = fence[2];
        var lang = sanitizeLang(fence[3]);
        var body = [];
        i++;
        while (i < lines.length && !new RegExp('^\\s*' + marker.charAt(0) + '{3,}\\s*$').test(lines[i])) {
          body.push(lines[i]);
          i++;
        }
        var cls = lang ? ' class="language-' + lang + '"' : '';
        out.push('<pre class="md-pre"><code' + cls + '>' + escapeHtml(body.join('\n')) + '</code></pre>');
        continue;
      }

      // 分割线
      if (isHr(line)) {
        flushPara();
        out.push('<hr>');
        continue;
      }

      // 标题
      var hm = line.match(/^(#{1,6})\s+(.*)$/);
      if (hm) {
        flushPara();
        var lv = hm[1].length;
        out.push('<h' + lv + '>' + inline(hm[2].replace(/\s+#+\s*$/, '')) + '</h' + lv + '>');
        continue;
      }

      // GFM 表格：当前行含 | 且下一行是对齐行
      if (line.indexOf('|') !== -1 && i + 1 < lines.length && isAlignRow(lines[i + 1]) &&
          lines[i + 1].indexOf('|') !== -1) {
        flushPara();
        var header = line;
        var align = lines[i + 1];
        i += 2;
        var bodyRows = [];
        while (i < lines.length && lines[i].indexOf('|') !== -1 && lines[i].trim() !== '') {
          bodyRows.push(lines[i]);
          i++;
        }
        i--; // for 循环再 +1
        out.push(renderTable(header, align, bodyRows));
        continue;
      }

      // 引用
      if (/^\s*>\s?/.test(line)) {
        flushPara();
        var quote = [];
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          quote.push(lines[i].replace(/^\s*>\s?/, ''));
          i++;
        }
        i--;
        out.push('<blockquote>' + renderBlocks(quote.join('\n')) + '</blockquote>');
        continue;
      }

      // 列表
      if (isListItem(line)) {
        flushPara();
        var listLines = [];
        while (i < lines.length && (isListItem(lines[i]) || (lines[i].trim() !== '' && listIndent(lines[i]) >= 2 && listLines.length))) {
          listLines.push(lines[i]);
          i++;
        }
        i--;
        var res = renderListBlock(listLines);
        out.push(res.html);
        continue;
      }

      // 空行分隔段落
      if (line.trim() === '') {
        flushPara();
        continue;
      }

      para.push(line);
    }
    flushPara();
    return out.join('\n');
  }

  /**
   * 渲染 Markdown 为 HTML 字符串（安全：所有文本节点已转义）。
   * @param {string} md
   * @returns {string}
   */
  function render(md) {
    return renderBlocks(md);
  }

  return {
    render: render,
    escapeHtml: escapeHtml
  };
});
