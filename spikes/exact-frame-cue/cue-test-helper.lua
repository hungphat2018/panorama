-- mpv helper script for the exact-frame cue spike.
--
-- script-opts (prefix "cuetest-"):
--   cues=PATH    cue file, one "start_seconds level" per line. When set, the
--                script drives the shader's manual_level from time-pos: the
--                naive out-of-band way of switching cues (control arm).
--   seeks=LIST   "at:to/at:to" in seconds, fired in list order: one absolute
--                exact seek when playback first passes "at" after the previous
--                seek in the list.
--   start_delay=S  unpause S seconds after the file loads (start mpv with
--                --pause), so a capture can attach to the window first.
-- For the run log it prints a line at every shader swap (glsl-shaders change)
-- and every change of the drop/delay counters, each with the playback position,
-- and the final playback counters before the file unloads.

local options = require("mp.options")
local opts = { cues = "", seeks = "", start_delay = 0 }
options.read_options(opts, "cuetest")

if opts.start_delay > 0 then
    mp.register_event("file-loaded", function()
        mp.add_timeout(opts.start_delay, function()
            mp.set_property_bool("pause", false)
            print("cue-unpause")
        end)
    end)
end

local cues = {}
if opts.cues ~= "" then
    for line in io.lines(opts.cues) do
        local t, level = line:match("^(%S+)%s+(%S+)")
        if t then
            cues[#cues + 1] = { tonumber(t), tonumber(level) }
        end
    end
end

local seeks = {}
for at, to in opts.seeks:gmatch("([%d%.]+):([%d%.]+)") do
    seeks[#seeks + 1] = { at = tonumber(at), to = tonumber(to) }
end
local next_seek = 1
local seeking = false  -- positions before the seek lands must not fire the next one
mp.register_event("playback-restart", function()
    seeking = false
end)

local function level_at(t)
    local level = cues[1][2]
    for _, cue in ipairs(cues) do
        if cue[1] > t then
            break
        end
        level = cue[2]
    end
    return level
end

local last_level = nil
mp.observe_property("time-pos", "number", function(_, t)
    if not t then
        return
    end
    if #cues > 0 then
        local level = level_at(t)
        if level ~= last_level then
            -- Setting the option replaces the whole list, so use_pts=0 must be repeated.
            mp.set_property_native("glsl-shader-opts", { use_pts = "0", manual_level = tostring(level) })
            last_level = level
        end
    end
    local s = seeks[next_seek]
    if s and not seeking and t >= s.at then
        next_seek = next_seek + 1
        seeking = true
        print(string.format("cue-seek from=%.3f to=%.3f", t, s.to))
        mp.commandv("seek", tostring(s.to), "absolute+exact")
    end
end)

local function position()
    return mp.get_property_number("time-pos", -1)
end

mp.observe_property("glsl-shaders", "native", function(_, paths)
    print(string.format("cue-swap time-pos=%.3f paths=%s", position(), table.concat(paths or {}, "|")))
end)

for _, name in ipairs({ "frame-drop-count", "decoder-frame-drop-count", "vo-delayed-frame-count",
                        "mistimed-frame-count" }) do
    mp.observe_property(name, "number", function(_, value)
        if value and value > 0 then
            print(string.format("cue-counter %s=%d time-pos=%.3f", name, value, position()))
        end
    end)
end

mp.add_hook("on_unload", 50, function()
    for _, name in ipairs({ "frame-drop-count", "decoder-frame-drop-count", "vo-delayed-frame-count",
                            "mistimed-frame-count", "display-fps", "container-fps", "hwdec-current",
                            "current-gpu-context", "video-sync" }) do
        print("counter " .. name .. "=" .. tostring(mp.get_property(name)))
    end
end)
