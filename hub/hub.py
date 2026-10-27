"""Media box hub API: kept library, resume points, browse and best-stream picking over Stremio addons.

Contract: DATAFLOW.md, "Hub API". Python 3.12 standard library only.
Run: HUB_CONFIG=/var/lib/hub/hub.toml HUB_DATA=/var/lib/hub python -m hub.hub
"""
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import tomllib
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import ranking, security

CINEMETA = 'https://v3-cinemeta.strem.io/manifest.json'
STREAM_TTL = 600         # contract: stream cache per title 10 min
MANIFEST_TTL = 3600
META_TTL = 86400
PAIR_WINDOW = 600     # pairing stays open 10 min
MAX_BODY = 64 * 1024
DONE = 0.92              # contract: "continue" = resume point under 92 %
IMDB = re.compile(r'tt\d+')


class AddonError(Exception):
    pass


class HttpError(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


def now_ms():
    return int(time.time() * 1000)


def host(url):
    return urllib.parse.urlsplit(url).hostname or '?'


def lst(x):
    return x if isinstance(x, list) else []


def fetch(url, timeout=10):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'mediabox-hub/1', 'Accept': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except (OSError, ValueError) as e:  # URLError/HTTPError/timeouts are OSError, bad JSON is ValueError
        # Only the host and error type: addon URLs can embed config tokens.
        raise AddonError(f'{host(url)}: {type(e).__name__} {getattr(e, "code", "")}'.strip()) from None
    if not isinstance(data, dict):
        raise AddonError(f'{host(url)}: response is not a JSON object')
    return data


class Addon:
    def __init__(self, url):
        self.url, self.host = url, host(url)
        self.base = url.removesuffix('/manifest.json')
        self._manifest, self._at = None, 0

    def manifest(self):
        if self._manifest is None or time.time() - self._at > MANIFEST_TTL:
            self._manifest, self._at = fetch(self.url), time.time()
        return self._manifest

    def supports(self, resource, type_, id_):
        m = self.manifest()
        for r in lst(m.get('resources')):
            if isinstance(r, str):
                name, types, prefixes = r, m.get('types'), m.get('idPrefixes')
            elif isinstance(r, dict):
                name, types, prefixes = r.get('name'), r.get('types', m.get('types')), r.get('idPrefixes', m.get('idPrefixes'))
            else:
                continue
            if name == resource and (not types or type_ in types) and (not prefixes or id_.startswith(tuple(prefixes))):
                return True
        return False

    def catalogs(self):
        """[(catalog, extra names, required extra names)]; handles both `extra` and the legacy extraSupported/extraRequired."""
        out = []
        for c in lst(self.manifest().get('catalogs')):
            if not isinstance(c, dict):
                continue
            extra = [e for e in lst(c.get('extra')) if isinstance(e, dict)]
            names = {e.get('name') for e in extra} | set(lst(c.get('extraSupported')))
            required = {e.get('name') for e in extra if e.get('isRequired')} | set(lst(c.get('extraRequired')))
            out.append((c, names, required))
        return out

    def get(self, resource, type_, id_, extra=None):
        q = lambda s: urllib.parse.quote(str(s), safe=':')
        url = f'{self.base}/{resource}/{q(type_)}/{q(id_)}'
        if extra:  # protocol: /{resource}/{type}/{id}/{k=v&k2=v2}.json, values percent-encoded
            url += '/' + urllib.parse.urlencode(extra, quote_via=urllib.parse.quote)
        return fetch(url + '.json')


# ---- metadata ----

def to_item(meta):
    if not isinstance(meta, dict):
        raise AddonError('meta response has no meta object')
    year = re.search(r'\d{4}', str(meta.get('year') or meta.get('releaseInfo') or ''))
    return {'id': meta.get('id'), 'type': meta.get('type'), 'title': meta.get('name'),
            'year': int(year[0]) if year else None, 'runtimeMin': ranking.minutes(meta.get('runtime')),
            'genres': lst(meta.get('genres') or meta.get('genre')), 'poster': meta.get('poster'),
            'backdrop': meta.get('background'), 'logo': meta.get('logo'), 'overview': meta.get('description'),
            'badges': None, 'inLibrary': False, 'keptAt': None, 'resume': None, 'nextEpisode': None}


def episodes(meta):
    """[[s, e, title]] in order, specials (season 0) left out."""
    eps = []
    for v in lst(meta.get('videos')):
        if isinstance(v, dict) and isinstance(v.get('season'), int) and v['season'] > 0:
            e = v.get('episode', v.get('number'))
            if isinstance(e, int):
                eps.append([v['season'], e, v.get('name') or v.get('title')])
    return sorted(eps, key=lambda x: (x[0], x[1]))


def done(r):
    return r['durationMs'] > 0 and r['positionMs'] / r['durationMs'] >= DONE


class Hub:
    def __init__(self, config_path, data_dir):
        cfg = {}
        if os.path.exists(config_path):
            with open(config_path, 'rb') as f:
                cfg = tomllib.load(f)
        self.port, self.bind = int(cfg.get('port', 8097)), cfg.get('bind', '0.0.0.0')
        public = security.public_addresses(self.bind)
        if public and cfg.get('allow_public') is not True:
            sys.exit(f'refusing to listen on public address {", ".join(public)}; set allow_public = true in hub.toml if you mean it')
        os.makedirs(data_dir, exist_ok=True)
        self.db = os.path.join(data_dir, 'hub.sqlite')
        self.sql('CREATE TABLE IF NOT EXISTS library(id TEXT PRIMARY KEY, type TEXT, item TEXT, videos TEXT, kept_at INTEGER)')
        self.sql('CREATE TABLE IF NOT EXISTS resume(id TEXT, s INTEGER, e INTEGER, position_ms INTEGER,'
                 ' duration_ms INTEGER, updated_at INTEGER, PRIMARY KEY(id, s, e))')
        self.sql('CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY, value BLOB)')
        self.key = security.secret_file(os.path.join(data_dir, 'key'))
        self.token_path = os.path.join(data_dir, 'token')
        self.token = security.b64(security.secret_file(self.token_path))
        try:
            self.presets = ranking.preset_table(cfg.get('presets'))
        except ValueError as e:
            sys.exit(f'hub.toml: {e}')
        self.preset = ranking.ALIASES.get(cfg.get('preset'), cfg.get('preset', 'standard'))
        if self.preset not in self.presets:
            sys.exit(f'hub.toml: preset must be one of {", ".join(self.presets)}')
        self.meta_addons = [Addon(u) for u in cfg.get('meta_addons', [CINEMETA])]
        if cfg.get('stream_addons'):
            if self.setting('stream_addons') is None:
                self.set_setting('stream_addons', cfg['stream_addons'])
                print(f'imported {len(cfg["stream_addons"])} stream addon(s) from hub.toml into the encrypted store', flush=True)
            print('hub.toml still lists stream_addons (they embed tokens): delete that line; the hub keeps them encrypted', flush=True)
        self.stream_addons = [Addon(u) for u in self.setting('stream_addons') or []]
        known = {a.url: a for a in self.meta_addons + self.stream_addons}
        new = cfg.get('new_catalog')
        new = [new] if isinstance(new, dict) else new
        # Default: Cinemeta's all-time "top" lists, so only the year filter makes them "new".
        self.new_recent_only = new is None
        self.new = [(known.setdefault(c['addon'], Addon(c['addon'])), c['type'], c['id'])
                    for c in new or [{'addon': CINEMETA, 'type': t, 'id': 'top'} for t in ('movie', 'series')]]
        self.meta_cache = {}
        self.cache = {}  # shortcut: in memory, lost on restart and never pruned; fine for one household, move to sqlite if it grows
        self.limits = {'play': security.Bucket(10, 10), 'setup': security.Bucket(10, 10), 'pair': security.Bucket(5 / 60, 5)}
        self.pair_words, self.pair_until = None, 0
        if not self.setting('paired'):
            self.open_pairing()

    # -- settings, token, pairing --

    def setting(self, name):
        row = self.sql('SELECT value FROM settings WHERE name = ?', (name,))
        return json.loads(security.decrypt(self.key, row[0][0])) if row else None

    def set_setting(self, name, value):
        self.sql('INSERT OR REPLACE INTO settings VALUES (?, ?)', (name, security.encrypt(self.key, json.dumps(value).encode())))

    def authorized(self, header):
        return hmac.compare_digest(str(header or '').encode(), f'Bearer {self.token}'.encode())

    def open_pairing(self):
        self.pair_words, self.pair_until = security.pairing_words(), time.time() + PAIR_WINDOW
        print('pairing open for 10 minutes: read the words from GET /pair on the box itself', flush=True)

    def pairing_open(self):
        return self.pair_words is not None and time.time() < self.pair_until

    def pair(self, body):
        words = body.get('words') if isinstance(body, dict) else None
        words = words.split() if isinstance(words, str) else words
        if not self.pairing_open():
            raise HttpError(403, 'pairing is closed; open it from the TV')
        if not isinstance(words, list) or not hmac.compare_digest(' '.join(map(str, words)).casefold().encode(),
                                                                   ' '.join(self.pair_words).encode()):
            raise HttpError(403, 'those words do not match')
        self.pair_words = None  # one use: the next box needs a fresh window
        self.set_setting('paired', True)
        return {'token': self.token}

    def rotate(self):
        self.token = security.b64(security.write_secret(self.token_path, secrets.token_bytes(32)))
        return {'token': self.token}

    def sql(self, query, args=()):
        con = sqlite3.connect(self.db, timeout=10)
        try:
            with con:
                return con.execute(query, args).fetchall()
        finally:
            con.close()

    # -- addons --

    def meta(self, type_, id_):
        hit = self.meta_cache.get((type_, id_))
        if hit and time.time() - hit[0] < META_TTL:
            return hit[1]
        errors = []
        for a in self.meta_addons:
            try:
                if a.supports('meta', type_, id_):
                    meta = a.get('meta', type_, id_).get('meta')
                    if isinstance(meta, dict):
                        self.meta_cache[(type_, id_)] = (time.time(), meta)
                    return meta
            except AddonError as e:
                errors.append(str(e))
        raise AddonError('; '.join(errors) or f'no meta addon serves {type_} {id_}')

    def streams(self, type_, vid):
        hit = self.cache.get((type_, vid))
        if hit and time.time() - hit[0] < STREAM_TTL:  # also the fallback when the addon is down
            return hit[1]
        if not self.stream_addons:
            raise AddonError('no stream addons configured')
        raw, errors = [], []
        for a in self.stream_addons:
            try:
                if a.supports('stream', type_, vid):
                    raw += [(a.host, s) for s in lst(a.get('stream', type_, vid).get('streams')) if isinstance(s, dict)]
            except AddonError as e:
                errors.append(str(e))
        if len(errors) == len(self.stream_addons):
            raise AddonError('; '.join(errors))
        if raw:
            self.cache[(type_, vid)] = (time.time(), raw)
        return raw

    def preset_for(self, qs):
        """The named preset (default from hub.toml) with the request's ?max_mbps= / ?max_res= / ?cut= on top."""
        name = qs.get('preset', self.preset)
        name = ranking.ALIASES.get(name, name)
        if name not in self.presets:
            raise HttpError(400, f'preset must be one of {", ".join(self.presets)}')
        p = dict(self.presets[name])
        if 'max_mbps' in qs:
            try:
                p['max_mbps'] = float(qs['max_mbps'])
            except ValueError:
                p['max_mbps'] = 0
            if not p['max_mbps'] > 0:
                raise HttpError(400, 'max_mbps must be a positive number')
        if 'max_res' in qs:
            if qs['max_res'] not in ranking.RES_ORDER:
                raise HttpError(400, f'max_res must be one of {", ".join(ranking.RES_ORDER)}')
            p['max_res'] = qs['max_res']
        if 'cut' in qs:
            if qs['cut'] not in ranking.CUT_NAMES:
                raise HttpError(400, f'cut must be one of {", ".join(sorted(ranking.CUT_NAMES))}')
            p['cut'] = qs['cut']
        return p

    def ranked(self, type_, id_, s, e, p, meta=None):
        if meta is None:
            try:
                meta = self.meta(type_, id_)
            except AddonError:  # match against the kept copy rather than refuse to play
                row = self.sql('SELECT item FROM library WHERE id = ?', (id_,))
                item = json.loads(row[0][0]) if row else {}
                meta = {'name': item.get('title'), 'year': item.get('year'), 'runtime': item.get('runtimeMin')}
        vid = f'{id_}:{s}:{e}' if (s, e) != (0, 0) else id_
        return ranking.select(self.streams(type_, vid), ranking.context(meta, type_, s, e), p)

    def pick(self, type_, id_, s, e, p, meta=None):
        streams, relaxed = self.ranked(type_, id_, s, e, p, meta)
        best = next((x for x in streams if x['url']), None)
        if not best:
            raise HttpError(404, 'no playable stream')
        best['source']['relaxed'] = relaxed
        return best

    def health(self):
        out = []
        for kind, addons in (('meta', self.meta_addons), ('stream', self.stream_addons)):
            for a in addons:
                try:
                    fetch(a.url, timeout=3)
                    out.append({'host': a.host, 'kind': kind, 'reachable': True})
                except AddonError:
                    out.append({'host': a.host, 'kind': kind, 'reachable': False})
        return {'ok': True, 'addons': out}

    # -- library and resume --

    def annotate(self, items, videos=None):
        """Fill inLibrary/keptAt/badges/resume/nextEpisode. `videos` maps id -> episodes for titles not in the library."""
        # shortcut: reads the whole library and resume table per request; fine to a few thousand titles, query per id beyond that
        lib = {r[0]: r for r in self.sql('SELECT id, item, videos, kept_at FROM library')}
        res = {}
        for id_, s, e, p, d, u in self.sql('SELECT * FROM resume ORDER BY updated_at DESC'):
            res.setdefault(id_, []).append({'s': s, 'e': e, 'positionMs': p, 'durationMs': d, 'updatedAt': u})
        for it in items:
            row = lib.get(it['id'])
            if row:
                it.update(inLibrary=True, keptAt=row[3], badges=json.loads(row[1])['badges'])
            rows = res.get(it['id'], [])
            last = rows[0] if rows else None
            it['resume'] = {k: last[k] for k in ('positionMs', 'durationMs', 'updatedAt')} if last else None
            eps = json.loads(row[2]) if row else (videos or {}).get(it['id'], [])
            if it['type'] == 'series' and eps:
                # The episode to play next: the unfinished one, else the one after the last finished, else the first.
                if last is None:
                    nxt = eps[0]
                else:
                    i = next((i for i, x in enumerate(eps) if x[:2] == [last['s'], last['e']]), None)
                    nxt = eps[i] if i is not None and not done(last) else eps[i + 1] if i is not None and i + 1 < len(eps) else None
                it['nextEpisode'] = {'s': nxt[0], 'e': nxt[1], 'title': nxt[2]} if nxt else None
        return items

    def library(self, collection):
        items = self.annotate([json.loads(r[0]) for r in self.sql('SELECT item FROM library ORDER BY kept_at DESC, rowid DESC')])
        resuming = lambda i: i['resume'] and i['resume']['positionMs'] > 0 and not done(i['resume'])
        match collection:
            case None:
                return items
            case 'continue':
                return sorted(filter(resuming, items), key=lambda i: i['resume']['updatedAt'], reverse=True)
            case 'next':
                nxt = [i for i in items if i['type'] == 'series' and i['nextEpisode'] and not resuming(i)]
                return sorted(nxt, key=lambda i: (i['resume'] or {}).get('updatedAt', 0), reverse=True)
            case 'movies' | 'shows':
                want = 'movie' if collection == 'movies' else 'series'
                return [i for i in items if i['type'] == want]
        raise HttpError(400, 'collection must be continue, next, movies or shows')

    def keep(self, body):
        if not isinstance(body, dict) or not IMDB.fullmatch(str(body.get('id'))) or body.get('type') not in ('movie', 'series'):
            raise HttpError(400, 'body must be {"id": "tt…", "type": "movie"|"series"}')
        id_, type_ = body['id'], body['type']
        if not self.sql('SELECT 1 FROM library WHERE id = ?', (id_,)):
            meta = self.meta(type_, id_)
            item, eps = to_item(meta), episodes(meta)
            item.update(id=id_, type=type_)
            s, e = eps[0][:2] if type_ == 'series' and eps else (0, 0)
            try:
                # Badges answer "what quality can I get", so they come from the max preset, not the play default.
                b = self.pick(type_, id_, s, e, self.presets['max'], meta)['badges']
                item['badges'] = {k: b[k] for k in ('res', 'hdr', 'audio')}
            except (AddonError, HttpError):
                pass  # keep works without stream addons; badges stay null
            self.sql('INSERT OR IGNORE INTO library VALUES (?, ?, ?, ?, ?)', (id_, type_, json.dumps(item), json.dumps(eps), now_ms()))
        return self.annotate([json.loads(self.sql('SELECT item FROM library WHERE id = ?', (id_,))[0][0])])[0]

    def put_resume(self, key, body):
        ok = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0
        if not isinstance(body, dict) or not ok(body.get('positionMs')) or not ok(body.get('durationMs')):
            raise HttpError(400, 'body must be {"positionMs": int, "durationMs": int}')
        updated = body.get('updatedAt', now_ms())  # optional so a front end that was offline can't overwrite newer progress
        if not ok(updated):
            raise HttpError(400, 'updatedAt must be epoch ms')
        self.sql('INSERT INTO resume VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id, s, e) DO UPDATE SET'
                 ' position_ms = excluded.position_ms, duration_ms = excluded.duration_ms, updated_at = excluded.updated_at'
                 ' WHERE excluded.updated_at >= resume.updated_at', (*key, body['positionMs'], body['durationMs'], updated))
        return self.get_resume(key)

    def get_resume(self, key):
        rows = self.sql('SELECT position_ms, duration_ms, updated_at FROM resume WHERE id = ? AND s = ? AND e = ?', key)
        return dict(zip(('positionMs', 'durationMs', 'updatedAt'), rows[0])) if rows else None

    # -- browse and search --

    def catalogs(self):
        out = []
        for a in self.meta_addons:
            try:
                out += [{'addon': a.host, 'type': c.get('type'), 'id': c.get('id'), 'name': c.get('name')}
                        for c, _, required in a.catalogs() if not required - {'skip'}]
            except AddonError:
                pass
        return out

    def catalog(self, type_, cid, skip):
        for a in self.meta_addons:
            try:
                if any(c.get('type') == type_ and c.get('id') == cid for c, _, _ in a.catalogs()):
                    metas = lst(a.get('catalog', type_, cid, {'skip': skip} if skip else None).get('metas'))
                    return self.annotate([to_item(m) for m in metas if isinstance(m, dict)])
            except AddonError:
                continue
        raise HttpError(404, f'no addon has catalog {type_}/{cid}')

    def new_releases(self, skip):
        items, seen, errors = [], set(), 0
        year = time.localtime().tm_year
        for addon, type_, cid in self.new:
            try:
                metas = lst(addon.get('catalog', type_, cid, {'skip': skip} if skip else None).get('metas'))
            except AddonError:
                errors += 1
                continue
            for m in metas:
                if isinstance(m, dict) and m.get('id') not in seen:
                    item = to_item(m)
                    if not self.new_recent_only or (item['year'] or 0) >= year - 1:
                        items.append(item)
                        seen.add(m.get('id'))
        if errors == len(self.new):
            raise AddonError('no new_catalog addon answered')
        # Stable sort: newest year first, the catalogs' own order within a year.
        return self.annotate(sorted(items, key=lambda i: i['year'] or 0, reverse=True))

    def search(self, q):
        q = q.strip().casefold()
        if not q:
            raise HttpError(400, 'q is required')
        lib = self.library(None)
        title = lambda i: (i['title'] or '').casefold()
        tiers = [lambda i: title(i).startswith(q),
                 lambda i: any(w.startswith(q) for w in re.split(r'\W+', title(i))),
                 lambda i: any(str(g).casefold() == q for g in i['genres'])]
        out, seen = [], set()
        for tier in tiers:
            for i in lib:
                if i['id'] not in seen and tier(i):
                    out.append(i)
                    seen.add(i['id'])
        for a in self.meta_addons:
            try:
                for c, names, required in a.catalogs():
                    if 'search' in names and not required - {'search'}:
                        for m in lst(a.get('catalog', c.get('type'), c.get('id'), {'search': q}).get('metas')):
                            if isinstance(m, dict) and m.get('id') not in seen:
                                out.append(to_item(m))
                                seen.add(m.get('id'))
            except AddonError:
                continue  # library results still come back when an addon is down
        return self.annotate(out)

    def subtitles(self, type_, vid):
        out = []
        for a in self.stream_addons + self.meta_addons:
            try:
                if a.supports('subtitles', type_, vid):
                    out += [s for s in lst(a.get('subtitles', type_, vid).get('subtitles')) if isinstance(s, dict)]
            except AddonError:
                continue
        return out


def episode(se):
    """[] -> (0, 0) for a movie; ['1', '3'] -> (1, 3)."""
    if not se:
        return 0, 0
    if len(se) == 2 and all(x.isdigit() for x in se):
        return int(se[0]), int(se[1])
    raise HttpError(404, 'expected /{id}/{season}/{episode}')


def skip(qs):
    if not qs.get('skip', '0').isdigit():
        raise HttpError(400, 'skip must be a number')
    return int(qs.get('skip', '0'))


def imdb(id_):
    if not IMDB.fullmatch(id_):
        raise HttpError(400, 'id must be an IMDb id (tt…)')
    return id_


class Handler(BaseHTTPRequestHandler):
    hub: Hub
    protocol_version = 'HTTP/1.1'

    def do_GET(self): self.route('GET')
    def do_HEAD(self): self.route('HEAD')  # Kodi probes .strm targets with HEAD before playing
    def do_POST(self): self.route('POST')
    def do_PUT(self): self.route('PUT')
    def do_DELETE(self): self.route('DELETE')
    def do_OPTIONS(self): self.route('OPTIONS')

    def body(self):
        try:
            return json.loads(self.raw or b'null')
        except ValueError:
            raise HttpError(400, 'body is not JSON') from None

    def route(self, method):
        t0, u = time.monotonic(), urllib.parse.urlsplit(self.path)
        headers, err = {}, ''
        try:
            length = int(self.headers.get('Content-Length') or 0)
            if length > MAX_BODY:
                self.close_connection = True  # the unread body would otherwise be parsed as the next request
                raise HttpError(413, 'request body over 64 KB')
            # Read the body before routing so an early 4xx can't leave it in the keep-alive stream.
            self.raw = self.rfile.read(length)
            parts = [urllib.parse.unquote(p) for p in u.path.split('/') if p]
            qs = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
            limit = self.hub.limits.get(parts[0] if parts else '')
            # /pair counts only word guesses (POST /pair), not reading the words or opening a window.
            if limit and (parts[0] != 'pair' or [method, *parts] == ['POST', 'pair']) and not limit.take(self.client_address[0]):
                raise HttpError(429, 'too many requests; slow down')
            public = method == 'OPTIONS' or [method, *parts] in (['GET', 'health'], ['GET', 'pair'], ['POST', 'pair'])
            if not public and not self.hub.authorized(self.headers.get('Authorization')):
                headers = {'WWW-Authenticate': 'Bearer'}
                raise HttpError(401, 'pair this device first: send Authorization: Bearer <token>')
            status, body, headers = self.dispatch('GET' if method == 'HEAD' else method, parts, qs)
        except HttpError as e:
            status, body = e.status, {'reason': e.reason}
        except AddonError as e:
            status, body = 503, {'reason': f'addon unreachable: {e}'}
        except Exception as e:  # a bad addon response must never take the server down
            tb = traceback.extract_tb(e.__traceback__)[-1]
            status, body, err = 500, {'reason': type(e).__name__}, f' {type(e).__name__}@{tb.lineno}'
        data = b'' if body is None and status != 200 else json.dumps(body).encode()
        self.send_response(status)
        # CORS only for pages the hub serves itself (the setup page); every other web origin gets "null".
        origin = self.headers.get('Origin')
        self.send_header('Access-Control-Allow-Origin', origin if origin == f'http://{self.headers.get("Host")}' else 'null')
        self.send_header('Vary', 'Origin')
        if method == 'OPTIONS':
            self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, POST, PUT, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if method != 'HEAD':
            self.wfile.write(data)
        # Path only, never the query, headers, addon URLs (see fetch) or stream URLs.
        print(f'{self.client_address[0]} {method} {u.path} {status} {(time.monotonic() - t0) * 1000:.0f}ms{err}', flush=True)

    def dispatch(self, method, parts, qs):
        hub = self.hub
        ok = lambda body: (200, body, {})
        match [method, *parts]:
            case ['OPTIONS', *_]:
                return 204, None, {}
            case ['GET', 'health']:
                return ok(hub.health())
            case ['GET', 'pair']:
                # The words are for the TV to show, so only the box itself or an already paired client may read them.
                if not ipaddress.ip_address(self.client_address[0]).is_loopback and not hub.authorized(self.headers.get('Authorization')):
                    raise HttpError(403, 'the pairing words are shown on the TV')
                if not hub.pairing_open():
                    raise HttpError(403, 'pairing is closed; open it from the TV')
                return ok({'words': hub.pair_words, 'until': int(hub.pair_until * 1000)})
            case ['POST', 'pair']:
                return ok(hub.pair(self.body()))
            case ['POST', 'pair', 'open']:
                hub.open_pairing()
                return ok({'until': int(hub.pair_until * 1000)})
            case ['POST', 'pair', 'rotate']:
                return ok(hub.rotate())
            case ['GET', 'library']:
                return ok({'items': hub.library(qs.get('collection'))})
            case ['DELETE', 'library', id_]:
                hub.sql('DELETE FROM library WHERE id = ?', (id_,))
                return ok({'ok': True})
            case ['POST', 'keep']:
                return ok(hub.keep(self.body()))
            case ['GET', 'play', id_, *se]:
                best = hub.pick('series' if se else 'movie', imdb(id_), *episode(se), hub.preset_for(qs))
                if qs.get('json') == '1':
                    return ok({k: best[k] for k in ('url', 'headers', 'badges', 'source', 'cut')})
                return 302, None, {'Location': best['url']}
            case ['GET', 'resume', id_, *se]:
                return ok(hub.get_resume((id_, *episode(se))))
            case ['PUT', 'resume', id_, *se]:
                return ok(hub.put_resume((id_, *episode(se)), self.body()))
            case ['GET', 'browse', 'catalogs']:
                return ok({'catalogs': hub.catalogs()})
            case ['GET', 'presets']:
                return ok({'default': hub.preset, 'presets': hub.presets, 'aliases': ranking.ALIASES})
            case ['GET', 'new']:
                return ok({'items': hub.new_releases(skip(qs))})
            case ['GET', 'browse', 'catalog', type_, cid]:
                return ok({'items': hub.catalog(type_, cid, skip(qs))})
            case ['GET', 'browse', 'meta', type_, id_]:
                meta = hub.meta(type_, id_)
                item = to_item(meta)
                eps = episodes(meta)
                hub.annotate([item], {item['id']: eps})
                return ok({**item, 'videos': [{'s': s, 'e': e, 'title': t} for s, e, t in eps]})
            case ['GET', 'browse', 'streams' | 'subtitles' as what, type_, id_, *se]:
                s, e = episode(se)
                vid = f'{id_}:{s}:{e}' if se else id_
                if what == 'subtitles':
                    return ok({'subtitles': hub.subtitles(type_, vid)})
                streams, relaxed = hub.ranked(type_, id_, s, e, hub.preset_for(qs))
                return ok({'streams': streams, 'relaxed': relaxed})
            case ['GET', 'search']:
                return ok({'items': hub.search(qs.get('q', ''))})
        raise HttpError(404, 'no such call')

    def log_message(self, *args):
        pass  # route() prints its own single line per request


def make_server(config_path, data_dir):
    hub = Hub(config_path, data_dir)
    handler = type('BoundHandler', (Handler,), {'hub': hub})
    return ThreadingHTTPServer((hub.bind, hub.port), handler)


def main():
    server = make_server(os.environ.get('HUB_CONFIG', '/var/lib/hub/hub.toml'), os.environ.get('HUB_DATA', '/var/lib/hub'))
    print(f'hub listening on {server.server_address[0]}:{server.server_address[1]}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
