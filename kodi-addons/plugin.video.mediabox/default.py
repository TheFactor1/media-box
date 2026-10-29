"""plugin://plugin.video.mediabox/... — listings and playback from the hub (DATAFLOW.md "Hub API").

Paths: /  /shelves  /shelf/<id>  /library/<continue|next|movies|shows>  /new  /browse  /catalog/<type>/<id>
       /search[?q=]  /title/<type>/<id>[/<season>]  /play/<type>/<id>[/<s>/<e>]  /keep/<type>/<id>
       /versions/<type>/<id>[/<s>/<e>]  /pair
Any ?r=… is ignored: the skin changes it to make Kodi re-read a widget."""
import json
import sys
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin

from resources.lib import mbhub
from resources.lib.mbhub import HubError, q

BASE = 'plugin://plugin.video.mediabox'
URL = sys.argv[0]
HANDLE = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].lstrip('-').isdigit() else -1
QS = dict(urllib.parse.parse_qsl(sys.argv[2].lstrip('?'))) if len(sys.argv) > 2 else {}
PARTS = [urllib.parse.unquote(p) for p in urllib.parse.urlsplit(URL).path.split('/') if p]
HOME = xbmcgui.Window(10000)


def notify(msg):
    xbmcgui.Dialog().notification('Mediabox', msg, xbmcgui.NOTIFICATION_WARNING, 4000)


def episode_path(se):
    return ''.join(f'/{x}' for x in se)


def folder(label, path, art=None):
    li = xbmcgui.ListItem(label, offscreen=True)
    if art:
        li.setArt(art)
    return BASE + path, li, True


def title_item(it, play_next=False):
    """A hub item (DATAFLOW.md item shape) as a ListItem. Movies, and series in Continue/Next, play at once."""
    type_, id_ = it.get('type'), it.get('id')
    li = xbmcgui.ListItem(it.get('title') or id_, offscreen=True)
    poster, backdrop = it.get('poster'), it.get('backdrop')
    li.setArt({k: v for k, v in (('poster', poster), ('thumb', poster), ('fanart', backdrop), ('landscape', backdrop),
                                 ('clearlogo', it.get('logo'))) if v})
    tag = li.getVideoInfoTag()
    tag.setTitle(it.get('title') or '')
    tag.setPlot(it.get('overview') or '')
    tag.setGenres([str(g) for g in it.get('genres') or []])
    if it.get('year'):
        tag.setYear(int(it['year']))
    if it.get('runtimeMin'):
        tag.setDuration(int(it['runtimeMin']) * 60)
    tag.setUniqueIDs({'imdb': id_}, 'imdb')
    b = it.get('badges') or {}
    join = lambda v: ' · '.join(map(str, v)) if isinstance(v, list) else str(v or '')
    li.setProperties({'res': join(b.get('res')), 'hdr': join(b.get('hdr')), 'audio': join(b.get('audio')),
                      'inlibrary': 'true' if it.get('inLibrary') else '', 'imdb': id_ or '', 'mbtype': type_ or ''})
    nxt = it.get('nextEpisode') if type_ == 'series' else None
    playable = type_ == 'movie' or (play_next and nxt)
    se = [nxt['s'], nxt['e']] if playable and nxt else []
    if type_ == 'movie':
        tag.setMediaType('movie')
    elif se:
        tag.setMediaType('episode')
        tag.setTvShowTitle(it.get('title') or '')
        tag.setSeason(se[0])
        tag.setEpisode(se[1])
        li.setProperty('episodelabel', f"S{se[0]} · E{se[1]}" + (f"  {nxt['title']}" if nxt.get('title') else ''))
    else:
        tag.setMediaType('tvshow')
    r = it.get('resume')
    if playable and r and r.get('durationMs'):
        tag.setResumePoint(r['positionMs'] / 1000, r['durationMs'] / 1000)
    menu = []
    if not it.get('inLibrary'):
        menu.append(('Add to library', f'RunPlugin({BASE}/keep/{type_}/{q(id_)})'))
    if playable:
        menu.append(('Other versions', f'RunPlugin({BASE}/versions/{type_}/{q(id_)}{episode_path(se)})'))
    li.addContextMenuItems(menu)
    if playable:
        li.setProperty('IsPlayable', 'true')
        return f'{BASE}/play/{type_}/{q(id_)}{episode_path(se)}', li, False
    return f'{BASE}/title/{type_}/{q(id_)}', li, True


