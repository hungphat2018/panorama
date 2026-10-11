---
title: Spike — one window across mixed refresh rates (Setup A)
date: 2026-10-11
type: spike
answers: "research-261011-1000 question 1 (does each screen refresh on its own clock?); clips for questions 4 and 6 (judged by eye)"
harness: spikes/mixed-rate-pacing/
results: spikes/mixed-rate-pacing/results-2026-10-11/
status: in progress — 96/72 Hz trials wait for custom display modes; by-eye results wait for the user
---

# Spike: one window across mixed refresh rates

Terms:

- A **refresh** is one chance for a screen to show a picture: 240 per second on the laptop, 100 on the external.
- **Composition** is Windows assembling the next desktop image from all open windows.
- A **film frame** is one picture of the film, 24 per second.

## Answer so far

Windows 11 25H2 composes the spanning mpv window for both screens at the same moment, once per refresh of the **main display** (the one Windows calls "main", where the taskbar's clock lives), not of the fastest screen. Each screen then refreshes on its own clock and shows the newest image composed before that refresh. The first trial had the laptop as main; the user later made the external main, and the second trial shows the clock moved with it.

Measured at 240 + 100 Hz over 30 s, laptop as main display:

| | Laptop (240 Hz) | External (100 Hz) |
|---|---|---|
| Refreshes per film frame | 10 for every frame (675 of 675 after start-up) | 4, and 5 for one frame in six (563 / 113): the predicted stumble |
| Measured refresh rate | 239.9993 Hz | 99.9997 Hz |
| Composition | 240 Hz, both screens within 0.14 ms of each other | same |

- The same film frame reaches the external −1.7 to +6.7 ms after the laptop (p5–p95), median +2.5 ms. These are refresh start times; the panels' own delay is not included.
- mpv reported 0 dropped and 0 late frames, 2 mistimed at start-up. It played 0.10% fast, locking 23.976 fps to 240.001 Hz ÷ 10.
- The counts are the same for a latch margin of 0, 1 or 2 ms. So the result does not hinge on that assumption.

Same rates, **external as main display** (second trial): Windows composes at 100 Hz, and mpv paces to 100 Hz (it slows the video 0.7% to hold 5 frames per 21 refreshes).

- External: 4 refreshes per frame, 5 for one frame in five (537 / 134).
- Laptop: uneven, 9, 10 or 12 refreshes per frame (215 / 322 / 134), because new images now arrive only every 10 ms.
- The same frame reaches the external 2.3–5.8 ms after the laptop (p5–p95), median +4.1 ms.
- mpv: 0 dropped, 0 late.

**Consequences:**

1. The main display sets the pace of the whole spanning window. To play the laptop's 240 Hz cleanly, either the laptop is main during playback, or the main screen runs at a whole multiple of 24 as well.
2. A screen picks up the newest image at its own refresh, so each screen is even when its rate is a whole multiple of the playback rate *and* the main display's rate is one too. Example: external at a custom 96 Hz is predicted even with either screen as main.
3. The two screens still change pictures a few ms apart. The user calls any flicker at the bezel unwatchable (by eye, 2026-10-11). For camera slides, pan mode leaves only a tiny mismatch. For other motion, the screens must refresh in step, which a single window under Windows cannot control.

## How it was measured

- **Test video:** 2240×800 at 23.976 fps, shown at exactly 2× fullscreen across both screens (`--fs-screen=all` gives one 4480×1600 window). Each frame carries its own number as a barcode twice, once on each side of the seam. Bars move across the seam for viewing by eye.
- **`dda-present-log.py`:** per screen, one thread logs every composed image through Desktop Duplication: the OS present time (QPC) and the barcode it holds. A second thread logs that screen's refresh times (`IDXGIOutput::WaitForVBlank`, then QPC). Everything sits on one clock.
- **`analyze-span-pacing.py`:** a refresh shows the newest image composed at least the latch margin before it. From that it counts refreshes per film frame on each screen, and the offset between screens.
- **Why not FFmpeg's capture filters:** neither has a clock shared by two captures. `ddagrab` stamps frames with its own polling clock, and `gfxcapture` subtracts each capture's first timestamp (FFmpeg 8.1 source: `vsrc_ddagrab.c`, `vsrc_gfxcapture_winrt.cpp`).
- **First attempt, corrected:** Desktop Duplication alone reports composition times, not refresh times. Both screens got a new image every 4.17 ms, 0.14 ms apart, so the external's 100 Hz refresh was invisible. The refresh log was added for that.
- **`display-refresh.py`:** switches one screen's rate for a trial without saving it (`ChangeDisplaySettingsEx` without `CDS_UPDATEREGISTRY`). The trial runner always switches it back afterwards.

## Results

| Trial | Laptop refreshes per frame | External refreshes per frame | Offset external − laptop, p5 / median / p95 (ms) | mpv drops / late |
|---|---|---|---|---|
| 240 + 100 (native), laptop main | 10 ×675 | 4 ×563, 5 ×113 | −1.7 / +2.5 / +6.7 | 0 / 0 |
| 240 + 100 (native), external main | 9 ×215, 10 ×322, 12 ×134 | 4 ×537, 5 ×134 | +2.3 / +4.1 / +5.8 | 0 / 0 |
| 240 + 96 (custom) | pending | pending | pending | pending |
| 240 + 72 (custom) | pending | pending | pending | pending |

## Viewing clips (by eye)

Made by `make-pan-viewing-clips.py` from a built-in Windows wallpaper (`C:\Windows\Web\Wallpaper\ThemeC\img29.jpg`, a lake with a forest), mirrored side by side for a long pan. Each is blurred like a film camera (1/48 s shutter). The clips are stored in `spikes/mixed-rate-pacing/viewing-clips/`. Play them with `watch-viewing-clip.py ab|seam|span`, optionally with `--refresh \\.\DISPLAY2=96`; q quits. A 6 s check of each played with 0 dropped, 0 late and 0 mistimed frames at 240.001 Hz.

| Clip | Where | What it shows | Question |
|---|---|---|---|
| `pan-ab-laptop.mkv` | laptop, 240 Hz | slow pan, every 6 s: A = 24 fps as filmed, B = smooth (a new picture every refresh, same blur) | smooth pans: acceptable for films and anime? |
| `seam-sim-laptop.mkv` | laptop, 240 Hz | 24 fps pan across a fake 20 mm bezel; the right side switches 0, 4, 8, 13, 17, then 0 ms after the left | from which offset do the two sides look out of step? **User, 2026-10-11: no threshold named, but the larger offsets "flicker crazy".** |
| `seam-pairs-laptop.mkv` | laptop, 240 Hz | the same pan; 0 ms alternates with 4, then 8, then 13 ms, every 3 s | same or different from 0 ms, for each offset |
| `pan-span.mkv` | both screens | 24 fps pan across the real seam, scaled per screen to one physical size (assumed: tops level, 20 mm gap) | does the pan read as one picture across the bezel? **User, 2026-10-11: the seam shows; "a little frustrating"; any flicker is unwatchable.** Main display at that moment unknown (laptop-main earlier, external-main later). |
| `pan-span-ab.mkv` | both screens, 240 fps | the same pan, every 6 s: A = 24 fps as filmed, B = smooth (pan mode) | does pan mode remove the flicker at the bezel? Needs the laptop as main (with the external main, Windows composes at 100 Hz and mpv drops the extra frames). **User, 2026-10-11, laptop main, 0 dropped: "the pan mode was good". In the 24 fps parts the laptop (main) "flickers more" than the external**: likely the faster, sharper, nearer and possibly brighter panel showing 24 fps steps more crisply (the cadence was an even 10). The flicker is only on the moving trees, not in still areas, so it is motion, not backlight PWM. |
| `anime-pan-ab-laptop.mkv` | laptop, 240 Hz | three real camera slides from the user's anime episode (14:13.9, 9:26.5, 9:44.1); each plays A = as drawn, then B = smooth | same as the first clip, on real anime. **User, 2026-10-11: prefers B** → [ADR 0016](../../docs/adr/0016-camera-slides-play-smooth.md) |

**The real-anime A/B:**

- **Finding the shots.** `find-pan-shots.py` measured every pair of neighbouring frames in the 23-minute episode, at 480×270 in grey (2 min). 72% of pairs are the same drawing held over, which is normal for anime. 7 slides lasted 1.5 s or more, almost all vertical camera tilts. The three used were checked on contact sheets; end-credit slides with separately moving labels were left out.
- **The smooth B version.** `make-real-pan-ab.py` measures each frame's slide (phase correlation refined by ECC, about 0.1 px). It draws a shape-preserving smooth path through every new drawing's position, so the animators' speed-up and slow-down are kept: shot 2 goes 6.9 → 10.5 → 7.6 px per frame. Each refresh shows the nearest frame slid onto the path, and the strip that uncovers at the edge comes from the neighbouring frame. All three shots move every frame, and the path passes exactly through every drawn position.
- **Limit.** This only works where one slide moves the whole picture. A character moving against the background would need a layer-aware method, not done here.
- **Tool note.** OpenCV's `phaseCorrelate` multiplies its inputs by the window in place when the image size is already FFT-friendly (480×270 is). The first scan measured garbage until copies were passed.

## Limits

- **A model, not a camera.** The method combines refresh start times with composition times, and assumes a refresh shows the newest finished composition. That the counts don't change with the latch margin supports this. Panel processing delay and pixel response are not measured; a camera is needed for those.
- Desktop Duplication ran during the trials and could slightly change Windows' scheduling. mpv reported no drops.
- One 30 s trial per configuration.

## Reproduce

- `python make-span-test-video.py WORK_DIR`
- `python run-span-trial.py MPV_EXE WORK_DIR NAME [--seconds 30] [--refresh \\.\DISPLAY2=96]`
- About 35 s per trial, and it takes over both screens. Needs Python 3 with numpy and FFmpeg 8.1 on PATH.
- Viewing clips: `python -s make-pan-viewing-clips.py IMAGE OUT_DIR`, then `python watch-viewing-clip.py CLIP`. Rendering needs OpenCV and NVENC.
- On this PC, run the OpenCV scripts (`make-pan-viewing-clips.py`, `find-pan-shots.py`, `make-real-pan-ab.py`) with `python -s` or `-I`. Plain `python` loads NumPy 2.2.6 from the per-user packages folder, and OpenCV 4.8 needs the system NumPy 1.24.4.
- Brightness match: `python match-brightness.py`. Grey bars run across the bezel; Up/Down set the laptop's backlight until the 50 % bar looks the same on both sides. Enter keeps the level, Esc puts it back. No OpenCV needed.

## Unresolved questions

1. 240 + 96 and 240 + 72: even pace on the external? Waiting on custom modes.
2. By eye: seam offset threshold, the real seam, 24 fps vs smooth. Waiting on the user.
3. Panel delay difference between the two screens: camera only.
4. Real physical layout: the gap between the screens and the height alignment. The span clip assumes 20 mm and tops level.
5. Does the laptop still flicker more than the external once both are equally bright? `match-brightness.py` is ready. The first run on 2026-10-11 matched, but Esc put the original level back. Rerun it, press Enter to keep the level, then watch `span`.
