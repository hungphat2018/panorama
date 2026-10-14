-- mpv script for the blind trials: on a "Trial N of M" card the playlist waits; pressing 1 or 2 saves the answer
-- and moves on. Answers go to <test dir>/answers-<stamp>.txt as "1,2,2,1,..." (the --score format), rewritten
-- after every press so a quit mid-test keeps what was answered.
local answers, current, out_path = {}, nil, nil

local function save()
    local n, parts = 0, {}
    for k in pairs(answers) do if k > n then n = k end end
    for k = 1, n do parts[k] = answers[k] and tostring(answers[k]) or "" end
    local f = io.open(out_path, "w")
    if not f then mp.osd_message("Could not write " .. out_path, 5) return end
    f:write(table.concat(parts, ",") .. "\n")
    f:close()
end

mp.register_event("file-loaded", function()
    local path = mp.get_property("path") or ""
    local dir, num = path:match("^(.-)prompts[/\\]trial%-(%d+)%.png$")
    current = num and tonumber(num) or nil
    if current then
        out_path = out_path or (dir .. "answers-" .. os.date("%Y%m%d-%H%M%S") .. ".txt")
        mp.set_property_bool("pause", true)  -- hold the question until a key is pressed
    elseif path:match("prompts[/\\]end%.png$") and out_path then
        mp.osd_message("Answers saved to " .. out_path, 600)
    end
end)

local function answer(choice)
    if not current then return end
    answers[current] = choice
    save()
    mp.osd_message("Trial " .. current .. ": " .. choice .. " saved", 1)
    current = nil
    mp.set_property_bool("pause", false)
    mp.command("playlist-next")
end

for _, key in ipairs({"1", "KP1"}) do mp.add_forced_key_binding(key, "answer-1-" .. key, function() answer(1) end) end
for _, key in ipairs({"2", "KP2"}) do mp.add_forced_key_binding(key, "answer-2-" .. key, function() answer(2) end) end
