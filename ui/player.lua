-- The film's controls: The Banner, from the Player B page of docs/broadsheet-prototype.html, drawn on mpv's OSD at 1920x1080.
-- The sheet's look is chosen once each time it rises and held until it hides, so cuts and flashes can't make it flicker.
-- mpv opens the film paused with no display, behind the loading page. The bridge then gives it the screen, and it starts from black.
local overlay = mp.create_osd_overlay('ass-events')
overlay.res_x, overlay.res_y = 1920, 1080

local LOOKS = {
    night = { sheet = '14110E', alpha = '2E', ink = 'F4EFE4', quiet = 'CFC7B8', soft = '39332C', inverse = '14110E', red = 'E8695A', track = '6B645A', menu = '0D' },
    paper = { sheet = 'F4EFE4', alpha = '2E', ink = '1A1714', quiet = '4F4840', soft = 'DDD6C7', inverse = 'F4EFE4', red = 'A3271C', track = 'B8AE9C', menu = '00' },
}
local BRIGHT = 0.45   -- mean luma above this gets the paper sheet; films are mostly dark, so the bar sits on the night side
local SAMPLE_WAIT = 0.25   -- a slow frame grab mustn't hold the controls back
local IDLE = 4
local DISPLAY, BODY = 'Playfair Display', 'Source Serif 4'
local BUTTONS = {
    { id = 'pp', x = 96, w = 110 }, { id = 'back', x = 226, w = 80, text = '−10' }, { id = 'fwd', x = 326, w = 80, text = '+30' },
    { id = 'audio', x = 960, w = 110, text = 'Audio' }, { id = 'subs', x = 1086, w = 160, text = 'Subtitles' },
    { id = 'copy', x = 1262, w = 240, text = 'Other versions' }, { id = 'stop', x = 1518, w = 306, text = 'Back to the paper' },
}
local MENU_ROWS = 9
-- mpv redraws the OSD only with a new film frame, about 24 a second: too few for a big move to look smooth, enough for a fade with a small rise.
local RISE_TIME, RISE_BY, FADE_TIME, LEAVE_TIME = 0.3, 24, 0.6, 0.35
local BLACK_AT_MOST = 25   -- seconds

local showing, rising, focus, on_seek, menu, last_key = false, false, 1, false, nil, 0
local look = LOOKS.night
local opacity, leaving, stats = 1, false, false
local shown, offset, curtain, loading, restarted, born, last_frame = 0, 0, 1, true, false, mp.get_time(), 0
local anim_timer

local function escape(text)
    return (tostring(text or ''):gsub('[\r\n]', ' '):gsub('\\', '\\\239\187\191'):gsub('{', '\\{'):gsub('}', '\\}'))
end

local function colour(rgb)
    return rgb:sub(5, 6) .. rgb:sub(3, 4) .. rgb:sub(1, 2)
end

-- An ASS alpha (00 is solid) with the sheet's current fade applied.
local function faded(alpha)
    return string.format('%02X', math.floor(255 - (255 - tonumber(alpha, 16)) * opacity))
end

local function box(x, y, w, h, rgb, alpha)
    return string.format('{\\an7\\pos(0,0)\\bord0\\shad0\\1c&H%s&\\1a&H%s&\\p1}m %d %d l %d %d %d %d %d %d{\\p0}', colour(rgb), faded(alpha or '00'), x, y + offset, x + w, y + offset, x + w, y + h + offset, x, y + h + offset)
end

local function text(x, y, align, font, size, rgb, content, bold, clip, tags)
    return string.format('{\\an%d\\pos(%d,%d)\\bord0\\shad0\\fn%s\\fs%d\\b%d\\1c&H%s&\\1a&H%s&%s%s}%s', align, x, y + offset, font, size, bold and 1 or 0, colour(rgb), faded('00'),
        clip and string.format('\\clip(%d,%d,%d,%d)', clip[1], clip[2] + offset, clip[3], clip[4] + offset) or '', tags or '', escape(content))
