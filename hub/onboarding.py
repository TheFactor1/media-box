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


def key_looks_valid(provider, key):
    # shortcut: format check only, as AIOStreams itself does at create time; call the provider's API if bad keys turn up
    return provider in PROVIDERS and bool(re.fullmatch(r'[A-Za-z0-9_-]{16,64}', str(key or '')))


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
    if action in ('test', 'provision') and provider not in PROVIDERS:
        raise ValueError('pick your debrid service')
    if action == 'test':
        ok = key_looks_valid(provider, key)
        return {'ok': ok, 'message': 'That looks like a valid key. It is checked for real when sources are set up.'
                if ok else f'That does not look like a {PROVIDERS[provider]} API key; copy it again from their site.'}
    if action == 'provision':
        if not key_looks_valid(provider, key):
            raise ValueError('add a valid debrid API key first')
        account = provision(hub.aiostreams_url, provider, key)
        manifest, configure = addon_urls(hub.aiostreams_url, account, public_host)
        hub.set_setting('aiostreams', account)  # includes the generated password, encrypted with the other settings
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
    if action == 'done':
        return hub.finish_setup()
    raise ValueError('unknown setup step')
