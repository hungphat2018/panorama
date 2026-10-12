-- Baseline probe for the supported-file matrix: runs inside stock mpv (no config) and writes
--   * one window screenshot (what is on screen: picture, black bars, subtitles) after `shot_at` seconds of playback,
--   * a JSON file with what mpv decided about the file (video params, decoder, hwdec, chapters, drop counters,
--     first-frame time, the frame's interlace flags) so the table can show mpv's own view next to the performer's.
-- Options (passed as --script-opts=matrix-out=...,matrix-shot=...,matrix-shot_at=2.1):
--   out      path of the JSON file          shot     path of the PNG screenshot        shot_at  playback seconds
--   shot_mode  "window" (what is on screen, scaled to the window) or "video" (decoded picture at its own size,
--              used for interlace checks so combing is not smeared by scaling)
local utils = require "mp.utils"
local options = require "mp.options"

local opts = { out = "", shot = "", shot_at = 2.0, shot_mode = "window" }
options.read_options(opts, "matrix")

local t0 = mp.get_time()
local info = { counters = {}, events = {} }
local shot_done = false
local written = false

local function prop(name)
    local v = mp.get_property_native(name)
    return v
end

local function snapshot()
    local c = info.counters
    c.time_pos = prop("time-pos") or c.time_pos
    c.duration = prop("duration") or c.duration
    c.frame_drop = prop("frame-drop-count") or c.frame_drop
    c.decoder_drop = prop("decoder-frame-drop-count") or c.decoder_drop
    c.vo_delayed = prop("vo-delayed-frame-count") or c.vo_delayed
    c.mistimed = prop("mistimed-frame-count") or c.mistimed
    c.estimated_frame_number = prop("estimated-frame-number") or c.estimated_frame_number
    c.estimated_vf_fps = prop("estimated-vf-fps") or c.estimated_vf_fps
    c.avsync = prop("avsync") or c.avsync
    local vp = prop("video-params")
    if vp then info.video_params = vp end
    local vo = prop("video-out-params")
    if vo then info.video_out_params = vo end
    info.hwdec_current = prop("hwdec-current") or info.hwdec_current
    info.current_vo = prop("current-vo") or info.current_vo
    info.video_format = prop("video-format") or info.video_format
    info.video_codec = prop("video-codec") or info.video_codec
    info.container_fps = prop("container-fps") or info.container_fps
    info.file_format = prop("file-format") or info.file_format
    info.chapter_list = prop("chapter-list") or info.chapter_list
    info.edition_list = prop("edition-list") or info.edition_list
    info.track_list = prop("track-list") or info.track_list
    info.path = prop("path") or info.path
end

local function write_json()
    if written or opts.out == "" then return end
    snapshot()
    info.t_script_start_s = t0
    local f = io.open(opts.out, "w")
    if f then
        f:write(utils.format_json(info))
        f:close()
        written = true
    end
end

mp.register_event("file-loaded", function()
    info.t_file_loaded_s = mp.get_time() - t0
end)

mp.register_event("playback-restart", function()
    if not info.t_first_frame_s then info.t_first_frame_s = mp.get_time() - t0 end
end)

mp.register_event("end-file", function(ev)
    info.end_reason = ev.reason
    info.end_error = ev.file_error
    write_json()
end)

mp.register_event("shutdown", write_json)

-- the screenshot is taken from the time-pos callback, after the VO has rendered at least one frame
mp.observe_property("time-pos", "number", function(_, t)
    if t and not shot_done and opts.shot ~= "" and t >= opts.shot_at then
        shot_done = true
        info.frame_info = prop("video-frame-info")
        info.shot_time_pos = t
        local ok, err = pcall(mp.commandv, "screenshot-to-file", opts.shot, opts.shot_mode)
        info.shot_ok = ok
        if not ok then info.shot_error = tostring(err) end
    end
end)

mp.add_periodic_timer(0.5, snapshot)
