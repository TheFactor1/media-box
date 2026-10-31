"""The TV interface's local bridge, 127.0.0.1 only: serves the page, adds the box's token to hub calls and hands the screen to mpv.

The page itself never holds the token or a stream URL. Python 3.12 standard library only.
Run: MB_HUB=http://127.0.0.1:8097 python3 -I shell.py
"""
import base64
import glob
import json
import os
import re
import select
import socket
import struct
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
REHEARSAL_SOCKET = os.environ.get('MB_REHEARSAL_SOCKET', '/run/mediabox/rehearsal.sock')
REHEARSAL_LIMIT = 25  # seconds the loading page waits for a film to open
READY_BUFFER = 5  # seconds of film held before the screen changes hands
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
LANGUAGES = {'en': 'English', 'eng': 'English', 'es': 'Spanish', 'spa': 'Spanish', 'fr': 'French', 'fre': 'French', 'fra': 'French', 'de': 'German',
             'ger': 'German', 'deu': 'German', 'it': 'Italian', 'ita': 'Italian', 'pt': 'Portuguese', 'por': 'Portuguese', 'ru': 'Russian', 'rus': 'Russian',
             'ja': 'Japanese', 'jpn': 'Japanese', 'ko': 'Korean', 'kor': 'Korean', 'zh': 'Chinese', 'chi': 'Chinese', 'zho': 'Chinese', 'ar': 'Arabic',
             'ara': 'Arabic', 'nl': 'Dutch', 'dut': 'Dutch', 'nld': 'Dutch', 'sv': 'Swedish', 'swe': 'Swedish', 'pl': 'Polish', 'pol': 'Polish',
             'tr': 'Turkish', 'tur': 'Turkish', 'hi': 'Hindi', 'hin': 'Hindi', 'und': 'Other'}


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
        self.stage = 'idle'  # what the loading page is waiting on: preparing, then ready or failed
        self.progress = 0
        self.note = None
        self.automatic = True
        self.chose_versions = False
        self.reached = (0, 0)
        self.cancelled = threading.Event()

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
        status, raw = hub('GET', f'/browse/streams/{type_}/{id_}{ep}{list_query()}')
        streams = [s for s in json.loads(raw).get('streams', []) if s.get('url')] if status == 200 else []
        if not isinstance(pick, int) or not 0 <= pick < len(streams):
            raise LookupError('that copy is no longer available')
        return type_, id_, ep, streams[pick]

    def start(self, req):
        chosen = self.choose(req)
        if NO_PLAY:
            self.stage = 'ready'
            return
        if not self.lock.acquire(blocking=False):
            raise LookupError('something is already playing')
        self.error = None
        self.stage, self.progress, self.note = 'preparing', 0, None
        self.automatic = req.get('version') is None
        self.chose_versions = False
        self.cancelled.clear()
        threading.Thread(target=self.run, args=chosen, daemon=True).start()

    def cancel_opening(self):
        """Back on the loading page. Too late once the screen is changing hands. Waits so Play can be pressed again straight away."""
        if self.stage != 'preparing':
            return False
        self.cancelled.set()
        if self.lock.acquire(timeout=2):
            self.lock.release()
        return True

    def open_a_copy(self, type_, id_, ep, stream, start):
        """The copy that opened, or None. A copy the viewer chose isn't swapped behind their back; the box's own pick falls back to its next best."""
        failed = stream
        if rehearse(stream, start, self):
            return stream
        if not self.automatic:
            return None
        status, raw = hub('GET', f'/browse/streams/{type_}/{id_}{ep}{prefs_query()}')
        for other in backups(json.loads(raw).get('streams', []) if status == 200 else [], failed.get('url')):
            if self.cancelled.is_set():
                return None
            self.note, self.progress = 'Trying another copy', 0
            if rehearse(other, start, self):
                return other
        return None

    def run(self, _type, id_, ep, stream):
        handed_over = False
        try:
            start = 0
            status, raw = hub('GET', f'/resume/{id_}{ep}')
            if status == 200:
                pos = json.loads(raw or b'{}').get('positionMs') or 0
                start = pos / 1000
            stream = self.open_a_copy(_type, id_, ep, stream, start)
            if self.cancelled.is_set():
                self.stage = 'idle'
                return
            if not stream:  # the interface stays up, so a bad copy costs no trip through a black screen
                self.error = "That copy wouldn't open. Try another version."
                self.stage = 'failed'
                return
            self.stage = 'ready'
            handed_over = True
            time.sleep(0.45)  # lets the page's lights-down finish
            subprocess.run(['systemctl', 'stop', UI_UNIT], check=False, timeout=30)
            subprocess.run(['systemctl', 'start', '--no-block', MPV_UNIT], check=False, timeout=30)
            if self.watch(_type, id_, ep, stream, start):
                self.state = after_film(self.state, *self.reached, self.chose_versions)
            else:
                self.error = "That copy wouldn't play."
        except Exception as e:  # the interface must come back whatever went wrong
            self.error = f"Playback failed ({type(e).__name__})."
            self.stage = 'failed'
        finally:
            if handed_over:
                subprocess.run(['systemctl', 'stop', MPV_UNIT], check=False, timeout=30)
                subprocess.run(['systemctl', 'start', '--no-block', UI_UNIT], check=False, timeout=30)
            self.lock.release()

    def watch(self, type_, id_, ep, stream, start):
        """Feeds mpv over its socket, so the URL is never on a command line or in a log. True once it really played."""
        sock = connect(MPV_SOCKET)
        if sock is None:
            return False
        played, position, duration, saved = False, 0, 0, time.monotonic()
        # The addons can be slow, and the film mustn't wait for them: the menu gains these whenever they arrive.
        found = []
        threading.Thread(target=lambda: found.append(addon_subtitles(type_, id_, ep)), daemon=True).start()
        finished = threading.Event()
        threading.Thread(target=remote_buttons, args=(finished,), daemon=True).start()
        with sock:
            for name, value in player_info(self.state, load_prefs()).items():
                send(sock, ['set_property', 'user-data/mediabox/' + name, value])
            feed(sock, stream, start)
            sock.settimeout(5)
            reader = sock.makefile('r', encoding='utf-8', errors='replace')
            while True:
                if found:
                    send(sock, ['set_property', 'user-data/mediabox/subtitles', found.pop()])
                send(sock, ['get_property', 'time-pos'], 1)
                send(sock, ['get_property', 'duration'], 2)
                try:
                    pending = {1, 2}
                    while pending:
                        line = reader.readline()
                        if not line:
                            raise EOFError
                        msg = json.loads(line)
                        if msg.get('event') == 'client-message' and msg.get('args') == ['mediabox-versions']:
                            self.chose_versions = True
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
        finished.set()
        self.reached = (position, duration)
        if played and duration:
            save(id_, ep, position, duration)
        return played


