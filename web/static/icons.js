/**
 * DeepPulse 内联 SVG 图标库（零 emoji、stroke=currentColor、统一 24x24）。
 * 双环境导出：window.DeepPulseIcons / module.exports。
 * icon() 返回自包含 SVG（不依赖页面 sprite）；spriteHtml() 额外输出 symbol defs。
 */
(function (root, factory) {
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = factory();
  } else {
    root.DeepPulseIcons = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // sprite symbol id（契约 §4.4 必备用名；i- 前缀供 <use href="#i-xxx"> 复用）
  var SYMBOL_IDS = {
    'logo': 'i-logo',
    'plus': 'i-plus',
    'send': 'i-send',
    'stop': 'i-stop',
    'gear': 'i-gear',
    'trash': 'i-trash',
    'edit': 'i-edit',
    'chat': 'i-chat',
    'clock': 'i-clock',
    'refresh': 'i-refresh',
    'chart-line': 'i-chart-line',
    'wallet': 'i-wallet',
    'star': 'i-star',
    'book': 'i-book',
    'search': 'i-search',
    'eye': 'i-eye',
    'check': 'i-check',
    'x': 'i-x',
    'chevron-down': 'i-chevron-down',
    'chevron-right': 'i-chevron-right',
    'warning': 'i-warning',
    'sparkle': 'i-sparkle',
    'brain': 'i-brain',
    'wrench': 'i-wrench',
    'database': 'i-database',
    'shield': 'i-shield',
    'layers': 'i-layers',
    'menu': 'i-menu',
    'user': 'i-user',
    'zap': 'i-zap',
    'sun': 'i-sun',
    'moon': 'i-moon'
  };

  // 图标内层路径（viewBox 0 0 24 24，stroke 由外层 svg 统一施加）
  var ICONS = {
    // 品牌标：蜡烛图（两根 K 线）
    logo:
      '<path d="M8 3v4"/><rect x="5.25" y="7" width="5.5" height="8" rx="1"/><path d="M8 15v6"/>' +
      '<path d="M16 5v3"/><rect x="13.25" y="8" width="5.5" height="6" rx="1"/><path d="M16 14v6"/>',
    plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
    send: '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4 20-7z"/>',
    stop: '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    gear:
      '<circle cx="12" cy="12" r="3"/>' +
      '<path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    trash:
      '<path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>' +
      '<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/>',
    edit: '<path d="M17 3a2.83 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/>',
    chat: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    refresh:
      '<path d="M22 4v6h-6"/><path d="M2 20v-6h6"/>' +
      '<path d="M4.9 9a8 8 0 0 1 13-3l4.1 4"/><path d="M2 15l4.1 4a8 8 0 0 0 13-3"/>',
    'chart-line': '<path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 6-7"/>',
    wallet:
      '<path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/>' +
      '<path d="M18 12a2 2 0 0 0 0 4h4v-4z"/>',
    star: '<path d="M12 2.5l2.95 5.98 6.6.96-4.78 4.66 1.13 6.58L12 17.57l-5.9 3.11 1.13-6.58L2.45 9.44l6.6-.96L12 2.5z"/>',
    book:
      '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>' +
      '<path d="M9 7h7"/>',
    search: '<circle cx="11" cy="11" r="7.5"/><path d="M21 21l-4.35-4.35"/>',
    eye: '<path d="M1.5 12S5.5 5 12 5s10.5 7 10.5 7-4 7-10.5 7S1.5 12 1.5 12z"/><circle cx="12" cy="12" r="3"/>',
    check: '<path d="M20 6L9 17l-5-5"/>',
    x: '<path d="M18 6L6 18"/><path d="M6 6l12 12"/>',
    'chevron-down': '<path d="M6 9l6 6 6-6"/>',
    'chevron-right': '<path d="M9 6l6 6-6 6"/>',
    warning:
      '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>' +
      '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    sparkle:
      '<path d="M12 3l1.75 5.25L19 10l-5.25 1.75L12 17l-1.75-5.25L5 10l5.25-1.75L12 3z"/>' +
      '<path d="M19 14.5l.7 2.1 2.1.7-2.1.7-.7 2.1-.7-2.1-2.1-.7 2.1-.7.7-2.1z"/>' +
      '<path d="M5 3l.55 1.65L7.2 5.2 5.55 5.75 5 7.4l-.55-1.65L2.8 5.2l1.65-.55L5 3z"/>',
    brain:
      '<path d="M9.5 4A2.5 2.5 0 0 0 7 6.5v.2A2.8 2.8 0 0 0 5 9.3c0 .9.4 1.7 1 2.2-.6.5-1 1.3-1 2.2 0 1 .6 1.9 1.5 2.3-.2.5-.3 1-.3 1.5 0 1.66 1.34 3 3 3h.3A2.5 2.5 0 0 0 12 20V4.5A2.5 2.5 0 0 0 9.5 4z"/>' +
      '<path d="M14.5 4A2.5 2.5 0 0 1 17 6.5v.2a2.8 2.8 0 0 1 2 2.6c0 .9-.4 1.7-1 2.2.6.5 1 1.3 1 2.2 0 1-.6 1.9-1.5 2.3.2.5.3 1 .3 1.5 0 1.66-1.34 3-3 3h-.3A2.5 2.5 0 0 1 12 20V4.5A2.5 2.5 0 0 1 14.5 4z"/>',
    wrench:
      '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
    database:
      '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/>' +
      '<path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="M9 12l2 2 4-4"/>',
    layers: '<path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/>',
    menu: '<path d="M3 6h18"/><path d="M3 12h18"/><path d="M3 18h18"/>',
    user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    zap: '<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/>',
    sun:
      '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/>' +
      '<path d="M4.93 4.93l1.41 1.41"/><path d="M17.66 17.66l1.41 1.41"/>' +
      '<path d="M2 12h2"/><path d="M20 12h2"/>' +
      '<path d="M6.34 17.66l-1.41 1.41"/><path d="M19.07 4.93l-1.41 1.41"/>',
    moon: '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>'
  };

  // 必备用名清单（顺序即契约 §4.4 文档顺序）
  var NAMES = Object.keys(SYMBOL_IDS);

  /**
   * 返回内联 SVG 字符串（自包含，不依赖页面 sprite）。
   * @param {string} name 图标名
   * @param {number} [size=16] 像素尺寸
   */
  function icon(name, size) {
    var key = String(name || '');
    if (!Object.prototype.hasOwnProperty.call(ICONS, key)) {
      // 未知图标退化为警告图标，避免整块 UI 崩坏
      key = 'warning';
    }
    var px = Math.max(8, Math.min(128, Number(size) || 16));
    return (
      '<svg class="dp-icon dp-icon-' + String(name).replace(/[^a-z0-9_-]/gi, '') + '"' +
      ' width="' + px + '" height="' + px + '"' +
      ' viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
      ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"' +
      ' aria-hidden="true" focusable="false">' +
      ICONS[key] +
      '</svg>'
    );
  }

  /** 返回 sprite（symbol defs）HTML，可注入页面以复用 <use href="#i-xxx">。 */
  function spriteHtml() {
    var out = '<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true" focusable="false">';
    for (var i = 0; i < NAMES.length; i++) {
      var n = NAMES[i];
      out += '<symbol id="' + SYMBOL_IDS[n] + '" viewBox="0 0 24 24" fill="none" stroke="currentColor"' +
        ' stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + ICONS[n] + '</symbol>';
    }
    out += '</svg>';
    return out;
  }

  return {
    icon: icon,
    spriteHtml: spriteHtml,
    ICONS: ICONS,
    SYMBOL_IDS: SYMBOL_IDS,
    NAMES: NAMES
  };
});
