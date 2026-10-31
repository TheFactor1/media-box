"""Stream badges, release matching and preset ranking. Pure functions over Stremio stream and meta dicts."""
import re
import unicodedata

# Numbers first, so a release named 1080p wins over a vaguer label. AIOStreams labels: 4K UHD, QHD, FHD, HD, SD.
RES = [('2160p', r'2160p|\b4k\b|\buhd\b'), ('1440p', r'1440p'), ('1080p', r'1080[pi]'), ('720p', r'720p'), ('576p', r'576[pi]'),
       ('480p', r'480[pi]'), ('360p', r'360p'), ('1440p', r'\bqhd\b|\b2k\b'), ('1080p', r'\bfhd\b|full.?hd'), ('720p', r'\bhd\b'), ('480p', r'\bsd\b|\bdvd(rip)?\b')]
RES_ORDER = {'360p': 360, '480p': 480, '576p': 576, '720p': 720, '1080p': 1080, '1440p': 1440, '2160p': 2160}
HDR = [('DV', r'\bdv\b|dolby.?vision|\bdovi\b'), ('HDR10+', r'hdr10\+|hdr10plus'),
       ('HDR10', r'hdr10(?!\+|plus)|\bhdr\b'), ('HLG', r'\bhlg\b')]
AUDIO = [('TrueHD', r'truehd'), ('Atmos', r'atmos'), ('DTS-HD', r'dts-?hd'), ('DTS:X', r'dts[:. -]?x\b'),
         ('DD+', r'dd\+|\bddp|e-?ac-?3'), ('DTS', r'\bdts\b(?!-?hd)'), ('DD', r'\bdd\b(?!\+)|\bac-?3\b|dolby.?digital'), ('AAC', r'\baac\b'), ('FLAC', r'\bflac\b')]
SOURCE = [('remux', r'remux'), ('web-dl', r'web-?dl|web-?rip|\bweb\b'), ('bluray', r'blu-?ray|bdrip|brrip'), ('hdrip', r'hd-?rip'),
          ('hdtv', r'hdtv'), ('dvd', r'dvd-?rip|\bdvd\b'), ('cam', r'\bcam(rip)?\b|\bhd-?ts\b|\btelesync\b|\bhd-?cam\b')]
CODEC = [('AV1', r'\bav1\b'), ('HEVC', r'hevc|[hx]\.?265'), ('AVC', r'\bavc\b|[hx]\.?264'), ('XviD', r'xvid|divx')]
# First match is the cut; every match goes into `editions`.
CUTS = [('directors', r"director'?s?[ ._-]*cut"), ('extended', r'\bextended\b'), ('unrated', r'\bunrated\b|\buncut\b'),
        ('final', r'\bfinal[ ._-]*cut\b'), ('imax', r'\bimax\b'), ('criterion', r'\bcriterion\b'),
        ('remastered', r'\bremaster'), ('theatrical', r'\btheatrical\b')]
CUT_NAMES = {c for c, _ in CUTS}

PRESET_KEYS = {'prefer', 'min_res', 'max_res', 'max_mbps', 'max_gb', 'require_cached', 'cut'}
DEFAULT_PRESETS = {
    'slow': {'max_res': '1080p', 'max_mbps': 8, 'prefer': 'highest_bitrate', 'cut': 'theatrical'},
    'standard': {'max_mbps': 20, 'prefer': 'highest_bitrate', 'cut': 'theatrical'},
    'fast': {'max_mbps': 45, 'prefer': 'highest_bitrate', 'cut': 'theatrical'},
    'max': {'prefer': 'highest_bitrate', 'require_cached': True, 'cut': 'theatrical'},
}
ALIASES = {'best': 'max', 'balanced': 'standard', 'saver': 'slow'}  # shortcut: old names kept for one release, drop after it
RELAX = ['require_cached', 'min_res', 'max_mbps']  # order constraints are given up when nothing is left
SERIES_RUNTIME = 45

