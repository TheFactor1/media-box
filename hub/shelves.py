"""Home rows (shelves), user collections and TMDB franchises. Contract: DATAFLOW.md "Shelves and collections"."""
import json
import re
import secrets
import sys
import time
import urllib.parse

PAGE = 50
SMART_CAP = 200
FRANCHISE_TTL = 7 * 86400
CAST_MAX = 50  # credits can list hundreds; X-Ray shows the top billing
TMDB_IMG = 'https://image.tmdb.org/t/p/w185'
BUILTIN = {'continue': 'Continue watching', 'next': 'Next up', 'new': 'New', 'recent': 'Recently kept'}
TYPES = {'movie': 'Movies', 'series': 'Shows'}
# Smart filters use the Refine sheet's fields: OR within genres and within length, AND within format.
LENGTHS = {'under1h': lambda m: m < 60, '1to2h': lambda m: 60 <= m <= 120, 'over2h': lambda m: m > 120}
FORMATS = {'4K': lambda b: b.get('res') == '2160p', 'HDR': lambda b: bool(b.get('hdr')),
           'Atmos': lambda b: 'Atmos' in (b.get('audio') or [])}
SORTS = {'newest': (lambda i: i['year'] or 0, True), 'az': (lambda i: (i['title'] or '').casefold(), False),
         'shortest': (lambda i: i['runtimeMin'] or 10**6, False)}


def _core(hub):
    # The module the running Hub came from (hub.hub, or __main__ under `python -m hub.hub`), so its
    # error classes are the ones the request handler catches. Importing hub.hub here would be circular.
    return sys.modules[type(hub).__module__]


def init(hub):
    hub.sql('CREATE TABLE IF NOT EXISTS shelves(id INTEGER PRIMARY KEY CHECK (id = 1), list TEXT)')
    hub.sql('CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY, title TEXT, kind TEXT, filter TEXT, created INTEGER)')
    hub.sql('CREATE TABLE IF NOT EXISTS collection_items(collection TEXT, item TEXT, type TEXT, added INTEGER,'
            ' PRIMARY KEY(collection, item))')
    hub.sql('CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, value TEXT, at REAL)')


def text(value, limit=80):
    return re.sub(r'[\x00-\x1f\x7f]', '', str(value or '')).strip()[:limit]


# ---- shelves ----

def shelf_entry(kind, ref, title, hidden=False):
    sid = {'builtin': lambda: ref, 'catalog': lambda: f"catalog:{ref['addon']}:{ref['type']}:{ref['id']}",
           'collection': lambda: f'collection:{ref}', 'franchise': lambda: f'franchise:{ref}'}[kind]()
    return {'id': sid, 'kind': kind, 'ref': ref, 'title': title, 'hidden': hidden}


def live_catalogs(hub):
    """{host: [catalog shelves]} for meta addons that answered; an unreachable addon keeps its rows untouched."""
    core, out = _core(hub), {}
    for a in hub.meta_addons:
        try:
            out[a.host] = [shelf_entry('catalog', {'addon': a.host, 'type': c['type'], 'id': c['id']},
                                       f"{text(c.get('name') or c['id'])} · {TYPES.get(c['type'], c['type'])}")
                           for c, _, required in a.catalogs()
                           if not required - {'skip'} and isinstance(c.get('type'), str) and isinstance(c.get('id'), str)]
        except core.AddonError:
            pass
    return out


def get_shelves(hub):
    """The stored list reconciled with what exists now: catalogs added upstream are appended, removed ones dropped;
    every collection has a row (hide it to keep it off Home), so a stale PUT can't lose one."""
    row = hub.sql('SELECT list FROM shelves WHERE id = 1')
    stored = json.loads(row[0][0]) if row else None
    shelves = stored if stored is not None else [shelf_entry('builtin', b, BUILTIN[b]) for b in ('continue', 'next', 'new')]
    live, hosts = live_catalogs(hub), {a.host for a in hub.meta_addons}
    collections = dict(hub.sql('SELECT id, title FROM collections ORDER BY created, rowid'))
    keep = [s for s in shelves if not (
        s['kind'] == 'catalog' and (s['ref']['addon'] not in hosts or
                                    s['ref']['addon'] in live and s['id'] not in {c['id'] for c in live[s['ref']['addon']]})
        or s['kind'] == 'collection' and s['ref'] not in collections)]
    ids = {s['id'] for s in keep}
    keep += [c for cats in live.values() for c in cats if c['id'] not in ids]
    keep += [shelf_entry('collection', cid, title) for cid, title in collections.items() if f'collection:{cid}' not in ids]
    if keep != stored:
        save(hub, keep)
    return keep