def list_items(items, content='videos', play_next=False):
    xbmcplugin.setContent(HANDLE, content)
    # Only the two types the hub plays; anything else would end up in plugin paths it can't serve.
    xbmcplugin.addDirectoryItems(HANDLE, [title_item(i, play_next) for i in items
                                          if i.get('id') and i.get('type') in ('movie', 'series')])


def hub_get(path):
    """GET or, on 401 from a hub that isn't paired yet, offer to pair once and retry."""
    try:
        return mbhub.get(path)
    except HubError as e:
        if e.status == 401 and xbmcgui.Dialog().yesno('Mediabox', 'This TV is not paired with your hub yet. Pair now?'):
            if mbhub.pair():
                return mbhub.get(path)
        raise


def root():
    for label, path in (('Continue', '/library/continue'), ('Next up', '/library/next'), ('New', '/new'),
                        ('Movies', '/library/movies'), ('Shows', '/library/shows'), ('Browse', '/browse'),
                        ('Search', '/search'), ('Home rows', '/shelves')):
        xbmcplugin.addDirectoryItem(HANDLE, *folder(label, path))


def shelves():
    for s in hub_get('/shelves')['shelves']:
        if not s.get('hidden'):
            xbmcplugin.addDirectoryItem(HANDLE, *folder(s['title'], f"/shelf/{q(s['id'])}"))


def browse():
    for c in hub_get('/browse/catalogs')['catalogs']:
        kind = 'Movies' if c['type'] == 'movie' else 'Shows' if c['type'] == 'series' else c['type']
        xbmcplugin.addDirectoryItem(HANDLE, *folder(f"{c.get('name') or c['id']} · {kind}", f"/catalog/{q(c['type'])}/{q(c['id'])}"))


def search():
    text = QS.get('q') or xbmcgui.Dialog().input('Search films and shows')
    if not text:
        return False
    list_items(hub_get('/search?q=' + q(text))['items'])
    return True


def title(type_, id_, season=None):
    meta = hub_get(f'/browse/meta/{q(type_)}/{q(id_)}')
    if type_ != 'series':
        list_items([meta], 'movies')
        return
    videos = meta.get('videos') or []
    seasons = sorted({v['s'] for v in videos})
    xbmcplugin.setPluginCategory(HANDLE, meta.get('title') or '')
    if season is None and len(seasons) > 1:
        xbmcplugin.setContent(HANDLE, 'seasons')
        for s in seasons:
            path, li, _ = folder(f'Season {s}', f'/title/series/{q(id_)}/{s}',
                                 {k: v for k, v in (('poster', meta.get('poster')), ('fanart', meta.get('backdrop'))) if v})
            tag = li.getVideoInfoTag()
            tag.setMediaType('season')
            tag.setSeason(s)
            tag.setTvShowTitle(meta.get('title') or '')
            tag.setPlot(meta.get('overview') or '')
            xbmcplugin.addDirectoryItem(HANDLE, path, li, True)
        return
    s = int(season) if season is not None else (seasons[0] if seasons else 1)
    nxt = meta.get('nextEpisode') or {}
    out = []
    for v in (v for v in videos if v['s'] == s):
        ep = dict(meta, nextEpisode={'s': v['s'], 'e': v['e'], 'title': v.get('title')},
                  resume=meta.get('resume') if (nxt.get('s'), nxt.get('e')) == (v['s'], v['e']) else None)
        path, li, _ = title_item(ep, play_next=True)
        li.setLabel(f"{v['e']}. {v.get('title') or 'Episode ' + str(v['e'])}")
        li.getVideoInfoTag().setTitle(v.get('title') or '')
        out.append((path, li, False))
    xbmcplugin.setContent(HANDLE, 'episodes')
    xbmcplugin.addDirectoryItems(HANDLE, out)


def preset_qs():
    p = mbhub.addon().getSettingString('preset')
    return f'preset={q(p)}' if p and p != 'auto' else ''  # auto: the hub's own default


