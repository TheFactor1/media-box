"""Run from the project root: python -I -m unittest discover -s hub -t .

(-I drops the working directory from sys.path, so `python -I -m unittest hub.test_hub` cannot import
any local package; discover -t . puts the project root back.) Without -I: python -m unittest hub.test_hub
"""
import contextlib
import http.client
import io
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hub import hub, security

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


class FakeAddon(BaseHTTPRequestHandler):
    def do_GET(self):
        p = self.path
        if p == '/manifest.json':
            body = {'id': 'fake', 'version': '1.0.0', 'name': 'Fake', 'types': ['movie', 'series'], 'idPrefixes': ['tt'],
                    'resources': ['catalog', 'meta', {'name': 'stream', 'types': ['movie', 'series'], 'idPrefixes': ['tt']}],
                    'catalogs': [{'type': 'movie', 'id': 'top', 'name': 'Top', 'extra': [{'name': 'search'}, {'name': 'skip'}]}]}
        elif p == '/catalog/movie/top.json':
            body = {'metas': [METAS['tt1'], METAS['tt5']]}
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

    @classmethod
    def setUpClass(cls):
        cls.addon = ThreadingHTTPServer(('127.0.0.1', 0), FakeAddon)
        threading.Thread(target=cls.addon.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        manifest = f'http://127.0.0.1:{cls.addon.server_port}/manifest.json'
        config = f'{cls.tmp.name}/hub.toml'
        with open(config, 'w') as f:
            f.write(f'bind = "127.0.0.1"\nport = 0\npreset = "standard"\nmeta_addons = ["{manifest}"]\nstream_addons = ["{manifest}"]\n'
                    f'new_catalog = {{addon = "{manifest}", type = "movie", id = "fresh"}}\n' + cls.extra)
        cls.server = hub.make_server(config, cls.tmp.name)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
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
        self.call('PUT', '/resume/tt9/1/1', {'positionMs': 2900000, 'durationMs': 3000000})
        nxt = self.get('/library?collection=next')['items']
        self.assertEqual(nxt[0]['nextEpisode'], {'s': 1, 'e': 2, 'title': 'Two'})

    def test_5_search(self):
        # title start, then any word, then genre (tt9 genre Drama, tt4 genre Drama: kept order), then the addon's search
        self.assertEqual([i['id'] for i in self.get('/search?q=Drama')['items']], ['tt1', 'tt3', 'tt9', 'tt4', 'tt5'])

    def test_5b_new(self):
        # Configured catalog: newest year first, catalog order within a year, duplicates dropped, kept one marked.
        self.assertEqual([(i['id'], i['inLibrary']) for i in self.get('/new')['items']],
                         [('tt1', True), ('tt5', False), ('tt6', False)])

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
        self.assertEqual((status, resp.getheader('Access-Control-Allow-Origin')), (200, 'null'))
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
                f.write('bind = "0.0.0.0"\nport = 0\n')
            with mock.patch('hub.security.public_addresses', return_value=['203.0.113.7']):
                with self.assertRaises(SystemExit):
                    hub.make_server(config, d)
                with open(config, 'a') as f:
                    f.write('allow_public = true\n')
                hub.make_server(config, d).server_close()


if __name__ == '__main__':
    unittest.main()
