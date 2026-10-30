// Generated once from the arranged prototype (docs/broadsheet-prototype.html) and its saved layouts. Positions are px on a 1920x1080 stage.
const PAGE_NAMES = { home: 'Home', continue: 'Continue', next: 'Next up', new: 'New', movies: 'Movies', shows: 'Shows', browse: 'Browse', search: 'Search', settings: 'Settings', lists: 'Lists', film: 'Film page', show: 'Show page', versions: 'Other versions', setup: 'First run', masthead: 'Masthead' };
const caps = { spacing: '.1em', transform: 'uppercase' };
const T = (id, label, x, y, w, h, size, text, o = {}) => ({ id, label, kind: 'text', x, y, w, h, size, text, font: 'body', weight: 400, color: '#1a1714', ...o });
const KICK = { font: 'body', weight: 600, color: '#a3271c', ...caps };
const CRED = { font: 'body', weight: 600, color: '#4f4840', ...caps };
const P = (id, label, x, y, w, h, color, o = {}) => ({ id, label, kind: 'pic', x, y, w, h, color, ...o });
const B = (id, label, x, y, w, h, text, to, main, o = {}) => ({ id, label, kind: 'btn', x, y, w, h, text, to, size: 28, color: main ? '#f4efe4' : '#1a1714', fill: main ? '#1a1714' : '#ddd6c7', font: 'body', weight: 600, ...o });
const R = (id, label, x, y, w, h, color) => ({ id, label, kind: 'pic', x, y, w, h, color });
const W = (id, label, widget, x, y, w, h, o = {}) => ({ id, label, kind: 'widget', widget, x, y, w, h, ...o });
const gridPage = (name, list) => [T('kick', 'Section name', 96, 170, 900, 30, 22, name, KICK), W('grid', 'Poster grid', 'grid', 96, 216, 1728, 702, { list })];

