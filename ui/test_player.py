"""Runs player.lua against a stand-in for mpv. Needs lupa (pip install lupa); skipped without it."""
import os
import unittest

try:
    import lupa
except ImportError:
    lupa = None

STUB = r"""
props = { pause = true, ['time-pos'] = 100, duration = 1000, ['user-data/mediabox/look'] = 'auto', ['user-data/mediabox/title'] = 'A {bad} title' }
bindings, commands, timers, events, clock, grab = {}, {}, {}, {}, 0, nil
overlay = { update = function() end, data = '' }
mp = {
  create_osd_overlay = function() return overlay end,
  -- As mpv does: user-data read as a string comes back JSON-quoted.
  get_property = function(n, d) local v = props[n]; if v == nil then return d end; if n:find('user-data/') then return '"' .. v .. '"' end; return v end,
  get_property_number = function(n, d) return props[n] or d end,
  get_property_bool = function(n) return props[n] end,
  get_property_native = function(n, d) local v = props[n]; if v == nil then return d end; return v end,
  set_property_native = function(n, v) props[n] = v end,
  set_property_bool = function(n, v) props[n] = v end,
  set_property_number = function(n, v) props[n] = v end,
  register_event = function(n, f) events[n] = f end,
  get_time = function() return clock end,
  commandv = function(...) commands[#commands + 1] = table.concat({...}, ' ') end,
  add_forced_key_binding = function(k, _, f) bindings[k] = f end,
  add_timeout = function(_, f) timers[#timers + 1] = f end,
  add_periodic_timer = function(every, f)
    if every == 0.5 then tick = f elseif every == 0.1 then load_check = f else anim = f end
    return { kill = function() end, resume = function() end }
  end,
  command_native_async = function(c, f)
    if c.name == 'sub-add' then added = c else grab = f end
  end,
}
function frame(level, format)
  return { w = 96, h = 54, stride = 384, format = format or 'bgr0', data = string.rep(string.char(level, level, level, 0), 96 * 54) }
end
"""
HERE = os.path.dirname(os.path.abspath(__file__))
NIGHT, PAPER = '0E1114', 'E4EFF4'  # sheet colours as ASS writes them


