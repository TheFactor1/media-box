import json
import unittest

import shell


class Allowed(unittest.TestCase):
    def test_browsing_calls_pass(self):
        for path in ('/api/shelves', '/api/shelf/new?skip=20', '/api/browse/meta/movie/tt1', '/api/resume/tt1/2/3', '/api/search?q=salt'):
            self.assertIsNotNone(shell.allowed(path), path)

    def test_query_is_kept(self):
        self.assertEqual(shell.allowed('/api/search?q=salt'), '/search?q=salt')

    def test_setup_pairing_and_climbing_are_refused(self):
        for path in ('/api/setup', '/api/setup/link', '/api/pair', '/api/pair/approve', '/api/devices', '/api/shelf/../pair',
                     '/api//pair', '/shelves', '/api/', '/api/browse/streams/movie/tt1/x/y'):
            self.assertIsNone(shell.allowed(path), path)


class Streams(unittest.TestCase):
    def test_page_never_sees_stream_links(self):
        out = shell.strip_urls(b'{"streams": [{"url": "https://debrid/secret", "headers": {"a": "b"}, "badges": {}}, {"badges": {}}]}')
        self.assertNotIn(b'secret', out)
        self.assertEqual(json.loads(out)['streams'][0]['playable'], True)
        self.assertEqual(json.loads(out)['streams'][1]['playable'], False)


class Choose(unittest.TestCase):
    def test_page_cannot_supply_a_url_or_bad_id(self):
        for req in ({'id': 'http://evil', 'type': 'movie'}, {'id': 'tt1/../../pair', 'type': 'movie'}, {'id': 'tt1', 'type': 'x'}, {}):
            with self.assertRaises(ValueError):
                shell.PLAYER.choose(req)

    def test_episode_must_be_numbers(self):
        self.assertEqual(shell.episode(2, 3), '/2/3')
        self.assertEqual(shell.episode(None, None), '')
        with self.assertRaises(ValueError):
            shell.episode('1/../..', 3)


class Copies(unittest.TestCase):
    def test_the_copy_list_asks_for_everything(self):
        self.assertIn('all=1', shell.list_query())
        self.assertNotIn('all=1', shell.prefs_query())


class Controls(unittest.TestCase):
    def test_info_for_the_sheet(self):
        info = shell.player_info({'item': {'title': 'Heat'}, 'ep': {'s': 2, 'e': 3}}, {'playerLook': 'paper'})
        self.assertEqual(info, {'title': 'Heat', 'kicker': 'Season 2, episode 3', 'look': 'paper'})

    def test_unknown_look_is_automatic(self):
        self.assertEqual(shell.player_info({}, {'playerLook': 'x'}), {'title': '', 'kicker': '', 'look': 'auto'})

    def test_other_versions_keeps_the_way_back(self):
        out = shell.to_versions({'page': 'film', 'item': {'id': 'tt1'}, 'history': [{'page': 'home'}]})
        self.assertEqual(out['page'], 'versions')
        self.assertEqual([h['page'] for h in out['history']], ['home', 'film'])
        self.assertEqual(shell.to_versions(out), out)


if __name__ == '__main__':
    unittest.main()
