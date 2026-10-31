-- The film's controls: the Broadsheet sheet from docs/broadsheet-prototype.html, drawn on mpv's OSD at 1920x1080.
-- The sheet's look is chosen once each time it rises and held until it hides, so cuts and flashes can't make it flicker.
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
    { id = 'pp', x = 96, w = 170 }, { id = 'back', x = 278, w = 110, text = '− 10' }, { id = 'fwd', x = 400, w = 110, text = '+ 30' },
    { id = 'audio', x = 950, w = 150, text = 'Audio' }, { id = 'subs', x = 1112, w = 190, text = 'Subtitles' },
    { id = 'copy', x = 1314, w = 250, text = 'Other versions' }, { id = 'stop', x = 1576, w = 248, text = 'Back to the paper' },
}
local MENU_ROWS = 9

local showing, rising, focus, on_seek, menu, last_key = false, false, 1, false, nil, 0
local look = LOOKS.night

local function escape(text)
    return (tostring(text or ''):gsub('[\r\n]', ' '):gsub('\\', '\\\239\187\191'):gsub('{', '\\{'):gsub('}', '\\}'))
end

local function colour(rgb)
    return rgb:sub(5, 6) .. rgb:sub(3, 4) .. rgb:sub(1, 2)
end

local function box(x, y, w, h, rgb, alpha)
    return string.format('{\\an7\\pos(0,0)\\bord0\\shad0\\1c&H%s&\\1a&H%s&\\p1}m %d %d l %d %d %d %d %d %d{\\p0}', colour(rgb), alpha or '00', x, y, x + w, y, x + w, y + h, x, y + h)
end

local function text(x, y, align, font, size, rgb, content, bold, clip)
    return string.format('{\\an%d\\pos(%d,%d)\\bord0\\shad0\\fn%s\\fs%d\\b%d\\1c&H%s&\\1a&H00&%s}%s', align, x, y, font, size, bold and 1 or 0, colour(rgb),
        clip and string.format('\\clip(%d,%d,%d,%d)', clip[1], clip[2], clip[3], clip[4]) or '', escape(content))
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

local function paint()
    if not showing then
        overlay.data = ''
        overlay:update()
        return
    end
    local now, total = mp.get_property_number('time-pos', 0), mp.get_property_number('duration', 0)
    local out = {
        box(0, 816, 1920, 264, look.sheet, look.alpha), box(96, 836, 1728, 2, look.ink), box(96, 842, 1728, 2, look.ink),
        text(96, 884, 4, DISPLAY, 44, look.ink, mp.get_property('user-data/mediabox/title', ''), true, { 96, 846, 1196, 926 }),
        text(1824, 885, 6, BODY, 22, look.quiet, mp.get_property('user-data/mediabox/kicker', '')),
        text(96, 947, 4, BODY, 24, look.ink, clock(now), true),
        text(1824, 947, 6, BODY, 24, look.ink, '-' .. clock(total - now), true),
    }
    local share = total > 0 and math.min(1, now / total) or 0
    local bar = on_seek and 12 or 6
    out[#out + 1] = box(216, 947 - bar / 2, 1488, bar, look.track)
    out[#out + 1] = box(216, 947 - bar / 2, math.floor(1488 * share), bar, look.red)
    if on_seek then out[#out + 1] = box(216 + math.floor(1488 * share) - 5, 933, 10, 28, look.ink) end
    for i, b in ipairs(BUTTONS) do
        local lit = i == focus and not on_seek
        out[#out + 1] = box(b.x, 984, b.w, 66, lit and look.ink or look.soft)
        local label = b.text or (mp.get_property_bool('pause') and 'Play' or 'Pause')
        out[#out + 1] = text(b.x + math.floor(b.w / 2), 1017, 5, BODY, b.id == 'stop' and 24 or 26, lit and look.inverse or look.ink, label, true)
    end
    if menu then draw_menu(out) end
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

local function rise(chosen)
    look = chosen
    showing, rising = true, false
    last_key = mp.get_time()
    paint()
end

-- Brings the sheet up. Anything uncertain (no frame, odd format, too slow) falls back to night.
local function wake()
    last_key = mp.get_time()
    if showing or rising then return paint() end
    local choice = mp.get_property('user-data/mediabox/look', 'auto')
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
    paint()
end

local function open_menu(kind)
    local rows = {}
    if kind == 'sub' then rows[1] = { label = 'Off', id = 'no', selected = not mp.get_property_native('sid') } end
    for _, t in ipairs(mp.get_property_native('track-list') or {}) do
        if t.type == kind then
            local name = t.title or t.lang or ('Track ' .. t.id)
            if t.title and t.lang then name = t.title .. ' · ' .. t.lang end
            rows[#rows + 1] = { label = name, id = t.id, selected = t.selected }
        end
    end
    if #rows == 0 then return end
    local at = 1
    for i, r in ipairs(rows) do
        if r.selected then at = i end
    end
    menu = { kind = kind, title = kind == 'sub' and 'SUBTITLES' or 'AUDIO', rows = rows, at = at }
end

local function press_enter()
    if menu then
        local row = menu.rows[menu.at]
        mp.set_property_native(menu.kind == 'sub' and 'sid' or 'aid', row.id)
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
            mp.commandv('quit')
        elseif id == 'stop' then mp.commandv('quit') end
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

-- First press of anything but left, right and back only raises the sheet.
local function key(name, action, opts)
    mp.add_forced_key_binding(name, 'mediabox-' .. name, function()
        action()
        if showing or rising then wake() end
    end, opts)
end

local function when_up(action)
    return function()
        if showing then action() else wake() end
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

local function back()
    if menu then menu = nil
    elseif showing then hide()
    else mp.commandv('quit') end
end

key('ENTER', when_up(press_enter))
key('SPACE', function() mp.commandv('cycle', 'pause'); wake() end)
key('LEFT', sideways(-1), { repeatable = true })
key('RIGHT', sideways(1), { repeatable = true })
key('UP', when_up(function() vertical(-1) end), { repeatable = true })
key('DOWN', when_up(function() vertical(1) end), { repeatable = true })
key('ESC', back)
key('BS', back)
key('q', back)

mp.add_periodic_timer(0.5, function()
    if not showing then return end
    if not menu and not mp.get_property_bool('pause') and mp.get_time() - last_key >= IDLE then return hide() end
    paint()
end)
