---
title: "Performer audio: sound that follows the film clock (WASAPI, own demux/decode, soft correction)"
date: 2026-10-12
type: implementation
answers: "M1 phase 02, step 5 (audio sync, audio follows the master display)"
harness: spikes/performer-p1/
results: spikes/performer-p1/results-audio/
---

# Performer audio

Built into the performer (`spikes/performer-p1/`). Compiles clean (no warnings). Automated checks all pass; no listening was done (phase 8). The sound was heard only in four short, quiet runs on the PC's default device (section 5).

## 1. What was built

The sound has its own demuxer and decoder (the video decoder is untouched), a converter, a WASAPI shared-mode sink on its own MMCSS thread, and a stand-in sink (`--audio-null`) that has the same interface and runs on the host clock, so everything below is testable with no sound device.

| Part | File | Job |
|---|---|---|
| open | `src/perf1-audio-open.c` | picks the stream (FFmpeg's best, or `--audio-track N`), opens the file a second time, film time 0 = start time of the video stream (the video decoder's first frame) |
| decode | `src/perf1-audio-decode.c/.h` (+ `-decode-priv.h`) | thread: demux, decode, queue of frames with film time (3 s cap). Same generation protocol as the video supply: `seek(gen, t)` empties the queue at once, the thread seeks 1 s before t and queues from there so the consumer can cut to the exact sample |
| convert | `src/perf1-audio-pipe.c`, `-pipe-feed.c`, `.h`, `-pipe-priv.h` | libswresample to the device's float format and rate, at the film's speed (rate lock), soft correction by `swr_set_compensation`, silence for gaps, cut for overlaps, exact positioning at an epoch's first sample; says which film time each block of frames carries |
| sync arithmetic | `src/perf1-audio-sync.c/.h` | device clock from (position, QPC) pairs, write log (device frame to film time), PI loop |
| sinks | `src/perf1-audio-wasapi.c`, `-null.c`, `-sink.h` | default render device, event driven, mix format (float), `IAudioClock` position + QPC, `ISimpleAudioVolume` (not persisted); stand-in with optional clock drift and driver delay |
| thread and state | `src/perf1-audio.c`, `-play.c`, `-priv.h`, `perf1-audio.h` | open/close, engine notes, one wake-up: clock, error, correction, placement, fill |
| log | `src/perf1-audio-log.c` | `audio.csv` next to the monitor logs, summary line |

**Engine hook (small edits to shared files).** `engine_host.note` (optional, NULL in the simulation) gets `NOTE_SEEK` (every seek and every resume), `NOTE_PAUSE`, `NOTE_HOLD` (a pause or resume arriving while a seek is in flight) and `NOTE_EPOCH` (master refresh J0 shows film time `origin`, `step_s` per master refresh). Nothing else in the engine changed; `latch-sim` builds and passes unchanged. Options: `--audio-track N`, `--no-audio`, `--audio-volume`, `--audio-offset-ms`, `--audio-null`, `--audio-null-drift-ppm`, `--audio-null-latency-ms`.