def backups(streams, failed_url, count=2):
    """The next best different copies with a link."""
    seen, out = {failed_url}, []
    for stream in streams:
        url = stream.get('url')
        if url and url not in seen:
            seen.add(url)
            out.append(stream)
    return out[:count]


# Remote buttons the terminal can't see, as Linux numbers them, with the mpv key each one becomes. Arrows, Enter and Esc stay with the terminal.
REMOTE_KEYS = {158: 'BS', 352: 'ENTER', 353: 'ENTER', 164: 'PLAYPAUSE', 207: 'PLAY', 200: 'PLAY', 119: 'PAUSE', 201: 'PAUSE', 128: 'STOP', 166: 'STOP',
               168: 'REWIND', 208: 'FORWARD', 358: 'i', 139: 'm', 370: 's', 392: 'a'}
INPUT_EVENT = struct.Struct('qqHHi')  # 64-bit Linux
EV_KEY = 1


def remote_key(kind, code, value):
    """The mpv key for one input event, or None. Presses and held repeats count, releases don't."""
    return REMOTE_KEYS.get(code) if kind == EV_KEY and value in (1, 2) else None


def remote_buttons(finished):
    """Passes media remote buttons to mpv while a film plays. Only the buttons in REMOTE_KEYS are looked at; nothing is stored or logged."""
    files = []
    for path in glob.glob('/dev/input/event*'):
        try:
            files.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
        except OSError:
            pass
    sock = connect(MPV_SOCKET, 5) if files else None
    try:
        while sock and not finished.is_set():
            ready, _, _ = select.select(files, [], [], 0.5)
            for fd in ready:
                raw = os.read(fd, INPUT_EVENT.size * 32)
                for _, _, kind, code, value in INPUT_EVENT.iter_unpack(raw[:len(raw) - len(raw) % INPUT_EVENT.size]):
                    key = remote_key(kind, code, value)
                    if key:
                        send(sock, ['keypress', key])
    except OSError:  # a remote unplugged, or mpv gone: the film's own keys still work
        pass
    finally:
        if sock:
            sock.close()
        for fd in files:
            os.close(fd)


