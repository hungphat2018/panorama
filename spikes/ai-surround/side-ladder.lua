-- mpv script for the side ladder (side-ladder.py builds the folder): the start card waits for space, two examples play,
-- then the trials of ladder.json's schedule. Each trial plays "1", a clip, "2", a clip and a question card; pressing 1
-- or 2 saves the answer and moves the trial's staircase (weighted up-down: down `step.down` levels after a right answer,
-- `step.fast_down` before the first wrong one, up `step.up` after a wrong one), then queues the next trial.
-- Answers go to <ladder dir>/ladder-answers-<stamp>.json, rewritten after every press, so a quit keeps them.
local utils = require "mp.utils"

local dir, plan, log_path = nil, nil, nil
local state, results = {}, {}
local current, waiting, on_start = nil, false, false

local function save()
    local parts = {}
    for _, r in ipairs(results) do
        parts[#parts + 1] = string.format(
            '  {"k": %d, "ladder": "%s", "level_index": %d, "size": %g, "side": "%s", "event_first": %s, ' ..
            '"answer": %d, "right": %s}',
            r.k, r.ladder, r.level_index, r.size, r.side, tostring(r.event_first), r.answer, tostring(r.right))
    end
    local f = io.open(log_path, "w")
    if not f then mp.osd_message("Could not write " .. log_path, 5) return end
    f:write('{"trials": [\n' .. table.concat(parts, ",\n") .. '\n]}\n')
    f:close()
end

local function queue_trial(k, mode)
    local s = plan.schedule[k]
    local l = plan.ladders[s.ladder]
    local idx = state[s.ladder].idx
    local event, clean = dir .. l.clips[s.side][idx], dir .. plan.clean
    local a, b = clean, event
    if s.event_first then a, b = event, clean end
    current = {k = k, ladder = s.ladder, idx = idx, side = s.side, event_first = s.event_first}
    mp.commandv("loadfile", dir .. plan.labels[1], mode)
    mp.commandv("loadfile", a, "append")
    mp.commandv("loadfile", dir .. plan.labels[2], "append")
    mp.commandv("loadfile", b, "append")
    mp.commandv("loadfile", dir .. string.format("prompts/trial-%02d.png", k), "append")
end

mp.register_event("file-loaded", function()
    local path = mp.get_property("path") or ""
    local d = path:match("^(.-)prompts[/\\]start%.png$")
    if d and not plan then
        dir = d
        local f = io.open(dir .. "ladder.json", "r")
        if not f then mp.osd_message("ladder.json not found in " .. dir, 10) return end
        plan = utils.parse_json(f:read("*a"))
        f:close()
        for name, l in pairs(plan.ladders) do state[name] = {idx = l.start + 1, wrong_seen = false} end
        log_path = dir .. "ladder-answers-" .. os.date("%Y%m%d-%H%M%S") .. ".json"
        for _, item in ipairs(plan.examples) do mp.commandv("loadfile", dir .. item, "append") end
        on_start = true
        mp.set_property_bool("pause", true)                      -- the start card waits for space
    elseif path:match("prompts[/\\]go%.png$") and plan then
        queue_trial(1, "append")
    elseif path:match("prompts[/\\]trial%-%d+%.png$") and current then
        waiting = true
        mp.set_property_bool("pause", true)                      -- hold the question until 1 or 2
    elseif path:match("prompts[/\\]end%.png$") and log_path then
        mp.osd_message("Answers saved to " .. log_path, 600)
    end
end)

local function answer(choice)
    if not waiting then return end
    waiting = false
    local c = current
    local l = plan.ladders[c.ladder]
    local right = (choice == 1) == c.event_first
    results[#results + 1] = {k = c.k, ladder = c.ladder, level_index = c.idx - 1, size = l.levels[c.idx],
                             side = c.side, event_first = c.event_first, answer = choice, right = right}
    save()
    local st = state[c.ladder]
    if right then
        st.idx = math.max(1, st.idx - (st.wrong_seen and plan.step.down or plan.step.fast_down))
    else
        st.wrong_seen = true
        st.idx = math.min(#l.levels, st.idx + plan.step.up)
    end
    mp.osd_message("Trial " .. c.k .. ": " .. choice .. " saved", 1)
    if c.k < #plan.schedule then
        queue_trial(c.k + 1, "replace")
    else
        mp.commandv("loadfile", dir .. "prompts/end.png", "replace")
    end
    mp.set_property_bool("pause", false)
end

local function begin()
    if on_start then
        on_start = false
        mp.set_property_bool("pause", false)
        mp.command("playlist-next")
    else
        mp.command("cycle pause")
    end
end

for _, key in ipairs({"1", "KP1"}) do mp.add_forced_key_binding(key, "ladder-1-" .. key, function() answer(1) end) end
for _, key in ipairs({"2", "KP2"}) do mp.add_forced_key_binding(key, "ladder-2-" .. key, function() answer(2) end) end
mp.add_forced_key_binding("SPACE", "ladder-begin", begin)
