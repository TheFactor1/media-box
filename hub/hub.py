"""Media box hub API: kept library, resume points, browse and best-stream picking over Stremio addons.

Contract: DATAFLOW.md, "Hub API". Python 3.12 standard library only.
Run: HUB_CONFIG=/var/lib/hub/hub.toml HUB_DATA=/var/lib/hub python -m hub.hub
"""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import time
import tomllib
import traceback
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import onboarding, ranking, security, shelves

CINEMETA = 'https://v3-cinemeta.strem.io/manifest.json'
with open(os.path.join(os.path.dirname(__file__), 'setup.html'), 'rb') as _f:
    SETUP_HTML = _f.read()
# CSP allows exactly the page's own inline script, not 'unsafe-inline'. Browsers hash it after turning CRLF into LF,
# so a CRLF checkout (Windows git) must be hashed the same way or the page's script is blocked.
SETUP_SCRIPT = "'sha256-%s'" % security.base64.b64encode(security.hashlib.sha256(
    re.search(rb'<script>(.*?)</script>', SETUP_HTML, re.S)[1].replace(b'\r\n', b'\n')).digest()).decode()
STREAM_TTL = 600         # contract: stream cache per title 10 min
MANIFEST_TTL = 3600
META_TTL = 86400
PAIR_WINDOW = 600     # pairing words, setup page and TV pairing requests each last 10 min
FIRST_BOOT_WINDOW = 1800  # an unpaired LAN device may get the setup link only this long after start, and never after setup
MAX_BODY = 64 * 1024
NEW_PAGE, NEW_MAX = 50, 300  # /new page size; titles read per catalog
MAX_ADDON = 8 * 2**20    # an addon answer over 8 MB is refused, so a hostile addon can't exhaust memory
OWN_PORTS = set()        # this process's hub ports: an addon URL (or its redirect) must never point back at the hub
DONE = 0.92              # contract: "continue" = resume point under 92 %
IMDB = re.compile(r'tt\d+')


class AddonError(Exception):
    pass


class HttpError(Exception):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason


def now_ms():
    return int(time.time() * 1000)


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def host(url):
    return urllib.parse.urlsplit(url).hostname or '?'


def lst(x):
    return x if isinstance(x, list) else []


def check_url(url):
    """http(s) only; never link-local (cloud metadata), multicast, 0.0.0.0, or the hub's own port on a non-public address.
    True when every address the host resolves to is public."""
    # shortcut: checked before connecting, so DNS rebinding between this lookup and urllib's can slip past; pin the IP if that matters
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise AddonError(f'{host(url)}: only http and https addon links')
    port = p.port or (443 if p.scheme == 'https' else 80)
    public = True
    for *_, sa in socket.getaddrinfo(p.hostname, port, proto=socket.IPPROTO_TCP):
        ip = ipaddress.ip_address(sa[0].split('%')[0])
        ip = getattr(ip, 'ipv4_mapped', None) or ip
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or (port in OWN_PORTS and not ip.is_global):
            raise AddonError(f'{host(url)}: refused, that address is the hub itself or link-local')
        public = public and ip.is_global
    return public


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # An addon on the internet must not bounce the hub onto the LAN (router pages, AIOStreams, other boxes).
        if not check_url(newurl) and getattr(req, 'public', False):
            raise AddonError(f'{host(newurl)}: refused, a public addon redirected to a private address')
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.public = getattr(req, 'public', False)  # once public, every later hop stays held to it
        return new


OPENER = urllib.request.build_opener(CheckedRedirect)