YEAR = re.compile(r'(19|20)\d\d')
ARTICLES = {'the', 'a', 'an'}
COUNTRIES = {'us', 'uk', 'au', 'ca', 'nz'}


def first(text, table):
    return next((label for label, rx in table if re.search(rx, text)), None)


def every(text, table):
    return [label for label, rx in table if re.search(rx, text)]


def minutes(value):
    """'148 min', '2h 28min', '148' -> minutes, else None."""
    t = str(value or '').casefold()
    h, m = re.search(r'(\d+)\s*h', t), re.search(r'(\d+)\s*m', t)
    if h or m:
        return int(h[1] if h else 0) * 60 + int(m[1] if m else 0)
    n = re.search(r'\d+', t)
    return int(n[0]) if n else None


def preset_table(cfg_presets):
    """Defaults merged with hub.toml [presets.*] tables (a table overrides only the keys it sets)."""
    out = {k: dict(v) for k, v in DEFAULT_PRESETS.items()}
    for name, table in (cfg_presets or {}).items():
        out.setdefault(ALIASES.get(name, name), {}).update(check_preset(name, table))
    return out


def check_preset(name, p):
    if not isinstance(p, dict) or set(p) - PRESET_KEYS:
        raise ValueError(f'presets.{name}: keys must be among {", ".join(sorted(PRESET_KEYS))}')
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
    bad = [k for k, ok in [('prefer', p.get('prefer', 'addon_order') in ('highest_bitrate', 'addon_order')),
                           ('min_res', p.get('min_res', '480p') in RES_ORDER), ('max_res', p.get('max_res', '480p') in RES_ORDER),
                           ('max_mbps', 'max_mbps' not in p or num(p['max_mbps'])), ('max_gb', 'max_gb' not in p or num(p['max_gb'])),
                           ('require_cached', isinstance(p.get('require_cached', False), bool)),
                           ('cut', p.get('cut', 'theatrical') in CUT_NAMES)] if not ok]
    if bad:
        raise ValueError(f'presets.{name}: bad value for {", ".join(bad)}')
    return p


# ---- release matching ----

def tokens(text):
    """Lowercase, no diacritics/punctuation/articles: "The Dark Knight: Rises" -> ['dark', 'knight', 'rises']."""
    t = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode().casefold()
    t = re.sub(r"['`]", '', t.replace('&', ' and '))  # "Schindler's" and "Schindlers" agree
    return [w for w in re.split(r'[^a-z0-9]+', t) if w and w not in ARTICLES]


def strip_prefix(line):
    """Drop leading release-group/site tags: "[TGx] www.site.org - Title..." -> "Title..."."""
    return re.sub(r'^(\s*(\[[^\]]*\]|www\.\S+)\s*[-–]?)+', '', line)


def context(meta, type_, s=0, e=0):
    """What the release names are checked against. meta None (meta addon down, title unknown) -> no matching."""
    if not isinstance(meta, dict) or not meta.get('name'):
        return {'type': type_, 'names': None, 'year': None, 'runtime': SERIES_RUNTIME if type_ == 'series' else None, 's': s, 'e': e}
    names = [meta['name']] + [a for k in ('aka', 'alternativeTitles') if isinstance(meta.get(k), list)
                              for a in meta[k] if isinstance(a, str)]
    year = YEAR.search(str(meta.get('year') or meta.get('releaseInfo') or ''))
    runtime = minutes(meta.get('runtime'))
    if type_ == 'series':
        video = next((v for v in meta.get('videos') or [] if isinstance(v, dict) and v.get('season') == s
                      and v.get('episode', v.get('number')) == e), {})
        runtime = minutes(video.get('runtime')) or runtime or SERIES_RUNTIME
    return {'type': type_, 'names': {tuple(tokens(n)) for n in names} - {()}, 'year': int(year[0]) if year else None,
            'runtime': runtime, 's': s, 'e': e}


