"""The TV interface's local bridge, 127.0.0.1 only: serves the page, adds the box's token to hub calls and hands the screen to mpv.

The page itself never holds the token or a stream URL. Python 3.12 standard library only.
Run: MB_HUB=http://127.0.0.1:8097 python3 -I shell.py
"""
import base64
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HUB = os.environ.get('MB_HUB', 'http://127.0.0.1:8097')
PORT = int(os.environ.get('MB_PORT', '8099'))
TOKEN_FILE = os.environ.get('MB_TOKEN_FILE', '/var/lib/hub/token')
MPV_SOCKET = os.environ.get('MB_MPV_SOCKET', '/run/mediabox/mpv.sock')
PREFS_FILE = os.environ.get('MB_PREFS', '/var/lib/media/ui-prefs.json')
NO_PLAY = bool(os.environ.get('MB_NO_PLAY'))  # for trying the page off the box: Play only reports what it would do
UI_UNIT, MPV_UNIT = 'mediabox-ui.service', 'mediabox-mpv.service'
SAVE_EVERY = 30  # seconds between resume writes while playing
MAX_BODY = 64 * 1024
HOSTS = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}

STATIC = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
    '/app.css': ('app.css', 'text/css; charset=utf-8'),
    '/layout.js': ('layout.js', 'text/javascript; charset=utf-8'),
    '/fonts/playfair.ttf': ('fonts/playfair.ttf', 'font/ttf'),
    '/fonts/source-serif.ttf': ('fonts/source-serif.ttf', 'font/ttf'),
}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; connect-src 'self'; img-src https: data:; base-uri 'none'; form-action 'none'"

# Only what browsing needs: pairing, setup and device management stay off this path.
ALLOWED = re.compile(r'^(health|shelves|shelf/[^/]+|library(/[^/]+)?|new|keep|presets|search|collections(/.*)?|franchise/[^/]+'
                     r'|resume/[^/]+(/\d+/\d+)?|browse/(catalogs|catalog/[^/]+/[^/]+|meta/[^/]+/[^/]+|cast/[^/]+/[^/]+|streams/[^/]+/[^/]+(/\d+/\d+)?))$')
IMDB = re.compile(r'^tt\d+$')


def token():
    if os.environ.get('MB_TOKEN'):
        return os.environ['MB_TOKEN']
    with open(TOKEN_FILE, 'rb') as f:
        return base64.urlsafe_b64encode(f.read()).rstrip(b'=').decode()


