"""Run from the project root: python -I -m unittest discover -s hub -t .

(-I drops the working directory from sys.path, so `python -I -m unittest hub.test_hub` cannot import
any local package; discover -t . puts the project root back.) Without -I: python -m unittest hub.test_hub
"""
import contextlib
import http.client
import io
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hub import hub, onboarding, ranking, security

YEAR = time.localtime().tm_year


def movie(id_, name, genres=('Comedy',), year=YEAR):
    return {'id': id_, 'type': 'movie', 'name': name, 'releaseInfo': str(year), 'runtime': '101 min', 'genres': list(genres),
            'poster': f'http://img/{id_}.jpg', 'background': f'http://img/{id_}-bg.jpg', 'description': f'{name} plot'}


METAS = {'tt1': movie('tt1', 'Drama Club'), 'tt3': movie('tt3', 'The Last Drama'), 'tt4': movie('tt4', 'Heat', ['Crime', 'Drama']),
         'tt5': movie('tt5', 'Dramaland', year=YEAR - 1), 'tt6': movie('tt6', 'Oldie', year=1999),
         'tt9': {'id': 'tt9', 'type': 'series', 'name': 'Show', 'releaseInfo': '2019–', 'genres': ['Drama'],
                 'videos': [{'id': 'tt9:1:2', 'season': 1, 'episode': 2, 'name': 'Two'},
                            {'id': 'tt9:1:1', 'season': 1, 'episode': 1, 'name': 'One'},
                            {'id': 'tt9:0:1', 'season': 0, 'episode': 1, 'name': 'Special'}]},
         'tt0468569': {'id': 'tt0468569', 'type': 'movie', 'name': 'The Dark Knight', 'year': 2008, 'runtime': '152 min'},
         'tt0372784': {'id': 'tt0372784', 'type': 'movie', 'name': 'Batman Begins', 'year': 2005},
         'tt1345836': {'id': 'tt1345836', 'type': 'movie', 'name': 'The Dark Knight Rises', 'year': 2012},
         'tt7': {'id': 'tt7', 'type': 'series', 'name': 'Lost Things', 'runtime': '40 min',
                 'videos': [{'id': 'tt7:1:1', 'season': 1, 'episode': 1}, {'id': 'tt7:1:2', 'season': 1, 'episode': 2, 'runtime': '50 min'}]}}
TT1_STREAMS = [
    {'name': 'AIO 1080p', 'description': 'Drama.Club.1080p.WEB-DL.DDP5.1 4.2 GB ⚡', 'url': 'http://cdn/1080web'},
    {'name': 'AIO 4K', 'description': '2160p WEB-DL DV HDR10 DD+ Atmos 14 GB ⚡', 'url': 'http://cdn/4kweb',
     'behaviorHints': {'notWebReady': True, 'proxyHeaders': {'request': {'Authorization': 'Bearer t'}}}},
    {'name': 'AIO 4K', 'description': '2160p BluRay REMUX HDR10 TrueHD Atmos 7.1 60 GB ⚡', 'url': 'http://cdn/remux'},
    {'name': 'AIO 4K', 'description': '2160p REMUX DV TrueHD 70GB ⏳', 'url': 'http://cdn/uncached-remux'},
    {'name': 'Torrent 4K', 'title': '2160p REMUX DV TrueHD', 'infoHash': 'abc'},
    {'name': 'AIO 720p', 'description': '720p WEB 1 GB ⚡', 'url': 'http://cdn/720'},
]
STREAMS = {
    '/stream/movie/tt1.json': TT1_STREAMS,
    '/stream/movie/tt0468569.json': [
        {'name': 'AIO 4K', 'description': 'The.Dark.Knight.Rises.2012.2160p.BluRay.REMUX 75 GB ⚡', 'url': 'http://cdn/rises'},
        {'name': 'AIO 1080p', 'description': 'The Dark Knight (2008) 1080p BluRay x264 DTS 12 GB ⚡', 'url': 'http://cdn/1080'},
        {'name': 'AIO 4K', 'description': 'The.Dark.Knight.2008.2160p.UHD.BluRay.REMUX.HDR10.TrueHD.Atmos-GRP\n💾 60 GB ⚡',
         'url': 'http://cdn/remux60'},
        {'name': '[TB⚡] AIOStreams 2160p', 'description': '💾 70 GB', 'url': 'http://cdn/imax70', 'behaviorHints': {
            'filename': 'The Dark Knight 2008 IMAX PROPER HYBRID 2160p UHD Blu-ray Remux DV HEVC DTS-HD MA 5.1-NOLANMUSIC.mkv'}},
        {'name': 'AIO 4K', 'description': '4K REMUX DV 80 GB ⚡', 'url': 'http://cdn/mystery'},
    ],
    '/stream/series/tt7:1:2.json': [
        {'name': 'AIO', 'description': 'Lost.Things.S01E01.1080p.WEB-DL 2 GB ⚡', 'url': 'http://cdn/wrong-episode'},
        {'name': 'AIO', 'description': 'Lost Things S01 COMPLETE 2160p WEB-DL 20 GB ⚡', 'fileIdx': 1, 'url': 'http://cdn/pack'},
        {'name': 'AIO', 'description': 'Lost Things Season 1 1080p WEB ⚡', 'url': 'http://cdn/pack-no-file'},
        {'name': 'AIO', 'description': 'Lost.Things.2021.S01E02.720p.WEB 1 GB ⚡', 'url': 'http://cdn/episode'},
    ],
}