**Sync model, as built.**
- The film clock at host time T is `origin + (T - light(J0)) / period * step_s`, with `light(J0)` the time master refresh J0 lights the aim row (row-timing, live grid, panel lag included) and `period` the master's fitted refresh period. The re-aim shift is not applied: the sound stays on the unshifted clock, the picture moves within one master refresh around it.
- Epoch start: silence is written until the device frame that will sound at `light(J0)` (from the device clock), then the sample for `origin` follows. If the moment has already passed when the decoder and the device are ready, the head of the sound is skipped by exactly the lateness (the epoch row logs it); sync is not affected.
- Playback: every device period the error (film time sounding now minus film clock now) is measured and fed to a PI loop (Kp 2/s, Ki 1/s^2, error low-passed 80 ms, at most 0.5 %) that sets the resampling ratio. The speed lock (23.976 at 96/72 Hz, +0.1 %) is the base ratio of the resampler (`step_s / period`), so the pitch is +0.1 % (not corrected). No video frame is ever dropped or repeated for the sound.
- Beyond 40 ms for 3 periods in a row: resync (silence inserted or sound skipped to the clock), logged, counted.
- Breaks: the device stream runs from open to close. A seek, resume or pause does not stop it: it goes to silence and the decoder seeks; the new epoch is placed on the existing device clock. (A first version stopped/reset/started the stream at every break; on the real device the position then stays at 0 for about 60 ms after a restart, so each new epoch started 60 to 68 ms late. The queue left at the break, at most 30 ms plus the driver's own delay, is still heard.) Pause: silence. Seek that lands paused: the epoch is not played. End of file: the converter's tail, then silence (`end` row).
- A restart announced less than (queue + driver delay) before its due time loses the first part of the new sound (skipped, logged as `skipped_ms`): about 25 ms on this PC's device, 0 on the stand-in. See question 1.

## 2. How to run

```
pwsh -File spikes\performer-p1\compile.ps1 -Output perf1-audio-lgpl.exe     # the program (the default name perf1-p3-lgpl.exe also includes the sound)
pwsh -File spikes\performer-p1\compile.ps1 -Program audio-test               # bin\audio-selftest.exe
python -I spikes\performer-p1\audio-run-tests.py [--quick] [--wasapi]        # everything below, about 8 min; --wasapi adds an audible 8 s run
python -I spikes\performer-p1\audio-analyze.py RESULTS\audio.csv [--min-steady N] [--max-resyncs N]
spikes\performer-p1\bin\run.cmd perf1-audio-lgpl.exe --headless 96 --file spikes\performer-p1\data\audio-test-24\cue-test.mkv --audio-null --seconds 30 --out results-audio\x
```

Clips (`audio-make-test-media.py`, video copied from the cue-test clips): `data/audio-test-24` (23.976 fps, AAC 44.1 kHz stereo, 60 s), `audio-test-25` (25 fps, FLAC 5.1), `audio-test-vfr` (24/30/60 fps, 12 s), `audio-test-ramp` (`ramp.nut`, `ramp.mkv`: a PCM track delayed 0.25 s whose sample k holds k/2^24, so any sample says which film time it is).

`audio.csv` columns and row kinds are in the header of `src/perf1-audio-log.c` (`start`/`epoch` placement, `err` every device period, `sec` once per second with mean error, correction in ppm, resyncs, underruns, latency, device clock spread).

## 3. Tests

- `audio-selftest.exe` (4,482 checks, both clips): seeks land on the exact sample (11 targets incl. before the track starts and past the end); frames carry the film time their samples hold (NUT: within 25 us; Matroska: within 700 us, it rounds timestamps to the millisecond); converter claims vs content at speed 1, 1.001, +/-0.2 % correction; 12,000 frames of silence before a track that starts 0.25 s late; owed silence; skip; end flag; device clock follows 300 ppm drift within 0.4 ms; write log; PI loop settles from 8 ms with 300 ppm mismatch, no overshoot, learns the mismatch.
- `audio-analyze.py --selftest`: 17 made-up logs, each check fires on its defect. Checks: P1 placement < 5 ms; E0 no error beyond 40 ms in the first second; E1 every error < 5 ms from 1 s after an epoch start; E2 steady-state (from 2 s) p99 < 2 ms; U1 no underrun from 1 s after a start; R1 resyncs only within 1.5 s after an epoch start (and at most N); C1 coverage.
- A negative control: with the old-epoch write log left in place (a real bug found with the driver-delay scenario), E0 and R1 fail (`results-audio/neg1`).

## 4. Measured, headless, `--audio-null` (`results-audio/final`, 2026-10-12 11:21 to 11:29, machine shared with another agent's picture-path runs)

| Run | epochs / breaks | steady-state \|error\| p50 / p99 / max (ms) | resyncs | underruns | largest correction |
|---|---|---|---|---|---|
| plain 60 s, 23.976 fps, 96 Hz (lock +0.1 %) | 1 / 0 | 0.038 / 0.295 / 0.458 (5,793 samples) | 0 | 0 | 808 ppm |
| plain, 72 Hz (lock +0.1 %) | 1 / 0 | 0.020 / 0.162 / 0.247 | 0 | 0 | 413 ppm |
| plain, no lock | 1 / 0 | 0.023 / 0.169 / 0.213 | 0 | 0 | 398 ppm |
| device clock +300 ppm | 1 / 0 | 0.032 / 0.161 / 0.198 | 0 | 0 | 596 ppm |
| device clock -500 ppm | 1 / 0 | 0.036 / 0.239 / 0.290 | 0 | 0 | 1003 ppm |
| adversarial seeks around swaps, 96 Hz | 8 / 7 | 0.006 / 0.039 / 0.046 | 0 | 0 | 89 ppm |
| same, device holds frames 40 ms | 8 / 7 | 0.008 / 0.025 / 0.029 | 0 | 0 | 48 ppm; 7 starts late, up to 26.5 ms skipped |
| scrub (5 seeks/s), device 40 ms delay | 25 / 24 | 0.007 / 0.021 / 0.023 | 0 | 0 | 40 ppm; 24 late, up to 33 ms skipped |
| pause, resume, seek while paused | 8 / 16 | 0.011 / 0.066 / 0.088 | 0 | 0 | 137 ppm |
| scrub (5 seeks/s) | 25 / 24 | 0.005 / 0.023 / 0.028 | 0 | 0 | 62 ppm |
| 20 seeks/s | 85 / 484 | no steady state | 0 | 0 | 38 ppm |
| variable frame rate 24/30/60 | 1 / 0 | 0.011 / 0.042 / 0.048 | 0 | 0 | 84 ppm |
| 25 fps, FLAC 5.1, 100 Hz, adversarial | 8 / 7 | 0.012 / 0.082 / 0.121 | 0 | 0 | 176 ppm |

Placement of the first sample: at most 0.245 ms off in every run (0.002 to 0.021 ms without drift), first-second error at most 0.22 ms. All 13 runs PASS the analyzer (max steady p99 0.295 ms against 2 ms; settle limit 5 ms).

Note on the 60 s run: the error grows slowly (0.02 ms at 10 s to 0.4 ms at 50 s). The sound's own film time wanders by about 0.15 ms around a straight line, i.e. the master film clock does (the fitted virtual-refresh grid under idle-priority wake-up jitter); the loop follows it. Not a loop instability (the unit test of the loop is clean).

## 5. Real device (WASAPI, shared, default render device = "FxSound Speakers (FxSound Audio Enhancer)", 48 kHz, 2 ch, buffer 1,920 frames, period 480)

Four short runs at stream volume 0.2 (the brief allowed one; the others exercised the break path, which a first version of mine got wrong on the real device, and then the fix; each 8 to 9 s). Last run, `results-audio/wasapi-seek-pause-resume` (script: seek at 2.5 s, pause at 4.5 s, resume at 6 s):
- 3 epochs, 736 measurements, placement 0.057 ms, steady p50 0.010 / p99 0.032 / max 0.035 ms, 0 resyncs, 0 underruns, largest correction 112 ppm. PASS.
- The first plain 8 s run: p99 0.034 ms, 0 resyncs, 0 underruns.
- Device clock spread 0.25 to 0.34 ms rms (the position readings are noisier than the stand-in's 0.007 ms). `GetStreamLatency` reports 0.0 ms for this virtual driver; the audible position lags the frames written by about 63 ms (30 ms we queue + about 33 ms downstream), which the clock includes. After the two breaks the sound of the new epoch started 24.8 ms late (skipped, not misplaced).
- What this does not prove: the error is sound against the film clock as the device reports its position. Whether the speakers are in step with the pictures depends on what the driver reports (a virtual enhancer driver here) and the panels' lag; that is a by-ear / loopback check (section 6).

## 6. Left for phase 8 (by ear), and limits

- Absolute lip sync: listen with a flash-and-click clip (the barcode clip with a click on each shot change); calibrate with `--audio-offset-ms` (+ delays the sound). Real hardware (not FxSound), Bluetooth, USB and HDMI audio untested.
- The +0.1 % pitch of the rate lock (about 1.7 cents), and short corrections up to 0.5 % in a transient; time-stretch is the later fix.
- Downmix of 5.1 to stereo is swresample's default matrix (tested for sync, not for sound).
- Device hot-plug and format change: not handled (the sink stops answering, a `device` row is logged, the film plays on silently).
- Exclusive mode, passthrough (AC-3/DTS bitstream), multichannel devices: not built.

## 7. Verification of the rest

- `python -I run-latch-test.py` (full, 2026-10-12 11:10 to 11:15, `results-audio/latch-regression.txt`): steps 1 to 6 PASS (183 unit checks, 33 judge tests, 1,441 swaps / 4,904 seeks / 0 wrong frames, picks, clock, 7 of 7 negative controls caught) and the named-pipe test 17 of 17. Step 7 (the real program, headless) FAILS 6 of 6 runs on `C7`/`C7b` only (a monitor's first refresh one later than planned), with wrong frames 0, shots changed 0, clock violations 0. This is the finding in `spike-261011-2145-performer-cues-and-seek-latch.md` section 6, not the sound: those clips have no audio stream (the audio thread never starts) and the same scenario with `--no-audio` fails the same way (11 and 6 events against 6 and 4 with the sound code present, `results-audio/c7-check*`). The machine was shared with another agent's GPU runs.
- Compile warnings: none (`-Wall -Wextra`) for `p3`, `latch-test`, `audio-test`.

## 8. Files

Created (all `spikes/performer-p1/`): `src/perf1-audio.h`, `-audio-priv.h`, `-audio.c`, `-audio-play.c`, `-audio-log.c`, `-audio-open.c`, `-audio-decode.h/.c`, `-audio-decode-priv.h`, `-audio-pipe.h/.c`, `-audio-pipe-feed.c`, `-audio-pipe-priv.h`, `-audio-sync.h/.c`, `-audio-sink.h`, `-audio-wasapi.c`, `-audio-null.c`; `audio-selftest.c`, `audio-analyze.py`, `audio-run-tests.py`, `audio-make-test-media.py`; `data/audio-test-{24,25,vfr,ramp}/`; `results-audio/`.
Changed with small edits: `src/perf1-engine.h` (note types + `engine_host.note`), `src/perf1-engine.c` (6 `host_note` calls), `src/perf1.h` (7 config fields), `src/perf1-args.c` (options, validation, usage), `src/perf1-main.c` (include, `.note`, open/close, 5 lines), `compile-perf1.sh` (sound sources, libswresample, `-lole32`, program `audio-test`).
Built: `bin/perf1-audio-lgpl.exe`, `bin/audio-selftest.exe`; `bin/perf1-p3-lgpl.exe`, `latch-sim.exe`, `cue-selftest.exe` were rebuilt from the shared tree (it holds the other agent's picture-path work as of 11:30).

## Unresolved questions

1. After a seek the engine restarts the clock about 60 ms ahead; the device needs queue (30 ms) + driver delay (about 33 ms here) before new sound can sound, so the first 25 to 33 ms of the new sound is skipped. Add the sound's lead to the engine's restart margin (a seek would show its picture about 25 ms later), or accept the loss?
2. Is the default device's reported position trustworthy for lip sync on the real hardware (FxSound is a virtual driver with 0 reported latency)? Needs a loopback or by-ear calibration, and a decision where the calibrated offset lives (per device, in the room profile?).
3. Film time 0 for the sound is the video stream's `start_time`; for files with edit lists or negative composition offsets the video decoder's first frame may differ (not checked against `perf1-decode.c`, which I may not read-modify). Expose the decoder's `file_first_pts` and use it?
4. Matroska rounds timestamps to the millisecond, so the sound's first sample after a seek is placed within about 0.5 ms of the film clock, not to the sample. Enough?
5. Pitch +0.1 % from the rate lock: accept until time-stretch, or switch the lock off when sound is on?
6. The sound ignores the re-aim shift (picture within one master refresh of the sound); confirm this is the intended meaning of "picture stays within one master refresh of the sound".
7. When the video ends before the sound, the run ends and the rest of the sound is cut; is that right for the end of a film?

Status: DONE_WITH_CONCERNS
Summary: Sound is built (own demux/decode, WASAPI sink, film-clock sync with soft correction, breaks, headless `--audio-null`) and passes its own automated checks on 13 headless runs and the real device (p99 error 0.3 ms or less against 2 ms).
Concerns: step 7 of run-latch-test fails on the known late-first-refresh issue (same with `--no-audio`); real-device runs were four, not one; the first 25 ms of sound after a seek is lost on this device; absolute lip sync unmeasured.

## Follow-up, 2026-10-12 (controller)

Unresolved question 1 resolved: each restart is now planned far enough ahead for the sound. The audio thread measures its lead (queue left from a break + one wake-up + what the device holds beyond its buffer) and `perf1-main.c` raises the engine's restart margin to it (`audio_start_lead_s`). Real device, same seek/pause/resume script: margin 78.8 ms (was 3.0); `skipped_ms` 0.00 at both restarts (was 24.85 / 24.88), placement max 0.027 ms, steady p99 0.038 ms, 0 resyncs, 0 underruns (`results-audio/wasapi-seek-margin/`). The analyzer's verdict there is FAIL on E2 only (137 steady measurements in a 9 s run with two breaks, it wants 300). Full `audio-run-tests.py --wasapi` and `run-latch-test.py --skip-exe` pass after the change. Cost: a seek restarts about 76 ms later.