def save(hub, shelves):
    hub.sql('INSERT OR REPLACE INTO shelves VALUES (1, ?)', (json.dumps(shelves),))


def put_shelves(hub, body):
    """Replace the order, titles and hidden flags. Ids are derived from kind + ref, so a client can't invent one."""
    core = _core(hub)
    entries = body.get('shelves') if isinstance(body, dict) else body
    if not isinstance(entries, list) or len(entries) > 200:
        raise core.HttpError(400, 'send the shelf list: [{kind, ref, title, hidden}]')
    collections = {r[0]: r[1] for r in hub.sql('SELECT id, title FROM collections')}
    out, seen = [], set()
    for e in entries:
        kind, ref = (e.get('kind'), e.get('ref')) if isinstance(e, dict) else (None, None)
        if kind == 'builtin' and ref in BUILTIN:
            default = BUILTIN[ref]
        elif kind == 'catalog' and isinstance(ref, dict) and all(isinstance(ref.get(k), str) for k in ('addon', 'type', 'id')):
            ref, default = {k: ref[k] for k in ('addon', 'type', 'id')}, ref['id']
        elif kind == 'collection' and ref in collections:
            default = collections[ref]
        elif kind == 'franchise' and str(ref).isdigit():
            ref, default = int(ref), 'Collection'
        else:
            raise core.HttpError(400, f'unknown shelf: {text(kind)} {text(json.dumps(ref))}')
        entry = shelf_entry(kind, ref, text(e.get('title')) or default, e.get('hidden') is True)
        if entry['id'] not in seen:
            seen.add(entry['id'])
            out.append(entry)
    save(hub, out)
    return get_shelves(hub)


def shelf(hub, sid, skip):
    core = _core(hub)
    s = next((x for x in get_shelves(hub) if x['id'] == sid), None)
    if not s:
        raise core.HttpError(404, 'no such shelf')
    kind, ref = s['kind'], s['ref']
    if kind == 'builtin':
        items = hub.new_releases(skip) if ref == 'new' else \
            hub.library(None if ref == 'recent' else ref)[skip:skip + PAGE]  # library() is newest kept first
    elif kind == 'catalog':
        addon = next((a for a in hub.meta_addons if a.host == ref['addon']), None)
        if not addon:
            raise core.HttpError(404, 'that addon is no longer configured')
        items = hub.annotate([core.to_item(m) for m in hub.catalog_metas(addon, ref['type'], ref['id'], skip)])
    elif kind == 'collection':
        c = collection(hub, ref)
        items = manual_items(hub, ref, skip) if c['kind'] == 'manual' else smart_items(hub, c['filter'])[skip:skip + PAGE]
    else:
        items = franchise_by_collection(hub, ref)['parts'][skip:skip + PAGE]
    return {'id': s['id'], 'title': s['title'], 'items': items}


# ---- collections ----

def check_filter(hub, f):
    core = _core(hub)
    ok = isinstance(f, dict) and not set(f) - {'genres', 'length', 'format', 'years', 'type', 'sort'} \
        and all(isinstance(g, str) for g in f.get('genres', [])) and len(f.get('genres', [])) <= 30 \
        and set(f.get('length', [])) <= set(LENGTHS) and set(f.get('format', [])) <= set(FORMATS) \
        and f.get('type') in (None, 'movie', 'series') and f.get('sort') in (None, *SORTS) \
        and isinstance(f.get('years', {}), dict) and not set(f.get('years', {})) - {'from', 'to'} \
        and all(isinstance(y, int) and not isinstance(y, bool) for y in f.get('years', {}).values())
    if not ok:
        raise core.HttpError(400, 'smart filter: {genres[], length[under1h|1to2h|over2h], format[4K|HDR|Atmos],'
                                  ' years{from,to}, type movie|series, sort newest|az|shortest}')
    return f


def collection(hub, cid):
    rows = hub.sql('SELECT id, title, kind, filter FROM collections WHERE id = ?', (cid,))
    if not rows:
        raise _core(hub).HttpError(404, 'no such collection')
    cid, title, kind, f = rows[0]
    count = hub.sql('SELECT COUNT(*) FROM collection_items WHERE collection = ?', (cid,))[0][0] if kind == 'manual' else None
    return {'id': cid, 'title': title, 'kind': kind, 'filter': json.loads(f) if f else None, 'count': count}


def list_collections(hub):
    return [collection(hub, r[0]) for r in hub.sql('SELECT id FROM collections ORDER BY created, rowid')]