def rehearsal_verdict(duration, position, cached, idle, expired):
    """go, wait or fail, from what mpv reports about a film opening off-screen. A slow film that has opened still goes when time runs out."""
    opened = bool(duration) and position is not None
    if opened and (idle or (cached or 0) >= READY_BUFFER):
        return 'go'
    if expired:
        return 'go' if opened else 'fail'
    return 'wait'


def rehearsal_progress(duration, position, cached, idle):
    """How far the loading page's rule has filled, 0 to 1: a quarter while opening, then by the film held."""
    if not duration or position is None:
        return 0.25
    return 1 if idle else min(1, 0.5 + 0.5 * (cached or 0) / READY_BUFFER)


def rehearse(stream, start, player):
    """Opens the film with no picture or sound while the interface keeps the screen. False only when the film itself won't open:
    if the rehearsal can't run at all, the film still gets its chance on screen."""
    try:
        proc = subprocess.Popen(['mpv', '--no-config', '--vo=null', '--ao=null', '--hwdec=no', '--pause', '--idle=once', '--ytdl=no', '--really-quiet', '--no-terminal',
                                 '--input-ipc-server=' + REHEARSAL_SOCKET], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return True
    try:
        sock = connect(REHEARSAL_SOCKET, 5)
        if sock is None:
            return True
        with sock:
            feed(sock, stream, start)
            sock.settimeout(5)
            reader = sock.makefile('r', encoding='utf-8', errors='replace')
            deadline = time.monotonic() + REHEARSAL_LIMIT
            try:
                while True:
                    report = [ask(sock, reader, name) for name in ('duration', 'time-pos', 'demuxer-cache-duration', 'demuxer-cache-idle')]
                    if player.cancelled.is_set():
                        return False
                    player.progress = rehearsal_progress(*report)
                    verdict = rehearsal_verdict(*report, time.monotonic() >= deadline)
                    if verdict != 'wait':
                        return verdict == 'go'
                    time.sleep(0.25)
            except (EOFError, OSError, ValueError):  # mpv quits by itself when the film won't load
                return False
    finally:
        proc.kill()
        proc.wait()


def feed(sock, stream, start):
    for name, value in (stream.get('headers') or {}).items():
        send(sock, ['change-list', 'http-header-fields', 'append', f'{name}: {value}'])
    if start > 0:
        send(sock, ['set_property', 'start', str(start)])
    send(sock, ['loadfile', stream['url']])


def ask(sock, reader, name):
    """One property's value, or None while mpv doesn't have it yet."""
    send(sock, ['get_property', name], 1)
    while True:
        line = reader.readline()
        if not line:
            raise EOFError
        msg = json.loads(line)
        if msg.get('request_id') == 1:
            return msg.get('data')


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


def deck(overview, most=110):
    """The line under the headline: the summary's first sentence if it fits on one line, else cut at a word."""
    text = ' '.join(str(overview or '').split())
    first = re.match(r'.+?[.!?](?=\s|$)', text)
    if first and len(first[0]) <= most:
        return first[0]
    return text if len(text) <= most else text[:most].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'


def player_info(state, prefs):
    """What the film's controls show, and which look they use."""
    ep = state.get('ep') or {}
    look = prefs.get('playerLook')
    return {'title': str((state.get('item') or {}).get('title') or ''),
            'kicker': f"Season {ep['s']}, episode {ep['e']}" if ep.get('s') and ep.get('e') else '',
            'deck': deck((state.get('item') or {}).get('overview')),
            'look': look if look in ('night', 'paper') else 'auto'}


def addon_subtitles(type_, id_, ep):
    status, raw = hub('GET', f'/browse/subtitles/{type_}/{id_}{ep}', timeout=20)
    try:
        return pick_subtitles(json.loads(raw).get('subtitles', [])) if status == 200 else []
    except (ValueError, AttributeError):
        return []


def pick_subtitles(subs):
    """A short menu from whatever the addons sent, which can run to hundreds: three per language, by language name.
    Addon data: the URL must be plain http(s), because mpv is told to fetch it."""
    by_language = {}
    for sub in subs:
        url, lang = sub.get('url'), sub.get('lang')
        if not (isinstance(url, str) and re.fullmatch(r'https?://[!-~]+', url, re.I)):
            continue
        lang = lang.lower()[:12] if isinstance(lang, str) and re.fullmatch(r'[A-Za-z-]+', lang) else 'und'
        by_language.setdefault(lang, []).append(url)
    out = []
    for lang in sorted(by_language, key=lambda x: LANGUAGES.get(x, x)):
        name = LANGUAGES.get(lang, lang.upper())
        for i, url in enumerate(by_language[lang][:3]):
            out.append({'url': url, 'lang': lang, 'label': name + (f' {i + 1}' if i else '')})
    return out[:40]


def stop_press(position, duration):
    """The line shown on return from a film left partway, or None for a quick exit or the very end."""
    if not duration or position < 60 or position / duration >= 0.95:
        return None
    seconds = int(position)
    at = f'{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}' if seconds >= 3600 else f'{seconds // 60}:{seconds % 60:02d}'
    left = max(1, round((duration - position) / 60))
    hours, minutes = left // 60, left % 60
    words = [f'{hours} hour{"s" if hours != 1 else ""}'] * bool(hours) + [f'{minutes} minute{"s" if minutes != 1 else ""}'] * bool(minutes)
    return f'Stop press. You stopped at {at}, with {" ".join(words)} left.'


def after_film(state, position, duration, chose_versions):
    """Where a film returns to: the title's own page with Play ready, not the copy list or a row. Back still leads where it did."""
    if chose_versions:
        return to_versions(state)
    item = state.get('item')
    if not item:
        return state
    target = 'show' if item.get('type') == 'series' else 'film'
    page, history = state.get('page'), list(state.get('history') or [])
    if page == 'versions':
        if history and history[-1].get('page') == target:
            history.pop()
    elif page != target:
        history.append({'page': page, 'item': item, 'ep': state.get('ep'), 'key': state.get('key')})
    return {**state, 'page': target, 'key': target + ':play', 'history': history, 'stopPress': stop_press(position, duration)}


def to_versions(state):
    """Other versions, pressed during the film: the interface comes back on the copies, with Back leading where it was."""
    if state.get('page') == 'versions':
        return {**state, 'key': None}
    back = {'page': state.get('page'), 'item': state.get('item'), 'ep': state.get('ep'), 'key': state.get('key')}
    return {**state, 'page': 'versions', 'key': None, 'history': [*(state.get('history') or []), back]}


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


def list_query():
    """Choosing a copy shows every copy: the speed setting decides the automatic pick, not what can be picked."""
    query = prefs_query()
    return query + ('&' if query else '?') + 'all=1'


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
            if path == '/play/status' and method == 'GET':
                out = {'stage': PLAYER.stage, 'progress': PLAYER.progress, 'note': PLAYER.note, 'error': PLAYER.error if PLAYER.stage == 'failed' else None}
                if PLAYER.stage == 'failed':
                    PLAYER.error, PLAYER.stage = None, 'idle'
                return self.reply_json(200, out)
            if path == '/play/cancel' and method == 'POST':
                return self.reply_json(200, {'cancelled': PLAYER.cancel_opening()})
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
                target = target.split('?')[0] + list_query()
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
