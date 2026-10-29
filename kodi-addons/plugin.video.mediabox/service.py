"""Background service: Home shelves for the skin, resume points to the hub, first-run setup QR.

Window(Home) properties it keeps (read by skin.mediabox):
  mediabox.shelf.N.path / .title   N = 1..MAX_SHELVES, the visible rows of GET /shelves in order
  mediabox.count.N                 set by the plugin when it lists row N; "0" hides the row
  mediabox.refresh                 bumped after playback, or when the plugin sets mediabox.bump (after a keep),
                                   so every widget path changes and Kodi re-reads it
  mediabox.attention               a short reason when the hub can't be reached, else empty"""
import hashlib
import json
import os
import time

import xbmc
import xbmcgui
import xbmcvfs

from resources.lib import mbhub, qr
from resources.lib.mbhub import HubError, q

MAX_SHELVES = 12  # the skin has this many rows
SHELF_EVERY = 600  # seconds between /shelves reads
SAVE_EVERY = 60  # seconds between resume writes while playing
HOME = xbmcgui.Window(10000)
PROFILE = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.mediabox/')


def log(msg):
    xbmc.log(f'[mediabox] {msg}', xbmc.LOGINFO)


SHELVES = []


def bump():
    """New refresh counter: every widget path changes, so Kodi re-reads them (Continue after playback, say)."""
    HOME.setProperty('mediabox.refresh', str(int(HOME.getProperty('mediabox.refresh') or 0) + 1))
    show_shelves()


def show_shelves():
    r = HOME.getProperty('mediabox.refresh') or '0'
    for n in range(1, MAX_SHELVES + 1):
        s = SHELVES[n - 1] if n <= len(SHELVES) else None
        HOME.setProperty(f'mediabox.shelf.{n}.path', f"plugin://plugin.video.mediabox/shelf/{q(s['id'])}?row={n}&r={r}" if s else '')
        HOME.setProperty(f'mediabox.shelf.{n}.title', s['title'] if s else '')
        HOME.clearProperty(f'mediabox.count.{n}')  # the row shows (and so loads) until the plugin reports it empty


def publish_shelves():
    """True when the hub answered, else the error status (0 = unreachable, 401 = not paired) with mediabox.attention set."""
    try:
        SHELVES[:] = [s for s in mbhub.get('/shelves')['shelves'] if not s.get('hidden')]
        HOME.clearProperty('mediabox.attention')
    except HubError as e:
        log(f'shelves: {e.reason}')
        HOME.setProperty('mediabox.attention', 'Hub unreachable' if e.status == 0 else 'Hub: ' + e.reason)
        return e.status
    show_shelves()
    return True


class Player(xbmc.Player):
    """Writes PUT /resume for what the plugin started (it leaves the title in Window(Home) mediabox.playing)."""

    def __init__(self):
        super().__init__()
        self.cur, self.pos, self.total, self.saved_at = None, 0, 0, 0

    def onAVStarted(self):
        raw = HOME.getProperty('mediabox.playing')
        HOME.clearProperty('mediabox.playing')
        self.cur = json.loads(raw) if raw else None  # anything else Kodi plays is not ours to track
        self.pos, self.total, self.saved_at = 0, 0, 0

    def tick(self, now):
        if not self.cur or not self.isPlayingVideo():
            return
        try:
            self.pos, self.total = self.getTime(), self.getTotalTime()
        except RuntimeError:  # stopped between the check and the call
            return
        if now - self.saved_at >= SAVE_EVERY:
            self.saved_at = now
            self.save()

    def save(self, ended=False):
        if not self.cur or self.total <= 0:
            return
        pos = self.total if ended else self.pos
        path = f"/resume/{q(self.cur['id'])}" + ''.join(f'/{x}' for x in self.cur.get('se') or [])
        try:
            mbhub.call('PUT', path, {'positionMs': int(pos * 1000), 'durationMs': int(self.total * 1000)})
        except HubError as e:
            log(f'resume not saved: {e.reason}')

    def finish(self, ended):
        self.save(ended)
        self.cur = None
        bump()

    def onPlayBackStopped(self):
        self.finish(False)

    def onPlayBackEnded(self):
        self.finish(True)

    def onPlayBackError(self):
        self.cur = None