def create_collection(hub, body):
    core = _core(hub)
    title, kind = text(body.get('title') if isinstance(body, dict) else ''), body.get('kind') if isinstance(body, dict) else None
    if not title or kind not in ('manual', 'smart'):
        raise core.HttpError(400, 'body must be {title, kind: manual|smart, filter?}')
    f = check_filter(hub, body.get('filter')) if kind == 'smart' else None
    cid = secrets.token_hex(6)
    hub.sql('INSERT INTO collections VALUES (?, ?, ?, ?, ?)', (cid, title, kind, json.dumps(f) if f else None, core.now_ms()))
    get_shelves(hub)  # a new collection shows up as a Home row
    return collection(hub, cid)


def patch_collection(hub, cid, body):
    c = collection(hub, cid)
    if not isinstance(body, dict):
        raise _core(hub).HttpError(400, 'body must be {title?, filter?}')
    title = text(body.get('title')) or c['title']
    f = check_filter(hub, body['filter']) if 'filter' in body and c['kind'] == 'smart' else c['filter']
    hub.sql('UPDATE collections SET title = ?, filter = ? WHERE id = ?', (title, json.dumps(f) if f else None, cid))
    # A row still carrying the old name follows the rename; a row the user renamed keeps its own title.
    save(hub, [dict(s, title=title) if s['id'] == f'collection:{cid}' and s['title'] == c['title'] else s for s in get_shelves(hub)])
    return collection(hub, cid)


def delete_collection(hub, cid):
    hub.sql('DELETE FROM collection_items WHERE collection = ?', (cid,))
    hub.sql('DELETE FROM collections WHERE id = ?', (cid,))
    get_shelves(hub)  # drops its row
    return {'ok': True}


def add_item(hub, cid, body):
    core = _core(hub)
    if collection(hub, cid)['kind'] != 'manual':
        raise core.HttpError(409, 'a smart collection fills itself; add titles to a manual one')
    id_ = body.get('id') if isinstance(body, dict) else None
    if not core.IMDB.fullmatch(str(id_)):
        raise core.HttpError(400, 'body must be {id: "tt…", type?}')
    kept = hub.sql('SELECT type FROM library WHERE id = ?', (id_,))
    type_ = kept[0][0] if kept else body.get('type') if body.get('type') in TYPES else None
    for t in TYPES if type_ is None else ():  # look it up rather than guess, so the row can show it later
        try:
            if isinstance(hub.meta(t, id_), dict):
                type_ = t
                break
        except core.AddonError:
            continue
    if type_ is None:
        raise core.HttpError(404, 'the meta addon does not know that title')
    hub.sql('INSERT OR IGNORE INTO collection_items VALUES (?, ?, ?, ?)', (cid, id_, type_, core.now_ms()))
    return collection(hub, cid)


def remove_item(hub, cid, item):
    collection(hub, cid)
    hub.sql('DELETE FROM collection_items WHERE collection = ? AND item = ?', (cid, item))
    return collection(hub, cid)


def manual_items(hub, cid, skip):
    core = _core(hub)
    rows = hub.sql('SELECT item, type FROM collection_items WHERE collection = ? ORDER BY added, rowid', (cid,))[skip:skip + PAGE]
    kept = {r[0]: json.loads(r[1]) for r in hub.sql('SELECT id, item FROM library')}
    items = []
    for id_, type_ in rows:
        if id_ in kept:
            items.append(kept[id_])
            continue
        try:
            item = core.to_item(hub.meta(type_, id_))
        except core.AddonError:  # meta addon down: keep the slot so the order and Remove still work
            item = core.to_item({'name': None})
        items.append(dict(item, id=id_, type=type_))
    return hub.annotate(items)


def smart_items(hub, f):
    core = _core(hub)
    match = lambda i: (not f.get('type') or i['type'] == f['type']) \
        and (not f.get('genres') or bool({g.casefold() for g in f['genres']} & {str(g).casefold() for g in i['genres']})) \
        and (not f.get('length') or i['runtimeMin'] is not None and any(LENGTHS[k](i['runtimeMin']) for k in f['length'])) \
        and all(FORMATS[k](i['badges'] or {}) for k in f.get('format', [])) \
        and (i['year'] or 0) >= f.get('years', {}).get('from', 0) and (i['year'] or 10**4) <= f.get('years', {}).get('to', 10**4)
    out, seen = [], set()

    def take(items):
        for i in items:
            if len(out) < SMART_CAP and i['id'] not in seen and match(i):
                seen.add(i['id'])
                out.append(i)

    take(hub.library(None))  # library first
    for a in hub.meta_addons:
        try:
            for c, _, required in a.catalogs():
                if not required - {'skip'} and (not f.get('type') or c.get('type') == f['type']) and len(out) < SMART_CAP:
                    take(core.to_item(m) for m in hub.catalog_metas(a, c['type'], c['id'], 0))
        except core.AddonError:
            continue  # library matches still come back
    key, reverse = SORTS[f.get('sort') or 'newest']
    return hub.annotate(sorted(out, key=key, reverse=reverse))


