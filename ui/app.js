/* The TV interface. Everything on screen is an element from layout.js filled from the hub through the local bridge.
   The remote gives arrows, Enter and Back. */
'use strict';
const $ = (s) => document.querySelector(s);
const stage = $('#stage'), pageEl = $('#page'), mastEl = $('#masthead');
const EASE = 'cubic-bezier(.3,.7,.2,1)';
const TAB_ORDER = ['home', 'continue', 'new', 'movies', 'shows', 'lists', 'browse', 'settings'];
const STAMP_ROWS = new Set(['new']);
const reduce = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const SPEED = 450;

function h(tag, props = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === 'style') Object.assign(e.style, v);
    else if (k === 'class') e.className = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null) e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

// ---- Talking to the bridge ------------------------------------------------------------------
async function api(path, opts = {}) {
  const r = await fetch(path.startsWith('/') ? path : '/api/' + path, { method: opts.method || 'GET', headers: opts.body ? { 'Content-Type': 'application/json' } : {}, body: opts.body ? JSON.stringify(opts.body) : undefined });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.reason || "That didn't work. Try again.");
  return data;
}
const q = encodeURIComponent;
let noticeTimer;
function notice(text) {
  const n = $('#notice');
  n.textContent = text; n.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { n.hidden = true; }, 5000);
}

// ---- State ----------------------------------------------------------------------------------
const S = {
  page: 'home', item: null, full: null, ep: null, season: null, streams: null, cast: null,
  shelves: [], row: null, rowItems: [], rowDone: false, grid: [], gridDone: false, sections: {},
  query: '', results: [], lists: [], draft: { name: '', kind: 'manual', genre: 'Drama' },
  prefs: {}, presets: [], setup: null, busy: false, history: [],
};
const GRIDS = { continue: 'library?collection=continue', next: 'library?collection=next', new: 'new', movies: 'library?collection=movies', shows: 'library?collection=shows' };
const EMPTY = { continue: 'Nothing in progress. Start a film and it appears here.', next: 'Nothing up next yet. Keep a show and its next episode appears here.', new: 'Nothing new right now.', movies: 'No films kept yet. Open any film and press Keep.', shows: 'No shows kept yet. Open any show and press Keep.' };

