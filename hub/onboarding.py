"""First-run setup: LAN discovery beacon, debrid key check, and provisioning a user config in AIOStreams.

AIOStreams API: docs/AIOSTREAMS-API.md (POST /api/v1/user {config, password} -> data {uuid, encryptedPassword}).
"""
import copy
import ipaddress
import json
import re
import secrets
import socket
import string
import threading
import urllib.error
import urllib.parse
import urllib.request

PROVIDERS = {'torbox': 'TorBox', 'realdebrid': 'Real-Debrid', 'alldebrid': 'AllDebrid', 'premiumize': 'Premiumize'}
STARTER = 'builtin.debrid-starter'


class ProvisionError(Exception):
    pass


# Each provider's "who am I" call and how it says the key worked. All four take the key as a Bearer header, so it
# never sits in a URL. Docs: TorBox https://api.torbox.app/openapi.json (GET /v1/api/user/me, {success, data});
# Real-Debrid https://api.real-debrid.com/ (GET /rest/1.0/user, 401 on a bad token); AllDebrid https://docs.alldebrid.com/
# (GET /v4/user, {status: "success"} or error AUTH_BAD_APIKEY); Premiumize https://www.premiumize.me/api
# (GET /api/account/info, {status: "success"}, a bad key is HTTP 200 with status "error").
KEY_CHECKS = {
    'torbox': ('https://api.torbox.app', '/v1/api/user/me', lambda j: j.get('success') is True),
    'realdebrid': ('https://api.real-debrid.com', '/rest/1.0/user', lambda j: 'username' in j or isinstance(j.get('id'), int)),
    'alldebrid': ('https://api.alldebrid.com', '/v4/user', lambda j: j.get('status') == 'success'),
    'premiumize': ('https://www.premiumize.me', '/api/account/info', lambda j: j.get('status') == 'success'),
}


def key_looks_valid(provider, key):
    return provider in PROVIDERS and bool(re.fullmatch(r'[A-Za-z0-9_-]{16,64}', str(key or '')))


def check_key(provider, key, base=None):
    """'ok', 'rejected' (the provider said no) or 'unreachable' (no clear answer). Calls only the chosen provider.
    `base` replaces the provider's host; only tests set it."""
    if not key_looks_valid(provider, key):
        return 'rejected'
    default, path, accepted = KEY_CHECKS[provider]
    req = urllib.request.Request((base or default) + path, headers={'Authorization': f'Bearer {key}', 'User-Agent': 'mediabox-hub/1'})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            answer = json.loads(r.read(2**20))
    except urllib.error.HTTPError as e:
        return 'rejected' if e.code in (401, 403) else 'unreachable'  # 429/5xx: the provider couldn't say
    except (OSError, ValueError):
        return 'unreachable'
    return 'ok' if isinstance(answer, dict) and accepted(answer) else 'rejected'


def start_beacon(bind, port, http_port, name):
    """Answer the datagram `MEDIABOX?` from a LAN address with `MEDIABOX 1 <port> <name>`; ignore everything else.

    The request must be padded (spaces) to at least the reply's length, 64 bytes is always enough, so a spoofed
    source can't use the beacon as an amplifier."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('' if bind in ('', '0.0.0.0') else bind, port))  # '' also receives broadcasts
    reply = f'MEDIABOX 1 {http_port} {re.sub(r"[^ -~]", "", name)[:40]}'.encode()

    def run():
        while True:
            try:
                data, addr = sock.recvfrom(64)
            except ConnectionResetError:  # Windows reports an earlier reply's ICMP "port unreachable" here
                continue
            except OSError:  # socket closed
                return
            if data.strip() == b'MEDIABOX?' and len(data) >= len(reply) and not ipaddress.ip_address(addr[0]).is_global:
                try:
                    sock.sendto(reply, addr)
                except OSError:
                    pass

    threading.Thread(target=run, daemon=True).start()
    return sock


def lan_ip():
    """This host's LAN address, for the QR. A UDP connect only picks a route; no packet leaves the box."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(('10.255.255.255', 1))
            return s.getsockname()[0]
        except OSError:
            return '127.0.0.1'