def stream_url(url, headers):
    # Kodi's header form: url|Name=Value&Name2=Value2, values URL-encoded.
    return url + ('|' + urllib.parse.urlencode(headers, quote_via=urllib.parse.quote) if headers else '')


def remember(type_, id_, se):
    """Tells the service what is about to play so its player monitor can write /resume."""
    HOME.setProperty('mediabox.playing', json.dumps({'type': type_, 'id': id_, 'se': [int(x) for x in se]}))


def play(type_, id_, se):
    try:
        r = hub_get(f'/play/{q(id_)}{episode_path(se)}?json=1&' + preset_qs())
    except HubError as e:
        notify("Can't play this right now: " + e.reason)
        xbmcplugin.setResolvedUrl(HANDLE, False, xbmcgui.ListItem(offscreen=True))
        return
    li = xbmcgui.ListItem(path=stream_url(r['url'], r.get('headers') or {}), offscreen=True)
    remember(type_, id_, se)
    xbmcplugin.setResolvedUrl(HANDLE, True, li)


def versions(type_, id_, se):
    streams = [s for s in hub_get(f'/browse/streams/{q(type_)}/{q(id_)}{episode_path(se)}?' + preset_qs())['streams']
               if s.get('url')]
    if not streams:
        notify('No playable copy right now')
        return
    labels = []
    for i, s in enumerate(streams):
        b = s.get('badges') or {}
        bits = [b.get('res'), ' '.join(b.get('hdr') or []), ' '.join(b.get('audio') or []), b.get('source'),
                f"{b['sizeGb']} GB" if b.get('sizeGb') else None, (s.get('source') or {}).get('name')]
        labels.append(('Best · ' if i == 0 else '') + ' · '.join(str(x) for x in bits if x))
    pick = xbmcgui.Dialog().select('Other versions', labels)
    if pick < 0:
        return
    s = streams[pick]
    remember(type_, id_, se)
    li = xbmcgui.ListItem(xbmc.getInfoLabel('ListItem.Title') or labels[pick], offscreen=True)  # the focused title
    xbmc.Player().play(stream_url(s['url'], s.get('headers') or {}), li)


def keep(type_, id_):
    try:
        it = mbhub.call('POST', '/keep', {'id': id_, 'type': type_})[1]
    except HubError as e:
        notify("Couldn't add it: " + e.reason)
        return
    xbmcgui.Dialog().notification('Mediabox', f"{it.get('title') or 'It'} is in your library", time=3000)
    HOME.setProperty('mediabox.bump', '1')  # the service refreshes the rows within a second


def main():
    p = PARTS
    try:
        match p:
            case []:
                root()
            case ['shelves']:
                shelves()
            case ['shelf', sid]:
                items = hub_get(f'/shelf/{q(sid)}')['items']
                list_items(items, play_next=sid in ('continue', 'next'))
                if QS.get('row', '').isdigit():  # a Home row: "0" tells the skin to hide it
                    HOME.setProperty(f"mediabox.count.{QS['row']}", str(len(items)))
            case ['library', coll]:
                list_items(hub_get(f'/library?collection={q(coll)}')['items'], play_next=coll in ('continue', 'next'))
            case ['new']:
                list_items(hub_get('/new')['items'])
            case ['browse']:
                browse()
            case ['catalog', type_, cid]:
                list_items(hub_get(f'/browse/catalog/{q(type_)}/{q(cid)}')['items'],
                           'movies' if type_ == 'movie' else 'tvshows')
            case ['search']:
                if not search():
                    xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
                    return
            case ['title', type_, id_, *season]:
                title(type_, id_, season[0] if season else None)
            case ['play', type_, id_, *se]:
                return play(type_, id_, se)
            case ['versions', type_, id_, *se]:
                return versions(type_, id_, se)
            case ['keep', type_, id_]:
                return keep(type_, id_)
            case ['pair']:
                mbhub.pair()
                if HANDLE >= 0:
                    xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
                return
            case _:
                raise HubError(404, 'no such page')
    except HubError as e:
        notify(e.reason)
        if HANDLE >= 0:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


main()
