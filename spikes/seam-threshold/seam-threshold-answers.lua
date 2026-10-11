-- mpv script for the seam-offset threshold test: pauses after each trial, records the
-- viewer's answer (1 or 2), and moves on. R replays the trial, Q quits early.
-- Options (--script-opts): seam_threshold-trials=KEY_JSON, seam_threshold-answers=OUT_JSONL
local utils = require "mp.utils"
local opts = { trials = "", answers = "" }
require("mp.options").read_options(opts, "seam_threshold")

local f = assert(io.open(opts.trials, "r"))
local key = utils.parse_json(f:read("*a"))
f:close()
local trials = key.trials
local COUNTERS = { "frame-drop-count", "vo-delayed-frame-count", "mistimed-frame-count" }

local current, waiting, started, at_interval = 1, false, false, nil

local function counters()
    local c = {}
    for _, name in ipairs(COUNTERS) do c[name] = mp.get_property_number(name, 0) end
    return c
end

local function prompt()
    mp.osd_message(string.format("Trial %d of %d.  Which one split at the black bar?   Press 1 or 2.   "
        .. "Not sure: guess.   R = replay", current, #trials), 3600)
end

local function play_trial(index)
    waiting, at_interval = false, nil
    mp.commandv("seek", tostring(trials[index].times.cue1), "absolute+exact")
    mp.set_property_bool("pause", false)
end

mp.observe_property("time-pos", "number", function(_, t)
    local trial = trials[current]
    if t == nil or waiting or not started or trial == nil then return end
    if at_interval == nil and t >= trial.times.interval1 then at_interval = counters() end
    if t >= trial.times.ask + 0.05 then
        mp.set_property_bool("pause", true)
        waiting = true
        prompt()
    end
end)

local function answer(choice)
    if not waiting then return end
    local trial, now = trials[current], counters()
    local glitches = {}
    for _, name in ipairs(COUNTERS) do glitches[name] = now[name] - ((at_interval or now)[name]) end
    local out = assert(io.open(opts.answers, "a"))
    out:write(utils.format_json({ trial = trial.index, content = trial.content, offset_ms = trial.offset_ms,
        offset_interval = trial.offset_interval, answer = choice, correct = (choice == trial.offset_interval),
        glitches = glitches, display_fps = mp.get_property_number("display-fps", 0) }) .. "\n")
    out:close()
    current = current + 1
    if current > #trials then
        mp.osd_message("Done. Thank you. Results are in the terminal.", 3)
        mp.add_timeout(2, function() mp.command("quit") end)
        return
    end
    mp.osd_message("", 0)
    play_trial(current)
end

mp.add_forced_key_binding("1", "seam-answer-1", function() answer(1) end)
mp.add_forced_key_binding("2", "seam-answer-2", function() answer(2) end)
mp.add_forced_key_binding("r", "seam-replay", function() if waiting then mp.osd_message("", 0); play_trial(current) end end)
mp.add_forced_key_binding("q", "seam-quit", function() mp.command("quit") end)
mp.add_forced_key_binding("SPACE", "seam-start", function()
    if started then return end
    started = true
    mp.osd_message("", 0)
    play_trial(current)
end)

mp.register_event("file-loaded", function()
    mp.set_property_bool("pause", true)
    mp.osd_message(string.format("Seam test, %d trials.\n\nEach trial plays clip 1, then clip 2, across the black bar.\n"
        .. "In one of the two, the right side switches pictures a few ms late.\n"
        .. "Afterwards press 1 or 2: which one split or flickered at the bar?\n"
        .. "Watch the bar, not the corners. Not sure: guess.\n\nPress SPACE to start.", #trials), 3600)
end)
