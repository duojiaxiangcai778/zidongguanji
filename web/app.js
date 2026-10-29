/* 自动关机工具 · 液态玻璃前端
 *
 * 铁律（针对旧版「输入的文字会消失」）：
 *   本文件里任何轮询/渲染代码都不允许写 input.value。
 *   输入框的值只由用户输入、快捷按钮、浏览对话框三处写入。
 */
'use strict';

/* 调试钩子：保留调用点，生产环境不做事 */
function dbg(msg) { /* no-op */ }
window.__dbg = dbg;

/* ============ 后端通道 ============
 * 不用 pywebview 的 js_api：它对 remote URL 不会注入（api 是空对象）。
 * 统一走 Python 自己起的本地 HTTP 接口：POST /api/<name>  JSON 进 JSON 出。
 */
async function call(name, arg) {
  const body = (arg === undefined || arg === null) ? {} : arg;
  const res = await fetch('/api/' + name, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error('HTTP ' + res.status);
  return await res.json();
}

/* 把脚本级错误暴露出来，方便从 Python 侧探测 */
window.__lastErr = 'none';
window.addEventListener('error', (e) => {
  window.__lastErr = (e.message || '') + ' @' + (e.filename || '') + ':' + (e.lineno || 0);
  dbg('JS-ERR ' + window.__lastErr);
});
window.addEventListener('unhandledrejection', (e) => {
  const r = e.reason;
  window.__lastErr = 'promise: ' + ((r && r.message) || r);
  dbg('JS-REJ ' + window.__lastErr);
});

const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  mode: 'countdown',
  action: 'shutdown',
  weeks: [false, false, false, false, false, false, false],
  booted: false,
  lastClock: '',
  lastStatus: '',
  lastTarget: '',
  urgent: false,
};

const ACTIONS = [
  { key: 'shutdown',    ico: '⏻', name: '关闭电脑',   desc: 'shutdown /s' },
  { key: 'restart',     ico: '↻', name: '重启电脑',   desc: 'shutdown /r' },
  { key: 'logoff',      ico: '⎋', name: '注销电脑',   desc: '注销当前用户' },
  { key: 'sleep',       ico: '☾', name: '睡眠模式',   desc: '进入系统睡眠' },
  { key: 'monitor_off', ico: '▣', name: '关闭显示器', desc: '只关屏不锁系统' },
  { key: 'run_program', ico: '▶', name: '运行程序',   desc: '启动 exe / 文件' },
  { key: 'play_sound',  ico: '♪', name: '播放声音',   desc: '播放音频文件' },
  { key: 'message',     ico: '✦', name: '弹出消息',   desc: '托盘气泡通知' },
];

const HINTS = {
  countdown: '到点后执行选定的操作',
  at_time: '按这台电脑的本地时间执行，已过去的时间会被拒绝',
  periodic: '选中的星期几，到点循环执行一次',
};

/* ============ 轻提示 ============ */
function toast(text, kind) {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + (kind || '');
  el.textContent = text;
  box.appendChild(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 220);
  }, 2600);
  while (box.children.length > 4) box.firstChild.remove();
}
window.toast = toast;

/* Python → 前端事件 */
window.__pyEvent = function (ev) {
  if (!ev) return;
  if (ev.type === 'toast') toast(ev.text, ev.kind);
  else if (ev.type === 'completed') toast(ev.text || '定时任务已执行', 'ok');
  else if (ev.type === 'error') toast(ev.text || '出错了', 'err');
};