const DEFAULTS = {
  masthead: [
    T('name', 'Masthead name', 96, 54, 490, 60, 48, 'The Media Box Times', { font: 'display', weight: 900, nowrap: true }),
    ...Object.entries({ home: [650, 90], continue: [760, 150], new: [930, 60], movies: [1010, 105], shows: [1135, 90], browse: [1245, 112], lists: [1370, 80], search: [596, 40], settings: [1470, 135] }).map(([p, [x, w]]) => ({ id: 'tab-' + p, label: PAGE_NAMES[p] + ' tab', kind: 'tab', x, y: 58, w, h: 52, size: 22, to: p, text: PAGE_NAMES[p], font: 'body', weight: 600, color: '#6b645a' })),
    T('clock', 'Clock', 1660, 72, 164, 30, 22, '8:42 PM', { font: 'body', weight: 600, color: '#4f4840', align: 'right' }),
    R('rule1', 'Masthead rule (top)', 96, 118, 1728, 2, '#1a1714'), R('rule2', 'Masthead rule (bottom)', 96, 124, 1728, 2, '#1a1714'),
  ],
  home: [
    T('kicker', 'Kicker', 96, 170, 900, 30, 22, '', { ...KICK, bind: 'kicker' }),
    T('headline', 'Headline', 96, 205, 960, 128, 104, '', { font: 'display', weight: 900, bind: 'title', nowrap: true }),
    T('credits', 'Credits line', 96, 340, 960, 30, 22, '', { ...CRED, bind: 'credits' }),
    T('summary', 'Summary', 96, 385, 940, 90, 34, '', { color: '#3a342d', italic: true, bind: 'over' }),
    B('resume', 'Resume button', 96, 500, 170, 74, 'Resume', 'play', true, { bindText: 'playLabel' }),
    B('details', 'Details button', 286, 500, 190, 74, 'Details', 'title', false),
    P('lead', 'Lead picture', 1090, 170, 734, 410, '#2b3a4a', { bind: 'color' }),
    R('rowrule', 'Row rule', 96, 624, 1728, 2, '#1a1714'),
    W('rowtabs', 'Row picker', 'rowtabs', 96, 632, 1728, 48),
    W('row', 'Poster row', 'row', 96, 686, 1728, 380, { list: 'current' }),
  ],
  continue: gridPage('Continue watching', 'prog'), next: gridPage('Next up', 'shows'), new: gridPage('New this week', 'fresh'), movies: gridPage('Movies', 'films'), shows: gridPage('Shows', 'shows'),
  browse: [T('kick', 'Section name', 96, 170, 900, 30, 22, 'Popular movies', KICK), W('r1', 'First row', 'row', 96, 210, 1728, 345, { list: 'films' }), T('kick2', 'Second section name', 96, 600, 900, 30, 22, 'Popular shows', KICK), W('r2', 'Second row', 'row', 96, 640, 1728, 345, { list: 'shows' })],
  search: [T('label', 'Search label', 96, 170, 700, 30, 22, 'Title, person or genre', CRED), T('query', 'What you typed', 96, 206, 700, 96, 72, '', { font: 'display', weight: 900, bind: 'query', nowrap: true }), R('qrule', 'Rule under search', 96, 306, 700, 4, '#1a1714'), W('keys', 'Keyboard', 'keys', 96, 336, 700, 460), W('results', 'Results', 'results', 880, 170, 944, 836)],
  lists: [T('title', 'Page title', 96, 170, 1000, 90, 72, 'Lists', { font: 'display', weight: 900 }), W('manager', 'Home rows', 'rowsmanager', 96, 280, 860, 760), W('newlist', 'New list', 'newlist', 1020, 280, 804, 760)],
  source: [T('title', 'Page title', 800, 300, 960, 100, 72, 'Add your AIOStreams', { font: 'display', weight: 900 }), T('body', 'Instructions', 800, 420, 960, 180, 34, 'Scan the code with your phone, then paste your AIOStreams link. It ends in manifest.json. Nothing is typed on the TV.', { color: '#3a342d' }), T('now', 'Current source', 800, 620, 960, 40, 26, '', { color: '#4f4840', bind: 'sourceNow', nowrap: true }), B('backbtn', 'Back button', 800, 700, 200, 74, 'Back', 'back', true), W('qr', 'QR code', 'qr', 160, 240, 560, 560)],
  settings: [T('title', 'Page title', 96, 170, 1000, 90, 72, 'Settings', { font: 'display', weight: 900 }), W('settings', 'Settings list', 'settings', 96, 280, 1100, 760)],
  film: [
    T('kicker', 'Kicker', 96, 170, 1000, 30, 22, '', { ...KICK, bind: 'filmkick' }),
    T('headline', 'Headline', 96, 206, 1000, 190, 150, '', { font: 'display', weight: 900, bind: 'title', nowrap: true }),
    T('summary', 'Summary', 96, 402, 1000, 100, 40, '', { color: '#3a342d', italic: true, bind: 'over' }),
    R('rule', 'Rule', 96, 520, 1000, 2, '#b8ae9c'),
    T('col1', 'Write-up, column 1', 96, 545, 480, 220, 26, '', { bind: 'facts' }),
    T('col2', 'Write-up, column 2', 616, 545, 480, 220, 26, '', { bind: 'cast' }),
    B('play', 'Play button', 96, 800, 170, 78, 'Play', 'play', true, { size: 30, bindText: 'playLabel' }),
    B('versions', 'Other versions button', 286, 800, 300, 78, 'Other versions', 'versions', false, { size: 30 }),
    B('keep', 'Keep button', 606, 800, 190, 78, 'Keep', 'keep', false, { size: 30, bindText: 'keepLabel' }),
    B('addlist', 'Add to list button', 816, 800, 260, 78, 'Add to list', 'addlist', false, { size: 30 }),
    P('lead', 'Picture', 1180, 170, 644, 560, '#2e5e5a', { bind: 'color' }),
    R('copybox', "Tonight's copy box", 1180, 760, 644, 180, '#fbf8f1'),
    T('copy1', "Tonight's copy label", 1205, 778, 600, 28, 20, "Tonight's copy", KICK),
    T('copy2', 'Copy details', 1205, 812, 600, 44, 32, '', { font: 'display', weight: 700, bind: 'copy', nowrap: true }),
    T('copy3', 'Copy note', 1205, 866, 600, 60, 24, '', { color: '#3a342d', bind: 'copyNote' }),
  ],
  show: [
    T('kicker', 'Kicker', 96, 170, 1000, 30, 22, '', { ...KICK, bind: 'filmkick' }),
    T('headline', 'Headline', 96, 203, 1100, 130, 104, '', { font: 'display', weight: 900, bind: 'title', nowrap: true }),
    T('summary', 'Summary', 96, 338, 1040, 40, 30, '', { color: '#3a342d', italic: true, bind: 'over' }),
    W('seasons', 'Seasons', 'seasons', 96, 396, 1040, 60),
    W('episodes', 'Episode list', 'episodes', 96, 476, 1040, 532),
    P('lead', 'Picture', 1220, 170, 604, 480, '#2e4f6b', { bind: 'color' }),
    B('play', 'Play next button', 1220, 680, 300, 78, 'Play next', 'play', true, { size: 30, bindText: 'playNextLabel' }), B('versions', 'Other versions button', 1540, 680, 284, 78, 'Other versions', 'versions', false, { size: 28 }),
    B('addlist', 'Add to list button', 1220, 776, 300, 78, 'Add to list', 'addlist', false, { size: 30 }),
  ],
  versions: [T('kicker', 'Kicker', 96, 170, 1000, 30, 22, '', { ...KICK, bind: 'marketKick' }), T('headline', 'Headline', 96, 205, 1500, 90, 64, '', { font: 'display', weight: 900, bind: 'versionsTitle', nowrap: true }), W('board', 'Ranked copies', 'board', 96, 318, 1728, 688)],
  setup: [T('kick', 'Kicker', 800, 330, 900, 30, 22, 'First run', KICK), T('title', 'Headline', 800, 370, 1000, 120, 96, 'Set up this box', { font: 'display', weight: 900 }), T('body', 'Instructions', 800, 510, 960, 140, 36, 'Scan the code with your phone. You need your debrid key. Nothing is typed on the TV.', { color: '#3a342d' }), T('link', 'Link', 800, 670, 960, 40, 26, '', { font: 'body', color: '#4f4840', bind: 'setupLink', nowrap: true }), W('qr', 'QR code', 'qr', 160, 260, 560, 560)],
};