def api(base, method, path, body=None):
    req = urllib.request.Request(base.rstrip('/') + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', 'User-Agent': 'mediabox-hub/1'})
    host = urllib.parse.urlsplit(base).hostname
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            envelope = json.loads(r.read(4 * 2**20))  # capped: truncated JSON fails as ValueError
    except urllib.error.HTTPError as e:
        try:
            envelope = json.loads(e.read(2**20))
        except ValueError:
            raise ProvisionError(f'AIOStreams at {host} answered HTTP {e.code}') from None
    except (OSError, ValueError) as e:
        raise ProvisionError(f'could not reach AIOStreams at {host} ({type(e).__name__})') from None
    if not isinstance(envelope, dict) or not envelope.get('success'):
        err = envelope.get('error') if isinstance(envelope, dict) and isinstance(envelope.get('error'), dict) else {}
        detail = envelope.get('detail') if isinstance(envelope, dict) else None
        raise ProvisionError(str(err.get('message') or detail or 'AIOStreams refused the request')[:300])
    return envelope.get('data')


def starter_config(base):
    # The user's own AIOStreams supplies its official starter template, so the hub ships no source list of its own.
    # shortcut: assumes GET /api/v1/templates lists full {metadata, config} objects (unverified); check on a real instance
    data = api(base, 'GET', '/api/v1/templates')
    items = data if isinstance(data, list) else (data or {}).get('templates', []) if isinstance(data, dict) else []
    for t in items:
        if isinstance(t, dict) and (t.get('metadata') or {}).get('id') == STARTER and isinstance(t.get('config'), dict):
            return t['config']
    raise ProvisionError('this AIOStreams has no debrid starter template; paste an addon link instead')


def provision(base, provider, key):
    """Create an AIOStreams user from its starter template with this debrid key. The key is passed through, never kept."""
    config = copy.deepcopy(starter_config(base))
    config['services'] = [{'id': provider, 'enabled': True, 'credentials': {'apiKey': key}}]
    if 'template_placeholder' in str(config.get('tmdbApiKey', '')):
        del config['tmdbApiKey']  # optional; a placeholder would fail validation
        # AIOStreams 2.37 refuses these without a TMDB key; the hub's own release matching covers the same ground.
        for feature in ('titleMatching', 'yearMatching', 'digitalReleaseFilter'):
            if isinstance(config.get(feature), dict):
                config[feature]['enabled'] = False
    password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    data = api(base, 'POST', '/api/v1/user', {'config': config, 'password': password})
    uuid, enc = (data or {}).get('uuid'), (data or {}).get('encryptedPassword')
    if not isinstance(uuid, str) or not isinstance(enc, str) or not uuid or not enc:
        raise ProvisionError('AIOStreams did not return a user id')
    return {'uuid': uuid, 'encryptedPassword': enc, 'password': password}


def addon_urls(base, account, public_host):
    """(manifest URL the hub uses, configure URL for the user's phone). A loopback AIOStreams is shown at the hub's own host."""
    q = lambda s: urllib.parse.quote(s, safe='')
    path = f'/stremio/{q(account["uuid"])}/{q(account["encryptedPassword"])}'
    parts = urllib.parse.urlsplit(base.rstrip('/'))
    public = parts
    if parts.hostname and ipaddress.ip_address(socket.gethostbyname(parts.hostname)).is_loopback and public_host:
        public = parts._replace(netloc=f'{public_host}:{parts.port}' if parts.port else public_host)
    return base.rstrip('/') + path + '/manifest.json', urllib.parse.urlunsplit(public) + path + '/configure'


def handle(hub, body, public_host):
    """POST /setup actions. The debrid key only passes through here on its way to AIOStreams."""
    if not isinstance(body, dict):
        raise ValueError('body must be a JSON object')
    action, provider, key = body.get('action'), body.get('provider'), str(body.get('key') or '').strip()
    if action in ('check', 'provision') and provider not in PROVIDERS:
        raise ValueError('pick your debrid service')
    if action == 'check':
        result, name = check_key(provider, key, hub.provider_base), PROVIDERS[provider]
        return {'result': result, 'message': {'ok': f'{name} accepted the key.',
                                              'rejected': f"That key wasn't accepted by {name}.",
                                              'unreachable': f"Couldn't reach {name}; continue anyway?"}[result]}
    if action == 'provision':  # the page runs `check` first; "continue anyway" sends unverified: true
        if not key_looks_valid(provider, key):
            raise ValueError('add a valid debrid API key first')
        account = provision(hub.aiostreams_url, provider, key)
        manifest, configure = addon_urls(hub.aiostreams_url, account, public_host)
        hub.set_setting('aiostreams', account)  # includes the generated password, encrypted with the other settings
        hub.set_setting('debrid_unverified', body.get('unverified') is True)  # for the status screen
        hub.use_stream_addon(manifest)
        return {'ok': True, 'configureUrl': configure}
    if action == 'link':
        return hub.use_stream_addon(body.get('manifest'))
    if action == 'internet':
        choice = body.get('preset')
        if choice != 'auto' and choice not in hub.presets:
            raise ValueError('pick one of the listed speeds')
        hub.set_setting('preset', choice)
        if choice != 'auto':
            hub.preset = choice
        return {'ok': True}
    if action == 'tmdb':  # optional: franchise collections. Empty clears it.
        if not key:
            hub.sql("DELETE FROM settings WHERE name = 'tmdb_api_key'")
            return {'ok': True, 'tmdb': False}
        if not re.fullmatch(r'[0-9a-f]{32}|eyJ[\w.-]{20,1000}', key):
            raise ValueError('that is not a TMDB API key (32 characters) or read access token (starts with eyJ)')
        hub.set_setting('tmdb_api_key', key)
        return {'ok': True, 'tmdb': True}
    if action == 'done':
        return hub.finish_setup()
    raise ValueError('unknown setup step')