# ---- franchises (TMDB, only with the user's key) ----

def cached(hub, key, ttl, build):
    row = hub.sql('SELECT value, at FROM cache WHERE key = ?', (key,))
    if row and time.time() - row[0][1] < ttl:
        return json.loads(row[0][0])
    value = build()
    hub.sql('INSERT OR REPLACE INTO cache VALUES (?, ?, ?)', (key, json.dumps(value), time.time()))
    return value


def tmdb_key(hub):
    key = hub.setting('tmdb_api_key')
    if not key:  # no key, no outbound TMDB call, ever (and no cached answer either)
        raise _core(hub).HttpError(404, 'franchise collections need a TMDB key; add one on the setup page')
    return key


def tmdb(hub, path, **params):
    core, key = _core(hub), tmdb_key(hub)
    headers = {}
    if key.startswith('eyJ'):  # a v4 read access token goes in a header; a v3 key in the query
        headers['Authorization'] = f'Bearer {key}'
    else:
        params['api_key'] = key
    return core.fetch(f'{hub.tmdb_base}{path}?{urllib.parse.urlencode(params)}', headers=headers)


def franchise_for(hub, imdb):
    core = _core(hub)
    if not core.IMDB.fullmatch(imdb):
        raise core.HttpError(400, 'id must be an IMDb id (tt…)')
    tmdb_key(hub)

    def find():
        movies = core.lst(tmdb(hub, f'/find/{imdb}', external_source='imdb_id').get('movie_results'))
        if not movies or not isinstance(movies[0], dict) or not isinstance(movies[0].get('id'), int):
            return {'collection': None}
        c = tmdb(hub, f'/movie/{movies[0]["id"]}').get('belongs_to_collection')
        return {'collection': c['id'] if isinstance(c, dict) and isinstance(c.get('id'), int) else None}

    cid = cached(hub, f'tmdb:find:{imdb}', FRANCHISE_TTL, find)['collection']
    if cid is None:
        raise core.HttpError(404, 'that title is not part of a collection')
    return franchise_by_collection(hub, cid)


def cast_for(hub, type_, imdb):
    """{'cast': [{name, character, photo}]} from TMDB credits; empty, with no outbound call, without a TMDB key."""
    core = _core(hub)
    if not hub.setting('tmdb_api_key'):
        return {'cast': []}
    kind = 'movie' if type_ == 'movie' else 'tv'

    def build():
        found = core.lst(tmdb(hub, f'/find/{imdb}', external_source='imdb_id').get(f'{kind}_results'))
        if not found or not isinstance(found[0], dict) or not isinstance(found[0].get('id'), int):
            return []
        people = [c for c in core.lst(tmdb(hub, f'/{kind}/{found[0]["id"]}/credits').get('cast')) if isinstance(c, dict)]
        photo = lambda p: TMDB_IMG + p if isinstance(p, str) and re.fullmatch(r'/[\w-]+\.(?:jpg|png)', p) else None
        return [{'name': text(c.get('name'), 200), 'character': text(c.get('character'), 200) or None, 'photo': photo(c.get('profile_path'))}
                for c in people if text(c.get('name'))][:CAST_MAX]

    return {'cast': cached(hub, f'tmdb:cast:{type_}:{imdb}', FRANCHISE_TTL, build)}


def franchise_by_collection(hub, cid):
    core = _core(hub)
    tmdb_key(hub)

    def build():
        c = tmdb(hub, f'/collection/{int(cid)}')
        parts = sorted((p for p in core.lst(c.get('parts')) if isinstance(p, dict) and isinstance(p.get('id'), int)),
                       key=lambda p: str(p.get('release_date') or '9999'))  # unreleased parts last
        items = []
        for p in parts:
            imdb = tmdb(hub, f'/movie/{p["id"]}/external_ids').get('imdb_id')
            if not core.IMDB.fullmatch(str(imdb)):
                continue  # no IMDb id: nothing the addons could play
            try:
                item = core.to_item(hub.meta('movie', imdb))
            except core.AddonError:
                item = core.to_item({'name': p.get('title'), 'releaseInfo': str(p.get('release_date') or '')[:4]})
            items.append(dict(item, id=imdb, type='movie'))
        return {'collectionId': int(cid), 'title': text(c.get('name'), 200), 'parts': items}

    data = cached(hub, f'tmdb:collection:{int(cid)}', FRANCHISE_TTL, build)
    return dict(data, parts=hub.annotate([dict(p) for p in data['parts']]))