const seasonsOf = (it) => [...new Set((it?.videos || []).map((v) => v.s))].sort((a, b) => a - b);
const pad = (n) => String(n).padStart(2, '0');
function lengthOf(it) {
  if (it.type === 'series') { const n = seasonsOf(it).length; return n ? (n === 1 ? '1 season' : `${n} seasons`) : null; }
  const m = it.runtimeMin;
  return m > 0 ? (m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`) : null;
}
function leftOf(it) {
  const r = it.resume;
  return r?.durationMs > 0 && r.positionMs > 0 ? `${Math.max(1, Math.round((r.durationMs - r.positionMs) / 60000))} min left` : null;
}
const nextLabel = (it) => (it.nextEpisode ? `S${pad(it.nextEpisode.s)}E${pad(it.nextEpisode.e)}` : null);
const SOURCE_NAMES = { remux: 'Remux', 'web-dl': 'Web', bluray: 'Blu-ray', hdrip: 'HD rip', hdtv: 'TV', dvd: 'DVD', cam: 'Cam' };
const RES_NAMES = { '2160p': '4K', '1440p': '1440p', '1080p': '1080p', '720p': '720p', '576p': '576p', '480p': '480p', '360p': '360p' };
function badgeLine(b) {
  if (!b) return '';
  const known = [RES_NAMES[b.res], ...(b.hdr || []), SOURCE_NAMES[b.source], b.codec, ...(b.audio || [])].filter(Boolean);
  return (b.res ? '' : 'Resolution not stated · ') + known.join(' · ');
}

function bound(key) {
  const it = S.full || S.item || {};
  const top = S.streams?.[0];
  const shelf = S.shelves.find((s) => s.id === S.row);
  switch (key) {
    case 'kicker': return [nextLabel(it), it === S.item && shelf ? shelf.title : null].filter(Boolean).join(' · ') || shelf?.title || '';
    case 'title': return it.title || '';
    case 'credits': return [it.year, it.genres?.[0], lengthOf(it), leftOf(it)].filter(Boolean).join(' · ');
    case 'over': return it.overview || '';
    case 'filmkick': return [it.type === 'series' ? 'Series' : 'Film', it.year, lengthOf(it)].filter(Boolean).join(' · ');
    case 'versionsTitle': return `Every copy of ${it.title || 'this title'}, ranked`;
    case 'marketKick': return S.streams ? `Market report · ${S.streams.length} ${S.streams.length === 1 ? 'copy' : 'copies'} found` : 'Market report';
    case 'facts': return (it.genres || []).join(', ');
    case 'cast': return S.cast?.length ? 'Featuring ' + S.cast.slice(0, 5).map((c) => c.name).join(', ') : '';
    case 'sourceNow': return S.sources?.length ? 'Now using: ' + S.sources.map((a) => hostName(a.host)).join(', ') : 'No source set yet';
    case 'copy': return S.streams === null ? 'Looking…' : top ? badgeLine(top.badges) || (top.source?.name ?? 'Available') : 'No copy found right now';
    case 'copyNote': return top?.badges?.mbps ? `${Math.round(top.badges.mbps)} Mb/s, best match for your speed` : '';
    case 'query': return S.query;
    case 'setupLink': return S.setup?.url || '';
    case 'playLabel': return it.resume?.positionMs > 0 ? 'Resume' : 'Play';
    case 'playNextLabel': return S.ep ? `Play S${S.ep.s} E${S.ep.e}` : it.nextEpisode ? `Play S${it.nextEpisode.s} E${it.nextEpisode.e}` : 'Play';
    case 'keepLabel': return it.inLibrary ? 'Kept' : 'Keep';
    default: return '';
  }
}
function artOf(it) { return it?.backdrop || it?.poster || null; }

// ---- Elements --------------------------------------------------------------------------------
function clearPencil() { document.querySelectorAll('.pencil').forEach((n) => n.remove()); }
function pencil(target) {
  clearPencil();
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('class', 'pencil'); svg.setAttribute('aria-hidden', 'true');
  const r = document.createElementNS(ns, 'rect');
  r.setAttribute('x', 3); r.setAttribute('y', 3); r.setAttribute('pathLength', 1000);
  r.style.width = 'calc(100% - 6px)'; r.style.height = 'calc(100% - 6px)';
  svg.append(r); target.append(svg);
}
function focusable(el, key, onpress, target) {
  el.classList.add('focusable');
  el.tabIndex = 0;
  el.dataset.key = key;
  el.addEventListener('click', onpress);
  el.addEventListener('focus', () => { if (el.classList.contains('tab')) clearPencil(); else pencil(target || el); revealIn(el); });
  return el;
}
// Scrollers keep what has focus in view, one whole item at a time.
function revealIn(el) {
  const sc = el.closest('.strip, .scrolly, .gridw, .chips');
  if (!sc) return;
  const a = el.getBoundingClientRect(), b = sc.getBoundingClientRect(), k = scale();
  if (a.left < b.left) sc.scrollLeft -= (b.left - a.left) / k;
  else if (a.right > b.right) sc.scrollLeft += (a.right - b.right) / k;
  if (a.top < b.top) sc.scrollTop -= (b.top - a.top) / k;
  else if (a.bottom > b.bottom) sc.scrollTop += (a.bottom - b.bottom) / k;
}
function card(it, opts = {}) {
  const prog = it.resume?.durationMs > 0 ? Math.min(100, it.resume.positionMs / it.resume.durationMs * 100) : 0;
  const poster = h('div', { class: 'poster' }, it.poster ? h('img', { src: it.poster, alt: '', loading: 'lazy', decoding: 'async', draggable: 'false' }) : null, prog ? h('div', { class: 'bar-w' }, h('i', { style: { width: prog + '%' } })) : null);
  const c = h('div', { class: 'card' }, poster, h('div', { class: 't' }, it.title));
  focusable(c, opts.key + ':' + it.id + (opts.i ?? ''), () => openTitle(it), poster);
  c.addEventListener('focus', () => {
    if (opts.hero && S.item?.id !== it.id) { S.item = it; S.full = null; resetHero(); }
    if (opts.stamp && !poster.querySelector('.stamp')) poster.append(h('div', { class: 'stamp' }, 'NEW'));
    opts.nearEnd?.(opts.i);
  });
  c.addEventListener('blur', () => poster.querySelector('.stamp')?.remove());
  return c;
}
function fontOf(e) { return e.font === 'display' ? 'var(--display)' : 'var(--body)'; }
function element(e, owner) {
  const base = { left: e.x + 'px', top: e.y + 'px', width: e.w + 'px', height: e.h + 'px' };
  if (e.hidden) return null;
  if (e.kind === 'pic') {
    const n = h('div', { class: 'el pic', style: { ...base, backgroundColor: e.bind ? '#e3dccd' : e.color } });
    if (e.bind) { n.dataset.art = '1'; const art = artOf(S.full || S.item); if (art) n.style.backgroundImage = `url("${CSS.escape ? art.replace(/"/g, '%22') : art}")`; }
    return n;
  }
  if (e.kind === 'widget') return h('div', { class: 'el', style: base, 'data-w': e.id }, widget(e));
  const style = { ...base, fontFamily: fontOf(e), fontSize: e.size + 'px', fontWeight: e.weight, color: e.color, lineHeight: '1.15' };
  if (e.italic) style.fontStyle = 'italic';
  if (e.align) style.textAlign = e.align;
  if (e.transform) style.textTransform = e.transform;
  if (e.spacing) { style.letterSpacing = e.spacing; style.whiteSpace = 'nowrap'; style.textOverflow = 'ellipsis'; }
  if (e.nowrap) style.whiteSpace = 'nowrap';
  if (e.kind === 'btn') {
    style.background = e.fill;
    const n = h('div', { class: 'el btn', style }, e.bindText ? bound(e.bindText) : e.text);
    if (e.bindText) n.dataset.btext = e.bindText;
    return focusable(n, owner + ':' + e.id, () => press(e.to));
  }
  if (e.kind === 'tab') {
    const search = e.to === 'search';
    const on = S.page === e.to || (e.to === 'home' && ['film', 'show', 'versions'].includes(S.page));
    if (search) { style.color = '#1a1714'; }
    const n = h('div', { class: 'el tab' + (on && !search ? ' on' : ''), style, role: 'button', 'aria-label': search ? 'Search' : null }, search ? searchIcon() : e.text);
    return focusable(n, 'tab:' + e.to, () => tabTo(e.to));
  }
  const n = h('div', { class: 'el txt', style }, e.bind ? bound(e.bind) : e.text);
  if (e.id === 'clock') n.id = 'clock';
  if (e.bind) { n.dataset.bind = e.bind; n.dataset.owner = owner; n.dataset.id = e.id; }
  return n;
}
function searchIcon() {
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  for (const [k, v] of Object.entries({ viewBox: '0 0 24 24', width: 36, height: 36, fill: 'none', stroke: 'currentColor', 'stroke-width': 2.4, 'stroke-linecap': 'round', 'aria-hidden': 'true' })) svg.setAttribute(k, v);
  const c = document.createElementNS(ns, 'circle');
  c.setAttribute('cx', 10.5); c.setAttribute('cy', 10.5); c.setAttribute('r', 6.5);
  const l = document.createElementNS(ns, 'path');
  l.setAttribute('d', 'M15.5 15.5L21 21');
  svg.append(c, l);
  return svg;
}
function mini(label, key, fn) { return focusable(h('div', { class: 'mini' }, label), key, fn); }
function line(key, fn, ...kids) { return focusable(h('div', { class: 'line' }, ...kids), key, fn); }