def parse_episode(toks):
    """Index of the first episode/season token and what it says: (i, season_from, season_to, ep_from, ep_to); eps None = pack."""
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ''
        if m := re.fullmatch(r's(\d{1,2})e(\d{1,3})(?:e(\d{1,3}))?', t) or re.fullmatch(r'(\d{1,2})x(\d{1,3})()', t):
            e2 = m[3] or (re.fullmatch(r'e(\d{1,3})', nxt) or [None, None])[1]  # S01E01E02, S01E01-E02
            return i, int(m[1]), int(m[1]), int(m[2]), int(e2 or m[2])
        if m := re.fullmatch(r's(\d{1,2})', t):
            if em := re.fullmatch(r'e(\d{1,3})', nxt):  # "S01 E02"
                return i, int(m[1]), int(m[1]), int(em[1]), int(em[1])
            to = re.fullmatch(r's(\d{1,2})', nxt)  # S01-S03
            return i, int(m[1]), int(to[1] if to else m[1]), None, None
        if t == 'season' and nxt.isdigit():
            return i, int(nxt), int(nxt), None, None
    return None


def match_line(line, ctx, stream):
    """True/False when the line names a release (title + year, or title + SxxEyy), None when it names none."""
    toks = tokens(strip_prefix(line))
    if ctx['type'] == 'movie':
        splits = [(tuple(toks[:i]), int(t)) for i, t in enumerate(toks) if i and YEAR.fullmatch(t)]
        if not splits:
            return None
        # Every year token is a candidate split, so "Blade Runner 2049 2017" and "2001 A Space Odyssey 1968" work.
        return any(title in ctx['names'] and (ctx['year'] is None or abs(y - ctx['year']) <= 1) for title, y in splits)
    ep = parse_episode(toks)
    if not ep or not ep[0]:
        return None
    i, s1, s2, e1, e2 = ep
    title = toks[:i]
    while len(title) > 1 and (YEAR.fullmatch(title[-1]) or title[-1] in COUNTRIES):  # "Show (2008) S01E02", "The.Office.US.S01E01"
        title.pop()
    if tuple(title) not in ctx['names'] and tuple(toks[:i]) not in ctx['names'] or not s1 <= ctx['s'] <= s2:
        return False
    if e1 is None:  # season pack: playable only when the addon points at one file in it
        hints = stream.get('behaviorHints') if isinstance(stream.get('behaviorHints'), dict) else {}
        return isinstance(stream.get('fileIdx'), int) or bool(hints.get('filename'))
    return e1 <= ctx['e'] <= e2


def verdict(stream, ctx):
    """'ok', 'unverified' (no line names a release) or None (drop: every named release is a different one)."""
    if ctx['names'] is None:
        return 'unverified'
    hints = stream.get('behaviorHints') if isinstance(stream.get('behaviorHints'), dict) else {}
    lines = [str(hints.get('filename') or '')] + [l for k in ('title', 'description', 'name') for l in str(stream.get(k) or '').splitlines()]
    results = [match_line(l, ctx, stream) for l in lines if l.strip()]
    return 'ok' if True in results else None if False in results else 'unverified'


# ---- badges and ranking ----

def badges(stream, ctx):
    hints = stream.get('behaviorHints') if isinstance(stream.get('behaviorHints'), dict) else {}
    text = ' '.join(str(stream.get(k) or '') for k in ('name', 'title', 'description')) + ' ' + str(hints.get('filename') or '')
    text = text.casefold()
    size = re.search(r'(\d+(?:[.,]\d+)?)\s*(gb|gib|mb|mib)\b', text)
    gb = round(float(size[1].replace(',', '.')) / (1 if size[2].startswith('g') else 1024), 2) if size else None
    if not gb and isinstance(hints.get('videoSize'), int):
        gb = round(hints['videoSize'] / 2**30, 2)
    # Cached markers: AIOStreams uses ⚡/⏳ or "⚡Ready"/"❌ Not Ready", Torrentio-style addons use [RD+] / [RD download].
    cached = False if re.search(r'uncached|not ready|❌|⏳|download\]', text) else True if re.search(r'⚡|\+\]|\bcached\b|\binstant\b', text) else None
    editions = every(text, CUTS)
    return {'res': first(text, RES), 'hdr': every(text, HDR), 'audio': every(text, AUDIO), 'source': first(text, SOURCE), 'codec': first(text, CODEC),
            'sizeGb': gb, 'mbps': round(gb * 8000 / (ctx['runtime'] * 60), 1) if gb and ctx['runtime'] else None,
            'cached': cached, 'cut': editions[0] if editions else 'theatrical', 'editions': editions}