/* ============ 初始化 ============ */
let bootTries = 0;
async function boot() {
  if (state.booted) return;
  dbg('BOOTING…');
  let d;
  try {
    d = await call('init');
  } catch (e) {
    bootTries++;
    dbg('BOOT-RETRY ' + bootTries);
    if (bootTries < 30) setTimeout(boot, 300);
    else dbg('BOOT-FAIL 后端无响应: ' + e);
    return;
  }
  state.booted = true;
  try {
    buildOps();
    applyPrefs(d.prefs || {});
    $('#ver').textContent = d.version || '';
    if (d.now) fillAtPane(d.now);
    if (d.last_start_seconds && d.last_start_seconds > 0) {
      const s = d.last_start_seconds;
      $('#cd-h').value = String(Math.floor(s / 3600));
      $('#cd-m').value = String(Math.floor((s % 3600) / 60));
      $('#cd-s').value = String(s % 60);
    }
    setMode(state.mode, true);
    selectAction(state.action, true);
    toast('已连接 · 界面就绪', 'ok');
    dbg('BOOT-OK ops=' + document.querySelectorAll('.op').length);
  } catch (e) {
    dbg('BOOT-FAIL ' + e);
    toast('初始化失败：' + e, 'err');
  }
  poll();
  setInterval(poll, 500);
}

/* ============ 渲染（只碰展示元素） ============ */
let polling = false;
async function poll() {
  if (polling || !state.booted) return;
  polling = true;
  try {
    const s = await call('get_state');
    render(s);
  } catch (e) {
    /* 忽略瞬时错误 */
  } finally {
    polling = false;
  }
}

function render(s) {
  if (!s) return;

  const clock = $('#clock');
  if (s.clock !== state.lastClock) {
    clock.textContent = s.clock;
    state.lastClock = s.clock;
  }
  clock.classList.toggle('idle', !s.running);
  clock.classList.toggle('urgent', !!s.urgent);

  const ring = $('#ring');
  ring.style.setProperty('--p', String(Math.max(0, Math.min(100, s.pct || 0))));
  ring.style.setProperty('--ring-a', s.urgent ? '#fbbf24' : '#6ee7ff');

  $('#clockcap').textContent = s.caption || '未开始';

  const dot = $('#statdot');
  dot.className = 'statdot ' + (s.running ? (s.paused ? 'warn' : 'on') : 'off');

  if (s.status !== state.lastStatus) {
    $('#status').textContent = s.status;
    $('#subtitle').textContent = s.status;
    state.lastStatus = s.status;
  }

  if (s.target !== state.lastTarget) {
    $('#target').innerHTML = s.target || '';
    state.lastTarget = s.target;
  }

  $('#btn-start').disabled = !!s.running;
  $('#btn-stop').disabled = !s.running;
  const pause = $('#btn-pause');
  pause.disabled = !s.running;
  pause.textContent = s.paused ? '继续' : '暂停';

  $('#hint').textContent = s.hint || '';
}

/* ============ 操作网格 ============ */
function buildOps() {
  const host = $('#ops');
  host.innerHTML = '';
  ACTIONS.forEach((a) => {
    const b = document.createElement('button');
    b.className = 'op';
    b.dataset.key = a.key;
    b.innerHTML =
      '<span class="ico">' + a.ico + '</span>' +
      '<span class="txt"><span class="n">' + a.name + '</span>' +
      '<span class="d">' + a.desc + '</span></span>';
    b.addEventListener('click', () => selectAction(a.key));
    host.appendChild(b);
  });
}

function selectAction(key, silent) {
  state.action = key;
  $$('.op').forEach((el) => el.classList.toggle('on', el.dataset.key === key));
  const a = ACTIONS.find((x) => x.key === key);
  $('#op-hint').textContent = a ? a.desc : '';

  const param = $('#param');
  const plabel = $('#plabel');
  const pinput = $('#pinput');
  const ptext = $('#ptext');
  const pbtn = $('#pbtn');

  if (key === 'run_program' || key === 'play_sound') {
    param.classList.remove('hide');
    pinput.classList.remove('hidden');
    ptext.classList.add('hidden');
    pbtn.classList.remove('hidden');
    plabel.textContent = key === 'run_program' ? '程序 / 文件路径' : '音频文件路径';
    pinput.placeholder = key === 'run_program'
      ? '选一个 .exe / .bat / 文档…'
      : '选一个 .wav / .mp3 …';
  } else if (key === 'message') {
    param.classList.remove('hide');
    pinput.classList.add('hidden');
    ptext.classList.remove('hidden');
    pbtn.classList.add('hidden');
    plabel.textContent = '消息内容';
    ptext.placeholder = '例如：该睡觉了';
  } else {
    param.classList.add('hide');
  }
  if (!silent) savePrefs();
}