function widget(e) {
  switch (e.widget) {
    case 'rowtabs': return h('div', { class: 'chipbar' }, h('div', { class: 'chips' }, S.shelves.filter((s) => !s.hidden).map((s) => focusable(h('div', { class: 'chip' + (s.id === S.row ? ' on' : ''), 'data-row': s.id }, s.title), 'chip:' + s.id, () => switchRow(s.id)))), focusable(h('div', { class: 'chip add' }, '+ Lists'), 'chip:add', () => tabTo('lists')));
    case 'row': {
      if (e.list === 'current') return rowBody();
      const items = S.sections[e.id] || [];
      return items.length ? h('div', { class: 'strip' }, items.map((it, i) => card(it, { key: e.id, i }))) : h('div', { class: 'empty' }, S.busy ? '' : 'Nothing here right now.');
    }
    case 'grid': return S.grid.length ? h('div', { class: 'gridw' }, S.grid.map((it, i) => card(it, { key: 'grid', i, nearEnd: moreGrid }))) : h('div', { class: 'empty' }, S.busy ? '' : EMPTY[S.page] || 'Nothing here yet.');
    case 'seasons': return h('div', { style: { display: 'flex', gap: '10px', height: '100%', overflow: 'hidden' } }, seasonsOf(S.full).map((n) => { const on = n === S.season; return focusable(h('div', { class: 'btn', style: { flex: 'none', width: '170px', height: '60px', fontSize: '24px', background: on ? '#1a1714' : '#ddd6c7', color: on ? '#f4efe4' : '#1a1714' } }, `Season ${n}`), 'season:' + n, () => { S.season = n; draw(); }); }));
    case 'episodes': { setTimeout(wireEpisodes); const list = (S.full?.videos || []).filter((v) => v.s === S.season); return h('div', { class: 'scrolly' }, list.map((v, i) => { const nxt = S.full.nextEpisode; const here = nxt && nxt.s === v.s && nxt.e === v.e; return line(`ep:${v.s}:${v.e}`, () => play({ s: v.s, e: v.e }), h('b', {}, v.e), h('span', {}, v.title || `Episode ${v.e}`), h('em', {}, here ? (leftOf(S.full) || 'Next up') : '')); })); }
    case 'board': return h('div', { class: 'scrolly', style: { borderTop: '2px solid #1a1714' } }, (S.streams || []).filter((s) => s.playable).slice(0, 30).map((c, i) => { const r = line('copy:' + i, () => play({ ...(S.ep || {}), version: i }), h('b', {}, i + 1), h('span', {}, badgeLine(c.badges) || c.source?.name || 'Copy'), h('i', {}, c.badges?.sizeGb ? c.badges.sizeGb + ' GB' : ''), h('i', {}, c.badges?.mbps ? Math.round(c.badges.mbps * 10) / 10 + ' Mb/s' : ''), h('em', { class: 'note' }, i === 0 ? 'Best match' : c.badges?.cached === false ? 'Not ready' : c.badges?.cached ? 'Ready' : '')); r.classList.add('set'); r.style.animationDelay = Math.min(i, 8) * 40 + 'ms'; if (i === 0) r.classList.add('best'); return r; }), S.streams && !S.streams.length ? h('div', { class: 'empty' }, 'No copies found right now.') : null);
    case 'keys': return keyboard('search', (t) => { S.query = t; refreshBound(); searchSoon(); }, () => S.query, false);
    case 'results': return h('div', { class: 'scrolly' }, S.results.map((it) => line('res:' + it.id, () => openTitle(it), h('span', { style: { fontFamily: 'var(--display)', fontWeight: 700 } }, it.title), h('em', {}, [it.year, it.type === 'series' ? 'Series' : 'Film'].filter(Boolean).join(' · ')))), !S.results.length && S.query.trim() && !S.busy ? h('div', { class: 'empty' }, 'Nothing matches that yet.') : null);
    case 'settings': return settingsBody();
    case 'rowsmanager': return managerBody();
    case 'newlist': return newListBody();
    case 'qr': return (S.page === 'source' ? S.sourceQr : S.setup)?.svg ? h('img', { class: 'qr', src: (S.page === 'source' ? S.sourceQr : S.setup).svg, alt: 'QR code for the setup page' }) : null;
    default: return null;
  }
}
function rowBody() {
  if (!S.rowItems.length) return h('div', { class: 'empty' }, S.busy ? '' : 'This row is empty. Open any film and press Add to list, or choose another row.');
  const stamp = STAMP_ROWS.has(S.shelves.find((s) => s.id === S.row)?.ref);
  return h('div', { class: 'strip' }, S.rowItems.map((it, i) => card(it, { key: 'row', i, hero: true, stamp, nearEnd: moreRow })));
}
function keyboard(key, set, get, small) {
  const add = (c) => set(get() + c);
  return h('div', { class: 'keys' + (small ? ' small' : '') }, [...'ABCDEFGHIJKLMNOPQRSTUVWXYZ0'].map((c) => focusable(h('div', { class: 'key' }, c), `${key}:${c}`, () => add(c.toLowerCase()))),
    focusable(h('div', { class: 'key wide' }, 'Space'), key + ':space', () => add(' ')), focusable(h('div', { class: 'key wide' }, 'Delete'), key + ':del', () => set(get().slice(0, -1))));
}
function settingsBody() {
  const speed = S.prefs.preset || S.presets.default || '';
  return h('div', { class: 'scrolly' },
    line('set:source', () => go('source'), h('span', {}, 'Stream source'), h('em', {}, S.sources?.length ? S.sources.map((a) => hostName(a.host) + (a.reachable ? '' : ' (not answering)')).join(', ') : 'Add AIOStreams')),
    line('set:ready', () => setPref('readyOnly', S.prefs.readyOnly === false), h('span', {}, 'Only play copies ready to stream'), h('em', {}, S.prefs.readyOnly === false ? 'Off' : 'On')),
    line('set:choose', () => setPref('choose', !S.prefs.choose), h('span', {}, 'When I press Play'), h('em', {}, S.prefs.choose ? 'Let me choose the copy' : 'Play the best copy')),
    line('set:turn', () => { const o = ['auto', 'full', 'off'], c = S.prefs.turn || 'auto'; setPref('turn', o[(o.indexOf(c) + 1) % o.length]); }, h('span', {}, 'Page turn'), h('em', {}, { auto: S.slowBox ? 'Automatic (simple on this box)' : 'Automatic', full: 'Always full', off: 'Off' }[S.prefs.turn || 'auto'])),
    line('set:speed', () => { const names = Object.keys(S.presets.presets || {}); if (!names.length) return; const i = names.indexOf(speed); S.prefs.preset = names[(i + 1) % names.length]; api('/prefs', { method: 'POST', body: { preset: S.prefs.preset } }).catch(() => notice("Couldn't save that.")); draw(); }, h('span', {}, 'Connection speed'), h('em', {}, speedName(speed))),
    line('set:privacy', () => {}, h('span', {}, 'Privacy'), h('em', {}, 'Nothing leaves this box')));
}
const hostName = (host) => (/^(127\.|localhost)/.test(host) ? 'Built in to this box' : host);
function setPref(name, value) {
  S.prefs[name] = value;
  S.streams = null;
  api('/prefs', { method: 'POST', body: { [name]: value } }).catch(() => notice("Couldn't save that."));
  draw();
}
function speedName(s) { return { slow: 'Slow', standard: 'Standard', fast: 'Fast', max: 'Fastest' }[s] || s || ''; }
function managerBody() {
  return h('div', { class: 'scrolly' }, h('div', { class: 'head' }, 'Rows on Home, in order'), S.shelves.map((s, i) => {
    const r = h('div', { class: 'mrow' + (s.hidden ? ' off' : '') }, h('span', {}, s.title, s.kind === 'collection' ? h('em', {}, 'your list') : null),
      mini('Up', `m:${s.id}:up`, () => moveShelf(i, -1)), mini('Down', `m:${s.id}:down`, () => moveShelf(i, 1)),
      mini(s.hidden ? 'Show' : 'Hide', `m:${s.id}:hide`, () => { s.hidden = !s.hidden; saveShelves(); }),
      s.kind === 'collection' ? mini('Delete', `m:${s.id}:del`, () => deleteList(s)) : null);
    return r;
  }));
}
function newListBody() {
  const d = S.draft;
  return h('div', { class: 'newlist' }, h('div', { class: 'head', style: { marginBottom: 0 } }, 'Start a new list'),
    h('div', { class: 'field' + (d.name ? '' : ' none'), id: 'nl-name' }, d.name || 'Type a name below'),
    keyboard('nl', (t) => { d.name = t; const f = $('#nl-name'); f.textContent = t || 'Type a name below'; f.classList.toggle('none', !t); }, () => d.name, true),
    h('div', { class: 'two' }, focusable(h('div', { class: 'chip' + (d.kind === 'manual' ? ' on' : '') }, 'I pick the titles'), 'nl:manual', () => { d.kind = 'manual'; draw(); }), focusable(h('div', { class: 'chip' + (d.kind === 'smart' ? ' on' : '') }, 'Fill it by genre'), 'nl:smart', () => { d.kind = 'smart'; draw(); }),
      d.kind === 'smart' ? focusable(h('div', { class: 'chip' }, d.genre), 'nl:genre', () => { const g = ['Drama', 'Comedy', 'Thriller', 'Action', 'Horror', 'Romance', 'Mystery', 'Sci-Fi', 'Documentary']; d.genre = g[(g.indexOf(d.genre) + 1) % g.length]; draw(); }) : null),
    focusable(h('div', { class: 'cta' }, 'Add to Home'), 'nl:go', createList));
}