def allowed(b, p, relaxed):
    res = RES_ORDER.get(b['res'])
    # Unknown values pass caps (can't tell) but fail floors (can't promise).
    checks = {'require_cached': not p.get('require_cached') or b['cached'] is True,
              'min_res': 'min_res' not in p or (res or 0) >= RES_ORDER[p['min_res']],
              'max_res': 'max_res' not in p or res is None or res <= RES_ORDER[p['max_res']],
              'max_mbps': 'max_mbps' not in p or b['mbps'] is None or b['mbps'] <= p['max_mbps'],
              'max_gb': 'max_gb' not in p or b['sizeGb'] is None or b['sizeGb'] <= p['max_gb']}
    return all(ok for k, ok in checks.items() if k not in relaxed)


def select(raw, ctx, p):
    """raw: [(addon host, stream)] in addon order -> (ranked streams within preset p, constraints relaxed to get any).

    Order: has a url (no torrents) > states a resolution > release verified > cached > preset cut, then theatrical > preference > addon order
    (Python's sort is stable with reverse=True, so ties keep the addon's order)."""
    out = []
    for addon, s in raw:
        match = verdict(s, ctx)
        if not match:
            continue
        hints = s.get('behaviorHints') if isinstance(s.get('behaviorHints'), dict) else {}
        proxy = hints.get('proxyHeaders') if isinstance(hints.get('proxyHeaders'), dict) else {}
        b = badges(s, ctx) | {'match': match}
        url = s.get('url') if isinstance(s.get('url'), str) else ''
        # http(s), printable ASCII only: the URL goes into a 302 Location, so CR/LF would split the response.
        out.append({'url': url if re.fullmatch(r'https?://[!-~]+', url, re.I) else None,
                    'headers': proxy.get('request') if isinstance(proxy.get('request'), dict) else {},
                    'badges': b, 'source': {'addon': addon, 'name': s.get('name')}, 'cut': b['cut'],
                    'description': s.get('description') or s.get('title')})
    cached, want = {True: 1, None: 0, False: -1}, p.get('cut')
    key = lambda x: (x['url'] is not None, x['badges']['res'] is not None, x['badges']['match'] == 'ok', cached[x['badges']['cached']],
                     *((x['cut'] == want, x['cut'] == 'theatrical') if want else ()),
                     # Within what the preset allows, more pixels beat more bitrate: 1080p ranks above a fatter 720p.
                     RES_ORDER.get(x['badges']['res'], 0) if p.get('prefer') == 'highest_bitrate' else 0,
                     (x['badges']['mbps'] or -1) if p.get('prefer') == 'highest_bitrate' else 0)
    if p.get('ready_only'):  # never start a copy the debrid service would have to fetch first; unmarked addons can't tell, so they stay
        out = [x for x in out if x['badges']['cached'] is not False]
    ranked = sorted(out, key=key, reverse=True)
    relaxed = []

    def finish(kept):  # choosing by hand: copies over the speed setting stay on the list, after the ones within it
        if not p.get('show_all'):
            return kept
        within = {id(x) for x in kept}
        rest = [x for x in ranked if id(x) not in within]
        for x in rest:
            x['badges']['overLimit'] = True
        return kept + rest

    for step in [None] + [k for k in RELAX if p.get(k)]:
        relaxed += [step] if step else []
        kept = [x for x in ranked if allowed(x['badges'], p, relaxed)]
        if any(x['url'] for x in kept):
            return finish(kept), relaxed
    return finish([]), relaxed