def fetch(url, timeout=10, headers=None):
    try:
        public = check_url(url)
        headers = dict(headers or {})
        auth = headers.pop('Authorization', None)
        req = urllib.request.Request(url, headers={'User-Agent': 'mediabox-hub/1', 'Accept': 'application/json', **headers})
        if auth:  # unredirected: a redirect to another host must not carry the key
            req.add_unredirected_header('Authorization', auth)
        req.public = public  # read by CheckedRedirect
        with OPENER.open(req, timeout=timeout) as r:
            buf, end = bytearray(), time.monotonic() + 3 * timeout  # `timeout` is per read; this caps a slow drip
            while chunk := r.read(65536):
                buf += chunk
                if len(buf) > MAX_ADDON or time.monotonic() > end:
                    raise AddonError(f'{host(url)}: answer too large or too slow')
            data = json.loads(buf)
    except (OSError, ValueError, RecursionError) as e:  # network errors are OSError, bad JSON ValueError, too-deep JSON RecursionError
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
        os.makedirs(data_dir, mode=0o700, exist_ok=True)
        self.db = os.path.join(data_dir, 'hub.sqlite')
        self.sql('CREATE TABLE IF NOT EXISTS library(id TEXT PRIMARY KEY, type TEXT, item TEXT, videos TEXT, kept_at INTEGER)')
        self.sql('CREATE TABLE IF NOT EXISTS resume(id TEXT, s INTEGER, e INTEGER, position_ms INTEGER,'
                 ' duration_ms INTEGER, updated_at INTEGER, PRIMARY KEY(id, s, e))')
        self.sql('CREATE TABLE IF NOT EXISTS settings(name TEXT PRIMARY KEY, value BLOB)')
        self.key = security.secret_file(os.path.join(data_dir, 'key'))
        if not self.sql("SELECT 1 FROM settings WHERE name = 'cipher_v2'"):
            self.seal_old_settings()
        self.token_path = os.path.join(data_dir, 'token')
        fresh = not self.sql("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'devices'")
        self.sql('CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY, name TEXT, kind TEXT, token_sha256 TEXT UNIQUE,'
                 ' created INTEGER, last_seen INTEGER)')
        self.seen = {}  # device id -> when last_seen was last written
        self.load_devices()
        if fresh and os.path.exists(self.token_path):
            # Before per-device tokens every TV shared the box's token: it lives on as one removable device.
            self.add_device('TVs paired before (shared token)', 'legacy', security.b64(security.secret_file(self.token_path, 0o640)))
        box = security.b64(security.secret_file(self.token_path, 0o640))  # group media: Kodi on the box reads it
        if self.sql("SELECT 1 FROM devices WHERE kind = 'legacy' AND token_sha256 = ?", (digest(box),)):
            # The file still holds the old shared token (also after a crash mid-migration): the box gets its own.
            box = security.b64(security.write_secret(self.token_path, secrets.token_bytes(32), 0o640))
        self.set_box(box)
        try:
            self.presets = ranking.preset_table(cfg.get('presets'))
        except ValueError as e:
            sys.exit(f'hub.toml: {e}')
        self.preset = ranking.ALIASES.get(cfg.get('preset'), cfg.get('preset', 'standard'))
        if self.preset not in self.presets:
            sys.exit(f'hub.toml: preset must be one of {", ".join(self.presets)}')
        if self.setting('preset') in self.presets:  # chosen on the setup page; "auto" leaves the default to the front end
            self.preset = self.setting('preset')
        self.aiostreams_url = cfg.get('aiostreams_url', 'http://127.0.0.1:3000')
        self.name, self.discovery_port = str(cfg.get('name', 'Media box')), int(cfg.get('discovery_port', 8098))
        self.hosts = {str(h).lower() for h in lst(cfg.get('hosts'))}
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
        self.new_cache = (0, None)
        self.catalog_cache = {}  # shortcut: per page, never pruned; bounded by catalogs x pages read
        self.tmdb_base = cfg.get('tmdb_base', 'https://api.themoviedb.org/3')  # only tests point this elsewhere
        self.provider_base = cfg.get('provider_base')  # only tests point the debrid key check elsewhere
        shelves.init(self)
        self.cache = {}  # shortcut: in memory, lost on restart and never pruned; fine for one household, move to sqlite if it grows
        self.limits = {'play': security.Bucket(10, 10), 'setup': security.Bucket(10, 10), 'health': security.Bucket(1, 5),
                       'pair': security.Bucket(5 / 60, 5), 'pair/request': security.Bucket(3 / 60, 3)}
        self.pair_words, self.pair_until, self.pair_requests = None, 0, {}
        self.setup_nonce, self.setup_until = None, 0
        self.started = time.monotonic()  # monotonic: an NTP step after a wrong RTC must not stretch the window
        if (self.stream_addons or self.setting('paired')) and not self.setting('trust_ended'):
            self.set_setting('trust_ended', True)  # boxes set up before this flag existed get no fresh window
        if not self.setting('paired'):
            self.open_pairing()
        if not self.stream_addons:
            self.open_setup(timed=False)  # unconfigured: setup stays open until it's done

    # -- settings, token, pairing --

    def setting(self, name):
        row = self.sql('SELECT value FROM settings WHERE name = ?', (name,))
        if not row:
            return None
        return json.loads(security.decrypt(self.key, row[0][0], name))  # named form only: see seal_old_settings

    def seal_old_settings(self):
        """Once per box: re-seal rows from before name binding with their name, then mark it done (cipher_v2). After
        that the unbound form is never accepted, so an old-style row planted in the database is refused."""
        for name, blob in self.sql('SELECT name, value FROM settings'):
            try:
                security.decrypt(self.key, blob, name)
                continue
            except ValueError:
                pass
            try:
                plain = security.decrypt(self.key, blob)
            except ValueError:  # tampered or another key's: left as it is; reading it fails as before
                print(f'setting {re.sub(r"[^!-~]", "?", name)} failed its integrity check; left as is', flush=True)
                continue
            self.sql('UPDATE settings SET value = ? WHERE name = ?', (security.encrypt(self.key, plain, name), name))
        self.set_setting('cipher_v2', True)

    def set_setting(self, name, value):
        self.sql('INSERT OR REPLACE INTO settings VALUES (?, ?)', (name, security.encrypt(self.key, json.dumps(value).encode(), name)))

    # One token per paired device; only its SHA-256 is stored, the raw token is handed out once.

    def load_devices(self):
        self.tokens = dict(self.sql('SELECT token_sha256, id FROM devices'))  # replaced whole: readers never see it half-built

    def add_device(self, name, kind, token):
        self.sql('INSERT INTO devices VALUES (?, ?, ?, ?, ?, NULL)', (secrets.token_urlsafe(6), name, kind, digest(token), now_ms()))
        self.load_devices()

    def mint(self, name):
        token = security.b64(secrets.token_bytes(32))
        self.add_device(name, 'tv', token)
        return token

    def set_box(self, token):
        if not self.sql("UPDATE devices SET token_sha256 = ? WHERE kind = 'box' RETURNING id", (digest(token),)):
            self.add_device('This box', 'box', token)
        self.load_devices()

    def authorized(self, header):
        """The calling device's id, or None."""
        header = str(header or '')
        did = self.tokens.get(digest(header[7:])) if header.startswith('Bearer ') else None
        if did and time.time() - self.seen.get(did, 0) >= 60:  # last_seen to the minute, not a write per call
            self.seen[did] = time.time()
            self.sql('UPDATE devices SET last_seen = ? WHERE id = ?', (now_ms(), did))
        return did

    def devices(self, caller):
        rows = self.sql('SELECT id, name, kind, created, last_seen FROM devices ORDER BY created, rowid')
        return [{'id': i, 'name': n, 'kind': k, 'created': c, 'lastSeen': seen, 'current': i == caller} for i, n, k, c, seen in rows]

    def remove_device(self, did):
        row = self.sql('SELECT kind FROM devices WHERE id = ?', (did,))
        if not row:
            raise HttpError(404, 'no such device')
        if row[0][0] == 'box':
            raise HttpError(409, "this box's own token can't be removed")
        self.sql('DELETE FROM devices WHERE id = ?', (did,))
        self.load_devices()
        return {'ok': True}

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
        self.set_setting('trust_ended', True)
        return {'token': self.mint('Paired with words')}

    def open_setup(self, timed=True):
        self.setup_nonce = secrets.token_urlsafe(16)
        self.setup_until = time.time() + PAIR_WINDOW if timed else float('inf')

    def setup_live(self):
        return self.setup_nonce is not None and time.time() < self.setup_until

    def setup_ok(self, n):
        return self.setup_live() and hmac.compare_digest(str(n or '').encode(), self.setup_nonce.encode())

    def require_setup(self, n):
        if not self.setup_live():
            raise HttpError(404, 'no such call')  # a locked setup page looks like no setup page
        if not self.setup_ok(n):
            raise HttpError(403, 'open this page from the QR code on the TV')

    def setup_url(self):
        return f'http://{onboarding.lan_ip()}:{self.port}/setup?n={self.setup_nonce}'

    def host_ok(self, header, local):
        """DNS rebinding guard: a rebound page's Host names the attacker's domain, so only loopback names, hub.toml
        `hosts`, or the very IP the client connected to (`local`) are served."""
        m = re.fullmatch(r'\[([0-9a-f:.]+)\](?::\d*)?|([^:\[\]@/?#\s]+)(?::\d*)?', str(header or '').lower())
        name = m and (m[1] or m[2])
        if not name:
            return False
        if name in ('127.0.0.1', 'localhost', '::1', *self.hosts):
            return True
        try:
            ip, own = (ipaddress.ip_address(x.split('%')[0]) for x in (name, local))
        except ValueError:  # a name that isn't an IP literal and isn't listed
            return False
        plain = lambda a: getattr(a, 'ipv4_mapped', None) or a
        return plain(ip) == plain(own)

    def finish_setup(self):
        if not self.stream_addons:
            raise HttpError(400, 'choose where streams come from first')
        self.setup_until = min(self.setup_until, time.time() + PAIR_WINDOW)  # keep the approval step usable a while
        self.set_setting('trust_ended', True)  # from now on only the phone page or a paired device can approve
        if not self.pairing_open():
            self.open_pairing()
        return {'words': self.pair_words}

    def use_stream_addon(self, url):
        url = re.sub(r'^stremio://', 'https://', str(url or '').strip())
        if not re.fullmatch(r'https?://[^\s/]+/\S*manifest\.json', url):
            raise HttpError(400, 'paste the addon link that ends in manifest.json')
        addon = Addon(url)
        if not addon.supports('stream', 'movie', 'tt0000000') and not addon.supports('stream', 'series', 'tt0000000'):
            raise HttpError(400, 'that addon does not offer streams')
        self.set_setting('stream_addons', [url])
        self.stream_addons, self.cache = [addon], {}
        return {'ok': True, 'host': addon.host}

    # TV-initiated pairing: the TV asks, shows a 6-digit code, and a phone (setup page) or paired client approves.

    def pair_request(self, body, authorized=False):
        now = time.time()
        for k, v in list(self.pair_requests.items()):  # in place: rebuilding the dict could drop another thread's request
            if now - v['created'] >= 2 * PAIR_WINDOW:
                self.pair_requests.pop(k, None)
        name = re.sub(r'[^ -~]', '', str(body.get('name') if isinstance(body, dict) else '') or 'TV')[:40]
        rid, code = secrets.token_urlsafe(16), f'{secrets.randbelow(10**6):06d}'
        self.pair_requests[rid] = {'name': name, 'code': code, 'created': now, 'approved': False}
        if self.first_boot_trust() and not self.setup_live():
            self.open_setup()  # first TV on a fresh hub: the setup page's approval step opens for it
        # The setup link carries the nonce that can approve requests, so an unauthenticated TV gets it only in the
        # first-boot trust window; otherwise anyone on the LAN could approve themselves. `paired` alone isn't enough:
        # Kodi on the box reads the token file and never pairs, so a Kodi-only box would stay open forever.
        share = self.setup_live() and (authorized or self.first_boot_trust())
        return {'requestId': rid, 'code': code, 'setupUrl': self.setup_url() if share else None}

    def first_boot_trust(self):
        return not self.setting('trust_ended') and time.monotonic() - self.started < FIRST_BOOT_WINDOW

    def pending_requests(self):
        now = time.time()
        # No code here: the approver must read it off the TV, so holding the nonce or a token isn't enough.
        return [{'requestId': k, 'name': v['name'], 'ageS': int(now - v['created'])}
                for k, v in list(self.pair_requests.items()) if not v['approved'] and now - v['created'] < PAIR_WINDOW]

    def approve(self, body):
        req = self.pair_requests.get(str(body.get('requestId')) if isinstance(body, dict) else '')
        if not req:
            raise HttpError(404, 'no such pairing request')
        if time.time() - req['created'] >= PAIR_WINDOW:
            raise HttpError(410, 'that pairing request expired; start again on the TV')
        if not hmac.compare_digest(str(body.get('code') or '').encode(), req['code'].encode()):
            raise HttpError(403, 'that code does not match the one on the TV')
        req['approved'] = True
        self.set_setting('paired', True)
        self.set_setting('trust_ended', True)
        return {'ok': True}

    def poll_request(self, rid):
        req = self.pair_requests.get(rid)
        if not req:
            raise HttpError(404, 'no such pairing request')
        if time.time() - req['created'] >= PAIR_WINDOW and not req['approved']:
            raise HttpError(410, 'that pairing request expired; start again')
        if not req['approved']:
            return 202, {'status': 'pending'}, {}
        if self.pair_requests.pop(rid, None) is None:  # the token is handed out once, even to two racing polls
            raise HttpError(404, 'no such pairing request')
        return 200, {'token': self.mint(req['name'])}, {}

    def rotate(self):
        """A new token for the box itself (Kodi re-reads the file); paired TVs keep theirs."""
        token = security.b64(security.write_secret(self.token_path, secrets.token_bytes(32), 0o640))
        self.set_box(token)
        return {'token': token}

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
        ok = lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2**63
        if not isinstance(body, dict) or not ok(body.get('positionMs')) or not ok(body.get('durationMs')):
            raise HttpError(400, 'body must be {"positionMs": int, "durationMs": int}')
        updated = body.get('updatedAt', now_ms())  # optional so a front end that was offline can't overwrite newer progress
        if not ok(updated):
            raise HttpError(400, 'updatedAt must be epoch ms')
        updated = min(updated, now_ms())  # a clock in the future must not pin this point against every later write
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

    def catalog_metas(self, addon, type_, cid, skip):
        """One catalog page, cached 10 min: Home rows and smart collections read the same pages over and over."""
        key = (addon.host, type_, cid, skip)
        hit = self.catalog_cache.get(key)
        if hit and time.time() - hit[0] < STREAM_TTL:
            return hit[1]
        metas = [m for m in lst(addon.get('catalog', type_, cid, {'skip': skip} if skip else None).get('metas')) if isinstance(m, dict)]
        self.catalog_cache[key] = (time.time(), metas)
        return metas

    def catalog(self, type_, cid, skip):
        for a in self.meta_addons:
            try:
                if any(c.get('type') == type_ and c.get('id') == cid for c, _, _ in a.catalogs()):
                    return self.annotate([to_item(m) for m in self.catalog_metas(a, type_, cid, skip)])
            except AddonError:
                continue
        raise HttpError(404, f'no addon has catalog {type_}/{cid}')

    def new_releases(self, skip):
        # Several catalogs feed New, so a per-catalog skip can't page the merged list: build it whole, then slice.
        at, merged = self.new_cache
        if merged is None or time.time() - at >= STREAM_TTL:
            merged = self.merge_new()
            self.new_cache = (time.time(), merged)
        return self.annotate([dict(i) for i in merged[skip:skip + NEW_PAGE]])  # copies: annotate fills them in

    def merge_new(self):
        items, seen, errors = [], set(), 0
        year = time.localtime().tm_year
        for addon, type_, cid in self.new:
            metas = []
            while len(metas) < NEW_MAX:
                try:
                    page = lst(addon.get('catalog', type_, cid, {'skip': len(metas)} if metas else None).get('metas'))
                except AddonError:
                    errors += not metas  # a failed later page just ends this catalog
                    break
                fresh = [m for m in page if isinstance(m, dict) and m.get('id') not in {x.get('id') for x in metas}]
                if not fresh:  # exhausted, or an addon that ignores skip
                    break
                metas += fresh
            for m in metas[:NEW_MAX]:
                if m.get('id') not in seen:
                    item = to_item(m)
                    if not self.new_recent_only or (item['year'] or 0) >= year - 1:
                        items.append(item)
                        seen.add(m.get('id'))
        if errors == len(self.new):
            raise AddonError('no new_catalog addon answered')
        # Stable sort: newest year first, the catalogs' own order within a year.
        return sorted(items, key=lambda i: i['year'] or 0, reverse=True)

    def search(self, q):
        q = q.strip().casefold()
        if not q or len(q) > 100:
            raise HttpError(400, 'q is required, at most 100 characters')
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
    if len(se) == 2 and all(re.fullmatch(r'\d{1,6}', x) for x in se):  # not isdigit(): '²' passes that and breaks int()
        return int(se[0]), int(se[1])
    raise HttpError(400, 'expected /{id}/{season}/{episode}')