// ---- Drawing --------------------------------------------------------------------------------
function scale() { return stage.getBoundingClientRect().width / 1920; }
function fit() {
  const k = Math.min(innerWidth / 1920, innerHeight / 1080);
  stage.style.transform = `translate(${(innerWidth - 1920 * k) / 2}px, ${(innerHeight - 1080 * k) / 2}px) scale(${k})`;
}
addEventListener('resize', fit);
function fitText() {
  document.querySelectorAll('#stage [data-bind]').forEach((n) => {
    const e = (DEFAULTS[n.dataset.owner] || []).find((x) => x.id === n.dataset.id);
    if (!e) return;
    n.style.fontSize = e.size + 'px';
    if (e.nowrap) {
      const min = Math.round(e.size * .45);
      let size = e.size;
      while (n.scrollWidth > n.clientWidth + 1 && size > min) { size -= 2; n.style.fontSize = size + 'px'; }
      n.style.textOverflow = 'ellipsis';
    } else if (!e.spacing) {
      n.style.display = '-webkit-box'; n.style.webkitBoxOrient = 'vertical'; n.style.overflow = 'hidden';
      const lines = Math.max(1, Math.floor(e.h / (e.size * 1.15)));
      n.style.webkitLineClamp = String(lines);
      n.style.height = Math.ceil(lines * e.size * 1.15) + 'px';
    }
  });
}
function listCues() {
  document.querySelectorAll('#stage .scrolly').forEach((box) => {
    const host = box.parentElement;
    const cue = () => {
      host.querySelector(':scope > .morev')?.remove();
      if (box.scrollTop + box.clientHeight < box.scrollHeight - 4) host.append(h('div', { class: 'morev' }, 'More below'));
    };
    box.addEventListener('scroll', cue, { passive: true });
    cue();
  });
}
function draw(focusKey) {
  const keep = focusKey || document.activeElement?.dataset?.key;
  if (S.page === 'setup') mastEl.textContent = '';
  else if (!mastEl.childElementCount || mastEl.dataset.page !== S.page) {
    mastEl.textContent = '';
    for (const e of DEFAULTS.masthead) { const n = element(e, 'masthead'); if (n) mastEl.append(n); }
    mastEl.dataset.page = S.page;
    tick();
  }
  pageEl.textContent = '';
  for (const e of DEFAULTS[S.page] || []) { const n = element(e, S.page); if (n) pageEl.append(n); }
  fitText();
  listCues();
  focusOn(keep);
  saveState();
}
function focusOn(key) {
  const safe = key && document.querySelector(`#stage [data-key="${CSS.escape(key)}"]`);
  const target = safe || pageEl.querySelector('.card, .line, .btn, .key, .chip, .mini') || mastEl.querySelector('.tab');
  target?.focus({ preventScroll: true });
}
function resetHero() {
  refreshBound();
  document.querySelectorAll('[data-art]').forEach((n) => { const art = artOf(S.full || S.item); n.style.backgroundImage = art ? `url("${art.replace(/"/g, '%22')}")` : ''; });
  if (!S.item) return;
  document.querySelectorAll('#page [data-bind="title"]').forEach((n) => { if (reduce()) return; n.animate([{ opacity: 0, transform: 'translateY(-34px)' }, { opacity: 1, transform: 'none' }], { duration: 280, easing: EASE }); });
}
function refreshBound() {
  document.querySelectorAll('#stage [data-bind]').forEach((n) => { n.textContent = bound(n.dataset.bind); });
  document.querySelectorAll('#stage [data-btext]').forEach((n) => { const p = n.querySelector('.pencil'); n.textContent = bound(n.dataset.btext); if (p) n.append(p); });
  fitText();
}
function tick() { const c = $('#clock'); if (c) c.textContent = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); }
setInterval(tick, 20000);
function saveState() {
  clearTimeout(saveState.t);
  saveState.t = setTimeout(() => api('/state', { method: 'POST', body: { page: S.page, item: S.item && { id: S.item.id, type: S.item.type, title: S.item.title, poster: S.item.poster, backdrop: S.item.backdrop }, row: S.row, ep: S.ep, season: S.season, key: document.activeElement?.dataset?.key, history: S.history.slice(-8) } }).catch(() => {}), 150);
}

