"""Hub client: address, token, JSON calls, TV pairing. Standard library + Kodi modules only."""
import base64
import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon
import xbmcgui

ADDON_ID = 'plugin.video.mediabox'
TOKEN_FILE = '/var/lib/hub/token'  # 32 raw bytes, written by the hub on this box


class HubError(Exception):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status, self.reason = status, reason


def addon():
    return xbmcaddon.Addon(ADDON_ID)  # fresh each time: a cached Addon keeps stale settings in the service


def base():
    return (addon().getSettingString('hub_url') or 'http://127.0.0.1:8097').strip().rstrip('/')


def is_local(url):
    host = urllib.parse.urlsplit(url).hostname or ''
    try:
        return host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def token():
    a = addon()
    if a.getSettingString('token') and a.getSettingString('token_hub') == base():  # paired with this very hub
        return a.getSettingString('token')
    # The token file belongs to the hub on this box, so it is only sent to a hub on this box.
    if is_local(base()):
        try:
            with open(TOKEN_FILE, 'rb') as f:
                raw = f.read()
            if len(raw) == 32:
                return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()
        except OSError:
            pass
    return ''


def call(method, path, body=None, auth=True, timeout=20):
    """(status, parsed JSON). Raises HubError with the hub's own reason, or status 0 when unreachable."""
    req = urllib.request.Request(base() + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={'Accept': 'application/json', 'Content-Type': 'application/json',
                                          'User-Agent': 'mediabox-kodi/0.1'})
    t = token() if auth else ''
    if t:
        req.add_header('Authorization', 'Bearer ' + t)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            return r.status, json.loads(data) if data else None
    except urllib.error.HTTPError as e:
        try:
            reason = json.loads(e.read()).get('reason') or e.reason
        except (ValueError, AttributeError, OSError):
            reason = e.reason
        raise HubError(e.code, str(reason)) from None
    except (OSError, ValueError) as e:  # URLError, timeouts and bad JSON
        raise HubError(0, f"can't reach the hub ({type(e).__name__})") from None


def get(path, **kw):
    return call('GET', path, **kw)[1]


def q(s):
    return urllib.parse.quote(str(s), safe='')


def setup_link():
    """The setup page URL while the hub has never been set up, else None.

    /setup/link also answers for 10 minutes after setup finishes or "Set up again" is pressed; only an
    unconfigured hub returns it with until = null, so that is the "setup not done" signal."""
    try:
        r = get('/setup/link', timeout=5)
    except HubError:
        return None
    return r['url'] if isinstance(r, dict) and r.get('url') and r.get('until') is None else None


def pair():
    """TV-side pairing: ask for a code, show it, poll until a phone or paired device approves. True when paired."""
    a = addon()
    try:
        r = call('POST', '/pair/request', {'name': xbmc.getInfoLabel('System.FriendlyName') or 'TV'}, auth=False)[1]
    except HubError as e:
        xbmcgui.Dialog().ok('Pair with your hub', f'{base()}\n{e.reason}')
        return False
    code = f"{r['code'][:3]} {r['code'][3:]}"
    where = r.get('setupUrl') or "the hub's setup page on your phone"
    dlg = xbmcgui.DialogProgress()
    dlg.create('Pair with your hub', f'Pairing code [B]{code}[/B]\nApprove it on {where}.')
    monitor, start = xbmc.Monitor(), time.time()
    try:
        while not dlg.iscanceled() and not monitor.abortRequested():
            try:
                status, body = call('GET', f"/pair/request/{q(r['requestId'])}", auth=False)
            except HubError as e:
                xbmcgui.Dialog().ok('Pair with your hub', e.reason)
                return False
            if status == 200 and body.get('token'):
                a.setSettingString('token', body['token'])
                a.setSettingString('token_hub', base())  # a token is only ever sent back to the hub that issued it
                xbmcgui.Dialog().notification('Mediabox', 'Paired with your hub', time=3000)
                return True
            dlg.update(min(99, int((time.time() - start) / 6)))  # the request lives 10 minutes
            monitor.waitForAbort(2)
    finally:
        dlg.close()
    return False