end

local function clock(seconds)
    local s = math.max(0, math.floor(seconds or 0))
    local h, m = math.floor(s / 3600), math.floor(s / 60) % 60
    if h > 0 then return string.format('%d:%02d:%02d', h, m, s % 60) end
    return string.format('%d:%02d', m, s % 60)
end

local function draw_menu(out)
    local x, y, w = 1180, 170, 644
    local first = math.max(1, math.min(menu.at - MENU_ROWS + 1, #menu.rows - MENU_ROWS + 1))
    local count = math.min(MENU_ROWS, #menu.rows)
    out[#out + 1] = box(x, y, w, 76 + count * 60, look.sheet, look.menu)
    out[#out + 1] = text(x + 28, y + 40, 4, BODY, 24, look.red, menu.title, true)
    for i = first, first + count - 1 do
        local row, top = menu.rows[i], y + 76 + (i - first) * 60
        if i == menu.at then out[#out + 1] = box(x + 12, top, w - 24, 56, look.ink) end
        out[#out + 1] = text(x + 28, top + 28, 4, BODY, 28, i == menu.at and look.inverse or look.ink, (row.selected and '• ' or '') .. row.label, row.selected, { x + 12, top, x + w - 12, top + 56 })
    end
end

-- A fade built from bands, because the overlay has no gradients. first and last are the top and bottom bands' alpha.
local function bands(out, y, height, count, first, last)
    for i = 0, count - 1 do
        local alpha = first + (last - first) * i / (count - 1)
        out[#out + 1] = box(0, y + math.floor(height * i / count), 1920, math.ceil(height / count), look.sheet, string.format('%02X', math.floor(alpha)))
    end
end

local function caps(x, y, align, rgb, content, clip)
    -- Not string.upper: in some locales it changes the bytes of characters like · and −.
    local upper = content:gsub('[a-z]', function(c) return string.char(c:byte() - 32) end)
    return text(x, y, align, BODY, 22, rgb, upper, true, clip, '\\fsp2')
end

-- The Banner: the film's name set as a headline over a fade, with no box in front of the picture.
local function draw_sheet(out)
    local now, total = mp.get_property_number('time-pos', 0), mp.get_property_number('duration', 0)
    local title = mp.get_property_native('user-data/mediabox/title', '')
    bands(out, 0, 120, 6, 0x70, 0xFF)
    bands(out, 560, 520, 26, 0xFF, tonumber(look.alpha, 16))
    out[#out + 1] = box(96, 44, 1728, 2, look.ink)
    out[#out + 1] = box(96, 50, 1728, 2, look.ink)
    out[#out + 1] = caps(96, 77, 4, look.ink, 'The Media Box Times · Now showing')
    out[#out + 1] = caps(1824, 77, 6, look.ink, mp.get_property_native('user-data/mediabox/kicker', ''))
    out[#out + 1] = text(96, 778, 4, DISPLAY, #title > 44 and 56 or #title > 28 and 72 or 100, look.ink, title, true, { 96, 700, 1824, 842 })
    out[#out + 1] = text(96, 864, 4, BODY, 30, look.quiet, mp.get_property_native('user-data/mediabox/deck', ''), false, { 96, 842, 1824, 888 }, '\\i1')
    local share = total > 0 and math.min(1, now / total) or 0
    out[#out + 1] = box(96, 904, 1728, 2, look.ink)
    out[#out + 1] = box(96, 910, 1728, 2, look.ink)
    local bar = on_seek and 20 or 12
    out[#out + 1] = box(96, 908 - bar / 2, math.floor(1728 * share), bar, look.red)
    if on_seek then out[#out + 1] = box(96 + math.floor(1728 * share) - 5, 892, 10, 32, look.ink) end
    out[#out + 1] = text(96, 940, 4, BODY, 24, look.ink, clock(now), true)
    out[#out + 1] = text(1824, 940, 6, BODY, 24, look.ink, '-' .. clock(total - now), true)
    for i, b in ipairs(BUTTONS) do
        local lit = i == focus and not on_seek
        local label = b.text or (mp.get_property_bool('pause') and 'Play' or 'Pause')
        out[#out + 1] = caps(b.x + math.floor(b.w / 2), 1001, 5, lit and look.ink or look.quiet, label)
        if lit then out[#out + 1] = box(b.x, 1030, b.w, 3, look.red) end
    end
    if menu then draw_menu(out) end
end

-- What is really playing, as the Android Player's stats show it. Only read while on screen.
local function stat(name)
    return mp.get_property(name, '–')
end

local function draw_stats(out)
    local through = (mp.get_property('audio-out-params/format', '') or ''):find('spdif') and 'Passed through to the receiver' or 'Decoded here'
    local lines = {
        { 'Picture', stat('video-params/w') .. ' × ' .. stat('video-params/h') .. ' · ' .. stat('video-codec') },
        { 'Colour', stat('video-params/gamma') .. ' · ' .. stat('video-params/primaries') .. ' · peak ' .. stat('video-params/sig-peak') },
        { 'Decoder', stat('hwdec-current') },
        { 'Frame rate', 'film ' .. stat('container-fps') .. ' · display ' .. stat('display-fps') },
        { 'Dropped', stat('frame-drop-count') .. ' shown · ' .. stat('decoder-frame-drop-count') .. ' decoded' },
        { 'Sound', stat('audio-codec-name') .. ' · ' .. stat('audio-params/channel-count') .. ' channels' },
        { 'Sound output', through },
        { 'Held', stat('demuxer-cache-duration') .. ' seconds' },
    }
    offset, opacity = 0, 1
    out[#out + 1] = box(96, 60, 1000, 60 + #lines * 44, LOOKS.night.sheet, '20')
    for i, line in ipairs(lines) do
        local y = 60 + i * 44
        out[#out + 1] = text(124, y, 4, BODY, 24, LOOKS.night.quiet, line[1])
        out[#out + 1] = text(344, y, 4, BODY, 24, LOOKS.night.ink, line[2], true, { 344, y - 22, 1076, y + 22 })
    end
end

local function paint()
    local out = {}
    if shown > 0 then
        opacity = shown * shown * (3 - 2 * shown)
        offset = math.floor(RISE_BY * (1 - opacity))
        draw_sheet(out)
    end
    if stats then draw_stats(out) end
    if curtain > 0 then
        offset, opacity = 0, 1
        out[#out + 1] = box(0, 0, 1920, 1080, '000000', string.format('%02X', math.floor(255 * (1 - curtain))))
    end
    overlay.data = table.concat(out, '\n')
    overlay:update()
end

-- Mean luma over a coarse grid, 0 to 1, or nil for a frame it can't read. A grid keeps a 4K frame to about 1300 reads.
local function brightness(frame)
    if type(frame) ~= 'table' or type(frame.data) ~= 'string' then return nil end
    local red, blue
    if frame.format == 'bgr0' or frame.format == 'bgra' then red, blue = 2, 0
    elseif frame.format == 'rgb0' or frame.format == 'rgba' then red, blue = 0, 2
    else return nil end
    local w, h, stride = frame.w, frame.h, frame.stride
    if not (w and h and stride) or w < 1 or h < 1 or stride < 1 or #frame.data < stride * h then return nil end
    local sum, count = 0, 0
    for row = 0, 26 do
        local y = math.floor((row + 0.5) * h / 27)
        for col = 0, 47 do
            local at = y * stride + math.floor((col + 0.5) * w / 48) * 4 + 1
            local r, g, b = frame.data:byte(at + red), frame.data:byte(at + 1), frame.data:byte(at + blue)
            sum, count = sum + 0.2126 * r + 0.7152 * g + 0.0722 * b, count + 1
        end
    end
    return sum / count / 255
end

-- Runs only while something is moving. Steps by real time, so a dropped frame shortens the move instead of stretching it.
local function animate()
    last_frame = mp.get_time()
    anim_timer:resume()
end

anim_timer = mp.add_periodic_timer(1 / 30, function()
    local now = mp.get_time()
    local dt = now - last_frame
    last_frame = now
    shown = showing and math.min(1, shown + dt / RISE_TIME) or math.max(0, shown - dt / RISE_TIME)
    if leaving then
        curtain = math.min(1, curtain + dt / LEAVE_TIME)
        mp.set_property_number('volume', 100 * (1 - curtain))
        if curtain == 1 then mp.commandv('quit') end
    elseif not loading and curtain > 0 then
        curtain = math.max(0, curtain - dt / FADE_TIME)
        mp.set_property_number('volume', 100 * (1 - curtain))  -- picture and sound arrive together
    end
    paint()
    if not leaving and curtain == 0 and shown == (showing and 1 or 0) then anim_timer:kill() end
end)
anim_timer:kill()

-- Out of the film: picture and sound go down together, then mpv quits.
local function leave()
    if leaving then return end
    leaving = true
    animate()
    mp.add_timeout(LEAVE_TIME + 0.5, function() mp.commandv('quit') end)  -- in case the fade can't run
end

local function rise(chosen)
    look = chosen
    showing, rising = true, false
    last_key = mp.get_time()
    animate()
end

-- Brings the sheet up. Anything uncertain (no frame, odd format, too slow) falls back to night.
local function wake()
    last_key = mp.get_time()
    if showing or rising then return paint() end
    local choice = mp.get_property_native('user-data/mediabox/look', 'auto')
    if LOOKS[choice] then return rise(LOOKS[choice]) end
    rising = true
    mp.add_timeout(SAMPLE_WAIT, function()
        if rising then rise(LOOKS.night) end
    end)
    mp.command_native_async({ name = 'screenshot-raw', flags = 'video' }, function(ok, frame)
        if not rising then return end
        local level = ok and brightness(frame) or nil
        rise(level and level > BRIGHT and LOOKS.paper or LOOKS.night)
    end)
end

local function hide()
    showing, menu, on_seek, focus = false, nil, false, 1
    animate()
end

local function open_menu(kind)
    local rows, fetched = {}, {}
    if kind == 'sub' then rows[1] = { label = 'Off', id = 'no', selected = not mp.get_property_native('sid') } end
    for _, t in ipairs(mp.get_property_native('track-list') or {}) do
        if t.type == kind then
            local name = t.title or t.lang or ('Track ' .. t.id)
            if t.title and t.lang then name = t.title .. ' · ' .. t.lang end
            rows[#rows + 1] = { label = name, id = t.id, selected = t.selected }
            if t['external-filename'] then fetched[t['external-filename']] = true end
        end
    end
    -- Offered by the addons, and only fetched if chosen, so a long list costs nothing. Once fetched it is an ordinary track above.
    if kind == 'sub' then
        for _, s in ipairs(mp.get_property_native('user-data/mediabox/subtitles') or {}) do
            if not fetched[s.url] then rows[#rows + 1] = { label = s.label, url = s.url, lang = s.lang } end
        end
    end
    if #rows == 0 then return end
    local at = 1
    for i, r in ipairs(rows) do
        if r.selected then at = i end
    end
    menu = { kind = kind, title = kind == 'sub' and 'CHOOSE SUBTITLES' or 'CHOOSE AUDIO', rows = rows, at = at }
end

local function press_enter()
    if menu then
        local row = menu.rows[menu.at]
        if row.url then
            mp.command_native_async({ name = 'sub-add', url = row.url, flags = 'select', title = row.label, lang = row.lang }, function() end)
        else
            mp.set_property_native(menu.kind == 'sub' and 'sid' or 'aid', row.id)
        end
        menu = nil
    elseif on_seek then
        mp.commandv('cycle', 'pause')
    else
        local id = BUTTONS[focus].id
        if id == 'pp' then mp.commandv('cycle', 'pause')
        elseif id == 'back' then mp.commandv('seek', -10)
        elseif id == 'fwd' then mp.commandv('seek', 30)
        elseif id == 'audio' then open_menu('audio')
        elseif id == 'subs' then open_menu('sub')
        elseif id == 'copy' then
            mp.commandv('script-message', 'mediabox-versions')
            leave()
        elseif id == 'stop' then leave() end
    end
end

local function step(dx)
    if on_seek then mp.commandv('seek', dx < 0 and -10 or 30)
    elseif not menu then focus = (focus - 1 + dx) % #BUTTONS + 1 end
end

local function vertical(dy)
    if menu then menu.at = (menu.at - 1 + dy) % #menu.rows + 1
    else on_seek = dy < 0 end
end

local function back()
    if menu then menu = nil
    elseif showing then hide()
    else leave() end
end

-- First press of anything but left, right and back only raises the sheet.
local function key(name, action, opts)
    mp.add_forced_key_binding(name, 'mediabox-' .. name, function()
        if loading then
            if action == back then mp.commandv('quit') end
            return
        end
        action()
        if showing or rising then wake() end
    end, opts)
end

local function pause_key()
    mp.commandv('cycle', 'pause')
    wake()
end

-- With the sheet down, as on the Android Player: OK pauses, Up goes to the progress bar, Down opens subtitles.
local function ok_key()
    if showing then press_enter() else pause_key() end
end

local function up_key()
    if showing then vertical(-1)
    else
        on_seek = true
        wake()
    end
end

local function down_key()
    if showing then vertical(1)
    else
        open_menu('sub')
        wake()
    end
end

local function menu_key(kind)
    return function()
        open_menu(kind)
        wake()
    end
end

local function jump(by)
    return function()
        mp.commandv('seek', by)
        wake()
    end
end

local function sideways(dx)
    return function()
        if showing then step(dx)
        else
            mp.commandv('seek', dx < 0 and -10 or 30)
            wake()
        end
    end
end

mp.set_property_number('volume', 0)
-- It waits idle if a copy fails to load, for the bridge to try another. Once a film is on screen, its end is the end.
mp.register_event('end-file', function()
    if not loading then mp.commandv('quit') end
end)
-- Resuming lands on the nearest keyframe, not the exact second: decoding up to the exact spot is what keeps big files on black.
mp.register_event('playback-restart', function()
    if not restarted then mp.set_property('hr-seek', 'default') end
    restarted = true
end)

local load_timer
load_timer = mp.add_periodic_timer(0.1, function()
    local age = mp.get_time() - born
    paint()
    if not mp.get_property_native('user-data/mediabox/show', false) then
        born = mp.get_time()
        return
    end
    if mp.get_property_bool('vo-configured') or age >= BLACK_AT_MOST then
        load_timer:kill()
        loading = false
        mp.set_property_bool('pause', false)
        animate()
    end
end)

key('ENTER', ok_key)
key('SPACE', pause_key)
key('PLAYPAUSE', pause_key)
key('PLAY', function() mp.set_property_bool('pause', false); wake() end)
key('PAUSE', function() mp.set_property_bool('pause', true); wake() end)
key('STOP', leave)
key('REWIND', jump(-10), { repeatable = true })
key('FORWARD', jump(30), { repeatable = true })
key('s', menu_key('sub'))
key('a', menu_key('audio'))
key('m', wake)
key('i', function()
    stats = not stats
    paint()
end)
key('LEFT', sideways(-1), { repeatable = true })
key('RIGHT', sideways(1), { repeatable = true })
key('UP', up_key, { repeatable = true })
key('DOWN', down_key, { repeatable = true })
key('ESC', back)
key('BS', back)
key('q', back)

mp.add_periodic_timer(0.5, function()
    if stats and not showing then paint() end
    if not showing then return end
    if not menu and not mp.get_property_bool('pause') and mp.get_time() - last_key >= IDLE then return hide() end
    paint()
end)