// ---- Moving around --------------------------------------------------------------------------
function withLoading(fn) {
  S.busy = true;
  return Promise.resolve(fn()).catch((e) => notice(e.message)).finally(() => { S.busy = false; });
}
async function loadPage(page) {
  if (page === 'home') {
    S.shelves = (await api('shelves')).shelves;
    if (!S.shelves.some((s) => s.id === S.row && !s.hidden)) S.row = (S.shelves.find((s) => !s.hidden) || {}).id || null;
    await loadRow(S.row);
    if (!S.item && !S.rowItems.length) await firstFilledRow();
    if (!S.item) S.item = S.rowItems[0] || null;
  } else if (GRIDS[page]) { S.grid = pickItems(await api(GRIDS[page])); S.gridDone = false; }
  else if (page === 'browse') {
    const { catalogs } = await api('browse/catalogs');
    const first = (type) => catalogs.find((c) => c.type === type && c.id === 'top') || catalogs.find((c) => c.type === type);
    const load = async (c) => (c ? pickItems(await api(`browse/catalog/${q(c.type)}/${q(c.id)}`).catch(() => ({ items: [] }))) : []);
    const [films, shows] = await Promise.all([load(first('movie')), load(first('series'))]);
    S.sections = { r1: films, r2: shows };
  } else if (page === 'lists') { S.shelves = (await api('shelves')).shelves; S.lists = (await api('collections')).collections; }
  else if (page === 'settings') { S.presets = await api('presets'); S.prefs = await api('/prefs'); S.sources = streamSources(await api('health')); }
  else if (page === 'source') { S.sourceQr = await api('/source-qr', { method: 'POST' }); S.sources = streamSources(await api('health')); watchSource(S.sources.map((a) => a.host).join()); }
  else if (page === 'film' || page === 'show') await loadTitle();
  else if (page === 'versions') { if (!S.streams) await loadStreams(); }
}
const pickItems = (d) => d.items || d.metas || [];
const streamSources = (h) => (h.addons || []).filter((a) => a.kind === 'stream');
// The phone page does the work; when the source changes, say so and return to Settings.
async function watchSource(before) {
  if (S.page !== 'source') return;
  const now = streamSources(await api('health').catch(() => ({ addons: S.sources })));
  if (S.page !== 'source') return;
  if (now.map((a) => a.host).join() !== before) {
    S.sources = now;
    S.history.pop();
    notice('Now using ' + now.map((a) => hostName(a.host)).join(', '));
    return go('settings', { back: true, key: 'set:source' });
  }
  setTimeout(() => watchSource(before), 4000);
}
async function firstFilledRow() {
  for (const s of S.shelves.filter((x) => !x.hidden)) { if (s.id === S.row) continue; await loadRow(s.id); if (S.rowItems.length) { S.row = s.id; return; } }
}
async function loadRow(id) {
  S.rowItems = []; S.rowDone = false;
  if (!id) return;
  S.rowItems = pickItems(await api(`shelf/${q(id)}`));
}
let moreBusy = false;
async function moreRow(i) {
  if (moreBusy || S.rowDone || i < S.rowItems.length - 6 || !S.row) return;
  moreBusy = true;
  try {
    const more = pickItems(await api(`shelf/${q(S.row)}?skip=${S.rowItems.length}`)).filter((x) => !S.rowItems.some((y) => y.id === x.id));
    if (!more.length) { S.rowDone = true; return; }
    S.rowItems.push(...more);
    const strip = document.querySelector('[data-w="row"] .strip');
    more.forEach((it, k) => strip?.append(card(it, { key: 'row', i: S.rowItems.length - more.length + k, hero: true, nearEnd: moreRow })));
  } catch { S.rowDone = true; } finally { moreBusy = false; }
}
async function moreGrid(i) {
  if (moreBusy || S.gridDone || i < S.grid.length - 14 || S.page !== 'new') return;
  moreBusy = true;
  try {
    const more = pickItems(await api(`new?skip=${S.grid.length}`)).filter((x) => !S.grid.some((y) => y.id === x.id));
    if (!more.length) { S.gridDone = true; return; }
    S.grid.push(...more);
    const grid = document.querySelector('[data-w="grid"] .gridw');
    more.forEach((it, k) => grid?.append(card(it, { key: 'grid', i: S.grid.length - more.length + k, nearEnd: moreGrid })));
  } catch { S.gridDone = true; } finally { moreBusy = false; }
}
async function loadTitle() {
  const it = S.item;
  if (!it) return;
  S.full = await api(`browse/meta/${q(it.type)}/${q(it.id)}`);
  S.streams = null; S.cast = null;
  const seasons = seasonsOf(S.full);
  if (it.type === 'series') S.season = S.full.nextEpisode?.s ?? seasons[0] ?? null;
  loadStreams().then(() => refreshBound()).catch(() => { S.streams = []; refreshBound(); });
  api(`browse/cast/${q(it.type)}/${q(it.id)}`).then((d) => { S.cast = d.cast; refreshBound(); }).catch(() => {});
}
async function loadStreams() {
  const it = S.full || S.item;
  const ep = S.ep || (it.type === 'series' ? it.nextEpisode || firstEpisode(it) : null);
  const path = `browse/streams/${q(it.type)}/${q(it.id)}` + (ep ? `/${ep.s}/${ep.e}` : '');
  S.streams = (await api(path)).streams || [];
}
function wireEpisodes() {
  document.querySelectorAll('[data-w="episodes"] .line').forEach((n) => {
    if (n.dataset.wired) return;
    n.dataset.wired = '1';
    n.addEventListener('focus', () => { const [, s, e] = n.dataset.key.split(':'); S.ep = { s: +s, e: +e }; refreshBound(); });
  });
}
function firstEpisode(it) { const v = (it.videos || [])[0]; return v ? { s: v.s, e: v.e } : null; }
async function go(page, opts = {}) {
  if (!opts.back) S.history.push({ page: S.page, item: S.item, ep: S.ep, key: document.activeElement?.dataset?.key });
  S.page = page;
  if (page !== 'film' && page !== 'show' && page !== 'versions') { S.full = page === 'home' ? null : S.full; }
  mastEl.dataset.page = '';
  await withLoading(() => loadPage(page));
  draw(opts.key);
  if (!reduce() && (page === 'film' || page === 'show')) pageEl.querySelectorAll('.el').forEach((n, i) => n.animate([{ opacity: 0, transform: 'translateY(16px)' }, { opacity: 1, transform: 'none' }], { duration: 320, delay: Math.min(i, 8) * 30, easing: 'ease-out', fill: 'backwards' }));
}
function openTitle(it) { S.item = it; S.full = null; S.ep = null; S.streams = null; return go(it.type === 'series' ? 'show' : 'film'); }
function back() {
  if (!$('#picker').hidden) { $('#picker').hidden = true; focusOn(pickerFrom); return; }
  const prev = S.history.pop();
  if (!prev) return;
  S.item = prev.item; S.ep = prev.ep; S.streams = null; if (prev.page !== 'film' && prev.page !== 'show' && prev.page !== 'versions') S.full = null;
  go(prev.page, { back: true, key: prev.key });
}
// A turn the box can't draw smoothly looks worse than none, so count its frames and step down by itself.
function watchTurn(t) {
  let frames = 0, on = true;
  const count = () => { frames += 1; if (on) requestAnimationFrame(count); };
  t.ready.then(() => {
    const start = performance.now();
    requestAnimationFrame(count);
    t.finished.catch(() => {}).finally(() => {
      on = false;
      const fps = Math.round(frames / ((performance.now() - start) / 1000));
      S.turnFps = fps;
      if (fps < 40) S.slowBox = true;
      api('/prefs', { method: 'POST', body: { lastTurnFps: fps, slowBox: !!S.slowBox } }).catch(() => {});
    });
  }).catch(() => {});
}
// Top tabs turn the page. The browser snapshots it, so the turn moves one flat picture and the masthead stays out of it.
async function tabTo(page) {
  if (page === S.page) return;
  const a = TAB_ORDER.indexOf(S.page), b = TAB_ORDER.indexOf(page);
  const backwards = a >= 0 && b >= 0 && b < a;
  S.history.push({ page: S.page, item: S.item, ep: S.ep, key: document.activeElement?.dataset?.key });
  S.page = page;
  if (page === 'home') S.full = null;
  await withLoading(() => loadPage(page));
  const paint = () => { mastEl.dataset.page = ''; draw('tab:' + page); };
  const style = S.prefs.turn === 'off' ? 'off' : S.prefs.turn === 'full' ? 'full' : S.slowBox ? 'simple' : 'full';
  if (!document.startViewTransition || reduce() || style === 'off') return paint();
  document.documentElement.classList.add(style === 'simple' ? 'turn-simple' : backwards ? 'turn-back' : 'turn-forward');
  const t = document.startViewTransition(paint);
  if (style === 'full') watchTurn(t);
  t.ready.catch(() => {});
  t.finished.catch(() => {}).finally(() => document.documentElement.classList.remove('turn-back', 'turn-forward', 'turn-simple'));
}
// Row tabs riffle: posters are dealt in, one after another, in the direction of travel.
let rowTimer;
async function switchRow(id) {
  if (id === S.row) return;
  const shown = S.shelves.filter((s) => !s.hidden);
  const dir = shown.findIndex((s) => s.id === id) >= shown.findIndex((s) => s.id === S.row) ? 1 : -1;
  S.row = id;
  document.querySelector('.chip.on')?.classList.remove('on');
  document.querySelector(`[data-row="${CSS.escape(id)}"]`)?.classList.add('on');
  await withLoading(() => loadRow(id));
  if (S.row !== id) return;
  if (S.rowItems[0]) { S.item = S.rowItems[0]; S.full = null; resetHero(); }
  const host = document.querySelector('[data-w="row"]');
  if (!host) return;
  host.textContent = '';
  const body = rowBody();
  host.append(body);
  saveState();
  if (reduce()) return;
  const cards = [...body.querySelectorAll('.card')].slice(0, 7);
  (dir < 0 ? cards.reverse() : cards).forEach((c, i) => c.animate([{ opacity: 0, transform: `translateX(${dir * 70}px)` }, { opacity: 1, transform: 'none' }], { duration: SPEED * .7, delay: i * SPEED * .07, easing: EASE, fill: 'backwards' }));
}
function press(to) {
  if (to === 'play') return S.page === 'home' ? playItem(S.item) : play(S.ep || {});
  if (to === 'title') return S.item && openTitle(S.item);
  if (to === 'versions') { S.streams = null; return go('versions'); }
  if (to === 'back') return back();
  if (to === 'keep') return toggleKeep();
  if (to === 'addlist') return addToList();
  if (to) tabTo(to);
}
function playItem(it) { if (!it) return; const n = it.nextEpisode; return play(n ? { s: n.s, e: n.e } : {}); }
async function play(opts) {
  let it = S.full || S.item;
  if (!it || S.playing) return;
  const ep = opts.s ? { s: opts.s, e: opts.e } : it.type === 'series' ? it.nextEpisode || firstEpisode(it) : null;
  if (S.prefs.choose && !Number.isInteger(opts.version) && S.page !== 'versions') { S.ep = ep; S.streams = null; return go('versions'); }
  S.playing = true;
  $('#press').hidden = false;
  try {
    S.ep = ep;
    clearTimeout(saveState.t);
    await api('/state', { method: 'POST', body: { page: S.page, item: { id: it.id, type: it.type, title: it.title, poster: it.poster, backdrop: it.backdrop }, row: S.row, ep, season: S.season, key: document.activeElement?.dataset?.key, history: S.history.slice(-8) } });
    await api('/play', { method: 'POST', body: { id: it.id, type: it.type, ...(ep || {}), ...(Number.isInteger(opts.version) ? { version: opts.version } : {}) } });
    $('#lights').classList.add('down');
  } catch (e) {
    $('#press').hidden = true;
    S.playing = false;
    notice(e.message);
  }
}
async function toggleKeep() {
  const it = S.full;
  if (!it) return;
  try {
    if (it.inLibrary) { await api('library/' + q(it.id), { method: 'DELETE' }); it.inLibrary = false; }
    else { await api('keep', { method: 'POST', body: { id: it.id, type: it.type } }); it.inLibrary = true; }
    refreshBound();
  } catch (e) { notice(e.message); }
}
let pickerFrom = null;
async function addToList() {
  const it = S.full || S.item;
  pickerFrom = document.activeElement?.dataset?.key;
  try { S.lists = (await api('collections')).collections; } catch (e) { return notice(e.message); }
  const close = () => { $('#picker').hidden = true; focusOn(pickerFrom); };
  const manual = S.lists.filter((l) => l.kind === 'manual');
  const box = $('#picker');
  box.textContent = '';
  box.append(h('div', { class: 'pick' }, h('div', { class: 'head' }, 'Add ' + it.title + ' to'), h('div', { class: 'scrolly' },
    manual.map((l) => line('pick:' + l.id, async () => { try { await api(`collections/${q(l.id)}/items`, { method: 'POST', body: { id: it.id, type: it.type } }); notice(`Added to ${l.title}`); } catch (e) { notice(e.message); } close(); }, h('span', {}, l.title))),
    line('pick:new', () => { box.hidden = true; tabTo('lists'); }, h('span', {}, '+ New list')), line('pick:cancel', close, h('span', {}, 'Cancel')))));
  box.hidden = false;
  box.querySelector('.line')?.focus({ preventScroll: true });
}
function moveShelf(i, d) { const j = i + d; if (j < 0 || j >= S.shelves.length) return; [S.shelves[i], S.shelves[j]] = [S.shelves[j], S.shelves[i]]; saveShelves(); }
async function saveShelves() { draw(); try { S.shelves = (await api('shelves', { method: 'PUT', body: { shelves: S.shelves } })).shelves; } catch (e) { notice(e.message); } }
async function deleteList(s) { try { await api('collections/' + q(s.ref), { method: 'DELETE' }); S.shelves = (await api('shelves')).shelves; draw(); notice(`Deleted ${s.title}`); } catch (e) { notice(e.message); } }
async function createList() {
  const d = S.draft, title = d.name.trim();
  if (!title) return notice('Name the list first.');
  const body = d.kind === 'smart' ? { title, kind: 'smart', filter: { genres: [d.genre] } } : { title, kind: 'manual' };
  try {
    const made = await api('collections', { method: 'POST', body });
    S.draft = { name: '', kind: 'manual', genre: 'Drama' };
    S.row = 'collection:' + made.id;
    S.item = null;
    tabTo('home');
  } catch (e) { notice(e.message); }
}
let searchTimer;
function searchSoon() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(async () => {
    const text = S.query.trim();
    if (!text) { S.results = []; return draw(); }
    try { const d = await api('search?q=' + q(text)); if (S.query.trim() === text) { S.results = pickItems(d); const key = document.activeElement?.dataset?.key; const host = document.querySelector('[data-w="results"]'); if (host) { host.textContent = ''; host.append(widget({ widget: 'results' })); listCues(); focusOn(key); } } } catch (e) { notice(e.message); }
  }, 300);
}