@unittest.skipIf(lupa is None, 'lupa is not installed')
class Controls(unittest.TestCase):
    def setUp(self):
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(STUB)
        with open(os.path.join(HERE, 'player.lua'), encoding='utf-8') as f:
            self.lua.execute(f.read())
        self.g = self.lua.globals()
        self.start_film()

    def start_film(self):
        self.g.events['playback-restart']()
        self.g.props['demuxer-cache-duration'] = 10
        self.g.clock = 3
        self.g.load_check()
        self.g.clock = 4
        self.g.anim()

    def press(self, name):
        self.g.bindings[name]()

    def screen(self):
        self.g.clock = self.g.clock + 1  # lets any slide finish
        self.g.anim()
        return self.g.overlay.data

    def commands(self):
        return list(self.g.commands.values())

    def rise_over(self, level, fmt='bgr0'):
        self.press('m')
        self.g.grab(True, self.g.frame(level, fmt))

    def test_dark_scene_gets_night_and_bright_gets_paper(self):
        self.rise_over(20)
        self.assertIn(NIGHT, self.screen())
        self.press('BS')
        self.rise_over(230)
        self.assertIn(PAPER, self.screen())

    def test_sheet_fades_in(self):
        self.g.props['user-data/mediabox/look'] = 'night'
        self.press('ENTER')
        self.g.clock = self.g.clock + 0.15
        self.g.anim()
        self.assertIn('&H96&', self.g.overlay.data)  # half way: the sheet's alpha, part faded
        self.assertIn(NIGHT, self.screen())
        self.assertIn('&H2E&', self.g.overlay.data)  # settled

    def test_remote_keys_with_the_sheet_down(self):
        self.g.props['user-data/mediabox/look'] = 'night'
        self.press('ENTER')
        self.assertEqual(self.commands(), ['cycle pause'])
        self.press('BS')
        self.screen()
        self.press('UP')
        self.assertIn(NIGHT, self.screen())
        self.press('LEFT')  # on the progress bar, so this seeks
        self.assertEqual(self.commands()[-1], 'seek -10')
        self.press('BS')
        self.screen()
        self.press('DOWN')
        self.assertIn('SUBTITLES', self.screen())

    def test_media_buttons(self):
        self.g.props['user-data/mediabox/look'] = 'night'
        self.press('FORWARD')
        self.press('PLAYPAUSE')
        self.assertEqual(self.commands(), ['seek 30', 'cycle pause'])
        self.press('PAUSE')
        self.assertTrue(self.g.props.pause)
        self.press('PLAY')
        self.assertFalse(self.g.props.pause)
        self.press('STOP')
        self.screen()
        self.assertEqual(self.commands()[-1], 'quit')

    def test_stats_toggle_and_show_passthrough(self):
        self.g.props['audio-out-params/format'] = 'spdif-truehd'
        self.g.props['hwdec-current'] = 'vaapi'
        self.press('i')
        self.assertIn('vaapi', self.screen())
        self.assertIn('Passed through', self.screen())
        self.press('i')
        self.assertNotIn('vaapi', self.screen())

    def test_look_holds_while_showing(self):
        self.rise_over(20)
        self.g.grab = None
        self.press('LEFT')
        self.assertIsNone(self.g.grab)  # no second frame grab
        self.assertIn(NIGHT, self.screen())

    def test_unreadable_failed_or_slow_frames_fall_back_to_night(self):
        self.rise_over(230, 'rgba64')
        self.assertIn(NIGHT, self.screen())
        self.press('BS')
        self.press('ENTER')
        self.g.grab(False, None)
        self.assertIn(NIGHT, self.screen())
        self.press('BS')
        self.press('ENTER')
        self.g.timers[len(self.g.timers)]()
        self.assertIn(NIGHT, self.screen())
        self.g.grab(True, self.g.frame(230))  # too late to change it
        self.assertIn(NIGHT, self.screen())

    def test_setting_skips_the_frame_grab(self):
        self.g.props['user-data/mediabox/look'] = 'paper'
        self.press('ENTER')
        self.assertIsNone(self.g.grab)
        self.assertIn(PAPER, self.screen())

    def test_hidden_keys(self):
        self.press('RIGHT')
        self.assertEqual(self.commands(), ['seek 30'])
        self.g.grab(True, self.g.frame(20))
        self.press('BS')
        self.assertEqual(self.screen(), '')
        self.press('BS')
        self.assertNotIn('quit', self.commands())  # fades first
        self.g.clock = self.g.clock + 0.175
        self.g.anim()
        self.assertAlmostEqual(self.g.props.volume, 50)
        self.screen()
        self.assertEqual(self.commands()[-1], 'quit')

    def test_hides_when_idle_but_not_when_paused(self):
        self.rise_over(20)
        self.g.tick()
        self.assertNotEqual(self.screen(), '')
        self.g.props.pause = True
        self.g.clock = 20
        self.g.tick()
        self.assertNotEqual(self.screen(), '')
        self.g.props.pause = False
        self.g.tick()
        self.assertEqual(self.screen(), '')

    def test_other_versions_tells_the_bridge_then_quits(self):
        self.rise_over(20)
        for _ in range(5):
            self.press('RIGHT')
        self.press('ENTER')
        self.screen()
        self.assertEqual(self.commands(), ['script-message mediabox-versions', 'quit'])

    def test_subtitle_menu_picks_off(self):
        self.g.props['track-list'] = self.lua.eval("{ { type = 'sub', id = 2, lang = 'eng', selected = true }, { type = 'audio', id = 1, title = 'Main' } }")
        self.g.props.sid = 2
        self.rise_over(20)
        for _ in range(4):
            self.press('RIGHT')
        self.press('ENTER')
        self.assertIn('SUBTITLES', self.screen())
        self.press('UP')
        self.press('ENTER')
        self.assertEqual(self.g.props.sid, 'no')
        self.assertNotIn('SUBTITLES', self.screen())

    def open_subtitles(self):
        self.rise_over(20)
        for _ in range(4):
            self.press('RIGHT')
        self.press('ENTER')

    def test_addon_subtitle_is_fetched_only_when_chosen(self):
        self.g.props['user-data/mediabox/subtitles'] = self.lua.eval("{ { url = 'https://s/en', lang = 'eng', label = 'English' } }")
        self.open_subtitles()
        self.assertIn('English', self.screen())
        self.assertIsNone(self.g.added)
        self.press('DOWN')
        self.press('ENTER')
        self.assertEqual((self.g.added.url, self.g.added.flags, self.g.added.lang), ('https://s/en', 'select', 'eng'))

    def test_fetched_subtitle_is_not_listed_twice(self):
        self.g.props['user-data/mediabox/subtitles'] = self.lua.eval("{ { url = 'https://s/en', lang = 'eng', label = 'English' } }")
        self.g.props['track-list'] = self.lua.eval("{ { type = 'sub', id = 3, title = 'English', ['external-filename'] = 'https://s/en', selected = true } }")
        self.open_subtitles()
        self.assertEqual(self.screen().count('English'), 1)

    def test_title_cannot_inject_drawing_codes(self):
        self.rise_over(20)
        self.assertIn('}A \\{bad\\} title', self.screen())  # and no quote marks around it


@unittest.skipIf(lupa is None, 'lupa is not installed')
class Loading(unittest.TestCase):
    setUp = Controls.setUp
    commands = Controls.commands

    def start_film(self):
        pass

    def test_film_waits_behind_the_card(self):
        self.g.load_check()  # nothing ready yet
        self.assertTrue(self.g.props.pause)
        self.g.events['playback-restart']()  # the first frame is ready
        self.g.load_check()
        self.assertFalse(self.g.props.pause)

    def test_sound_fades_in_with_the_picture(self):
        self.assertEqual(self.g.props.volume, 0)
        self.g.clock = 26
        self.g.load_check()
        self.g.clock = 26.3
        self.g.anim()
        self.assertAlmostEqual(self.g.props.volume, 50)
        self.g.clock = 27
        self.g.anim()
        self.assertEqual(self.g.props.volume, 100)

    def test_a_stuck_stream_still_starts(self):
        self.g.clock = 26
        self.g.load_check()
        self.assertFalse(self.g.props.pause)

    def test_keys_wait_except_back(self):
        self.g.bindings['ENTER']()
        self.assertIsNone(self.g.grab)
        self.g.bindings['BS']()
        self.assertEqual(self.commands(), ['quit'])


if __name__ == '__main__':
    unittest.main()