def skip(qs):
    if not re.fullmatch(r'\d{1,6}', qs.get('skip', '0')):
        raise HttpError(400, 'skip must be a number')
    return int(qs.get('skip', '0'))


def kind(t):
    """Strict: the hub's own logic (play, streams, episodes) knows only these two."""
    if t not in ('movie', 'series'):
        raise HttpError(400, 'type must be movie or series')
    return t


def addon_type(t):
    """Any well-formed Stremio type (tv, anime, channel…): catalogs and meta just pass it to the addon."""
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,19}', t):
        raise HttpError(400, 'type is not a valid addon type')
    return t


def shape(path):
    """The route without titles or ids, for the log: /play/tt1/1/2 -> /play/:id/:id/:id."""
    path = re.sub(r'(?<=/request/).+', ':id', path)
    return re.sub(r'(?<=/)(?:tt\d+|\d+)(?=/|$)', ':id', path)


def imdb(id_):
    if not IMDB.fullmatch(id_):
        raise HttpError(400, 'id must be an IMDb id (tt…)')
    return id_


class Handler(BaseHTTPRequestHandler):
    hub: Hub
    protocol_version = 'HTTP/1.1'
    server_version, sys_version = 'mediabox-hub', ''  # no Python version in the Server header
    timeout = 30  # idle or slow-sending connections are dropped instead of holding a thread forever

    def do_GET(self): self.route('GET')
    def do_HEAD(self): self.route('HEAD')  # Kodi probes .strm targets with HEAD before playing
    def do_POST(self): self.route('POST')
    def do_PUT(self): self.route('PUT')
    def do_DELETE(self): self.route('DELETE')
    def do_PATCH(self): self.route('PATCH')
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
            if not 0 <= length <= MAX_BODY:  # -1 would read to EOF, unbounded
                self.close_connection = True  # the unread body would otherwise be parsed as the next request
                raise HttpError(413, 'request body over 64 KB')
            # Read the body before routing so an early 4xx can't leave it in the keep-alive stream.
            self.raw = self.rfile.read(length)
            if not self.hub.host_ok(self.headers.get('Host'), self.connection.getsockname()[0]):
                raise HttpError(421, 'unknown host name; add it to hosts in hub.toml')
            parts = [urllib.parse.unquote(p) for p in u.path.split('/') if p]
            qs = {k: v[-1] for k, v in urllib.parse.parse_qs(u.query).items()}
            mp = ['GET' if method == 'HEAD' else method, *parts]
            # /pair counts only word guesses and TV requests, not reading the words or opening a window.
            # Approvals share the guess bucket: each one tries a 6-digit code.
            limit = self.hub.limits.get('pair' if mp in (['POST', 'pair'], ['POST', 'pair', 'approve']) else 'pair/request' if mp == ['POST', 'pair', 'request']
                                        else parts[0] if parts and parts[0] in ('play', 'setup', 'health') else '')
            if limit and not limit.take(self.client_address[0]):
                raise HttpError(429, 'too many requests; slow down')
            # Open to the LAN: health and pairing. Setup checks its nonce, /pair and approvals check token or nonce inside.
            public = method == 'OPTIONS' or mp in (['GET', 'health'], ['GET', 'pair'], ['POST', 'pair'], ['POST', 'pair', 'request'],
                                                    ['GET', 'pair', 'requests'], ['POST', 'pair', 'approve'], ['GET', 'devices']) \
                or mp[:3] == ['GET', 'pair', 'request'] or mp[:2] == ['DELETE', 'devices'] or (parts[:1] == ['setup'] and mp != ['POST', 'setup', 'open'])
            # The setup page has no token; its nonce (in a header) also opens the Home rows and collections calls.
            page = parts[:1] in (['shelves'], ['shelf'], ['collections']) and self.hub.setup_ok(self.headers.get('X-Setup-Nonce'))
            if not public and not page and not self.hub.authorized(self.headers.get('Authorization')):
                headers = {'WWW-Authenticate': 'Bearer'}
                raise HttpError(401, 'pair this device first: send Authorization: Bearer <token>')
            status, body, headers = self.dispatch(mp[0], parts, qs)
        except HttpError as e:
            status, body = e.status, {'reason': e.reason}
        except AddonError as e:
            status, body = 503, {'reason': f'addon unreachable: {e}'}
        except Exception as e:  # a bad addon response must never take the server down
            tb = traceback.extract_tb(e.__traceback__)[-1]
            status, body, err = 500, {'reason': type(e).__name__}, f' {type(e).__name__}@{tb.lineno}'
        data = body if isinstance(body, bytes) else b'' if body is None and status != 200 else json.dumps(body).encode()
        self.send_response(status)
        # CORS only for pages the hub serves itself (the setup page); every other web origin gets "null".
        # Never "null": sandboxed iframes and data: pages send Origin: null and would then be allowed to read.
        origin = self.headers.get('Origin')
        if origin and origin == f'http://{self.headers.get("Host")}':
            self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Vary', 'Origin')
        if method == 'OPTIONS':
            self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Setup-Nonce')
        headers = {'X-Content-Type-Options': 'nosniff', 'Cross-Origin-Resource-Policy': 'same-origin',
                   'Referrer-Policy': 'no-referrer', **headers}
        if 'Content-Type' not in headers:
            headers.update({'Content-Type': 'application/json', 'Cache-Control': 'no-store'})
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if method != 'HEAD':
            self.wfile.write(data)
        # Route shape only, never ids, the query, headers, addon URLs (see fetch) or stream URLs.
        print(f'{self.client_address[0]} {method} {re.sub(r"[^!-~]", "?", shape(u.path))} {status} {(time.monotonic() - t0) * 1000:.0f}ms{err}', flush=True)

    def dispatch(self, method, parts, qs):
        hub = self.hub
        ok = lambda body: (200, body, {})
        match [method, *parts]:
            case ['OPTIONS', *_]:
                return 204, None, {}
            case ['GET', 'health']:
                # Unpaired callers learn only that a hub is here; the addon list (and its outbound checks) needs the token.
                return ok(hub.health() if hub.authorized(self.headers.get('Authorization')) else {'ok': True})
            case ['GET', 'pair']:
                # The words are for the TV to show, so only the box itself or an already paired client may read them.
                if not self.local_or_paired():
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
                if not self.loopback():
                    raise HttpError(403, 'rotate the box token on the box itself')
                return ok(hub.rotate())
            case ['GET', 'devices']:
                self.trusted(qs.get('n'))
                return ok({'devices': hub.devices(hub.authorized(self.headers.get('Authorization')))})
            case ['DELETE', 'devices', did]:
                body = self.body()
                self.trusted(body.get('n') if isinstance(body, dict) else qs.get('n'))
                return ok(hub.remove_device(did))
            case ['POST', 'pair', 'request']:
                return ok(hub.pair_request(self.body(), hub.authorized(self.headers.get('Authorization'))))
            case ['GET', 'pair', 'request', rid]:
                return hub.poll_request(rid)
            case ['GET', 'pair', 'requests']:
                self.trusted(qs.get('n'))
                return ok({'requests': hub.pending_requests()})
            case ['POST', 'pair', 'approve']:
                body = self.body()
                self.trusted(body.get('n') if isinstance(body, dict) else None)
                return ok(hub.approve(body))
            case ['POST', 'setup', 'open']:
                hub.open_setup()
                return ok({'url': hub.setup_url(), 'until': int(hub.setup_until * 1000)})
            case ['GET', 'setup', 'link']:
                # For the TV's QR. Like the pairing words: the box itself or a paired client only.
                if not self.local_or_paired():
                    raise HttpError(403, 'the setup link is shown on the TV')
                if not hub.setup_live():
                    raise HttpError(404, 'setup is closed; open it from the TV')
                return ok({'url': hub.setup_url(), 'until': None if hub.setup_until == float('inf') else int(hub.setup_until * 1000)})
            case ['GET', 'setup']:
                hub.require_setup(qs.get('n'))
                return 200, SETUP_HTML, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store',
                                         'X-Frame-Options': 'DENY', 'Content-Security-Policy':
                                         "default-src 'none'; script-src " + SETUP_SCRIPT + "; style-src 'unsafe-inline'; "
                                         "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"}
            case ['GET', 'setup', 'info']:
                hub.require_setup(qs.get('n'))
                return ok({'presets': hub.presets, 'default': hub.preset, 'providers': onboarding.PROVIDERS,
                           'configured': bool(hub.stream_addons), 'aiostreams': host(hub.aiostreams_url),
                           'tmdb': bool(hub.setting('tmdb_api_key'))})
            case ['POST', 'setup']:
                body = self.body()
                hub.require_setup(body.get('n') if isinstance(body, dict) else None)
                try:
                    return ok(onboarding.handle(hub, body, self.headers.get('Host', '').rsplit(':', 1)[0]))
                except onboarding.ProvisionError as e:
                    raise HttpError(502, str(e)) from None
                except ValueError as e:
                    raise HttpError(400, str(e)) from None
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
                return ok(hub.get_resume((imdb(id_), *episode(se))) or {})
            case ['PUT', 'resume', id_, *se]:
                return ok(hub.put_resume((imdb(id_), *episode(se)), self.body()))
            case ['GET', 'browse', 'catalogs']:
                return ok({'catalogs': hub.catalogs()})
            case ['GET', 'presets']:
                return ok({'default': hub.preset, 'presets': hub.presets, 'aliases': ranking.ALIASES})
            case ['GET', 'shelves']:
                return ok({'shelves': shelves.get_shelves(hub)})
            case ['PUT', 'shelves']:
                return ok({'shelves': shelves.put_shelves(hub, self.body())})
            case ['GET', 'shelf', sid]:
                return ok(shelves.shelf(hub, sid, skip(qs)))
            case ['GET', 'collections']:
                return ok({'collections': shelves.list_collections(hub)})
            case ['POST', 'collections']:
                return ok(shelves.create_collection(hub, self.body()))
            case ['PATCH', 'collections', cid]:
                return ok(shelves.patch_collection(hub, cid, self.body()))
            case ['DELETE', 'collections', cid]:
                return ok(shelves.delete_collection(hub, cid))
            case ['POST', 'collections', cid, 'items']:
                return ok(shelves.add_item(hub, cid, self.body()))
            case ['DELETE', 'collections', cid, 'items', item]:
                return ok(shelves.remove_item(hub, cid, item))
            case ['GET', 'franchise', id_]:
                return ok(shelves.franchise_for(hub, id_))
            case ['GET', 'new']:
                return ok({'items': hub.new_releases(skip(qs))})
            case ['GET', 'browse', 'catalog', type_, cid]:
                return ok({'items': hub.catalog(addon_type(type_), cid, skip(qs))})
            case ['GET', 'browse', 'meta', type_, id_]:
                meta = hub.meta(addon_type(type_), imdb(id_))
                item = to_item(meta)
                eps = episodes(meta)
                hub.annotate([item], {item['id']: eps})
                return ok({**item, 'videos': [{'s': s, 'e': e, 'title': t} for s, e, t in eps]})
            case ['GET', 'browse', 'cast', type_, id_]:
                return ok(shelves.cast_for(hub, kind(type_), imdb(id_)))
            case ['GET', 'browse', 'streams' | 'subtitles' as what, type_, id_, *se]:
                type_, id_, (s, e) = kind(type_), imdb(id_), episode(se)
                vid = f'{id_}:{s}:{e}' if se else id_
                if what == 'subtitles':
                    return ok({'subtitles': hub.subtitles(type_, vid)})
                streams, relaxed = hub.ranked(type_, id_, s, e, hub.preset_for(qs))
                return ok({'streams': streams, 'relaxed': relaxed})
            case ['GET', 'search']:
                return ok({'items': hub.search(qs.get('q', ''))})
        raise HttpError(404, 'no such call')

    def loopback(self):
        return ipaddress.ip_address(self.client_address[0]).is_loopback

    def local_or_paired(self):
        return self.loopback() or self.hub.authorized(self.headers.get('Authorization'))

    def trusted(self, nonce):
        """A paired client, or the setup page presenting its nonce."""
        if not self.hub.authorized(self.headers.get('Authorization')) and not self.hub.setup_ok(nonce):
            raise HttpError(401, 'needs a paired device or the setup page')

    def log_message(self, *args):
        pass  # route() prints its own single line per request


def make_server(config_path, data_dir):
    hub = Hub(config_path, data_dir)
    handler = type('BoundHandler', (Handler,), {'hub': hub})
    server = ThreadingHTTPServer((hub.bind, hub.port), handler)
    hub.port = server.server_port
    OWN_PORTS.add(hub.port)
    server.beacon = onboarding.start_beacon(hub.bind, hub.discovery_port, hub.port, hub.name)
    return server


def main():
    os.umask(0o077)  # hub.sqlite and anything else the hub writes stay private to the hub user
    server = make_server(os.environ.get('HUB_CONFIG', '/var/lib/hub/hub.toml'), os.environ.get('HUB_DATA', '/var/lib/hub'))
    print(f'hub listening on {server.server_address[0]}:{server.server_address[1]}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