/* ============ 模式切换 ============ */
function setMode(mode, silent) {
  state.mode = mode;
  $$('#seg-mode button').forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
  $('#pane-countdown').classList.toggle('hidden', mode !== 'countdown');
  $('#pane-at').classList.toggle('hidden', mode !== 'at_time');
  $('#pane-week').classList.toggle('hidden', mode !== 'periodic');
  $('#modehint').textContent = HINTS[mode] || '';
  if (!silent) savePrefs();
}

function fillAtPane(now) {
  const d = new Date(now);
  // 默认给到下一个整点
  const t = new Date(d.getTime() + 3600 * 1000);
  $('#at-y').value = String(t.getFullYear());
  $('#at-mo').value = String(t.getMonth() + 1);
  $('#at-d').value = String(t.getDate());
  $('#at-h').value = String(t.getHours());
  $('#at-mi').value = String(t.getMinutes());
  $('#at-s').value = '0';
}

/* ============ 读输入 ============ */
function num(sel, def) {
  const v = parseInt(($(sel).value || '').replace(/\D/g, ''), 10);
  return Number.isFinite(v) ? v : (def || 0);
}

function readPayload() {
  const p = { mode: state.mode, action: state.action };

  if (state.mode === 'countdown') {
    p.h = num('#cd-h', 0);
    p.m = num('#cd-m', 0);
    p.s = num('#cd-s', 0);
  } else if (state.mode === 'at_time') {
    p.y = num('#at-y', 0);
    p.mo = num('#at-mo', 1);
    p.d = num('#at-d', 1);
    p.h = num('#at-h', 0);
    p.m = num('#at-mi', 0);
    p.s = num('#at-s', 0);
  } else {
    p.weeks = state.weeks.map((v, i) => (v ? i : -1)).filter((i) => i >= 0);
    p.h = num('#wk-h', 0);
    p.m = num('#wk-m', 0);
    p.s = num('#wk-s', 0);
  }

  p.param = ($('#ptext').classList.contains('hidden')
    ? $('#pinput').value
    : $('#ptext').value) || '';
  return p;
}

/* ============ 设置 ============ */
function applyPrefs(prefs) {
  state.mode = prefs.mode || 'countdown';
  state.action = prefs.action || 'shutdown';
  if (Array.isArray(prefs.weeks) && prefs.weeks.length === 7) {
    state.weeks = prefs.weeks.map(Boolean);
  }
  $$('#weeks .wk').forEach((b) => {
    b.classList.toggle('on', !!state.weeks[Number(b.dataset.wd)]);
  });

  $('#set-tray').checked = prefs.minimize_to_tray !== false;
  $('#set-force').checked = prefs.force_close !== false;
  $('#set-sound').checked = !!prefs.play_sound_last_10s;
  $('#set-startup').checked = !!prefs.startup;
}

function savePrefs() {
  if (!state.booted) return;
  const p = {
    mode: state.mode,
    action: state.action,
    weeks: state.weeks,
    minimize_to_tray: $('#set-tray').checked,
    force_close: $('#set-force').checked,
    play_sound_last_10s: $('#set-sound').checked,
    startup: $('#set-startup').checked,
  };
  try { call('save_prefs', p); } catch (e) { }
}

