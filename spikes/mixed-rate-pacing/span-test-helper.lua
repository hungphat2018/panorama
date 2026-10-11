-- Helper for the mixed-refresh pacing spike.
-- Unpauses after start_delay seconds (so the screen logger is attached first),
-- quits after play_seconds of playback, and prints mpv's own pacing counters
-- just before quitting ("counter NAME=VALUE" lines in the log).

local opts = { start_delay = 3.0, play_seconds = 30 }
require("mp.options").read_options(opts, "spantest")

local counters = {
    "frame-drop-count", "decoder-frame-drop-count", "vo-delayed-frame-count", "mistimed-frame-count",
    "vsync-jitter", "display-fps", "estimated-display-fps", "container-fps", "estimated-vf-fps",
    "video-speed-correction", "audio-speed-correction", "video-sync", "interpolation", "tscale",
    "hwdec-current", "display-names", "osd-width", "osd-height",
}

local function dump()
    for _, name in ipairs(counters) do
        print("counter " .. name .. "=" .. tostring(mp.get_property(name)))
    end
end

mp.register_event("file-loaded", function()
    mp.add_timeout(opts.start_delay, function()
        mp.set_property_bool("pause", false)
        mp.add_timeout(opts.play_seconds, function()
            dump()
            mp.command("quit")
        end)
    end)
end)
