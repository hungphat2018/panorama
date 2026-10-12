---
title: "Spike P0b: supported-file matrix for the own performer (perf0 against stock mpv)"
date: 2026-10-11
type: spike
answers: "ADR 0022 gate: how much of the long tail of files does the own performer (libplacebo + FFmpeg) play right today, what does stock mpv do on the same files, which gaps must close before the concept demo, and which can stay on the mpv per-file fallback?"
harness: spikes/file-matrix/ (scripts, media, results); spikes/performer-p0/ (six default-off switches added to perf0)
results: spikes/file-matrix/results/ (summary.json, one folder per case, sheets/)
---

# Spike P0b: supported-file matrix

Terms (plain). **perf0** is the P0 player (libplacebo master 7.375 + our LGPL FFmpeg 9.0.2, D3D11, windowed 1920x1080, never fullscreen); it decodes with **D3D11VA** (the GPU's decoder) or **software**. **Stock mpv** is v0.41.0-dev (libplacebo 7.358) with `--no-config`, run once with software decode (its default) and once with `--hwdec=d3d11va`. A **case** is one file. **Shown/expected** is film frames put on screen over the run against frames the file should give in that time (8 s at 23.976 fps is 192). **Late** is a loop interval over 1.5 refreshes. **Combing** is the comb pattern of interlaced video drawn without deinterlacing. A **switch** is a perf0 command-line option I added for this spike; all are off by default. Evidence tags: [L] measured here, [V] source read, [D] derived, [K] prior knowledge.

## Answer

- **Nothing in the matrix needs mpv's core.** 53 cases (42 generated and probed, 9 downloaded from FFmpeg's FATE suite and sample server, the 2 real files). perf0 with software decode opened and decoded every file that is media (one raw stream showed a single frame, see G7); the only failures are the three non-media files (empty, random bytes, MP4 without index), which mpv also refuses [L]. Every gap below is glue code, none is a library limit.
- **Already right [L]:** H.264, HEVC Main10, AV1, VP9, MPEG-2, MPEG-4 ASP in MKV, MP4, WebM, TS, AVI and MPG; 8 and 10 bit, 4:2:2, 4:4:4; 4K at 60 fps (478 of 480 frames, 4 refreshes each); variable frame rate (all 456 frames); MP4 edit lists, negative timestamps, 10 s start offsets; anamorphic pictures (aspect in the stream or only in the MKV); 1918x1078 crops; HDR10 and HLG tone-mapped to SDR (11 of 11 grey steps rise; a real 4K HDR10 clip looks right); Dolby Vision profile 7 base layer equals mpv; truncated and corrupted files play to the cut or over the damage. Median first frame 0.40 s (D3D11VA 0.41, software 0.39).
- **Gaps, biggest first [L]:** (1) **no subtitles at all**: none of the 5 subtitle test tracks (ASS with attached fonts, SRT, mov_text, PGS generated and real) is drawn, nor the 8 tracks of the Judas episode; mpv draws all 5 test tracks. (2) **MKV ordered chapters and segment linking** play the wrong timeline (FFmpeg ignores them [V]); mpv builds the edit timeline. (3) **The GPU decoder refuses** H.264 Hi10P, 4:2:2, 4:4:4 and MPEG-4 ASP on this PC; perf0 has no fallback, so 6 cases fail on D3D11VA (all play in software). (4) **Colour policy**: perf0 converts SD BT.601 primaries to BT.709 and gamut-maps, so saturated SD colours come out up to 62 levels off where mpv is within 1.5; HDR looks darker and less saturated than mpv's defaults. (5) **Rotation** is ignored (portrait video shown squeezed). (6) **Interlaced video combs** exactly as in mpv's default; hard-telecined video needs IVTC; raw `.264` streams carry no timestamps and show one frame.
- **Most fixes are tiny and measured.** Six perf0 switches, +81 / -4 lines in three files in total, fix: deinterlacing (combing 0.79 % to 0.003 %), 5 of the 6 GPU-decoder failures (4 by the software fallback, 1 by the timestamp switch), rotation (aspect 1.78 to 0.563), SD colour (error 62.5 to 2.7 levels), timestamps for raw streams; skipping bad packets changed nothing on any file here. The big pieces are libass plus overlays (about 250 lines [D]), PGS (about 150 [D]) and an ordered-chapters timeline (300 to 400 [D]; FFmpeg's concat demuxer already plays both editions once told the parts, [L]).
- **Verdict for the ADR 0022 gate: keep the own performer.** Close before the concept demo: subtitles (text), the software fallback, the colour policy, rotation and the window-after-open order. Leave on the mpv per-file fallback until built: ordered chapters, PGS, Dolby Vision profile 5 and 8.1 (no real-picture sample found), IVTC, audio passthrough. Details under "Verdict".
- **Caveat on pacing.** Pass 1 (51 cases, 4 runs each) had a median of 0.3 % late refreshes; the second pass ran while `DD2.exe` (Dragon's Dogma 2, 42 % of the dGPU's 3D engine at 19:46) shared the machine, so its late counts (hundreds per run) say nothing about the switches: with the switches off I got the same [L]. Second-pass results are judged on pictures and frame counts only.

## What was run

- **Files.** `spikes/file-matrix/make-test-media.py` regenerates all 42 generated files from `ffmpeg` (MSYS2 GPL build, a test-media tool only) and MKVToolNix 102.0 portable (official 7z, SHA-256 matched the published sum); a forced rebuild in a scratch copy gave the same 44 files (42 generated plus the 2 real ones referenced), all 27 MKV/WebM files with identical stream payload (md5 of the copied packets; the containers differ only in segment UID and date). Pictures are `testsrc2` (moving bars, a checkerboard, a frame counter) or known cards made with numpy (PQ and HLG grey/colour cards, an SDR colour card). The ASS file has styles, karaoke, `\pos`, `\fad` and CJK, with two DejaVu fonts attached; the PGS file comes from our own writer, whose output FFmpeg decodes and overlays. Every file is probed (`results/probe/`), and the structure behind the name is checked: MBAFF from the SPS flags, field order and the telecine pattern from `idet`, MP4 edit lists parsed (empty edit 1.5 s; trim 3.3 s; negative `ctts`). `fetch-samples.py` downloads the 9 sample files and the MKVToolNix archive, each into its own folder with URL and SHA-256 (`downloads/manifest.json`). The two real files are only referenced.
- **Runs.** `run-matrix.py`: per case perf0 with D3D11VA and with software decode (`perf0-lgpl.exe`, 1920x1080 window, so a 1080p picture is dumped 1:1; 8 s of playback, up to 12 s for the VFR and ordered-chapter files; SD interlaced files get a window of their displayed size at native height so combing is dumped 1:1), one frame dump per run, and stock mpv twice with a probe script (`mpv-matrix-probe.lua`: one window screenshot, or for interlaced files the picture at its own size, plus mpv's own view of the file and counters). 280 runs recorded in `results/summary.json`; per case `results/<id>/` holds the JSON, the grab, stderr or the mpv log. Picture checks are automatic (picture bounds and aspect, combing score, left-bar scene colour, patch colours against the card) and by eye from `results/sheets/*.png`. The per-run grabs are git-ignored (regenerable); the sheets are kept.
- **Second pass** (`--variant fixes`): perf0 with `--deinterlace bwdif --rotation --sw-fallback --skip-bad-packets --synth-pts --sd-passthrough` on the 34 cases where one of them can matter (interlace, geometry, colour and broken groups plus 15 others), and the two concat-script timelines run plain.
- **Not covered:** audio (perf0 has none), seeking, monitor hot-plug, HDR output to an HDR display, 10-minute soaks.

## Results

Cells: perf0 D3D11VA | perf0 software | stock mpv (software; "hw" if `--hwdec=d3d11va` really used the GPU). Numbers are shown/expected frames. Gap numbers (G1 ...) point to the table after this one. The grabs for each group are in `results/sheets/<group>-N.png`; `<group>-fixes-N.png` puts perf0 with and without the switches next to mpv.

**Codecs and containers**

| Case | D3D11VA | Software | mpv | Picture | Needs (size) |
|---|---|---|---|---|---|
| h264-1080p24-mkv / -mp4 (mp4 has mov_text) | 192/192 / 192/192 | 193/192 / 192/192 | OK, hw | right; mov_text not drawn | G1 text subs (about 250 lines) |
| hevc-main10-1080p24-mkv | 193/192 | 192/192 | OK, hw | right | - |
| av1-10bit-1080p24-mkv, vp9-1080p24-webm | 192/192 | 192/192 | OK, hw | right | - |
| h264-hi10p, h264-422-10bit, h264-444 (1080p) | **FAIL, no frames** | 192/192 | OK (mpv falls back to software) | right | G2 fallback (+9 lines) |
| mpeg4-asp-480p25-avi | **FAIL, decoder_open** | 203/200 | OK, software | geometry right, colours off | G2 (+9, plus about 10 at open), G4 (+5) |
| hevc-main10-2160p60-mkv | 478/480 | 477/480 | OK, hw | right | - |

**Interlaced and telecined**

| Case | D3D11VA | Software | mpv | Picture | Needs (size) |
|---|---|---|---|---|---|
| h264-1080i-tff-mbaff-ts | 202/200 | 202/200 | OK, hw | combing 0.79 % (mpv 0.76 %, it does not deinterlace by default) | G5 (2 statements) |
| mpeg2-1080i-tff-ts | 202/200 | 202/200 | OK, software (MPEG-2 not in mpv's hardware list [V]) | combing 0.78 % (mpv 0.76 %) | G5 |
| mpeg2-576i-bff-16x9-ts | 202/200 | 201/200 | OK | 16:9 right, BFF combing, SD colours off | G5, G4 (+5) |
| mpeg2-480i-hard-telecine-mpg | 241/240 | 240/240 | OK | 29.97 fps shown as is; 60 of 151 frames carry a repeated field | G6 IVTC (about 150 lines) |
| dl-h264-paff (raw H.264, PAFF) | **FAIL, no frames** | **1 of 30** | OK | no timestamps in the stream | G7 (+3 statements), G5 |

**Timing**

| Case | D3D11VA | Software | mpv | Picture | Needs (size) |
|---|---|---|---|---|---|
| vfr-24-30-60-mkv | 456 of 456 | 456 of 456 | OK | refreshes per frame 4 / 8 / 10 for the 60 / 30 / 24 fps parts (236 / 121 / 91) | - |
| mp4-empty-edit-1p5s | 192/192 | 192/192 | OK | perf0 starts at once; mpv shows pts 0.63 s at 2.1 s (keeps the offset) | G9 convention (about 20 lines) |
| mp4-negative-cts, mkv-start-10s | 192/192 | 192/192 | OK | right, both rebase to 0 | - |
| mp4-trimmed-edit-list (starts 3.3 s in) | 144/144 | 144/144 | OK | both start on frame 78 (3.253 s), 2 frames before the 3.3 s edit start: FFmpeg's mov demuxer, same in both | - |
| dl-mp4-empty-edit-5s (4:4:4) | **FAIL, no frames** | 80/80 | OK | right | G2 (+9 lines) |
| dl-mov-neg-first-pts | 15, EOF | 15, EOF | OK | right | - |

**Ordered chapters**

| Case | D3D11VA | Software | mpv | Picture | Needs (size) |
|---|---|---|---|---|---|
| mkv-ordered-chapters-internal (15 s, plays scenes 1 and 3) | 288/288 | 288/288 | OK, 10 s | at 6.25 s perf0 shows scene 2 (green); mpv shows scene 3 (blue) | G8 (300 to 400 lines) |
| mkv-ordered-chapters-linked (op, body, ed) | 240, EOF | 240, EOF | OK, OP first | perf0 plays the body alone; mpv's first picture is the OP (red) | G8 |
| ordered-internal-via-ffconcat, ordered-linked-via-ffconcat | 240 EOF / 288/288 | same | n/a | scene 3 at 6.25 s and the OP at 2.0 s, as mpv: **a concat script gives the timeline** | the script generator is part of G8 |

**Subtitles** (all four: perf0 plays the video and draws nothing; mpv draws the line at 2 s): ass-attached-fonts-mkv (serif font from the attachments, karaoke), pgs-generated-mkv, dl-pgs-supsample (real Blu-ray PGS), srt-mkv (italic and bold). Needs: G1, text about 250 lines, PGS about 150.

**HDR and Dolby Vision**

| Case | D3D11VA | Software | mpv | Picture | Needs (size) |
|---|---|---|---|---|---|
| hdr10-pq-card-mkv | 192/192 | 192/192 | OK, hw | 11 of 11 grey steps rise; 203 nit patch 135 of 255 (mpv 160), red 203 nit (159,0,0) (mpv (227,0,0)); the two decode paths agree within 0.2 | G4 HDR look: a decision |
| hlg-card-mkv | 192/192 | 192/192 | OK | 11 of 11 steps rise, a little darker than mpv | G4 |
| dl-hdr10-leopard-4k (real, 47 Mbit/s) | 101, EOF | 101, EOF | OK | looks right, a little darker than mpv | G4 |
| dl-hdr10plus (one frame) | 1 | 1 | OK | renders; dynamic metadata effect not measured | - |
| dl-dovi-p5, dl-dovi-p81 | 10, EOF | 10, EOF | OK (mpv reports `dolbyvision` for profile 5) | the 4 KB FATE streams are black frames: cannot judge | G12 needs a real clip |
| dl-dovi-p7 (UHD dual layer, 1 frame) | 1 | 1 | OK | same picture as mpv | - |

**Colour** (known card; max / mean error in 8-bit levels)

| Case | perf0 | mpv | Note | Needs (size) |
|---|---|---|---|---|
| color-bt709-limited-mp4, -fullrange-mp4 | 2.2 / 0.8, 3.6 / 1.4 | 1.2 / 0.4, 1.9 / 0.6 | right, limited and full range | - |
| color-bt601-480p-tagged-mp4, -untagged-mp4 | **62.5 / 12.6** | 1.5 / 0.5 | same bits tagged or not: the 601 guess is right, the policy differs; with `--sd-passthrough` 2.7 / 1.1 | G4 (+5 lines) |

**Geometry**

| Case | D3D11VA | Software | mpv | Picture | Needs (size) |
|---|---|---|---|---|---|
| anamorphic-sar-vui-mkv, anamorphic-container-dar-mkv | 192/192 | 192/192 | OK | 16:9 right; stream and container aspect both honoured; SD colours off | G4 (+5) |
| rotate-90-mp4 | 192/192 | 192/192 | OK | **aspect 1.78, picture squeezed** (mpv 0.5625); with `--rotation` 0.563 | G3 (+1 statement in two places) |
| odd-size-h264 / -hevc 1918x1078 | 192/192 | 192/192 | OK | right | - |

**Broken and real**

| Case | perf0 | mpv | Note | Needs (size) |
|---|---|---|---|---|
| truncated-mkv (55 %) | 131, EOF | plays to the cut | "File ended prematurely", clean end | - |
| corrupt-midstream-mkv | 216 of 240 frames, no crash | plays on | decoder errors logged ("Reference 4 >= 4", EBML element overruns), plays over the damage | - |
| truncated-mp4-no-moov | FAIL, "moov atom not found", exit 1 | "unrecognized file format" | the window is already open | G10 (about 10 lines) |
| garbage-not-media-mkv, empty-file-mkv | FAIL, clean, exit 1 | same | same | G10 |
| real-judas-hevc-main10-mkv, real-hotel-h264-mp4 | 192/192, 202/200 | OK | Judas has 8 subtitle tracks (3 ASS, 5 SRT) and 3 font attachments (FFmpeg warns once per attachment, harmless) | G1 |

**Log excerpts** (full logs in `results/<id>/`): mpv, ordered chapters: `[i][demux] File uses ordered chapters, will build edit timeline.` (`mkv-ordered-chapters-internal/mpv-sw.log.gz`). perf0, MP4 without index: `[mov,mp4,...] moov atom not found` then `perf0: avformat_open_input failed` and `perf0: ERROR: decoder_open failed`; mpv: `[e][cplayer] Failed to recognize file format.`. perf0, truncated MKV: `[matroska,webm] File ended prematurely`. perf0, corrupt MKV: `[h264] Reference 4 >= 4`, `error while decoding MB 99 44`, `Element at 0x3d583e ending at 0x279ae295c exceeds containing master element`. perf0, Judas: `Could not find codec parameters for stream 10 (Attachment: none)` for streams 10 to 12. perf0, GPU decoder refusal (Hi10P): `perf0: decoder does not offer d3d11 for this stream`, `[h264] no frame!`, `avcodec_send_packet: Invalid data found`, `ERROR: no frames decoded`; MPEG-4 ASP: `perf0: no decoder for codec id 12`; with `--sw-fallback`: `perf0: decoder does not offer d3d11 for this stream, decoding in software (yuv420p10le)`.

**Roll-up [L].** First frame (process start to first present): perf0 median 0.41 s D3D11VA (n=44, 0.36 to 0.53), 0.39 s software (n=50, 0.35 to 0.67); mpv 0.43 s (software) and 0.44 s (hw) counted from its core start (n=48 each). Late refreshes over about 1,900 per run: median 0.28 %, 90th percentile 0.63 %, worst 0.9 % (86 runs; two single-frame runs rerun under the game's load are left out). Frames shown equal frames expected within 4 on every constant-frame-rate case that played, except the raw streams and the corrupted file (216 of 240, the damaged stretch is gone). D3D11VA decoded H.264 High 8-bit, HEVC Main10, AV1 10-bit, VP9, MPEG-2 and 4K60; it refused H.264 High 10, High 4:2:2, High 4:4:4 and MPEG-4 ASP.

## Gaps and what the performer needs

Sizes: [L] where a perf0 switch measured it (switch plumbing aside), [D] otherwise. Reference code is named, not copied (mpv is GPL).

| # | Gap | Evidence | The performer needs | Size | Reference code |
|---|---|---|---|---|---|
| G1 | Subtitles: ASS/SSA with attached fonts, SRT, mov_text, PGS | none of the 5 test tracks drawn (nor the 8 of the Judas episode); mpv draws all 5 | libass (library, DirectWrite fonts, one renderer per monitor at monitor pixels, `ass_add_font` for attachments, event chunks from FFmpeg's text decoders), `ASS_Image` to `pl_overlay` (A8 plus colour); PGS: `avcodec_decode_subtitle2` bitmaps to a BGRA overlay scaled from the PGS plane, cue end at the next event | text 250, PGS 150 [D] | mpv `sub/sd_ass.c` (1259), `sub/ass_mp.c` (179), `sub/osd_libass.c` (723), `video/out/bitmap_packer.c` (195), `sub/sd_lavc.c` (794), `video/out/vo_gpu_next.c:466-558`; FFmpeg `libavcodec/assdec.c` (84), `srtdec.c` (116), `movtextdec.c` (605), `pgssubdec.c` (705); libplacebo `renderer.h:474-526` (`pl_overlay`); libass `ass/ass.h` |
| G2 | GPU decoder lacks a profile; no fallback | 6 cases fail on D3D11VA, 0 in software; `--sw-fallback` plays Hi10P, 4:2:2, 4:4:4 (192 to 194 frames) | return FFmpeg's default software format when no hardware format is offered; for a codec with no hwaccel at all (MPEG-4 ASP still fails with the switch) pick the software decoder at open; better: probe first, open once with the right thread count (the fallback decodes on one thread) | +9 lines [L]; open-time part about 10 [D] | FFmpeg `avcodec_default_get_format`; mpv `video/decode/vd_lavc.c` (hwdec-codecs default `:176`) |
| G3 | Display-matrix rotation | aspect 1.78 against 0.5625; `--rotation` gives 0.563 | rotate the source crop before fitting it to the target | +1 statement in two places [L] | libplacebo `demos/plplay.c:409-411`; `src/include/libplacebo/utils/libav_internal.h:848-850, 898-904` |
| G4 | Colour policy | SD card 62.5 against 1.5 levels; mpv `--target-prim=bt.709 --target-trc=srgb` reproduces perf0's numbers exactly, so the cause is mpv's `auto` passing SD primaries through; libplacebo master and the 7.360 package give identical output (`results/experiments/sd-colour-primaries.txt`); HDR 203 nit patch 135 against 160 and no mpv option I tried (target prim/trc, gamut mode, target peak 203, spline) reproduced it (`hdr-target.txt`) | decide target primaries, SDR white and HDR to SDR mapping on purpose (ADR 0012 colour calibration); SD pass-through is 5 lines | `--sd-passthrough` +5 lines [L]; HDR look is a decision | mpv `video/out/vo_gpu_next.c:1205-1260, 2924-2947`; libplacebo `src/include/libplacebo/shaders/colorspace.h:363-380` |
| G5 | Deinterlacing | combing 0.79 / 0.78 / 0.45 % to 0.003 / 0.002 / 0.004 % with `--deinterlace bwdif`, field-rate output (380 frames shown in 8 s), both decode paths | `pl_deinterlace_params` in the render params and `.first_field = pl_field_from_avframe(frame)` on each pushed frame; the queue then splits fields | 2 statements [L] | libplacebo `demos/plplay.c:310-316`, `renderer.h:213-222`, `src/shaders/deinterlacing.c` (387); mpv inserts a filter instead: `filters/f_auto_filters.c`, `video/filter/vf_d3d11vpp.c` (840) |
| G6 | Hard telecine (3:2 baked in) | FFmpeg `fieldmatch,yadif=deint=interlaced,decimate` turns 300 frames into 240 at 23.98 fps; combing score back to the clean-frame level (0.15 to 0.18 % against 0.8 to 0.86 % on combed frames) (`results/experiments/ivtc.txt`) | a libavfilter graph on software frames (a copy for D3D11VA frames), pts after decimation | about 150 [D] | FFmpeg `doc/examples/decode_filter_video.c` (317), `libavfilter/vf_fieldmatch.c` (1077), `vf_decimate.c` (468) |
| G7 | Raw elementary streams (no timestamps) | PAFF stream: 1 of 30 frames (software), none (D3D11VA): every frame gets the same time and the queue drops the rest; with `--synth-pts` both play (38 to 40 field frames with the deinterlacer) | count frames at the stream's rate when pts is missing | +3 statements [L] | FFmpeg `libavcodec/decode.c:296, 694` (`guess_correct_pts` finds nothing to guess from); plplay has the same gap |
| G8 | Ordered chapters and segment linking | FFmpeg skips the edition flags and segment UIDs (`matroskadec.c:481, 687-689`) and merges chapters of all editions (`:3553-3562`) [V]; perf0 plays 15 s linear and the body alone; a concat script reproduces mpv's timeline | a small EBML scan for editions, chapter times, ChapterSegmentUID and each file's SegmentUID; scan the folder for matching files; feed libavformat a concat script (`inpoint`, `outpoint`); handle a part with different codec parameters | 300 to 400 [D] | mpv `demux/demux_mkv_timeline.c` (640), `demux/demux_timeline.c` (850); FFmpeg `libavformat/concatdec.c` (980) |
| G9 | Timeline start convention | perf0 rebases to the first video frame; mpv keeps an empty edit as playback time; FFmpeg's mov demuxer already handles edit lists and negative `ctts` (6 files right; the trim lands 2 frames early in both players) | define time 0 as the first presented sample of audio or video before the A/V clock exists (P5) | about 20 [D] | FFmpeg `libavformat/mov.c:4374-4567, 4825-4879`, `libavcodec/decode.c:357, 1584` (discard flags) |
| G10 | Failure path | non-media files end with FFmpeg's message and exit 1 after the window exists; `--skip-bad-packets` changed nothing on the corrupt file (the decoder concealed the damage) | open and probe the file before creating windows; keep clean exits | about 10 [D] | perf0 `main()` order: `gpu_init` then `decoder_open` [V] |
| G11 | HDR output to an HDR display | not in this matrix (SDR window only) | `pl_swapchain_colorspace_hint` and the DXGI colour space on our own swapchain | untested | libplacebo `src/d3d11/swapchain.c:187-239` |
| G12 | Dolby Vision profiles 5 and 8.1 | only black 4 KB samples exist in FATE; FFmpeg exposes RPU and metadata on all three samples; libplacebo maps them (`map_dovi`) | a real P5 or P8.1 clip with a picture to judge | none known | libplacebo `src/include/libplacebo/utils/libav_internal.h:941-1000` |
| G13 | Audio passthrough, hot-plug | not in this matrix (perf0 has no audio) | WASAPI exclusive plus IEC 61937 packing; device-change handling | about 300 [D] | FFmpeg `libavformat/spdifenc.c` (692); mpv `audio/out/ao_wasapi.c` (546), `ao_wasapi_utils.c` (1138), `audio/decode/ad_spdif.c` (548) |

The six switches are `--deinterlace`, `--rotation`, `--sw-fallback`, `--skip-bad-packets`, `--synth-pts`, `--sd-passthrough` (spikes/performer-p0, all default off, so the P0 numbers stay reproducible). The pre-switch binary produced pass 1; flags off is the same code path.

## Verdict for the ADR 0022 fallback gate

The fallback to mpv's core is judged on whether the long tail needs something only mpv's core has. After 53 cases it does not: the largest missing piece for a demo is subtitles, which is libass plus a bitmap path; mpv's own overlay glue in `vo_gpu_next.c` is about 130 lines on top of libass (engine research). What remains is effort: if the libass plus PGS glue passes about 600 lines or the timeline layer 800, reconsider. The matrix stays as the living check: rerun `run-matrix.py` after each performer milestone and judge the fallback against it.

**Close before the concept demo (own performer):**
1. G1 text subtitles (ASS with attached fonts, SRT, mov_text): the Judas episode has 8 tracks and ADR 0013 places dialogue per screen, so this is a demo feature anyway.
2. G2 software fallback, with the probe-first design (Hi10P is the anime staple).
3. G4 colour policy, decided on purpose (SD pass-through and the HDR to SDR look); colour matching across monitors is a demo goal.
4. G3 rotation, G7 timestamps for raw streams and G5 deinterlacing: switches that already exist, a few lines each.
5. G10 order (open first, then windows); G9 convention together with the audio clock.

**Stay on stock mpv per file until built:** G8 ordered chapters and linking, G1 PGS, G12 Dolby Vision 5 and 8.1, G6 IVTC (mpv does not do it by default either), G13 audio passthrough, G11 HDR output. A router that probes the file (ordered-chapter flag, bitmap subtitle track) and starts mpv is about 60 lines [D].

mpv is not flawless here either: it needs `--hwdec-codecs` to use the GPU for MPEG-2 and MPEG-4 [V], does not deinterlace by default [L] and has no IVTC unless a filter is added [K], and it shows SD colours passed through, not converted [L].

## What worked and what did not

- Worked: probing every generated file for the structure its name promises (it caught that FFmpeg 9's command line ignores `-color_primaries` and `-color_trc`, so tags now go through a `setparams` filter, and that FFmpeg's MKV muxer writes subtitle tracks without a default flag, so mpv showed none until `-disposition default`); comparing perf0 with mpv on known cards (that is how the SD primaries finding appeared); the concat-script route for ordered chapters. The three side experiments are in `spikes/file-matrix/experiments/` with their output in `results/experiments/` (`sd-colour-primaries.txt`, `hdr-target.txt`, `ivtc.txt`).
- Did not work or is weak: `testsrc2` is synthetic, so colour findings use the cards and combing uses a score, not footage; mpv's window screenshot is scaled (comb scores for mpv use its native-size grab); the comb score of a single telecined frame depends on which frame it is (use `idet` for those); single-frame samples (HDR10+, Dolby Vision 7) say little about playback; the DV 5 and 8.1 samples are black; second-pass pacing was disturbed by a game; perf0's `dropped` count includes frames discarded when the run ends, so shown/expected is the number to trust; the first pass ran against the binary before the switches existed (flags off is the same code path, not re-verified byte for byte); the pass-1 runner stopped at case 46 on an mpv edge case (Lua writes an empty table as `[]`, which the runner did not expect), was fixed, and the last six cases were run next (`results/run-matrix-console.log` keeps the traceback, `run-matrix-console-2.log` the rest).

## Reproduce

```text
python -I spikes\file-matrix\fetch-samples.py             # 9 FATE/sample files + MKVToolNix, SHA-256 recorded
python spikes\file-matrix\make-test-media.py [--force]     # 42 generated files + media\cases.json
python spikes\file-matrix\run-matrix.py                    # pass 1: perf0 x2 + mpv x2 per case
python spikes\file-matrix\run-matrix.py --variant fixes "--deinterlace bwdif --rotation --sw-fallback --skip-bad-packets --synth-pts --sd-passthrough" --group interlace
bash   spikes\file-matrix\run-second-pass.sh               # the whole second pass (about 18 min)
python spikes\file-matrix\run-matrix.py --sheets [fixes]   # contact sheets; --table prints the result table
```

Needs `spikes\performer-p0\bin\perf0-lgpl.exe` (rebuild with `compile-perf0.sh lgpl`), `tools\mpv-v0.41.0-x86_64-w64-mingw32\mpv.exe`, MKVToolNix in `spikes\file-matrix\tools\mkvtoolnix` and the DejaVu fonts in `tools\fonts` (both downloaded or copied, git-ignored).

Files added under `spikes/file-matrix/`: `fetch-samples.py`, `make-test-media.py`, `matrix-media-tools.py`, `matrix-picture-checks.py`, `run-matrix.py`, `run-second-pass.sh`, `mpv-matrix-probe.lua`, `experiments/` (three scripts), `downloads/manifest.json` (URL, size and SHA-256 of each download; each file sits in its own folder with a `source.json`), `results/` (`summary.json`, `probe/`, `experiments/`, `sheets/`, one folder per case), `.gitignore`; `media/` and `tools/` are git-ignored. Changed under `spikes/performer-p0/src/`: `perf0.h`, `perf0-main.c`, `perf0-decode.c` (+81 / -4 lines).

## Unresolved questions

1. What does the author's own library contain (ordered chapters, PGS, interlaced, 4:2:2)? A scan with `ffprobe` over the folders would size the long tail; it needs the user's folders.
2. HDR to SDR look and SD primaries: pass through like mpv, or colour-manage (G4)? A what-you-see decision.
3. Is there a real Dolby Vision profile 5 or 8.1 clip with a picture, to judge G12?
4. Does D3D11VA decode work, and look the same, when the picture goes to a screen on the other graphics adapter (Setup B)? Not tested.
5. Clean pacing for the switches: rerun the second pass when the machine is quiet.
6. Subtitle layout across three screens (ADR 0013) is untouched here; libass per monitor needs one design decision on where a line may cross a bezel.
7. Seeking, audio sync and long files (soaks) are outside this matrix and decide the same fallback gate.