/* ============ 事件绑定 ============ */
document.addEventListener('DOMContentLoaded', () => {
  /* --- 窗口按钮 --- */
  $('#btn-min').addEventListener('click', () => call('win', {_arg: 'minimize'}));
  $('#btn-close').addEventListener('click', () => call('win', {_arg: 'close'}));

  /* --- 模式 --- */
  $$('#seg-mode button').forEach((b) => {
    b.addEventListener('click', () => setMode(b.dataset.mode));
  });

  /* --- 星期 --- */
  $$('#weeks .wk').forEach((b) => {
    b.addEventListener('click', () => {
      const i = Number(b.dataset.wd);
      state.weeks[i] = !state.weeks[i];
      b.classList.toggle('on', state.weeks[i]);
      savePrefs();
    });
  });

  /* --- 快捷时长（用户主动点击，允许写值） --- */
  $$('#quick .chip').forEach((c) => {
    c.addEventListener('click', () => {
      const sec = Number(c.dataset.sec);
      $('#cd-h').value = String(Math.floor(sec / 3600));
      $('#cd-m').value = String(Math.floor((sec % 3600) / 60));
      $('#cd-s').value = String(sec % 60);
      $$('#quick .chip').forEach((x) => x.classList.toggle('on', x === c));
    });
  });

  /* --- 数字输入：只允许数字，滚轮不误改 --- */
  $$('.num').forEach((inp) => {
    inp.addEventListener('input', () => {
      const v = inp.value.replace(/\D/g, '');
      if (v !== inp.value) inp.value = v;
    });
    inp.addEventListener('focus', () => inp.select());
    inp.addEventListener('wheel', (e) => e.preventDefault(), { passive: false });
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') startTimer();
      else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        e.preventDefault();
        const step = e.key === 'ArrowUp' ? 1 : -1;
        const cur = parseInt(inp.value || '0', 10) || 0;
        const max = inp.id === 'cd-h' ? 999 : 59;
        let n = cur + step;
        if (n < 0) n = 0;
        if (n > max) n = max;
        inp.value = String(n);
      }
    });
  });

  /* --- 开始 / 暂停 / 停止 --- */
  $('#btn-start').addEventListener('click', startTimer);
  $('#btn-stop').addEventListener('click', () => call('stop'));
  $('#btn-pause').addEventListener('click', () => call('toggle_pause'));

  /* --- 浏览 --- */
  $('#pbtn').addEventListener('click', async () => {
    try {
      const p = await call('pick_file', {_arg: state.action});
      if (p) {
        $('#pinput').value = p;
        toast('已选择：' + p.split(/[\\/]/).pop(), 'ok');
      }
    } catch (e) {
      toast('打开文件选择器失败：' + e, 'err');
    }
  });

  /* --- 设置项 --- */
  ['#set-tray', '#set-force', '#set-sound', '#set-startup'].forEach((sel) => {
    $(sel).addEventListener('change', async () => {
      savePrefs();
      if (sel === '#set-force') {
        try { await call('apply_force_close', {_arg: document.querySelector('#set-force').checked}); } catch (e) { }
      }
      if (sel === '#set-startup') {
        try {
          const r = await call('set_startup', {_arg: document.querySelector('#set-startup').checked});
          if (r && r.ok === false) {
            toast(r.error || '设置开机启动失败', 'err');
            $('#set-startup').checked = !$('#set-startup').checked;
            savePrefs();
          } else {
            toast($('#set-startup').checked ? '已加入开机启动' : '已取消开机启动', 'ok');
          }
        } catch (e) {
          toast('设置开机启动失败：' + e, 'err');
        }
      }
    });
  });

  /* --- 底部按钮 --- */
  $('#btn-log').addEventListener('click', () => call('open_log'));
  $('#btn-cfg').addEventListener('click', () => call('open_config'));

  /* --- 键盘 --- */
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') call('win', {_arg: 'minimize'});
    if (e.key === ' ' && e.target.tagName === 'BODY') {
      e.preventDefault();
      call('toggle_pause');
    }
  });

  /* --- 启动 --- */
  boot();
});

async function startTimer() {
  if (!state.booted) return;
  try {
    const r = await call('start', readPayload());
    if (r && r.ok) toast('已开始计时', 'ok');
    else toast((r && r.error) || '开始失败', 'err');
  } catch (e) {
    toast('开始失败：' + e, 'err');
  }
}
