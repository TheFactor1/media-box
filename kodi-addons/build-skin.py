#!/usr/bin/env python3
"""Builds skin.mediabox = the installed Estuary + our overlay (kodi-addons/skin.mediabox/).

    python3 build-skin.py /usr/share/kodi/addons/skin.estuary /usr/share/kodi/addons/skin.mediabox

Estuary is copied whole (its LICENSE.txt included), so its windows, dialogs and settings come with every Kodi
update; then our files are laid on top and four Estuary files get small, checked edits:
  addon.xml         ours, with Estuary's <requires> and resolutions filled in
  xml/Includes.xml  + <include file="Includes_Mediabox.xml"/>
  xml/Font.xml      Default fontset: Noto → Instrument Sans / Geist Mono; our mb_* fonts added to every fontset
  colors/defaults.xml  Space palette; Estuary's colour names kept, ours added
Any edit that no longer matches Estuary fails the build instead of shipping a half-patched skin."""
import os
import re
import shutil
import sys

OVERLAY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'skin.mediabox')

# name, file, size, style. Sizes are 1080p pixels.
FONTS = [('mb_display_l', 'Orbitron-Medium.ttf', 64, ''), ('mb_display', 'Orbitron-Medium.ttf', 56, ''),
         ('mb_display_s', 'Orbitron-Medium.ttf', 24, ''), ('mb_tab', 'InstrumentSans-Regular.ttf', 30, ''),
         ('mb_ui', 'InstrumentSans-Regular.ttf', 28, ''), ('mb_ui_s', 'InstrumentSans-Regular.ttf', 24, ''),
         ('mb_ui_b', 'InstrumentSans-SemiBold.ttf', 23, ''), ('mb_mono', 'GeistMono-Regular.ttf', 21, 'uppercase'),
         ('mb_mono_s', 'GeistMono-Regular.ttf', 16, 'uppercase'), ('mb_code', 'GeistMono-Regular.ttf', 21, '')]

# Space look (docs/prototype.html, data-look="space"): #05070f page, deep blue panels, #9fd8ff accent, amber for attention.
COLORS = {'primary_background': 'FF0C1A3C', 'secondary_background': '33203A6E', 'dialog_tint': 'FF0A1124',
          'dialog_header_tint': 'FF142650', 'background': 'FF05070F', 'blue': 'FF9FD8FF', 'button_focus': 'FF2F6F9E',
          'button_alt_focus': '802F6F9E', 'selected': 'FFFFB547', 'white': 'FFEEF3FF', 'grey': 'FF9AA8C4',
          'mb_bg': 'FF05070F', 'mb_fg': 'FFEEF3FF', 'mb_muted': 'FF9AA8C4', 'mb_dim': 'FF5E6B88',
          'mb_accent': 'FF9FD8FF', 'mb_amber': 'FFFFB547'}


def edit(path, fn):
    with open(path, encoding='utf-8') as f:
        old = f.read()
    new = fn(old)
    if new == old:
        sys.exit(f'build-skin: {path} did not change; Estuary moved on, update build-skin.py')
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(new)


def one(pattern, text, what):
    m = re.findall(pattern, text, re.S)
    if len(m) != 1:
        sys.exit(f'build-skin: expected one {what} in Estuary, found {len(m)}')
    return m[0]


def build(estuary, dest):
    with open(os.path.join(estuary, 'addon.xml'), encoding='utf-8') as f:
        est = f.read()
    version = one(r'<addon id="skin.estuary" version="([^"]+)"', est, 'version')
    requires = one(r'<requires>\n(.*?)\n\s*</requires>', est, '<requires>')
    res = '\n'.join(re.findall(r'\t*<res [^>]+/>', est))
    if not res:
        sys.exit('build-skin: no <res> in Estuary addon.xml')

    if os.path.exists(dest):
        shutil.rmtree(dest)
    shutil.copytree(estuary, dest)
    for root, _, files in os.walk(OVERLAY):
        rel = os.path.relpath(root, OVERLAY)
        os.makedirs(os.path.join(dest, rel), exist_ok=True)
        for name in files:
            shutil.copy2(os.path.join(root, name), os.path.join(dest, rel, name))

    edit(os.path.join(dest, 'addon.xml'), lambda s: s.replace('@ESTUARY_VERSION@', version)
         .replace('@ESTUARY_REQUIRES@', requires).replace('@ESTUARY_RES@', res))
    edit(os.path.join(dest, 'xml', 'Includes.xml'),
         lambda s: s.replace('<includes>\n', '<includes>\n\t<include file="Includes_Mediabox.xml" />\n', 1))

    def fonts(s):
        ours = ''.join(f'\t\t<font>\n\t\t\t<name>{n}</name>\n\t\t\t<filename>{f}</filename>\n\t\t\t<size>{z}</size>\n'
                       + (f'\t\t\t<style>{st}</style>\n' if st else '') + '\t\t</font>\n' for n, f, z, st in FONTS)
        default = one(r'(<fontset id="Default".*?</fontset>)', s, 'Default fontset')
        # Estuary's bold faces are NotoSans with <style>bold</style>: use the real SemiBold cut instead of faux bold.
        d = re.sub(r'<filename>NotoSans-Regular\.ttf</filename>(\s*<size>\d+</size>\s*)<style>bold</style>',
                   r'<filename>InstrumentSans-SemiBold.ttf</filename>\1<style>normal</style>', default)
        d = d.replace('NotoSans-Regular.ttf', 'InstrumentSans-Regular.ttf').replace('NotoMono-Regular.ttf', 'GeistMono-Regular.ttf')
        s = s.replace(default, d)
        return s.replace('</fontset>', ours + '\t</fontset>')
    edit(os.path.join(dest, 'xml', 'Font.xml'), fonts)

    def colors(s):
        for name, value in COLORS.items():
            s, n = re.subn(rf'(<color name="{name}">)[0-9A-Fa-f]{{8}}(</color>)', rf'\g<1>{value}\2', s)
            if not n:
                s = s.replace('</colors>', f'\t<color name="{name}">{value}</color>\n</colors>')
        return s
    edit(os.path.join(dest, 'colors', 'defaults.xml'), colors)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(sys.argv[1], sys.argv[2])