AIO_PREFIX = '/stremio/u-1/enc-1'  # the fake AIOStreams user the setup page provisions
TMDB = {  # a fake TMDB under /3: The Dark Knight's collection, parts out of order, one without an IMDb id
    '/3/find/tt0468569': {'movie_results': [{'id': 155}]},
    '/3/movie/155': {'belongs_to_collection': {'id': 263, 'name': 'The Dark Knight Collection'}},
    '/3/collection/263': {'name': 'The Dark Knight Collection', 'parts': [
        {'id': 49026, 'release_date': '2012-07-16'}, {'id': 272, 'release_date': '2005-06-10'},
        {'id': 155, 'release_date': '2008-07-14'}, {'id': 999, 'release_date': '2020-01-01'}]},
    '/3/movie/272/external_ids': {'imdb_id': 'tt0372784'}, '/3/movie/155/external_ids': {'imdb_id': 'tt0468569'},
    '/3/movie/49026/external_ids': {'imdb_id': 'tt1345836'}, '/3/movie/999/external_ids': {'imdb_id': None},
}


class FakeAddon(BaseHTTPRequestHandler):
    """A Stremio addon, and also a fake AIOStreams: templates, user creation, and the user's addon under AIO_PREFIX."""
    created = []
    tmdb_calls = []
    provider_paths = []
    # A fake debrid provider: the Bearer key picks the answer (good… accepted, down… a 503, anything else refused),
    # each refusal in that provider's documented shape.
    PROVIDER_REFUSALS = {'/v1/api/user/me': (401, {'success': False, 'error': 'BAD_TOKEN', 'detail': 'Invalid token'}),
                         '/rest/1.0/user': (401, {'error': 'bad_token', 'error_code': 8}),
                         '/v4/user': (200, {'status': 'error', 'error': {'code': 'AUTH_BAD_APIKEY', 'message': 'bad'}}),
                         '/api/account/info': (200, {'status': 'error', 'message': 'not logged in', 'code': 'authentication_failed'})}
    PROVIDER_OK = {'/v1/api/user/me': {'success': True, 'data': {'email': 'x'}}, '/rest/1.0/user': {'id': 1, 'username': 'u'},
                   '/v4/user': {'status': 'success', 'data': {'user': {'username': 'u'}}},
                   '/api/account/info': {'status': 'success', 'customer_id': 1}}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path != '/api/v1/user':
            return self.send_error(404)
        if body['config']['services'][0]['credentials']['apiKey'].startswith('FAIL'):
            return self.reply({'success': False, 'error': {'code': 'X', 'message': 'Invalid torbox credentials'}, 'data': None}, 400)
        FakeAddon.created.append(body)
        self.reply({'success': True, 'detail': 'created', 'error': None, 'data': {'uuid': 'u-1', 'encryptedPassword': 'enc-1'}}, 201)

    def reply(self, body, status=200):
        self.reply_raw(json.dumps(body).encode(), status)

    def reply_raw(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.removeprefix(AIO_PREFIX)
        if p in self.PROVIDER_OK:
            FakeAddon.provider_paths.append(self.path)
            key = (self.headers.get('Authorization') or '').removeprefix('Bearer ')
            if key.startswith('down'):
                return self.reply({'error': 'maintenance'}, 503)
            return self.reply(self.PROVIDER_OK[p]) if key.startswith('good') else self.reply(*self.PROVIDER_REFUSALS[p][::-1])
        if p.startswith('/3/'):
            path, _, query = p.partition('?')
            FakeAddon.tmdb_calls.append((path, urllib.parse.parse_qs(query)))
            return self.reply(TMDB[path]) if path in TMDB else self.send_error(404)
        if p.startswith('/redirect?to='):  # a hostile addon bouncing the hub somewhere else
            self.send_response(302)
            self.send_header('Location', urllib.parse.unquote(p.split('=', 1)[1]))
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if p == '/deep':
            return self.reply_raw(b'[' * 200000)
        if p == '/api/v1/templates':
            return self.reply({'success': True, 'error': None, 'data': [
                {'metadata': {'id': 'other'}, 'config': {}},
                {'metadata': {'id': 'builtin.debrid-starter'}, 'config': {
                    'services': [{'id': 'realdebrid', 'credentials': {'apiKey': '<template_placeholder>'}}],
                    'presets': [], 'formatter': {'id': 'prism'}, 'sortCriteria': {'global': []},
                    'titleMatching': {'enabled': True, 'requestTypes': ['movie', 'series']}, 'yearMatching': {'enabled': True},
                    'tmdbApiKey': '<template_placeholder>'}}]})
        if p == '/manifest.json':
            body = {'id': 'fake', 'version': '1.0.0', 'name': 'Fake', 'types': ['movie', 'series'], 'idPrefixes': ['tt'],
                    'resources': ['catalog', 'meta', {'name': 'stream', 'types': ['movie', 'series'], 'idPrefixes': ['tt']}],
                    'catalogs': [{'type': 'movie', 'id': 'top', 'name': 'Top', 'extra': [{'name': 'search'}, {'name': 'skip'}]}]}
        elif p == '/catalog/movie/top.json':
            body = {'metas': [METAS['tt1'], METAS['tt5']]}
        elif p == '/catalog/movie/fresh2.json':  # a second New catalog, two pages long
            body = {'metas': [METAS['tt4']]}
        elif p == '/catalog/movie/fresh2/skip=1.json':
            body = {'metas': [METAS['tt5']]}
        elif p.startswith(('/catalog/movie/fresh/skip=', '/catalog/movie/fresh2/skip=')):
            body = {'metas': []}
        elif p == '/catalog/movie/fresh.json':
            body = {'metas': [METAS['tt6'], METAS['tt5'], METAS['tt1'], METAS['tt5']]}
        elif p == '/catalog/movie/top/search=drama.json':
            body = {'metas': [METAS['tt5'], METAS['tt1']]}  # tt1 is in the library, so it must not repeat
        elif p.startswith('/meta/'):
            body = {'meta': METAS[p.split('/')[3].removesuffix('.json')]}
        elif p in STREAMS:
            body = {'streams': STREAMS[p]}
        elif p.startswith('/stream/'):
            body = {'streams': [{'name': 'AIO', 'description': '1080p WEB ⚡', 'url': 'http://cdn/' + p.split('/')[3]}]}
        else:
            self.send_error(404)
            return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class Fixture(unittest.TestCase):
    """A hub and its own fake addon per test class; `extra` is appended to hub.toml."""
    extra = ''
    streams = True  # False: an unconfigured hub (no stream addon yet)

    @classmethod
    def setUpClass(cls):
        cls.addon = ThreadingHTTPServer(('127.0.0.1', 0), FakeAddon)
        threading.Thread(target=cls.addon.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        manifest = f'http://127.0.0.1:{cls.addon.server_port}/manifest.json'
        config = f'{cls.tmp.name}/hub.toml'
        with open(config, 'w') as f:
            f.write(f'bind = "127.0.0.1"\nport = 0\ndiscovery_port = 0\npreset = "standard"\nmeta_addons = ["{manifest}"]\n'
                    + (f'stream_addons = ["{manifest}"]\n' if cls.streams else '')
                    + f'aiostreams_url = "http://127.0.0.1:{cls.addon.server_port}"\n'
                    f'new_catalog = [{{addon = "{manifest}", type = "movie", id = "fresh"}},'
                    f' {{addon = "{manifest}", type = "movie", id = "fresh2"}}]\n'
                    + cls.extra.format(addon=f'http://127.0.0.1:{cls.addon.server_port}'))
        cls.server = hub.make_server(config, cls.tmp.name)
        cls.hub = cls.server.RequestHandlerClass.hub
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server.beacon.close()
        cls.tmp.cleanup()

    def auth(self):
        with open(os.path.join(self.tmp.name, 'token'), 'rb') as f:  # re-read: a rotate replaces it
            return {'Authorization': 'Bearer ' + security.b64(f.read())}

    def raw(self, method, path, body=b'', headers=None):
        """No token unless given; returns (status, json, response)."""
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=30)
        conn.request(method, path, body, headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, json.loads(data or b'null'), resp

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json', **self.auth()})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def get(self, path):
        status, body = self.call('GET', path)
        self.assertEqual(status, 200, body)
        return body


class HubTest(Fixture):
    """Steps run in name order and build on each other: keep, library, play, resume, search, unreachable."""

    def test_1_keep(self):
        for id_, type_ in [('tt1', 'movie'), ('tt3', 'movie'), ('tt4', 'movie'), ('tt9', 'series')]:
            status, item = self.call('POST', '/keep', {'id': id_, 'type': type_})
            self.assertEqual(status, 200, item)
        self.assertEqual(item['nextEpisode'], {'s': 1, 'e': 1, 'title': 'One'})
        status, again = self.call('POST', '/keep', {'id': 'tt1', 'type': 'movie'})
        self.assertEqual((again['title'], again['year'], again['runtimeMin'], again['inLibrary']), ('Drama Club', YEAR, 101, True))
        self.assertEqual(again['badges'], {'res': '2160p', 'hdr': ['HDR10'], 'audio': ['TrueHD', 'Atmos']})  # best copy (max)
        self.assertEqual(self.call('POST', '/keep', {'id': 'nope', 'type': 'movie'})[0], 400)

    def test_2_library(self):
        self.assertEqual(len(self.get('/library')['items']), 4)
        self.assertEqual([i['title'] for i in self.get('/library?collection=movies')['items']], ['Heat', 'The Last Drama', 'Drama Club'])
        self.assertEqual([i['id'] for i in self.get('/library?collection=shows')['items']], ['tt9'])
        self.assertEqual(self.call('GET', '/library?collection=new')[0], 400)
        self.assertEqual(self.call('GET', '/library?collection=bogus')[0], 400)
        catalog = self.get('/browse/catalog/movie/top')['items']
        self.assertEqual([(i['id'], i['inLibrary']) for i in catalog], [('tt1', True), ('tt5', False)])

    def test_3_play_ranking(self):
        # tt1 runs 101 min: 1080web 5.5 Mbps, 4kweb 18.5, remux 79, uncached remux 92, 720 1.3
        self.assertEqual(self.get('/play/tt1?preset=max&json=1')['url'], 'http://cdn/remux')
        self.assertEqual(self.get('/play/tt1?preset=standard&json=1')['url'], 'http://cdn/4kweb')
        self.assertEqual(self.get('/play/tt1?preset=slow&json=1')['url'], 'http://cdn/1080web')
        self.assertEqual(self.get('/play/tt1?preset=best&json=1')['url'], 'http://cdn/remux')  # old name, alias of max
        self.assertEqual(self.get('/presets')['presets']['standard']['max_mbps'], 20)
        self.assertEqual(self.get('/play/tt1?json=1')['headers'], {'Authorization': 'Bearer t'})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=30)
        conn.request('GET', '/play/tt9/1/2', headers=self.auth())
        r = conn.getresponse()
        self.assertEqual((r.status, r.getheader('Location')), (302, 'http://cdn/tt9:1:2.json'))
        conn.close()
        streams = self.get('/browse/streams/movie/tt1?preset=fast')['streams']
        self.assertEqual(streams[-1]['url'], None)  # infoHash-only goes last

    def test_4_resume(self):
        self.assertEqual(self.call('PUT', '/resume/tt1', {'positionMs': 60000, 'durationMs': 6000000, 'updatedAt': 2000})[0], 200)
        self.call('PUT', '/resume/tt1', {'positionMs': 1, 'durationMs': 6000000, 'updatedAt': 1000})  # older: ignored
        self.assertEqual(self.get('/resume/tt1'), {'positionMs': 60000, 'durationMs': 6000000, 'updatedAt': 2000})
        self.assertEqual([i['id'] for i in self.get('/library?collection=continue')['items']], ['tt1'])
        self.assertEqual(self.get('/resume/tt5'), {})  # nothing stored
        self.call('PUT', '/resume/tt9/1/1', {'positionMs': 2900000, 'durationMs': 3000000})
        nxt = self.get('/library?collection=next')['items']
        self.assertEqual(nxt[0]['nextEpisode'], {'s': 1, 'e': 2, 'title': 'Two'})

    def test_5_search(self):
        # title start, then any word, then genre (tt9 genre Drama, tt4 genre Drama: kept order), then the addon's search
        self.assertEqual([i['id'] for i in self.get('/search?q=Drama')['items']], ['tt1', 'tt3', 'tt9', 'tt4', 'tt5'])

    def test_5b_new(self):
        # Two configured catalogs (fresh2 pages): merged, newest year first, catalog order within a year,
        # duplicates dropped, kept ones marked.
        self.assertEqual([(i['id'], i['inLibrary']) for i in self.get('/new')['items']],
                         [('tt1', True), ('tt4', True), ('tt5', False), ('tt6', False)])

    def test_5c_new_skip_pages_the_merged_list(self):
        self.assertEqual([i['id'] for i in self.get('/new?skip=1')['items']], ['tt4', 'tt5', 'tt6'])

    def test_7_beacon(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            beacon = ('127.0.0.1', self.server.beacon.getsockname()[1])
            s.sendto(b'HELLO', beacon)
            s.sendto(b'MEDIABOX?', beacon)  # unpadded: shorter than the reply, so not answered (no amplification)
            s.sendto(b'MEDIABOX?'.ljust(64), beacon)  # only this one is answered
            reply = s.recvfrom(128)[0]
            self.assertEqual(reply, f'MEDIABOX 1 {self.server.server_port} Media box'.encode())
            self.assertLessEqual(len(reply), 64)
            s.settimeout(0.3)
            with self.assertRaises(socket.timeout):
                s.recvfrom(128)

    def test_8_setup_locked_when_configured(self):
        self.assertEqual(self.raw('GET', '/setup?n=anything')[0], 404)
        self.assertEqual(self.raw('POST', '/setup/open')[0], 401)
        status, opened, _ = self.raw('POST', '/setup/open', headers=self.auth())
        nonce = opened['url'].split('n=')[1]
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=30)
        conn.request('GET', '/setup?n=' + nonce)
        page = conn.getresponse()
        self.assertEqual((page.status, page.getheader('Content-Type')), (200, 'text/html; charset=utf-8'))
        csp = page.getheader('Content-Security-Policy')
        self.assertIn("script-src 'sha256-", csp)
        self.assertNotIn("'unsafe-inline'; style", csp)
        self.assertIn(b'Set up your box', page.read())
        conn.close()
        self.assertEqual(self.raw('GET', '/setup?n=wrong')[0], 403)

    def test_6_unreachable(self):
        self.addon.shutdown()
        self.addon.server_close()
        self.assertEqual(self.get('/play/tt3?json=1')['url'], 'http://cdn/tt3.json')  # cached at keep, under 10 min
        status, body = self.call('GET', '/play/tt5?json=1')
        self.assertEqual(status, 503)
        self.assertIn('unreachable', body['reason'])



class ReleaseTest(Fixture):
    """Release matching and bitrate presets on real-looking names. The Dark Knight runs 152 min."""
    extra = '[presets.max]\nmin_res = "2160p"\n'  # merged over the default max table

    def test_matching_and_unverified_last(self):
        streams = self.get('/browse/streams/movie/tt0468569?preset=max')['streams']
        urls = [s['url'] for s in streams]
        self.assertNotIn('http://cdn/rises', urls)  # 2012, and the extra token "rises"
        self.assertEqual((urls[-1], streams[-1]['badges']['match']), ('http://cdn/mystery', 'unverified'))
        self.assertEqual({s['badges']['match'] for s in streams[:-1]}, {'ok'})
        self.assertEqual(next(s for s in streams if s['url'] == 'http://cdn/imax70')['badges']['mbps'], 61.4)

    def test_cut_and_bitrate(self):
        imax = self.get('/play/tt0468569?preset=max&cut=imax&json=1')
        self.assertEqual((imax['url'], imax['cut']), ('http://cdn/imax70', 'imax'))
        self.assertEqual(self.get('/play/tt0468569?preset=max&json=1')['url'], 'http://cdn/remux60')  # theatrical first
        capped = self.get('/play/tt0468569?preset=standard&max_mbps=25&json=1')
        self.assertEqual((capped['url'], capped['source']['relaxed']), ('http://cdn/1080', []))

    def test_relaxation(self):
        # max wants 2160p and cached; capped at 25 Mbps nothing is left until min_res goes.
        relaxed = self.get('/play/tt0468569?preset=max&max_mbps=25&json=1')
        self.assertEqual((relaxed['url'], relaxed['source']['relaxed']), ('http://cdn/1080', ['require_cached', 'min_res']))

    def test_series_episode_and_pack(self):
        streams = self.get('/browse/streams/series/tt7/1/2?preset=slow&max_res=2160p&max_mbps=100')['streams']
        self.assertEqual(sorted(s['url'] for s in streams), ['http://cdn/episode', 'http://cdn/pack'])
        self.assertEqual(streams[0]['badges']['mbps'], 53.3)  # 20 GB over the episode's own 50 min



class SecurityTest(Fixture):
    """Token, pairing, limits, encrypted settings, logging, bind check."""

    def test_1_token_required(self):
        self.assertEqual(self.raw('GET', '/library')[0], 401)
        self.assertEqual(self.raw('GET', '/library', headers={'Authorization': 'Bearer wrong'})[0], 401)
        self.assertEqual(self.raw('GET', '/library', headers=self.auth())[0], 200)
        status, body, resp = self.raw('GET', '/health', headers={'Origin': 'http://evil.example'})
        self.assertEqual((status, resp.getheader('Access-Control-Allow-Origin')), (200, None))
        # Origin: null (sandboxed iframe, data: page) must not be granted either
        nul = self.raw('OPTIONS', '/pair/request', headers={'Origin': 'null'})[2]
        self.assertIsNone(nul.getheader('Access-Control-Allow-Origin'))
        self.assertEqual(self.raw('HEAD', '/health')[0], 200)  # HEAD is public wherever GET is
        self.assertEqual(self.raw('POST', '/keep', b'', {**self.auth(), 'Content-Length': '-1'})[0], 413)  # not "read to EOF"
        self.assertNotIn('127.0.0.1:', json.dumps(body))  # hosts only, no URLs or ports
        self.assertEqual(self.raw('POST', '/keep', b'x' * (65 * 1024), self.auth())[0], 413)

    def test_2_pair_round_trip_and_rotate(self):
        status, body, _ = self.raw('GET', '/pair')
        self.assertEqual((status, len(body['words'])), (200, 6))
        status, paired, _ = self.raw('POST', '/pair', json.dumps({'words': ' '.join(body['words']).upper()}))
        self.assertEqual((status, 'Bearer ' + paired['token']), (200, self.auth()['Authorization']))
        self.assertEqual(self.raw('GET', '/pair')[0], 403)  # one use, then closed
        old = self.auth()
        self.assertEqual(self.raw('POST', '/pair/rotate', headers=old)[0], 200)
        self.assertEqual(self.raw('GET', '/library', headers=old)[0], 401)
        self.assertEqual(self.raw('GET', '/library', headers=self.auth())[0], 200)

    def test_3_pair_rate_limit(self):
        self.server.RequestHandlerClass.hub.limits['pair'] = security.Bucket(5 / 60, 5)  # fresh bucket for this client
        self.assertEqual(self.raw('POST', '/pair/open', headers=self.auth())[0], 200)
        bad = json.dumps({'words': 'abandon ' * 6})
        self.assertEqual([self.raw('POST', '/pair', bad)[0] for _ in range(6)], [403] * 5 + [429])

    def test_4_encrypted_settings(self):
        key = os.urandom(32)
        secret = b'https://aio.example/stremio/SECRET-TOKEN/manifest.json'
        blob = security.encrypt(key, secret)
        self.assertNotIn(b'SECRET-TOKEN', blob)
        self.assertNotEqual(blob, security.encrypt(key, secret))  # fresh nonce each time
        self.assertEqual(security.decrypt(key, blob), secret)
        with self.assertRaises(ValueError):
            security.decrypt(key, blob[:20] + bytes([blob[20] ^ 1]) + blob[21:])
        with self.assertRaises(ValueError):
            security.decrypt(os.urandom(32), blob)
        con = sqlite3.connect(os.path.join(self.tmp.name, 'hub.sqlite'))
        stored = con.execute("SELECT value FROM settings WHERE name = 'stream_addons'").fetchone()[0]
        con.close()
        self.assertNotIn(b'manifest.json', stored)  # imported from hub.toml, encrypted at rest
        self.assertEqual(self.server.RequestHandlerClass.hub.stream_addons[0].host, '127.0.0.1')

    def test_5_log_has_no_query(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.get('/search?q=supersecret')
            for _ in range(100):  # the line is printed just after the response is sent
                if '/search' in out.getvalue():
                    break
                time.sleep(0.01)
        self.assertIn('GET /search 200', out.getvalue())
        self.assertNotIn('supersecret', out.getvalue())

    def test_6_refuses_public_bind(self):
        with tempfile.TemporaryDirectory() as d:
            config = os.path.join(d, 'hub.toml')
            with open(config, 'w') as f:
                f.write('bind = "0.0.0.0"\nport = 0\ndiscovery_port = 0\n')
            with mock.patch('hub.security.public_addresses', return_value=['203.0.113.7']):
                with self.assertRaises(SystemExit):
                    hub.make_server(config, d)
                with open(config, 'a') as f:
                    f.write('allow_public = true\n')
                server = hub.make_server(config, d)
                server.server_close()
                server.beacon.close()


    def test_7_stream_url_must_be_http_without_crlf(self):
        ctx = ranking.context(None, 'movie')
        raw = [('a', {'url': 'http://cdn/x\r\nSet-Cookie: a=b'}), ('a', {'url': 'javascript:alert(1)'}),
               ('a', {'url': 'file:///etc/passwd'}), ('a', {'url': 'https://cdn/ok?a=1'})]
        self.assertEqual([x['url'] for x in ranking.select(raw, ctx, {})[0]], ['https://cdn/ok?a=1', None, None, None])

    def test_8_ssrf_guard(self):
        addon = f'http://127.0.0.1:{self.addon.server_port}'
        for url in ['http://169.254.169.254/latest/meta-data/', f'http://127.0.0.1:{self.server.server_port}/pair',
                    f'http://localhost:{self.server.server_port}/manifest.json', 'ftp://127.0.0.1/x', 'http://0.0.0.0:3000/m']:
            with self.assertRaises(hub.AddonError, msg=url):
                hub.fetch(url)
        to = urllib.parse.quote(f'http://127.0.0.1:{self.server.server_port}/health', safe='')
        with self.assertRaises(hub.AddonError):  # a redirect back into the hub is refused too
            hub.fetch(f'{addon}/redirect?to={to}')
        self.assertIn('id', hub.fetch(f'{addon}/redirect?to=' + urllib.parse.quote(f'{addon}/manifest.json', safe='')))
        with self.assertRaises(hub.AddonError):  # 200000-deep JSON is an addon error, not a crash
            hub.fetch(f'{addon}/deep')
        with mock.patch('hub.hub.MAX_ADDON', 100), self.assertRaises(hub.AddonError):
            hub.fetch(f'{addon}/manifest.json')

    def test_9_resume_future_clock_cannot_pin(self):
        self.call('PUT', '/resume/tt42', {'positionMs': 5, 'durationMs': 10, 'updatedAt': 99999999999999})
        self.assertLessEqual(self.get('/resume/tt42')['updatedAt'], hub.now_ms())
        self.call('PUT', '/resume/tt42', {'positionMs': 7, 'durationMs': 10})
        self.assertEqual(self.get('/resume/tt42')['positionMs'], 7)
        self.assertEqual(self.call('PUT', '/resume/tt42', {'positionMs': 2**64, 'durationMs': 10})[0], 400)

    def test_a_log_line_has_no_control_chars(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=30)
            conn._validate_path = lambda url: None  # let the hostile path through the client's own check
            conn.putrequest('GET', '/x\x1b[2J\x7fevil', skip_accept_encoding=True)
            conn.endheaders()
            conn.getresponse().read()
            conn.close()
            for _ in range(100):
                if 'evil' in out.getvalue():
                    break
                time.sleep(0.01)
        self.assertIn('/x?[2J?evil', out.getvalue())
        self.assertNotIn('\x1b', out.getvalue())

    def test_b_health_rate_limited(self):
        self.hub.limits['health'] = security.Bucket(1, 5)
        self.assertEqual([self.raw('GET', '/health')[0] for _ in range(6)], [200] * 5 + [429])

    def test_c_secret_write_and_public_route(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'key')
            with open(path + '.tmp', 'wb') as f:  # leftover from a crash, or planted with a wide mode
                f.write(b'old')
            if os.name == 'posix':
                os.chmod(path + '.tmp', 0o666)
            security.write_secret(path, b'k' * 32)
            self.assertFalse(os.path.exists(path + '.tmp'))
            with open(path, 'rb') as f:
                self.assertEqual(f.read(), b'k' * 32)
            if os.name == 'posix':
                self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with mock.patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.1.1', 0))]), \
                mock.patch('hub.security.route_source', return_value='8.8.4.4'):
            self.assertEqual(security.public_addresses('0.0.0.0'), ['8.8.4.4'])  # hostname said loopback; the route says public


class SetupTest(Fixture):
    """An unconfigured hub: nonce, key check against a fake provider, provisioning against a fake AIOStreams, done, TV pairing."""
    streams = False
    extra = 'provider_base = "{addon}"\n'

    def setup(self, body):
        # The suite runs faster than any person; keep the per-IP limit from tripping on fast CI runners.
        self.server.RequestHandlerClass.hub.limits['setup'] = security.Bucket(1000, 1000)
        return self.raw('POST', '/setup', json.dumps({'n': self.nonce, **body}))

    def test_1_nonce_required(self):
        status, link, _ = self.raw('GET', '/setup/link')  # the TV asks from the box itself
        self.assertEqual((status, link['until']), (200, None))  # unconfigured: open until done
        SetupTest.nonce = link['url'].split('n=')[1]
        self.assertEqual(self.raw('GET', '/setup')[0], 403)
        self.assertEqual(self.raw('GET', '/setup?n=wrong')[0], 403)
        self.assertEqual(self.raw('POST', '/setup', json.dumps({'action': 'check'}))[0], 403)
        self.assertEqual(self.raw('GET', '/setup/info?n=' + self.nonce)[1]['configured'], False)

    def test_1b_key_check(self):
        check = lambda key, provider='torbox': self.setup({'action': 'check', 'provider': provider, 'key': key})[1]
        self.assertEqual(check('good-0123456789abcdef')['result'], 'ok')
        self.assertEqual(check('bad-0123456789abcdef'), {'result': 'rejected', 'message': "That key wasn't accepted by TorBox."})
        self.assertEqual(check('down-0123456789abcdef'), {'result': 'unreachable', 'message': "Couldn't reach TorBox; continue anyway?"})
        self.assertEqual(check('short')['result'], 'rejected')  # not even key-shaped: no call
        base = f'http://127.0.0.1:{self.addon.server_port}'
        for provider in ('torbox', 'realdebrid', 'alldebrid', 'premiumize'):  # each provider's own success and refusal shapes
            self.assertEqual([onboarding.check_key(provider, k, base) for k in ('good-0123456789abcdef', 'nope-0123456789abcdef')],
                             ['ok', 'rejected'], provider)
        with socket.socket() as s:  # a port nobody listens on: unreachable, not rejected
            s.bind(('127.0.0.1', 0))
            closed = f'http://127.0.0.1:{s.getsockname()[1]}'
        self.assertEqual(onboarding.check_key('realdebrid', 'good-0123456789abcdef', closed), 'unreachable')
        self.assertFalse(any('0123456789abcdef' in p for p in FakeAddon.provider_paths))  # the key never goes in a URL

    def test_2_provision(self):
        status, failed, _ = self.setup({'action': 'provision', 'provider': 'torbox', 'key': 'FAIL0123456789abcdef'})
        self.assertEqual((status, failed['reason']), (502, 'Invalid torbox credentials'))  # shown, with the link path offered
        status, done, _ = self.setup({'action': 'provision', 'provider': 'torbox', 'key': 'tb-0123456789abcdef'})
        self.assertEqual(status, 200, done)
        self.assertTrue(done['configureUrl'].endswith(AIO_PREFIX + '/configure'))
        sent = FakeAddon.created[-1]
        self.assertEqual(sent['config']['services'], [{'id': 'torbox', 'enabled': True, 'credentials': {'apiKey': 'tb-0123456789abcdef'}}])
        self.assertNotIn('tmdbApiKey', sent['config'])
        # without a TMDB key, AIOStreams 2.37 rejects these (seen on a real instance), so they go off
        self.assertEqual((sent['config']['titleMatching']['enabled'], sent['config']['yearMatching']['enabled']), (False, False))
        self.assertEqual(len(sent['password']), 12)
        con = sqlite3.connect(os.path.join(self.tmp.name, 'hub.sqlite'))
        rows = con.execute('SELECT value FROM settings').fetchall()
        con.close()
        self.assertFalse(any(b'enc-1' in v or b'tb-0123' in v for v, in rows))  # manifest URL encrypted, debrid key never stored
        streams = self.get('/browse/streams/movie/tt1?preset=fast')['streams']
        self.assertIn('http://cdn/4kweb', [s['url'] for s in streams])  # served through the provisioned addon

    def test_3_speed_and_done(self):
        self.assertEqual(self.setup({'action': 'internet', 'preset': 'fast'})[0], 200)
        self.assertEqual(self.hub.preset, 'fast')
        status, done, _ = self.setup({'action': 'done'})
        self.assertEqual((status, len(done['words'])), (200, 6))

    def test_4_tv_pairing(self):
        status, req, _ = self.raw('POST', '/pair/request', json.dumps({'name': 'Living room'}))
        self.assertEqual((status, len(req['code']), req['code'].isdigit()), (200, 6, True))
        self.assertIn('/setup?n=' + self.nonce, req['setupUrl'])  # never paired yet: the TV shows it as a QR
        self.assertEqual(self.raw('GET', '/pair/request/' + req['requestId'])[0], 202)  # pending
        self.assertEqual(self.raw('GET', '/pair/requests')[0], 401)  # listing needs the setup page or a paired device
        pending = self.raw('GET', '/pair/requests?n=' + self.nonce)[1]['requests']
        self.assertEqual([(p['name'], p['code']) for p in pending], [('Living room', req['code'])])
        self.assertEqual(self.raw('POST', '/pair/approve', json.dumps({'n': self.nonce, 'requestId': req['requestId']}))[0], 200)
        status, got, _ = self.raw('GET', '/pair/request/' + req['requestId'])
        self.assertEqual((status, 'Bearer ' + got['token']), (200, self.auth()['Authorization']))
        self.assertEqual(self.raw('GET', '/pair/request/' + req['requestId'])[0], 404)  # handed out once
        old = self.raw('POST', '/pair/request', json.dumps({'name': 'Bedroom'}))[1]
        self.assertIsNone(old['setupUrl'])  # paired now: an unknown LAN device must not get the approving nonce
        self.hub.pair_requests[old['requestId']]['created'] -= 601
        self.assertEqual(self.raw('GET', '/pair/request/' + old['requestId'])[0], 410)



class ShelfTest(Fixture):
    """Home rows, collections and franchises. The fake addon lists one catalog (movie/top)."""
    extra = 'tmdb_base = "{addon}/3"\n'

    def shelves(self):
        return self.get('/shelves')['shelves']

    def test_1_default_shelves(self):
        for id_ in ('tt1', 'tt3', 'tt4'):
            self.call('POST', '/keep', {'id': id_, 'type': 'movie'})
        self.assertEqual([(s['id'], s['title'], s['hidden']) for s in self.shelves()],
                         [('continue', 'Continue watching', False), ('next', 'Next up', False), ('new', 'New', False),
                          ('catalog:127.0.0.1:movie:top', 'Top · Movies', False)])
        self.assertEqual([i['id'] for i in self.get('/shelf/catalog:127.0.0.1:movie:top')['items']], ['tt1', 'tt5'])

    def test_2_reorder_and_hide_persist(self):
        rows = self.shelves()[::-1]
        rows[1]['hidden'], rows[0]['title'] = True, 'Popular'
        self.assertEqual(self.call('PUT', '/shelves', rows)[0], 200)
        self.assertEqual([(s['id'], s['title'], s['hidden']) for s in self.shelves()],
                         [('catalog:127.0.0.1:movie:top', 'Popular', False), ('new', 'New', True),
                          ('next', 'Next up', False), ('continue', 'Continue watching', False)])
        self.assertEqual(self.call('PUT', '/shelves', [{'kind': 'builtin', 'ref': 'nope'}])[0], 400)

    def test_3_manual_collection(self):
        status, col = self.call('POST', '/collections', {'title': 'Faves', 'kind': 'manual'})
        self.assertEqual(self.shelves()[-1]['id'], 'collection:' + col['id'])  # appears as a Home row
        self.call('POST', f'/collections/{col["id"]}/items', {'id': 'tt5'})  # not kept: type found via the meta addon
        self.call('POST', f'/collections/{col["id"]}/items', {'id': 'tt1'})
        items = self.get(f'/shelf/collection:{col["id"]}')['items']
        self.assertEqual([(i['id'], i['title'], i['inLibrary']) for i in items], [('tt5', 'Dramaland', False), ('tt1', 'Drama Club', True)])
        self.assertEqual(self.call('DELETE', f'/collections/{col["id"]}/items/tt5')[0], 200)
        self.assertEqual([i['id'] for i in self.get(f'/shelf/collection:{col["id"]}')['items']], ['tt1'])
        self.assertEqual(self.call('DELETE', f'/collections/{col["id"]}')[0], 200)
        self.assertNotIn('collection:' + col['id'], [s['id'] for s in self.shelves()])

    def test_4_smart_collection(self):
        status, col = self.call('POST', '/collections', {'title': 'Comedies', 'kind': 'smart', 'filter': {'genres': ['comedy'], 'sort': 'az'}})
        # library (tt1, tt3) plus the catalog (tt5), A-Z
        self.assertEqual([i['title'] for i in self.get(f'/shelf/collection:{col["id"]}')['items']], ['Drama Club', 'Dramaland', 'The Last Drama'])
        status, drama = self.call('POST', '/collections', {'title': 'Drama', 'kind': 'smart', 'filter': {'genres': ['Drama'], 'type': 'movie'}})
        self.assertEqual([i['id'] for i in self.get(f'/shelf/collection:{drama["id"]}')['items']], ['tt4'])
        self.assertEqual(self.call('POST', f'/collections/{drama["id"]}/items', {'id': 'tt1'})[0], 409)
        self.assertEqual(self.call('POST', '/collections', {'title': 'X', 'kind': 'smart', 'filter': {'length': ['forever']}})[0], 400)

    def test_5_franchise_needs_a_key(self):
        self.assertEqual(self.call('GET', '/franchise/tt0468569')[0], 404)
        self.assertEqual(FakeAddon.tmdb_calls, [])  # no key, no TMDB call

    def test_6_franchise_with_key(self):
        nonce = self.call('POST', '/setup/open')[1]['url'].split('n=')[1]
        key = '0123456789abcdef0123456789abcdef'
        self.assertEqual(self.raw('POST', '/setup', json.dumps({'n': nonce, 'action': 'tmdb', 'key': key}))[0], 200)
        f = self.get('/franchise/tt0468569')
        self.assertEqual((f['title'], f['collectionId']), ('The Dark Knight Collection', 263))
        self.assertEqual([(p['id'], p['title']) for p in f['parts']],  # by release; the part with no IMDb id is left out
                         [('tt0372784', 'Batman Begins'), ('tt0468569', 'The Dark Knight'), ('tt1345836', 'The Dark Knight Rises')])
        self.assertTrue(all(q.get('api_key') == [key] for _, q in FakeAddon.tmdb_calls))
        rows = self.shelves() + [{'kind': 'franchise', 'ref': 263, 'title': 'The Dark Knight Trilogy'}]
        self.call('PUT', '/shelves', rows)
        self.assertEqual(len(self.get('/shelf/franchise:263')['items']), 3)
        self.assertEqual(self.raw('GET', '/shelves', headers={'X-Setup-Nonce': nonce})[0], 200)  # the setup page edits rows
        self.assertEqual(self.raw('GET', '/shelves')[0], 401)
        self.raw('POST', '/setup', json.dumps({'n': nonce, 'action': 'tmdb', 'key': ''}))
        self.assertEqual(self.call('GET', '/franchise/tt0468569')[0], 404)  # key removed: even the cached answer stops


if __name__ == '__main__':
    unittest.main()
