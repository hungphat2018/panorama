---
title: "Content model schema v0, the timeline checked against the performer, the cache key, and a thin worker that fills the schema"
date: 2026-10-12
type: spike
answers: "M1 phase 4, the hard-to-change parts: schema v0, the timeline convention (against the performer's own frame times), the cache key, and a worker that fills every schema field from the file"
harness: spikes/film-analysis/ (worker `analyze.py`, `film_analysis/`, `schema/`, `selftest.py`, `cache-test.py`, `validate-content.py`, `verify-frame-table.py`, `native-checks/`)
results: spikes/film-analysis/results/ (episode-v0-scan.json, frame-table-vs-performer.json, edge-case-scan.json)
status: "done for the scope the user set on 2026-10-12; the playback-load check moved to phase 8, accuracy and test-set work to a later milestone"
---

# Content model schema v0 and the worker

Run 2026-10-12, 10:15 to 11:05, Asia/Bangkok, on Setup A, below-normal priority. The GPU timing lock was held for every GPU run. The playback-load trial (S1) was **not** run: it needs the user's OK and both screens.

**Terms.** A *shot* is one unbroken piece of footage. A *cut* is where one shot ends and the next begins. *Black bars* are black strips stored in the video around the real picture. A *camera slide* is a pan or tilt that moves the whole picture together. The *frame table* is the list of every frame the performer shows, in order, with its time stamp. The *cache key* is what says "this folder holds the model of this file".

Evidence tags: **[L]** measured here, **[V]** source read, **[D]** derived.

## 1. Answer

1. **Schema v0 is complete and the worker fills it.** Every field is computed from the file or is absent with a reason written in the schema (section 3). The folder for the anime episode passes the validator: 29 of 29 checks [L].
2. **The timeline matches the performer.** The frame table (container packet list plus one rule) equals the performer's decode loop frame for frame and bit for bit on 47 of the 49 test files that open; the 2 others are raw streams with no stamps. On the 12 files also run through the real performer (`--headless`), the frame times differ by at most 3.3e-10 s, which is the rounding of the 9 decimals its log prints [L]. Convention in section 4.
3. **The cache key works as designed, and one part was missing.** Same file gives the same key; a changed byte in any of the three sampled blocks gives a new key; a changed table version makes that table again and touches no other file (section 5). Before this work the table versions were stored but never compared, so a policy change was never picked up; that is built now.
4. **The worker runs the real episode in 164 s**, 8.7 times faster than playing it, with the first scanned result 8.4 s after launch, and leaves an 8.3 MB folder (section 6) [L].
5. **Not measured, by instruction:** whether this load drops frames of a playing performer. The detector calls last 35 to 156 ms, longer than the "short jobs" the research measured (section 7).

## 2. What was there, what was wrong, what changed

The stopped work was close to complete: a segmented worker (quick pre-scan, then the full scan in keyframe-aligned pieces, subtitles in the background), nine JSON Schemas, a validator, a frame-time comparison against the performer's decode loop, and 13 unit checks that all passed [L]. No report had been written. What I found and fixed:

| Problem | Fix |
|---|---|
| Table versions were written to `content.json` and never compared. A finished folder run again rebuilt nothing, and redid every subtitle track (26.6 s on the episode) every time. | `manifest.stale_tables`, `refresh.py`: a stale table is made again alone; subtitles are skipped when current. |
| `shots.json` held ids of slide runs and subject counts, so the three derived tables could not be made again separately. | Removed from `shots.json` and its schema. `slide-paths` and `subjects` name their shot; nothing points back. `derive_tables.py` split into `derive_spine.py`, `derive_tables.py`, `derive_slides_subjects.py`. |
| The raw face/head/person detections, their model files and the sampling step were in no version, so a new model or step reused stale detections. | New signal group `subjects-raw`; its version holds the four detector hashes and the step. |
| The cache folder was whatever `--out` said; the cache key was only checked inside it. | `--cache-root`: the folder is named by the key. `same_file` compares scheme, size and sample hash, so a key that later gained the full hash still matches. `--full-hash` stores the hash of every byte (the function existed and was never called). |
| The scan re-derived all tables after every segment: the 15-second timer was reset to minus infinity instead of to now. | Fixed. |
| `run-throughput-trial.py` deadlocked: the worker blocked on its closing print (longer than a pipe holds) because the wrapper read stdout only after the worker exited. Found when the first episode run finished its work and never exited. | Both pipes are read while it runs. |
| Text in `file-facts.schema.json` and the Dolby Vision note disagreed with the code (nominal-rate tolerance, how variable rate is decided; a note said the worker reads Dolby Vision active-area offsets with a helper it never runs). | Texts corrected to what the code does. |

## 3. What the worker fills

All from the file alone; the episode numbers are from the finished run [L].

