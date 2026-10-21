// Shared helpers: DOM builder, API wrapper, toast, header, translations (window.I18N comes from /i18n.js).
const P = (() => {
  const t = (key, params) => {
    const s = (window.I18N || {})[key];
    return s == null ? key : params ? s.replace(/\{(\w+)\}/g, (_, n) => params[n] ?? '') : s;
  };
  // Static markup carries the English text plus data-i18n[-ph|-title|-aria]="key"; this swaps in the active language.
  const applyI18n = (root = document) => {
    for (const [attr, set] of [['i18n', (el, v) => (el.textContent = v)], ['i18n-ph', (el, v) => (el.placeholder = v)],
      ['i18n-title', (el, v) => (el.title = v)], ['i18n-aria', (el, v) => el.setAttribute('aria-label', v)]])
      for (const el of root.querySelectorAll(`[data-${attr}]`)) set(el, t(el.getAttribute(`data-${attr}`)));
    document.documentElement.lang = window.LOCALE || 'en';
  };
  const h = (tag, props = {}, ...kids) => {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === 'class') el.className = v;
      else if (k.startsWith('on')) el[k] = v;
      else if (v !== false && v != null) el.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
    return el;
  };
  let timer;
  const toast = (msg, kind = '') => {
    let t = document.getElementById('toast');
    if (!t) { t = h('div', {id: 'toast'}); document.body.append(t); }
    t.textContent = msg; t.className = 'show ' + kind;
    clearTimeout(timer); timer = setTimeout(() => (t.className = ''), 3500);
  };
  // Readable text of a failed response: the server's message, or the first validation error.
  const errorText = async r => {
    try {
      const j = await r.json();
      if (typeof j.detail === 'string') return j.detail;
      if (Array.isArray(j.detail) && j.detail[0]) return j.detail[0].loc.slice(1).join('.') + ': ' + j.detail[0].msg.replace(/^Value error, /, '');
    } catch {}
    return t('common.error');
  };
  const api = async (method, url, body) => {
    const r = await fetch(url, {method, headers: {'Content-Type': 'application/json'}, body: body && JSON.stringify(body)});
    if (r.status === 401 || (r.status === 403 && /^\/api\/(admin|sites|employees|corrections|audit)/.test(url))) {
      location.href = '/login' + (/^\/p\/[\w-]+$/.test(location.pathname) ? '?next=' + encodeURIComponent(location.pathname) : '');
      throw new Error('auth');
    }
    if (!r.ok) {
      const d = await errorText(r);
      toast(d, 'bad'); throw new Error(d);
    }
    return r.status === 204 ? null : r.json();
  };
  const PATHS = {
    dashboard: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    reports: '<path d="M3 3v18h18"/><path d="M18 17V9"/><path d="M13 17V5"/><path d="M8 17v-3"/>',
    corrections: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/>',
    employees: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    sites: '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    qr: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="M14 14h3v3h-3zM20 14v.01M14 20h3M20 17v4"/>',
    logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    close: '<path d="M18 6 6 18M6 6l12 12"/>',
    download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
    printer: '<path d="M6 9V2h12v7"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><path d="M6 14h12v8H6z"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
    shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
    history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
    leave: '<path d="M8 2v4M16 2v4"/><rect x="3" y="4" width="18" height="18" rx="2"/><path d="M3 10h18"/>',
    help: '<circle cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    user: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  };
  // Static icon markup only (no user data ever reaches innerHTML).
  const icon = (name, size = 20) => {
    const w = document.createElement('span');
    w.className = 'ico';
    w.innerHTML = `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${PATHS[name] || ''}</svg>`;
    return w;
  };
  const ADMIN = [['/admin', 'nav.dashboard', 'dashboard'], ['/admin/reports', 'nav.reports', 'reports'],
    ['/admin/corrections', 'nav.corrections', 'corrections'], ['/admin/leave', 'nav.leave', 'leave'], ['/admin/history', 'nav.history', 'history'], ['/admin/attempts', 'nav.attempts', 'shield'],
    ['/admin/employees', 'nav.employees', 'employees'],
    ['/admin/sites', 'nav.sites', 'sites'], ['/admin/qr', 'nav.qr', 'qr'], ['/admin/help', 'nav.manual', 'help']];
  const logout = async () => { await fetch('/auth/logout', {method: 'POST'}); location.href = '/login'; };
  const header = (user, admin) => {
    const current = ADMIN.find(([href]) => location.pathname === href);
    if (!admin) {
      const out = h('button', {class: 'btn ghost sm', onclick: logout}, icon('logout', 16), t('nav.logout'));
      document.body.prepend(h('header', {class: 'top'}, h('div', {class: 'top-in'},
        h('a', {href: '/'}, h('img', {src: '/brand/logo', alt: 'Punctual'})),
        h('div', {class: 'spacer'}), h('a', {href: '/help', class: 'btn ghost sm'}, icon('help', 16), t('nav.help')), h('span', {class: 'who'}, user ? user.name : ''), out)));
      return;
    }
    document.body.classList.add('admin');
    const close = () => document.body.classList.remove('nav-open');
    const side = h('aside', {class: 'side'},
      h('div', {class: 'brand'}, h('a', {href: '/admin'}, h('img', {src: '/brand/logo', alt: 'Punctual'}))),
      h('nav', {}, ADMIN.map(([href, label, ic]) =>
        h('a', {href, class: 'item' + (location.pathname === href ? ' on' : ''), onclick: close}, icon(ic), t(label)))),
      h('div', {class: 'foot'}, icon('user', 18), h('span', {class: 'who'}, user ? user.name : ''),
        h('button', {class: 'btn ghost sm', title: t('nav.logout'), 'aria-label': t('nav.logout'), onclick: logout}, icon('logout', 16))));
    const mtop = h('header', {class: 'mtop'},
      h('button', {class: 'btn ghost sm', 'aria-label': t('nav.menu'), onclick: () => document.body.classList.toggle('nav-open')}, icon('menu')),
      h('img', {src: '/brand/logo', alt: 'Punctual'}), h('span', {class: 'mtitle'}, current ? t(current[1]) : ''));
    document.body.prepend(h('div', {class: 'scrim', onclick: close}), side, mtop);
    fetch('/api/admin/pending-counts').then(r => r.ok ? r.json() : null).then(j => {
      for (const [k, href] of [['correction', '/admin/corrections'], ['leave', '/admin/leave'], ['accounts', '/admin/employees']])
        if (j && j[k] > 0) side.querySelector(`a[href="${href}"]`).append(h('span', {class: 'navbadge'}, j[k]));
    }).catch(() => {});
    // Desktop: hide/show the sidebar, remembered per browser.
    const KEY = 'punctual.sideoff';
    const setOff = off => { document.body.classList.toggle('side-off', off); try { localStorage.setItem(KEY, off ? '1' : ''); } catch {} };
    try { document.body.classList.toggle('side-off', !!localStorage.getItem(KEY)); } catch {}
    document.body.prepend(h('button', {class: 'btn ghost sm sidetoggle', title: t('nav.menu'), 'aria-label': t('nav.menu'),
      onclick: () => setOff(!document.body.classList.contains('side-off'))}, icon('menu', 16)));
    const h1 = document.querySelector('main h1');
    if (h1 && current) h1.prepend(icon(current[2], 26));
  };
  // Resolves with the GPS fix or rejects with an actionable message. Never punch without a fix.
  const getFix = () => new Promise((resolve, reject) => {
    const fail = (reason, msg, e) => reject(Object.assign(new Error(msg), {reason, detail: `${e ? e.code + ' ' + e.message : ''} | ${navigator.userAgent}`}));
    if (!navigator.geolocation) return fail('geo_unsupported', t('geo.unsupported'));
    const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
    navigator.geolocation.getCurrentPosition(resolve, e =>
      e.code === 1 ? fail('geo_denied', t(ios ? 'geo.blocked_ios' : 'geo.blocked'), e)
      : e.code === 2 ? fail('geo_unavailable', t('geo.unavailable'), e)
      : fail('geo_timeout', t('geo.timeout'), e),
    {enableHighAccuracy: true, timeout: 20000, maximumAge: 0});
  });
  // Warn early (before tapping) when location access is already blocked.
  const watchPermission = onBlocked => {
    if (!navigator.permissions) return;
    navigator.permissions.query({name: 'geolocation'}).then(st => {
      const check = () => onBlocked(st.state === 'denied');
      check(); st.onchange = check;
    }).catch(() => {});
  };
  const locale = {en: 'en-GB', ro: 'ro-RO'}[window.LOCALE] || 'en-GB';  // for toLocale*String
  const fmt = n => n.toFixed(2).replace(/\.00$/, '');
  // Browsers show datetime-local in the OS locale (AM/PM on en-US). Swap it for date + 24 h selects; the hidden
  // native input stays the source of truth, so `el.value` (get, set, form reset) works unchanged for page code.
  const two = n => String(n).padStart(2, '0');
  const range = n => Array.from({length: n}, (_, i) => new Option(two(i), two(i)));
  document.querySelectorAll('input[type=datetime-local]').forEach(el => {
    const date = h('input', {type: 'date', required: el.required}), hh = h('select', {}), mm = h('select', {});
    hh.append(...range(24)); mm.append(...range(60));
    const get = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
    const show = () => { const v = get.get.call(el); date.value = v.slice(0, 10); hh.value = v.slice(11, 13) || '00'; mm.value = v.slice(14, 16) || '00'; };
    const push = () => get.set.call(el, date.value ? `${date.value}T${hh.value}:${mm.value}` : '');
    [date, hh, mm].forEach(c => c.addEventListener('input', push));
    Object.defineProperty(el, 'value', {get: () => get.get.call(el), set: v => { get.set.call(el, v); show(); }});
    // min/max only bound the date part here; the server still rejects future times.
    for (const k of ['min', 'max']) Object.defineProperty(el, k, {get: () => date[k], set: v => { date[k] = v.slice(0, 10); }});
    el.required = false; el.style.display = 'none';
    el.form?.addEventListener('reset', () => setTimeout(show));
    el.before(h('div', {class: 'dt24', style: 'display:grid;grid-template-columns:1fr auto auto;gap:.4rem'}, date, hh, mm));
    show();
  });
  applyI18n();
  document.addEventListener('DOMContentLoaded', () => applyI18n());
  return {h, api, toast, header, fmt, getFix, watchPermission, icon, t, applyI18n, errorText, locale};
})();