// ---- The remote -------------------------------------------------------------------------------
function nextFocus(dir) {
  const from = document.activeElement;
  const scope = $('#picker').hidden ? stage : $('#picker');
  const all = [...scope.querySelectorAll('.focusable')].filter((n) => n !== from && n.getClientRects().length);
  if (!from || !from.classList.contains('focusable')) return all[0];
  const a = from.getBoundingClientRect();
  const ax = a.left + a.width / 2, ay = a.top + a.height / 2;
  let best = null, bestScore = Infinity;
  for (const n of all) {
    const b = n.getBoundingClientRect();
    const bx = b.left + b.width / 2, by = b.top + b.height / 2;
    const along = dir === 'left' ? a.left - b.right : dir === 'right' ? b.left - a.right : dir === 'up' ? a.top - b.bottom : b.top - a.bottom;
    const ok = dir === 'left' ? bx < ax - 1 : dir === 'right' ? bx > ax + 1 : dir === 'up' ? by < ay - 1 : by > ay + 1;
    if (!ok) continue;
    const across = dir === 'left' || dir === 'right' ? Math.abs(by - ay) : Math.abs(bx - ax);
    const score = Math.max(along, 0) + across * 2.2;
    if (score < bestScore) { bestScore = score; best = n; }
  }
  return best;
}
addEventListener('keydown', (ev) => {
  const dirs = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' };
  if (dirs[ev.key]) {
    ev.preventDefault();
    const n = nextFocus(dirs[ev.key]);
    if (n) { n.focus({ preventScroll: true }); saveState(); }
  } else if (ev.key === 'Enter' || ev.key === ' ') {
    ev.preventDefault();
    if (ev.repeat) return;
    document.activeElement?.click();
  } else if (ev.key === 'Escape' || ev.key === 'GoBack' || ev.key === 'BrowserBack') {
    ev.preventDefault();
    back();
  } else if (ev.key === 'Backspace') {
    ev.preventDefault();
    if (S.page === 'search' && S.query) { S.query = S.query.slice(0, -1); refreshBound(); searchSoon(); } else back();
  } else if (S.page === 'search' && /^[a-z0-9]$/i.test(ev.key) && !ev.ctrlKey && !ev.altKey && !ev.metaKey) {
    S.query += ev.key.toLowerCase(); refreshBound(); searchSoon();
  }
});