| Field | How | On the episode |
|---|---|---|
| Frame table, frame rate, whether it varies | container packet list, no decoding | 34,047 frames, 24000/1001, constant |
| Streams, colour, HDR, Dolby Vision, chapters | ffprobe, first-frame side data | 8 subtitle streams |
| Shots and cuts (hard, gradual) | TransNetV2 on ONNX (CUDA), plus keyframe cuts for stretches not yet scanned | 357 shots: 355 hard cuts, 1 gradual |
| Black bars per shot, also when the ratio changes | cropdetect per frame, per-shot union snapped to the file's few real crop shapes (vocabulary); held from the previous shot when no evidence | one rectangle, no bars in this episode (see below) |
| Text in the bars | bright-edge distances of the four bars, runs of 3 frames | none in this episode |
| Faces, heads, people, keep-clear map | anime face/head/person models (ONNX, GPU) and YuNet, every 12th frame | 517 face, 593 head, 574 person tracks; 5 of 357 shots have no sample (stored as unknown) |
| Dialogue lines | every subtitle stream, libass boxes, dialogue/sign rules | 8 tracks, 449 to 524 events each |
| Camera slides, every frame's position | phase correlation then ECC at 480x270, purity label | 16 runs: 5 pure, 9 layered, 2 unknown; longest 5.0 s |
| Per-shot motion and brightness | from the same signals | all 357 shots |
| Region (opening, ending, credits) | chapter titles | none marked |

Absent with a reason, written in the schema: the `text` subject class (no text detector in v0), Dolby Vision active-area offsets (FFmpeg gives them only through its C API; not sampled), faces in a shot shorter than the sampling step (`samples: 0` means unknown).

The episode has no black bars, so the bar fields were also checked elsewhere [L]. On a generated 1280x720 clip with 92-pixel bars the worker returns the crop `[0, 92, 1280, 628]` and the cut. On the test set's `ctor-hardsub-tos-48.mp4` (1920x1080 with 140-pixel bars and burned-in text) it makes 24 shots and a vocabulary of three rectangles, `[0,140,1920,940]`, `[0,140,1920,1010]` and `[0,140,1920,1042]`, and reports a 36-pixel text extension in one shot. Whether those crops are right is accuracy work and was not judged.

Thirteen small odd files (variable frame rate, MKV starting at 10 s, MP4 empty and trimmed edit lists, negative composition offsets, ordered chapters, ASS with attached fonts, SRT, HEVC 10-bit, interlaced TS, Dolby Vision 8.1) scanned to completion with 0 failed segments and passed the validator, software decode, models on the CPU [L]. During every scan each segment's decoded frame times are compared with the table and a segment that differs is refused: 0 refused on the episode and on those 13 [L].

## 4. Timeline convention

Checked with `verify-frame-table.py`, which compares three things: the worker's table; `native-checks/frame-times.exe`, the performer's decode loop (`perf1-decode.c` `handle_frame`) with no window, in software and through D3D11VA; and the real `perf1-p3-lgpl.exe --headless` cue log [V][L].

- **Time base:** the stream's, in integer ticks. `pts` is the stamp the performer reads (`frame->pts`, else the best-effort stamp), taken from the packet list. Packets flagged to be discarded, without a stamp, or repeating a stamp are dropped, as the performer refuses frames whose time does not increase.
- **Seconds:** `t[i] = pts[i] * (tb_num / tb_den) - pts[0] * (tb_num / tb_den)` in IEEE double, in that order. Evaluated this way the worker's seconds equal the performer's bit for bit; the shorter `(pts[i] - pts[0]) * tb` need not.
- **Keyed by:** the frame's row in the frame table, for every per-frame array; times in seconds for everything that meets another component. A shot beginning at frame `a > 0` starts at `(t[a-1] + t[a]) / 2`, the cue sheet's `start`.
- **Start offset:** time zero is the first frame; the offset is dropped from the times and kept (`first_pts_ticks`, `first_pts_s_abs`). Subtitle and chapter times are moved to the same zero.
- **Variable frame rate:** needs nothing; each frame has its own time.

Results [L], 52 files of the file-matrix set and the episode:

| Result | Files |
|---|---|
| Table equals the decode loop (count, ticks, seconds as doubles) | 47 |
| Same through D3D11VA | 42 of those 47; D3D11VA gives 0 frames for H.264 Hi10P, 4:2:2, 4:4:4 (3 files) and for one 4:4:4 MP4; MPEG-4 ASP in AVI has no hardware decoder |
| Same as the real performer's cue log (first 14 s) | 12 of 12 run, max difference 3.3e-10 s |
| Raw streams with no stamps (`.264`, `.hevc`) | 2: the worker makes a table (`frame_table_source: synthetic`); the performer's loop keeps one frame |
| Broken on purpose (empty, garbage, MP4 without index) | 3: ffprobe cannot read them, nothing opens them |

The 47 include the timing cases that matter: an MKV starting at 10 s, MP4 empty edits of 1.5 s and 5 s, a trimmed edit list, negative composition offsets, a MOV with a negative first stamp, a variable-rate MKV (24, 30 and 60 fps), MKV ordered chapters (not followed: FFmpeg and the performer play the linear file), and 8 time bases from 1/25 to 1/90000. Hardware decoding adds a gap not in the timeline: the performer chooses the decoder at start and I found no fallback to software in `perf1-decode.c`, so the four files D3D11VA cannot decode need `software` chosen.

