# 0027. Content model schema v0: the timeline, the tables and the cache key

- **Status:** Accepted
- **Date:** 2026-10-12
- **Refines:** [0024](0024-film-analysis-worker-and-content-model-shape.md)

## Context

[ADR 0024](0024-film-analysis-worker-and-content-model-shape.md) chose the worker and the shape of the content model. This one fixes the details that are hard to change once a planner and a cache exist: the schema, the timeline every table is keyed by, and the cache key. They were checked on running code ([report](../../plans/reports/spike-261012-1103-content-model-schema-v0-and-worker.md)):

- The worker made every table for the real anime episode (34,047 frames) in 164 s, and for 13 small odd files, and all pass the schema validator.
- The timeline matches the performer's own decoder loop bit for bit on 47 of the 49 test files that open (the other 2 are raw streams with no stamps; 3 more are broken on purpose), and the real performer's frame times to 3.3e-10 s (the rounding of its log) on the 12 files it was run on.
- The cache key was tested for same file, changed byte and changed version.

## Decision

### What you see

Nothing new is decided here. The model carries what earlier decisions need and changes none of them: the whole real picture is never cropped and the last confident crop is held ([0001](0001-crop-none-of-the-real-picture-by-default.md), [0010](0010-crop-changes-hold-last-confident-crop.md)); a crop change lands at a cut ([0015](0015-picture-size-may-change-at-cuts.md)); dialogue stays inside one screen ([0013](0013-dialogue-subtitles-stay-inside-one-screen.md)); camera slides play smooth ([0016](0016-camera-slides-play-smooth.md)). The model only records the evidence for these. Which subtitle line counts as dialogue and which as a sign decides which lines [0013](0013-dialogue-subtitles-stay-inside-one-screen.md) applies to; the v0 rules are guesses with their evidence kept (`features`), and any change to what the viewer sees from them goes to the user.

### How it works

**Tables.** One folder per film. `content.json` is the index; the schemas are in [`spikes/film-analysis/schema/`](../../spikes/film-analysis/schema/) (they move with the worker).

| File | Holds | Schema |
|------|-------|--------|
| `content.json` | file key, tools, decode path, every table's state and version, scan progress | [content](../../spikes/film-analysis/schema/content.schema.json) |
| `file-facts.json` | streams, colour, HDR and Dolby Vision, chapters, frame rate and whether it varies, black level, crop vocabulary, text-in-bars summary | [file-facts](../../spikes/film-analysis/schema/file-facts.schema.json) |
| `signals.bin` and `signals.json` | raw per-frame columns, about 52 bytes a frame: the frame table (`pts`, `key`), shot-model scores, slide measurements, brightness, crop rectangle, bar edges | [signals-header](../../spikes/film-analysis/schema/signals-header.schema.json) |
| `shots.json` | the spine: one entry per shot, covering the film: boundary kind and span, the cue-sheet start, crop with its evidence, text in the bars, brightness, motion | [shots](../../spikes/film-analysis/schema/shots.schema.json) |
| `slide-paths.json` | camera slides: every frame's position, new drawing or held, confidence, purity | [slide-paths](../../spikes/film-analysis/schema/slide-paths.schema.json) |
| `subjects.json`, `subjects-raw.jsonl` | faces, heads and people per shot as tracks, the 64x36 keep-clear map; the raw boxes under it | [subjects](../../spikes/film-analysis/schema/subjects.schema.json), [raw](../../spikes/film-analysis/schema/subjects-raw.schema.json) |
| `subtitles/track-N.json` | one file per subtitle stream: events with time, rendered box, class | [subtitles](../../spikes/film-analysis/schema/subtitles.schema.json) |

Shared conventions are in [common](../../spikes/film-analysis/schema/common.schema.json). `slide-paths` and `subjects` name their shot by id and `shots` points at neither, so any of the three can be made again alone. Every field is computed from the file or is absent with a reason written in the schema: the `text` subject class (no text detector in v0; text in the bars comes from the bar guard), Dolby Vision active-area offsets (not sampled), and faces in a shot shorter than the sampling step (`samples: 0` means unknown, not empty).

**Timeline.**

- A frame is a row of the frame table: the frames the performer's decoder path presents, in presentation order, strictly increasing. Every per-frame array is indexed by that row.
- The frame's stamp is its `pts` in ticks of the video stream's time base. The worker reads it from the container's packet list (no decoding); packets flagged to be discarded (MP4 edit lists), packets without a stamp, and a repeated stamp are dropped. This is the stamp the performer reads (`frame->pts`, else the best-effort stamp).
- Time zero is the first frame. Seconds are `t[i] = pts[i] * (tb_num / tb_den) - pts[0] * (tb_num / tb_den)` in IEEE double, evaluated in that order, as the performer does. The file's own start offset (an MKV that starts at 10 s, an MP4 empty edit, a negative first stamp) is removed by this and kept as `first_pts_ticks` and `first_pts_s_abs`; subtitle and chapter times are moved to the same zero.
- A shot that begins with frame `a > 0` starts halfway between the frames: `(t[a-1] + t[a]) / 2`. This is the cue sheet's `start`. A frame lasts until the next one; the last lasts as long as its packet says.
- Variable frame rate needs no special case: the table has each frame's time. `fps_avg` and the nominal rate are facts, not keys.
- Positions are pixels of the stored picture, independent of any crop, aspect ratio or rotation; rectangles are half-open.
- A file with no stamps at all (a raw `.264` or `.hevc`) gets a made-up table (`frame_table_source: synthetic`); the performer shows one frame of such a file until it counts frames itself.
- MKV ordered chapters and linked segments are not followed. FFmpeg ignores them, and so do the model and the performer's decoder loop: they describe the file's linear timeline.

**Cache key.**

- The folder is named by file identity: the first 32 hex digits of a SHA-256 over `panorama.filekey/0`, the size (8 bytes) and three 1 MiB blocks at the start, the middle and the end (the whole file up to 3 MiB), then `-` and the size. The name, folder and modification time are not in it, so a renamed or moved copy finds its model. A remux is a new file. The full hash of every byte is stored in the key later (`--full-hash`); a key with it still matches one without.
- The analyzer versions decide what inside the folder is reused. Signals carry one version per group; the shot and subject versions include the model file hashes, and the subject version includes the sampling step. Any difference means the signals are scanned again. Each derived table carries its own version: a stale table is made again alone, from the signals, in under a second for a 24-minute film (slide-paths 0.06 s). A new `shots` version also makes `slide-paths` and `subjects` again, because they name shot ids. Subtitles and file facts are read from the file again.
- Known gap: a changed byte between the three blocks keeps the same sample key. The full hash tells such files apart; until it is computed, two files that differ only there share a folder. Local films rarely change in place.

**Stays swappable.** Every detector behind a field (shot model, crop rule, bar-text guard, face and person models, slide measurement, subtitle class rules); every threshold and policy in the derived tables; the models' hashes and sampling step (they only change the version); the size of the keep-clear grid. None of these touch the schema or the timeline.

## Consequences

- A planner reads `shots.json` for what to decide and `slide-paths.json` and `subjects.json` by shot id; a cue sheet can copy `start_s` unchanged.
- Changing a rule costs a table version bump and seconds. Changing a model costs a scan (2.7 minutes for the episode, 8.7 times faster than playing it).
- A different time base rule, key scheme or table split is a new schema version (`panorama.content/1`), and old folders are made again.
- Not yet measured: whether the scan drops frames of a playing performer. The detector calls last 35 to 156 ms, not the 20 ms the research counted as short; this is checked in the thin prototype, with the user's OK.