class SetupDialog(xbmcgui.WindowDialog):
    """Full-screen "Set up this box": QR of the hub's setup link and the link as text. Back hides it until the next start."""

    def __init__(self, link):
        super().__init__()
        self.closed = False
        w, h = self.getWidth(), self.getHeight()
        os.makedirs(PROFILE, exist_ok=True)
        bg = os.path.join(PROFILE, 'bg.png')
        with open(bg, 'wb') as f:
            f.write(qr.solid_png(255))
        side = int(h * 0.5)
        self.addControl(xbmcgui.ControlImage(0, 0, w, h, bg, colorDiffuse='FF05070F'))
        self.qr = xbmcgui.ControlImage(int(w * 0.08), int((h - side) / 2), side, side, '')
        self.addControl(self.qr)
        x, tw = int(w * 0.08) + side + int(w * 0.05), int(w * 0.45)
        y = int(h * 0.30)
        self.addControl(xbmcgui.ControlLabel(x, y, tw, 40, 'FIRST RUN', 'mb_mono', '0xFF9FD8FF'))
        self.addControl(xbmcgui.ControlLabel(x, y + int(h * 0.05), tw, 80, 'Set up this box', 'mb_display_l', '0xFFEEF3FF'))
        body = xbmcgui.ControlTextBox(x, y + int(h * 0.15), tw, int(h * 0.1), 'mb_ui', '0xFF9AA8C4')
        self.addControl(body)
        body.setText('Scan the code with your phone. You need your debrid key; nothing is typed on the TV.')
        self.addControl(xbmcgui.ControlLabel(x, y + int(h * 0.26), tw, 40, 'Or open this link on a phone on the same Wi-Fi:',
                                             'mb_ui_s', '0xFF9AA8C4'))
        self.link_label = xbmcgui.ControlLabel(x, y + int(h * 0.30), tw, 40, '', 'mb_code', '0xFFEEF3FF')  # not uppercased: the nonce is case-sensitive
        self.addControl(self.link_label)
        self.addControl(xbmcgui.ControlLabel(x, y + int(h * 0.38), tw, 40, 'This screen closes by itself when setup is done.',
                                             'mb_ui_s', '0xFF9AA8C4'))
        self.link = None
        self.update(link)

    def update(self, link):
        self.link = link
        path = os.path.join(PROFILE, 'setup-%s.png' % hashlib.sha256(link.encode()).hexdigest()[:12])  # new name: Kodi caches textures by path
        if not os.path.exists(path):
            for old in os.listdir(PROFILE):
                if old.startswith('setup-'):
                    os.remove(os.path.join(PROFILE, old))
            with open(path, 'wb') as f:
                f.write(qr.qr_png(link))
        self.qr.setImage(path, False)
        self.link_label.setLabel(link)

    def onAction(self, action):
        if action.getId() in (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU):
            self.closed = True
            self.close()


def setup_watch(monitor):
    link = mbhub.setup_link()
    if not link:
        return
    log('hub is not set up: showing the setup QR')
    dlg = SetupDialog(link)
    dlg.show()
    while not dlg.closed and not monitor.waitForAbort(5):
        link = mbhub.setup_link()
        if not link:
            break
        if link != dlg.link:
            dlg.update(link)
    if not dlg.closed:
        dlg.close()
        publish_shelves()
        bump()
    del dlg


def main():
    monitor, player = xbmc.Monitor(), Player()
    if publish_shelves() == 401 and mbhub.pair():  # a TV the hub doesn't know: show the pairing code at once
        publish_shelves()
    setup_watch(monitor)
    last = time.time()
    while not monitor.waitForAbort(1):  # 1 s: the position saved on stop is the last one read here
        now = time.time()
        player.tick(now)
        if HOME.getProperty('mediabox.bump'):
            HOME.clearProperty('mediabox.bump')
            bump()
        if now - last >= SHELF_EVERY:
            last = now
            publish_shelves()


main()