## 5. Cache key

Rule: the folder is named by `sha256("panorama.filekey/0" NUL, size, three 1 MiB blocks at the start, middle and end; whole file up to 3 MiB)[:32]` and the size. Versions decide what inside is reused: signals by group (the shot and subject versions hold model hashes, the subject version also the sampling step), each derived table by its own version.

`cache-test.py` (real worker on a 6 s generated clip, software decode, CPU models, 15 checks, 15 s) [L]:

| Claim | Result |
|---|---|
| same file, same key; copy under another name and folder: same key and folder | pass |
| one changed byte in the first, middle or last block (first and last byte of each too), or a changed size: new key | pass |
| a byte between the blocks: same sample key, different full hash, `same_file` tells them apart once the full hash is stored | pass; this is the known gap |
| second run on a renamed copy: finds the folder, scans nothing, rewrites no file | pass |
| a changed byte in the clip: new folder, first folder untouched | pass |
| another model hash, sampling step or signal-group version: signals not reused | pass |
| a new `slide-paths`, `subjects`, `subtitles` or `file-facts` version: that file alone is made again; `shots`: shots, slide-paths and subjects, never the signals; a deleted table counts as stale | pass |

Time to make tables again, on the episode [L]: slide-paths 0.06 s, subjects and shots about 0.5 s, file-facts about the same; subtitles are one libass pass (26.6 s for 8 tracks, in the background of the scan).

## 6. The worker on the real episode

`run-throughput-trial.py` under the GPU lock, 3 pool processes, hardware decode (CUDA path), ONNX on CUDA, below-normal priority, nothing else of mine running [L].

| Measure | Value |
|---|---|
| Wall time, whole run | 164 s for 1420 s of film (8.7 times real time); the scan itself 160 s, 212 frames/s |
| Quick pre-scan (provisional shots and crops from keyframes) | 2.8 s |
| First scanned segment | 5.2 s into the scan, 8.4 s after launch |
| CPU | 892 CPU-seconds, 5.4 cores on average |
| GPU | 21.7 % mean, 91 % peak; decoder 21.9 % mean, 90 % peak; video memory 5.6 GiB peak (1.5 GiB before) |
| Memory | 10.9 GB peak across the worker's job (cap 12 GiB) |
| Folder size | 8.3 MB: signals 1.8, subjects 2.8 + 1.1 raw, subtitles 2.2, shots 0.3 |
| Failed segments | 0 |

A 2-hour film at the same rate: about 14 minutes and 40 MB [D].

## 7. Not done, and why

- **Playback-load trial (S1)** and the "0 dropped frames over 10 minutes" criterion: moved to phase 8 by the user's scope. Reason to watch it: TransNetV2 calls average 9.5 ms (max 13) but the face, head and person calls average 36, 41 and 33 ms (max 106, 156, 109) in batches of 8, where ADR 0024 counted 20 ms as short. Smaller batches or fewer frames sampled are the levers.
- **Accuracy per field and the labelled test set:** moved to a later milestone. The picks rest on the research's measurements.
- **Slide path accuracy on the finished model, faces at planner level, bars and text on real scope films:** same.
- **Licences** of the detector models (YOLO-class networks are AGPL): matter when the licence is chosen.

## 8. Verify

```
tools\venv-analysis\Scripts\python.exe -I spikes\film-analysis\selftest.py            # 13 checks, 0.3 s
tools\venv-analysis\Scripts\python.exe -I spikes\film-analysis\cache-test.py          # 15 checks, 15 s
tools\venv-analysis\Scripts\python.exe -I spikes\film-analysis\validate-content.py DIR # DIR = a content folder
tools\venv-analysis\Scripts\python.exe -I spikes\film-analysis\verify-frame-table.py --hw d3d11va --files FILE...
tools\venv-analysis\Scripts\python.exe -I spikes\film-analysis\analyze.py FILE --cache-root ROOT
```

## Unresolved questions

1. Does the scan drop frames of a playing performer? The face/head/person batches are the likely cost. Needs the user's OK and both screens (phase 8).
2. A raw `.264` or `.hevc` has no stamps and the performer shows one frame. Count frames at the stream's rate in the performer, or refuse such files?
3. D3D11VA gives no frames for H.264 Hi10P, 4:2:2 and 4:4:4 (the anime-fansub staple among them) and the performer has no automatic software fallback that I found. Whose decision, and when?
4. Where does the cache live (which folder, how it is cleaned), and should the full hash always run after a complete scan (about 1 s per GB)?
5. On the burned-in-subtitle clip, 15 of 24 shots have a crop reaching below the bar (to 1010 or 1042 against bars ending at 940). Is that the text being kept (safe) or missed bars? Not judged here.
6. Dolby Vision active-area offsets are not sampled. Worth wiring `dovi-probe` into the worker, or leave crop to the picture?
