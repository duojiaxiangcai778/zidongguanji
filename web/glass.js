/* ============================================================
   glass.js — 液态玻璃的取色与底图定位
   ------------------------------------------------------------
   按 UI-RULES.md 第 2 节：
     · 壁纸缩到 40×24 采样
     · 平均色做底（压暗当玻璃底色）
     · 饱和度最高的像素做强调色种子
     · 强调色钳制：S∈[0.30,0.50]、L≈0.70（深色底），否则会出荧光色
   另外把壁纸按窗口在屏幕上的位置做偏移，看起来才像「透过窗口看桌面」。
   ============================================================ */
(function () {
  'use strict';

  var COLS = 40, ROWS = 24;
  var root = document.documentElement;

  function clamp(v, lo, hi) { return v < lo ? lo : (v > hi ? hi : v); }

  function rgb2hsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    var max = Math.max(r, g, b), min = Math.min(r, g, b);
    var h = 0, s = 0, l = (max + min) / 2;
    if (max !== min) {
      var d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      if (max === r) h = ((g - b) / d + (g < b ? 6 : 0)) / 6;
      else if (max === g) h = ((b - r) / d + 2) / 6;
      else h = ((r - g) / d + 4) / 6;
    }
    return [h, s, l];
  }

  function hsl2rgb(h, s, l) {
    if (s === 0) {
      var v = Math.round(l * 255);
      return [v, v, v];
    }
    var q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    var p = 2 * l - q;
    function hue(t) {
      if (t < 0) t += 1;
      if (t > 1) t -= 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    }
    return [
      Math.round(hue(h + 1 / 3) * 255),
      Math.round(hue(h) * 255),
      Math.round(hue(h - 1 / 3) * 255)
    ];
  }

  function hex(rgb) {
    return '#' + rgb.map(function (v) {
      var s = clamp(Math.round(v), 0, 255).toString(16);
      return s.length === 1 ? '0' + s : s;
    }).join('');
  }

  // 采样壁纸 → 写入 CSS 变量
  function applyPalette(img) {
    var cv = document.createElement('canvas');
    cv.width = COLS; cv.height = ROWS;
    var ctx = cv.getContext('2d', { willReadFrequently: true });
    if (!ctx) return;
    ctx.drawImage(img, 0, 0, COLS, ROWS);
    var data;
    try {
      data = ctx.getImageData(0, 0, COLS, ROWS).data;
    } catch (e) {
      return; // 跨域或解码失败就算了，用 CSS 里的兜底色
    }

    var sr = 0, sg = 0, sb = 0, n = COLS * ROWS;
    var bestSat = -1, seed = [0, 0, 0];
    for (var i = 0; i < data.length; i += 4) {
      var r = data[i], g = data[i + 1], b = data[i + 2];
      sr += r; sg += g; sb += b;
      var hsl = rgb2hsl(r, g, b);
      // 只取中高明度的像素当强调色种子，暗部没有参考价值
      if (hsl[2] > 0.18 && hsl[1] > bestSat) {
        bestSat = hsl[1];
        seed = [r, g, b];
      }
    }

    var avg = [sr / n, sg / n, sb / n];
    var ah = rgb2hsl(avg[0], avg[1], avg[2]);

    // 玻璃底色：保色相，压到很暗（L 0.13），饱和度封顶 0.12，避免整屏发彩
    var tintRgb = hsl2rgb(ah[0], Math.min(ah[1], 0.12), 0.13);
    root.style.setProperty('--glass-tint',
      'rgba(' + tintRgb.join(',') + ',0.26)');

    if (bestSat < 0.08) {
      // 壁纸本身就灰，不硬造强调色
      return;
    }

    var sh = rgb2hsl(seed[0], seed[1], seed[2]);
    var cs = clamp(sh[1] < 0.30 ? 0.34 : sh[1], 0.30, 0.50);  // S∈[0.30,0.50]
    var accentRgb = hsl2rgb(sh[0], cs, 0.70);                 // L≈0.70（深色底）
    var accentHex = hex(accentRgb);

    // 强调色上的文字：同色相压到 L 0.14，保证对比度
    var inkRgb = hsl2rgb(sh[0], Math.min(cs, 0.30), 0.14);

    root.style.setProperty('--accent', accentHex);
    root.style.setProperty('--accent-ink', hex(inkRgb));
    root.style.setProperty('--accent-soft',
      'rgba(' + accentRgb.join(',') + ',0.16)');
  }

  // 壁纸按窗口在屏幕上的位置偏移，看起来才像真的在「透过」
  function alignBackdrop() {
    var el = document.getElementById('backdrop');
    if (!el) return;
    var x = (typeof window.screenX === 'number') ? window.screenX : 0;
    var y = (typeof window.screenY === 'number') ? window.screenY : 0;
    if (x || y) {
      el.style.backgroundPosition = 'calc(50% - ' + x + 'px) calc(50% - ' + y + 'px)';
    }
  }

  function boot() {
    var el = document.getElementById('backdrop');
    var img = new Image();
    img.onload = function () {
      applyPalette(img);
      alignBackdrop();
    };
    img.onerror = function () {
      // 没有壁纸（或后端没给）：用 CSS 里的中性灰蓝兜底
      if (el) el.classList.add('empty');
    };
    img.src = 'backdrop.jpg';

    // 拖窗口时跟着动（easy_drag 拖动不改 screenX 的情况下也没副作用）
    var timer = null;
    window.addEventListener('resize', function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(alignBackdrop, 120);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
