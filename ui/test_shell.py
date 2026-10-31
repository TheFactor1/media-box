import json
import os
import tempfile
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


class Rehearsal(unittest.TestCase):
    def test_waits_for_length_position_and_picture(self):
        self.assertEqual(shell.rehearsal_verdict(None, None, None, None, False), 'wait')
        self.assertEqual(shell.rehearsal_verdict(6000, 0, 2, False, False), 'wait')
        self.assertEqual(shell.rehearsal_verdict(6000, 0, 5, False, False), 'go')
        self.assertEqual(shell.rehearsal_verdict(6000, 0, 1, True, False), 'go')

    def test_when_time_runs_out(self):
        self.assertEqual(shell.rehearsal_verdict(6000, 0, 1, False, True), 'go')
        self.assertEqual(shell.rehearsal_verdict(None, None, None, None, True), 'fail')


class Opening(unittest.TestCase):
    def test_rule_fills_with_real_progress(self):
        self.assertEqual(shell.rehearsal_progress(None, None, None, None), 0.25)
        self.assertEqual(shell.rehearsal_progress(6000, 0, 0, False), 0.5)
        self.assertEqual(shell.rehearsal_progress(6000, 0, 50, False), 1)
        self.assertEqual(shell.rehearsal_progress(6000, 0, 1, True), 1)

    def test_back_only_cancels_while_opening(self):
        player = shell.Player()
        self.assertFalse(player.cancel_opening())
        player.stage = 'preparing'
        self.assertTrue(player.cancel_opening())
        self.assertTrue(player.cancelled.is_set())
        player.stage = 'ready'
        self.assertFalse(player.cancel_opening())


class AfterFilm(unittest.TestCase):
    ITEM = {'id': 'tt1', 'type': 'movie'}

    def test_copy_list_returns_to_the_film_page(self):
        state = {'page': 'versions', 'item': self.ITEM, 'key': 'copy:22', 'history': [{'page': 'home'}, {'page': 'film'}]}
        out = shell.after_film(state, 600, 6000, False)
        self.assertEqual((out['page'], out['key']), ('film', 'film:play'))
        self.assertEqual([h['page'] for h in out['history']], ['home'])

    def test_home_returns_to_the_title_with_back_to_home(self):
        state = {'page': 'home', 'item': {'id': 'tt2', 'type': 'series'}, 'key': 'row:3', 'history': []}
        out = shell.after_film(state, 600, 6000, False)
        self.assertEqual((out['page'], out['key']), ('show', 'show:play'))
        self.assertEqual(out['history'][0]['key'], 'row:3')

    def test_other_versions_goes_to_the_top_of_the_list(self):
        state = {'page': 'versions', 'item': self.ITEM, 'key': 'copy:22'}
        out = shell.after_film(state, 600, 6000, True)
        self.assertEqual((out['page'], out['key']), ('versions', None))

    def test_stop_press(self):
        self.assertEqual(shell.stop_press(4324, 7444), 'Stop press. You stopped at 1:12:04, with 52 minutes left.')
        self.assertEqual(shell.stop_press(600, 8000), 'Stop press. You stopped at 10:00, with 2 hours 3 minutes left.')
        self.assertIsNone(shell.stop_press(30, 6000))
        self.assertIsNone(shell.stop_press(5900, 6000))


class Browser(unittest.TestCase):
    def test_finds_chromium_and_its_helpers_only(self):
        with tempfile.TemporaryDirectory() as proc:
            for pid, command in (('10', b'/usr/lib64/chromium-browser/chromium-browser\0--type=broker\0'), ('13', b'/usr/lib64/chromium-browser/chromium-browser --type=gpu-process --user-data-dir=/var/lib/media/ui-profile'),
                                 ('11', b'/usr/bin/cage\0--\0chromium\0'), ('12', b''), ('self', b'x')):
                os.mkdir(os.path.join(proc, pid))
                with open(os.path.join(proc, pid, 'cmdline'), 'wb') as f:
                    f.write(command)
            self.assertEqual(sorted(shell.browser_pids(proc)), [10, 13])


class Recovery(unittest.TestCase):
    def test_backups_skip_the_failed_copy_and_torrents(self):
        streams = [{'url': 'a'}, {'url': None}, {'url': 'b'}, {'url': 'b'}, {'url': 'c'}, {'url': 'd'}]
        self.assertEqual([s['url'] for s in shell.backups(streams, 'a')], ['b', 'c'])


class Remote(unittest.TestCase):
    def test_presses_and_repeats_pass_but_not_releases(self):
        self.assertEqual(shell.remote_key(shell.EV_KEY, 158, 1), 'BS')
        self.assertEqual(shell.remote_key(shell.EV_KEY, 208, 2), 'FORWARD')
        self.assertIsNone(shell.remote_key(shell.EV_KEY, 158, 0))
        self.assertIsNone(shell.remote_key(4, 158, 1))

    def test_typing_on_a_keyboard_is_not_looked_at(self):
        for code in (28, 30, 103, 105, 106, 108, 1):  # Enter, A, the arrows, Esc
            self.assertIsNone(shell.remote_key(shell.EV_KEY, code, 1))


class Subtitles(unittest.TestCase):
    def test_short_named_menu(self):
        subs = [{'url': f'https://s/{i}', 'lang': 'eng'} for i in range(5)] + [{'url': 'https://s/fr', 'lang': 'fre'}]
        out = shell.pick_subtitles(subs)
        self.assertEqual([x['label'] for x in out], ['English', 'English 2', 'English 3', 'French'])

    def test_addon_junk_is_dropped(self):
        subs = [{'url': 'file:///etc/passwd', 'lang': 'eng'}, {'url': 'https://s/a\nb', 'lang': 'eng'}, {'url': 7}, {'lang': 'eng'}, {'url': 'https://s/ok', 'lang': '{x}'}]
        self.assertEqual(shell.pick_subtitles(subs), [{'url': 'https://s/ok', 'lang': 'und', 'label': 'Other'}])


class Controls(unittest.TestCase):
    def test_info_for_the_sheet(self):
        info = shell.player_info({'item': {'title': 'Heat'}, 'ep': {'s': 2, 'e': 3}}, {'playerLook': 'paper'})
        self.assertEqual(info, {'title': 'Heat', 'kicker': 'Season 2, episode 3', 'deck': '', 'look': 'paper'})

    def test_unknown_look_is_automatic(self):
        self.assertEqual(shell.player_info({}, {'playerLook': 'x'}), {'title': '', 'kicker': '', 'deck': '', 'look': 'auto'})

    def test_deck_fits_one_line(self):
        self.assertEqual(shell.deck('A thief. He runs away for a very long time.'), 'A thief.')
        long = 'word ' * 40
        out = shell.deck(long)
        self.assertTrue(len(out) <= 111 and out.endswith('word…'))
        self.assertEqual(shell.deck(None), '')

    def test_other_versions_keeps_the_way_back(self):
        out = shell.to_versions({'page': 'film', 'item': {'id': 'tt1'}, 'history': [{'page': 'home'}]})
        self.assertEqual(out['page'], 'versions')
        self.assertEqual([h['page'] for h in out['history']], ['home', 'film'])
        self.assertEqual(shell.to_versions(out), out)


if __name__ == '__main__':
    unittest.main()
