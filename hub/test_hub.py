"""Run from the project root: python -I -m unittest discover -s hub -t .

(-I drops the working directory from sys.path, so `python -I -m unittest hub.test_hub` cannot import
any local package; discover -t . puts the project root back.) Without -I: python -m unittest hub.test_hub
"""
import http.client
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hub import hub

YEAR = time.localtime().tm_year


def movie(id_, name, genres=('Comedy',), year=YEAR):
    return {'id': id_, 'type': 'movie', 'name': name, 'releaseInfo': str(year), 'runtime': '101 min', 'genres': list(genres),
            'poster': f'http://img/{id_}.jpg', 'background': f'http://img/{id_}-bg.jpg', 'description': f'{name} plot'}


METAS = {'tt1': movie('tt1', 'Drama Club'), 'tt3': movie('tt3', 'The Last Drama'), 'tt4': movie('tt4', 'Heat', ['Crime', 'Drama']),
         'tt5': movie('tt5', 'Dramaland', year=YEAR - 1), 'tt6': movie('tt6', 'Oldie', year=1999),
         'tt9': {'id': 'tt9', 'type': 'series', 'name': 'Show', 'releaseInfo': '2019–', 'genres': ['Drama'],
                 'videos': [{'id': 'tt9:1:2', 'season': 1, 'episode': 2, 'name': 'Two'},
                            {'id': 'tt9:1:1', 'season': 1, 'episode': 1, 'name': 'One'},
                            {'id': 'tt9:0:1', 'season': 0, 'episode': 1, 'name': 'Special'}]}}
TT1_STREAMS = [
    {'name': 'AIO 1080p', 'description': 'Drama.Club.1080p.WEB-DL.DDP5.1 4.2 GB ⚡', 'url': 'http://cdn/1080web'},
    {'name': 'AIO 4K', 'description': '2160p WEB-DL DV HDR10 DD+ Atmos 18 GB ⚡', 'url': 'http://cdn/4kweb',
     'behaviorHints': {'notWebReady': True, 'proxyHeaders': {'request': {'Authorization': 'Bearer t'}}}},
    {'name': 'AIO 4K', 'description': '2160p BluRay REMUX HDR10 TrueHD Atmos 7.1 60 GB ⚡', 'url': 'http://cdn/remux'},
    {'name': 'AIO 4K', 'description': '2160p REMUX DV TrueHD 70GB ⏳', 'url': 'http://cdn/uncached-remux'},
    {'name': 'Torrent 4K', 'title': '2160p REMUX DV TrueHD', 'infoHash': 'abc'},
    {'name': 'AIO 720p', 'description': '720p WEB 1 GB ⚡', 'url': 'http://cdn/720'},
]


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
        elif p == '/stream/movie/tt1.json':
            body = {'streams': TT1_STREAMS}
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


class HubTest(unittest.TestCase):
    """Steps run in name order and build on each other: keep, library, play, resume, search, unreachable."""

    @classmethod
    def setUpClass(cls):
        cls.addon = ThreadingHTTPServer(('127.0.0.1', 0), FakeAddon)
        threading.Thread(target=cls.addon.serve_forever, daemon=True).start()
        cls.tmp = tempfile.TemporaryDirectory()
        manifest = f'http://127.0.0.1:{cls.addon.server_port}/manifest.json'
        config = f'{cls.tmp.name}/hub.toml'
        with open(config, 'w') as f:
            f.write(f'bind = "127.0.0.1"\nport = 0\npreset = "balanced"\nmeta_addons = ["{manifest}"]\nstream_addons = ["{manifest}"]\n'
                    f'new_catalog = {{addon = "{manifest}", type = "movie", id = "fresh"}}\n')
        cls.server = hub.make_server(config, cls.tmp.name)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def get(self, path):
        status, body = self.call('GET', path)
        self.assertEqual(status, 200, body)
        return body

    def test_1_keep(self):
        for id_, type_ in [('tt1', 'movie'), ('tt3', 'movie'), ('tt4', 'movie'), ('tt9', 'series')]:
            status, item = self.call('POST', '/keep', {'id': id_, 'type': type_})
            self.assertEqual(status, 200, item)
        self.assertEqual(item['nextEpisode'], {'s': 1, 'e': 1, 'title': 'One'})
        status, again = self.call('POST', '/keep', {'id': 'tt1', 'type': 'movie'})
        self.assertEqual((again['title'], again['year'], again['runtimeMin'], again['inLibrary']), ('Drama Club', YEAR, 101, True))
        self.assertEqual(again['badges'], {'res': '2160p', 'hdr': ['DV', 'HDR10'], 'audio': ['Atmos', 'DD+']})  # balanced pick
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
        self.assertEqual(self.get('/play/tt1?preset=best&json=1')['url'], 'http://cdn/remux')
        self.assertEqual(self.get('/play/tt1?preset=balanced&json=1')['url'], 'http://cdn/4kweb')
        self.assertEqual(self.get('/play/tt1?preset=saver&json=1')['url'], 'http://cdn/1080web')
        self.assertEqual(self.get('/play/tt1?json=1')['headers'], {'Authorization': 'Bearer t'})
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=30)
        conn.request('GET', '/play/tt9/1/2')
        r = conn.getresponse()
        self.assertEqual((r.status, r.getheader('Location')), (302, 'http://cdn/tt9:1:2.json'))
        conn.close()
        streams = self.get('/browse/streams/movie/tt1?preset=best')['streams']
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


if __name__ == '__main__':
    unittest.main()