def hub(method, path, body=None, timeout=60):
    """(status, raw bytes) from the hub; status 0 when it can't be reached."""
    req = urllib.request.Request(HUB + path, method=method, data=body,
                                 headers={'Authorization': 'Bearer ' + token(), 'Content-Type': 'application/json', 'Accept': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (OSError, ValueError) as e:
        return 0, json.dumps({'reason': f"can't reach the hub ({type(e).__name__})"}).encode()


def allowed(path):
    """The hub path for a page's /api/ request, or None. Rejects anything that could climb out of the list."""
    parsed = urllib.parse.urlsplit(path)
    rest = parsed.path.removeprefix('/api/')
    if rest == parsed.path or '..' in rest or '//' in rest or not ALLOWED.match(rest):
        return None
    return '/' + rest + ('?' + parsed.query if parsed.query else '')


def episode(s, e):
    if s is None and e is None:
        return ''
    if not (isinstance(s, int) and isinstance(e, int)):
        raise ValueError('bad episode')
    return f'/{s}/{e}'


class Player:
    """One film at a time: stops the interface, plays in mpv on the same screen, writes the position back, restarts the interface."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {}      # the page's place, kept here because Chromium is killed for every film
        self.error = None

    def choose(self, req):
        """(type, id, episode path, {url, headers}) from what the page asked for. The page sends an id, never a URL."""
        id_, type_ = req.get('id'), req.get('type')
        if not (isinstance(id_, str) and IMDB.match(id_)) or type_ not in ('movie', 'series'):
            raise ValueError('bad title')
        ep = episode(req.get('s'), req.get('e'))
        pick = req.get('version')
        query = prefs_query()
        if pick is None:
            status, raw = hub('GET', f'/play/{id_}{ep}{query}{"&" if query else "?"}json=1')
            if status != 200:
                raise LookupError(json.loads(raw or b'{}').get('reason') or "couldn't find a copy")
            return type_, id_, ep, json.loads(raw)
        status, raw = hub('GET', f'/browse/streams/{type_}/{id_}{ep}{query}')
        streams = [s for s in json.loads(raw).get('streams', []) if s.get('url')] if status == 200 else []
        if not isinstance(pick, int) or not 0 <= pick < len(streams):
            raise LookupError('that copy is no longer available')
        return type_, id_, ep, streams[pick]

    def start(self, req):
        chosen = self.choose(req)
        if NO_PLAY:
            return
        if not self.lock.acquire(blocking=False):
            raise LookupError('something is already playing')
        self.error = None
        threading.Thread(target=self.run, args=chosen, daemon=True).start()

    def run(self, _type, id_, ep, stream):
        try:
            start = 0
            status, raw = hub('GET', f'/resume/{id_}{ep}')
            if status == 200:
                pos = json.loads(raw or b'{}').get('positionMs') or 0
                start = pos / 1000
            time.sleep(0.45)  # lets the page's lights-down finish
            subprocess.run(['systemctl', 'stop', UI_UNIT], check=False, timeout=30)
            subprocess.run(['systemctl', 'start', '--no-block', MPV_UNIT], check=False, timeout=30)
            if not self.watch(id_, ep, stream, start):
                self.error = "That copy wouldn't play."
        except Exception as e:  # the interface must come back whatever went wrong
            self.error = f"Playback failed ({type(e).__name__})."
        finally:
            subprocess.run(['systemctl', 'stop', MPV_UNIT], check=False, timeout=30)
            subprocess.run(['systemctl', 'start', '--no-block', UI_UNIT], check=False, timeout=30)
            self.lock.release()

    def watch(self, id_, ep, stream, start):
        """Feeds mpv over its socket, so the URL is never on a command line or in a log. True once it really played."""
        sock = connect(MPV_SOCKET)
        if sock is None:
            return False
        played, position, duration, saved = False, 0, 0, time.monotonic()
        with sock:
            for name, value in (stream.get('headers') or {}).items():
                send(sock, ['change-list', 'http-header-fields', 'append', f'{name}: {value}'])
            if start > 0:
                send(sock, ['set_property', 'start', str(start)])
            send(sock, ['loadfile', stream['url']])
            sock.settimeout(5)
            reader = sock.makefile('r', encoding='utf-8', errors='replace')
            while True:
                send(sock, ['get_property', 'time-pos'], 1)
                send(sock, ['get_property', 'duration'], 2)
                try:
                    pending = {1, 2}
                    while pending:
                        line = reader.readline()
                        if not line:
                            raise EOFError
                        msg = json.loads(line)
                        if msg.get('request_id') in pending:
                            pending.discard(msg['request_id'])
                            if isinstance(msg.get('data'), (int, float)):
                                if msg['request_id'] == 1:
                                    position, played = msg['data'], True
                                else:
                                    duration = msg['data']
                except (EOFError, OSError, ValueError):
                    break
                if played and duration and time.monotonic() - saved >= SAVE_EVERY:
                    save(id_, ep, position, duration)
                    saved = time.monotonic()
                time.sleep(2)
        if played and duration:
            save(id_, ep, position, duration)
        return played


def connect(path, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.connect(path)
            return sock
        except OSError:
            sock.close()
            time.sleep(0.2)
    return None


def send(sock, command, request_id=0):
    sock.sendall(json.dumps({'command': command, 'request_id': request_id}).encode() + b'\n')


def save(id_, ep, position, duration):
    hub('PUT', f'/resume/{id_}{ep}', json.dumps({'positionMs': int(position * 1000), 'durationMs': int(duration * 1000)}).encode(), timeout=10)


def strip_urls(raw):
    """Stream URLs carry the debrid account, so the page gets the ranking but never the links; it plays by position."""
    data = json.loads(raw)
    for stream in data.get('streams', []):
        stream['playable'] = bool(stream.pop('url', None))
        stream.pop('headers', None)
    return json.dumps(data).encode()


def prefs_query():
    """The viewer's speed and ready-only choice, as the hub wants them. Ready-only is on unless turned off."""
    prefs = load_prefs()
    args = {}
    if isinstance(prefs.get('preset'), str) and prefs['preset']:
        args['preset'] = prefs['preset']
    if prefs.get('readyOnly', True):
        args['ready'] = '1'
    return '?' + urllib.parse.urlencode(args) if args else ''


def qr_svg(url):
    from qrcodegen import QrCode
    code = QrCode.encode_text(url, QrCode.Ecc.MEDIUM)
    n = code.get_size()
    cells = ''.join(f'M{x + 4} {y + 4}h1v1h-1z' for y in range(n) for x in range(n) if code.get_module(x, y))
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n + 8} {n + 8}"><rect width="100%" height="100%" fill="#fff"/><path d="{cells}" fill="#000"/></svg>'
    return 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode()


def load_prefs():
    try:
        with open(PREFS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(data):
    tmp = PREFS_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f)
    os.replace(tmp, PREFS_FILE)


def setup_qr():
    """The setup link and its QR as SVG while the hub has never been set up, else nulls."""
    status, raw = hub('GET', '/setup/link', timeout=5)
    info = json.loads(raw or b'{}') if status == 200 else {}
    if not info.get('url') or info.get('until') is not None:
        return {'url': None, 'svg': None}
    return {'url': info['url'], 'svg': qr_svg(info['url'])}


PLAYER = Player()


class Handler(BaseHTTPRequestHandler):
    server_version = 'mediabox-shell'
    sys_version = ''

    def log_message(self, *_):  # paths can name what's being watched; nothing about that goes in a log
        pass

    def reply(self, status, body=b'', kind='application/json'):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def reply_json(self, status, value):
        self.reply(status, json.dumps(value).encode())

    def body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length > MAX_BODY:
            raise ValueError('too large')
        return self.rfile.read(length) if length else None

    def guarded(self):
        """Another site's page in a local browser, or a rebound name, gets nothing."""
        if self.headers.get('Host') not in HOSTS:
            self.reply_json(421, {'reason': 'unknown host'})
            return False
        origin = self.headers.get('Origin')
        if origin and urllib.parse.urlsplit(origin).netloc not in HOSTS:
            self.reply_json(403, {'reason': 'wrong origin'})
            return False
        return True

    def handle_any(self, method):
        if not self.guarded():
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            if method == 'GET' and path in STATIC:
                name, kind = STATIC[path]
                with open(os.path.join(HERE, name), 'rb') as f:
                    return self.reply(200, f.read(), kind)
            if path == '/state':
                if method == 'POST':
                    PLAYER.state = json.loads(self.body() or b'{}')
                    return self.reply_json(200, {'ok': True})
                out = {'state': PLAYER.state, 'error': PLAYER.error}
                PLAYER.error = None
                return self.reply_json(200, out)
            if path == '/prefs':
                if method == 'POST':
                    body = json.loads(self.body() or b'{}')
                    if not isinstance(body, dict):
                        raise ValueError('bad prefs')
                    save_prefs({**load_prefs(), **body})
                return self.reply_json(200, load_prefs())
            if path == '/source-qr' and method == 'POST':
                # Opens the phone page for 10 minutes, straight at "paste your AIOStreams link": nobody types a link on a TV.
                status, raw = hub('POST', '/setup/open', b'{}')
                info = json.loads(raw or b'{}')
                if status != 200 or not info.get('url'):
                    return self.reply_json(status or 502, {'reason': info.get('reason') or "couldn't open the setup page"})
                url = info['url'] + '&add=1'
                return self.reply_json(200, {'url': url, 'svg': qr_svg(url)})
            if path == '/setup-qr' and method == 'GET':
                return self.reply_json(200, setup_qr())
            if path == '/play' and method == 'POST':
                try:
                    PLAYER.start(json.loads(self.body() or b'{}'))
                except (LookupError, ValueError) as e:
                    return self.reply_json(409, {'reason': str(e)})
                return self.reply_json(200, {'ok': True})
            target = allowed(self.path)
            if target is None:
                return self.reply_json(404, {'reason': 'no such call'})
            if '/browse/streams/' in target:
                target = target.split('?')[0] + prefs_query()
            status, raw = hub(method, target, self.body())
            if status == 200 and '/browse/streams/' in target:
                raw = strip_urls(raw)
            self.reply(status or 502, raw)
        except (OSError, ValueError) as e:
            self.reply_json(500, {'reason': type(e).__name__})

    def do_GET(self):
        self.handle_any('GET')

    def do_POST(self):
        self.handle_any('POST')

    def do_PUT(self):
        self.handle_any('PUT')

    def do_PATCH(self):
        self.handle_any('PATCH')

    def do_DELETE(self):
        self.handle_any('DELETE')


if __name__ == '__main__':
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