// ---- Start ------------------------------------------------------------------------------------
async function setupWatch() {
  const info = await api('/setup-qr').catch(() => ({}));
  if (info.url) {
    S.setup = info;
    if (S.page !== 'setup') { S.page = 'setup'; draw(); }
    else refreshBound();
    return setTimeout(setupWatch, 5000);
  }
  if (S.page === 'setup') { S.setup = null; S.page = 'home'; await withLoading(() => loadPage('home')); draw(); }
}
async function start() {
  fit();
  const { state, error } = await api('/state').catch(() => ({ state: {} }));
  S.prefs = await api('/prefs').catch(() => ({}));
  S.slowBox = !!S.prefs.slowBox;
  const cold = !state?.page;
  const setup = await api('/setup-qr').catch(() => ({}));
  if (setup.url) { S.setup = setup; S.page = 'setup'; draw(); if (cold) document.body.classList.add('boot'); setTimeout(setupWatch, 5000); return; }
  if (!cold) {
    S.page = state.page; S.item = state.item; S.row = state.row; S.ep = state.ep; S.history = state.history || [];
    await withLoading(() => loadPage(S.page));
    if (S.page === 'home' && S.item) S.item = S.rowItems.find((x) => x.id === S.item.id) || await api(`browse/meta/${q(S.item.type)}/${q(S.item.id)}`).catch(() => S.rowItems[0] || null);
    if (state.season != null) S.season = state.season;
  } else { document.body.classList.add('boot'); await withLoading(() => loadPage('home')); }
  draw(state?.key);
  setTimeout(() => document.body.classList.remove('boot'), 900);
  if (error) notice(error);
}
document.fonts?.ready.then(start, start);
