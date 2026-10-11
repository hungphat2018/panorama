---
title: "Room model and display-settings restore (M1 phases 06 and 07)"
date: 2026-10-11
type: research
sources: "23 web searches/fetches (Microsoft Learn, madVR tracker, CRU, nDisplay, NVIDIA/AMD pages), 12 raw primary-source reads (Windows SDK 10.0.26100.0 headers, edid-decode, Linux drm_edid.c and amdgpu, NVIDIA nvapi.h, Intel igcl_api.h, MPC-BE, Kodi), local probes on Setup A, one bounded display test on Setup A"
scripts: "spikes/room-probe/room-probe.py, spikes/display-restore/display-restore-kill-test.py"
---

# Room model and display-settings restore

Tags: **[V]** fetched or read this pass (primary page or source). **[S]** search snippet only. **[K]** prior knowledge, not re-verified. **[L]** measured on this PC (Setup A, Windows 11 25H2, build 26200.8875). **[D]** my derivation.

## 1. Answer

1. **Room model = physical rectangles in a seat-centred frame, in three layers.** *Detected* (re-read at every launch: ids, pixels, exact timing, colour state), *aligned* (pose, bezels, seat, size override: only the user can give these), *verified* (measured once and kept: custom modes that round-tripped, latency, colour correction). Nothing in it says "left/right/centre": seams are computed from poses. Schema in 2.3.
2. **Identity key = `DisplayTarget.StableMonitorId`**, an opaque string Microsoft documents as persisting "no matter which adapter or connector" [V]. On both Setup A monitors it equals the key Windows itself uses in `GraphicsDrivers\MonitorDataStore` and my rebuild from the EDID (`mfg+product+serialText_week_year_checksum`) [L]. Do not use LUID, PnP path or `\\.\DISPLAYn`: the same laptop panel has three different PnP instance ids in the registry [L]. Identical models with placeholder serials (the EGM22F100H reports serial `0000000000001`) cannot be told apart by EDID; they need port, desktop order and a one-click "identify" (2.4).
3. **Pixels and timing are exact from Windows; physical size is not.** Exact current timing comes from CCD (`pixelRate / totalW / totalH`; the rounded `vSync` 240001/1000 hides 240.00102 Hz). Physical size comes from the EDID and is only cm-precise on the external: 3.7% non-square pixels, square-pixel rule gives 480 x 270 mm [L]. Windows' `DisplayMonitor.PhysicalSizeInInches` is width x height (not "diagonal only" as the 10-10 report said) and just repeats the EDID mm [L].
4. **Variable refresh: "declared" is readable, "enabled" is not (without a vendor SDK).** Four EDID signals to parse (DisplayID dynamic range, AMD FreeSync block, HDMI Forum VRRmin/max, base-block range limits, the last one only valid under the EDID 1.4 + continuous-flag + range-limits-only rule [V, Linux]). Setup A: laptop declares 60-240 Hz via three signals; the external declares only AMD's FreeSync-over-HDMI block (48-100 Hz, EDID 1.3), which NVIDIA drivers do not use [S]; the LG UltraFine declares none (its 40-60 Hz is a timing envelope). "Enabled" on NVIDIA = `NvAPI_Disp_GetVRRInfo.bIsVRREnabled` [V header]; plain Win32 has no such flag.
5. **Display-settings test (3 hard kills, [L]):** a change made with `ChangeDisplaySettingsEx` flags 0 and one made with `SetDisplayConfig` without save both **stayed** after `TerminateProcess` (60 Hz for the full 10 s). The `CDS_FULLSCREEN` change **reverted by itself**, already visible at the first read, 0.45 s after the kill. The saved (registry) mode never changed in any trial. `ChangeDisplaySettingsEx(dev, NULL, NULL, 0, NULL)` brought the 100 Hz back every time. The test ran once (13:24-13:25 +0700), before the coordinator's hold message; no refresh rate has been switched since.
6. **Recommended restore design:** make the change temporary by construction (`CDS_FULLSCREEN`, held by the long-lived process; this is what MPC-BE and Kodi do today [V]), write a dirty snapshot to disk first, add a detached guardian only for changes `CDS_FULLSCREEN` cannot express (exact rational rates, primary display, rotation), recover from the snapshot at next launch, never persist. A logon task and Restart Manager add nothing (4.4).
7. **Bug in the existing spike tool: `display-refresh.py` defines `CDS_TEST = 0x4`.** In the SDK `CDS_TEST` is 0x2 and 0x4 is `CDS_FULLSCREEN` [V, local header]. Its "test" call therefore applies a process-bound mode, and `run-span-trial.py` runs the tool as a short-lived subprocess and plays for 30 s afterwards. Prediction [D]: the 96/72 Hz trials can silently fall back to 100 Hz when the setter exits. Check in one minute before trusting them (4.5).
8. **Setup B:** the registry holds only one Setup B screen, the LG UltraFine (27-inch class, 3840x2160 @ 60 Hz, HDMI 2.0, HDR10 declared at 400 nits, no VRR, last enumerated 2026-02-16 on this laptop's HDMI). The other two screens were never connected to this PC. Everything physical (sizes, orientation, gaps, ports, seat) is the user's to give, or comes from running the probe on Setup B.

## 2. Room model

### 2.1 Fields, sources, kind

Kind: **E** exact (Windows or EDID), **D** derived, **A** aligned or typed by the user, **M** measured. Needed by: **G** geometry (fold and curve), **C** colour, **T** timing, **I** identity and matching, **R** restore.

| Field | Source | Kind | Needed by |
|---|---|---|---|
| Stable monitor id | `DisplayTarget.StableMonitorId` (Win10 1809+, via WinRT) [V]; fallback rebuild from EDID | E | I |
| EDID id, serial u32, serial text, week/year, base-block SHA-256, placeholder-serial flag | registry `Enum\DISPLAY\..\Device Parameters\EDID`, or `DisplayMonitor.GetDescriptor` | E | I |
| Name | CCD target name, EDID descriptor 0xFC | E | I (UI) |
| Windows container id | registry `ContainerID`: a version-5 UUID for the external monitors, a null-style `{00000000-...-ffff-...}` for the internal panel [L]; Windows derives it from the EDID [K] | E | I (secondary; would be the same for duplicates) |
| Connector kind and instance, target id | CCD `outputTechnology`, `AdapterRelativeId` (stable across reboots for the same GPU [V]) | E | I (tie-break), T |
| GPU vendor/device/subsystem; LUID | DXGI `GetDesc1`; CCD `adapterId` | E | T (which VRR path), session handle only |
| Native pixels | EDID preferred timing / DisplayID Type VII / CCD | E | G |
| Native orientation | EDID DTD shape, DisplayID scan orientation | D | G |
| Desktop rectangle, primary flag | CCD source mode | E | G, T (main display sets the pace of a spanning window [spike]) |
| Rotation, GPU scaling mode | CCD `rotation`, `scaling` (must be identity) | E | G |
| DPI scale | `GetDpiForMonitor` (150% on the laptop) | E | G (UI only) |
| Physical size, pixel pitch | DisplayID params (0.1 mm) > first DTD mm > base-block cm; square-pixel rule on the longer axis | D, +-1-4% | G |
| Size sanity flags | non-square pixels, cm-only precision, DTD vs base-block mismatch, placeholder size | D | G |
| Curvature radius | spec sheet or typed (not in EDID) | A | G |
| Panel pose in the seat frame (centre, yaw, pitch, roll) | on-screen alignment | A | G |
| Bezel widths per edge | typed seed; checked against the aligned gap | A | G |
| Seams: gap, stagger, depth step, toe-in | from the poses | D | G |
| Seat: eye point and gaze (ADR 0007, 0011) | typed or aligned; default 700 mm on the anchor monitor's normal | A | G (curve), T (deg/s of pans) |
| Current timing: pixel clock, totals, rational refresh | CCD target mode | E | T |
| Built-in mode list, exact rational rates | DXGI `GetDisplayModeList` (59.94 shows as 60000/1001) [L]; GDI list only has integer Hz | E | T |
| Fallback mode (what Windows returns to) | `EnumDisplaySettingsEx(ENUM_REGISTRY_SETTINGS)` | E | R |
| EDID-declared timings (DTD, CTA VICs, DisplayID Type VII) | EDID | E | T (cross-check) |
| Link facts: HDMI max TMDS, FRL code, ALLM | HDMI LLC and HDMI Forum blocks | E | T |
| VRR declared: range and which signal | EDID, 4 sources (1.2) | E | T |
| VRR usable on this link and GPU | rule table: DP/eDP Adaptive-Sync; HDMI Forum VRR needs an HDMI 2.1 VRR source; AMD block is AMD-driver only [S/K] | D | T |
| VRR enabled | NVAPI `NvAPI_Disp_GetVRRInfo` (`bIsVRREnabled`, `bIsVRRPossible`) or IGCL/ADL; otherwise a present-interval measurement | E (vendor) or M | T |
| Custom modes verified | made by a tool, then CCD read-back and measured rate | M | T |
| Panel latency | camera or photodiode, stored relative to a reference monitor | M (later) | T |
| EDID primaries, white, gamma | EDID base block (nominal, not measured) | E | C |
| Luminance min/max/avg | CTA HDR static metadata block, also DXGI `GetDesc1` | E | C |
| Advanced colour, ACM, HDR, active mode, SDR white level, bits | CCD `GET_ADVANCED_COLOR_INFO` and `_2`, `GET_SDR_WHITE_LEVEL` [V SDK 26100] | E | C, R |
| Per-monitor correction (gain, white, gamma, black lift) | by-eye or colorimeter | A/M | C |
| EDID override present (CRU style) | registry `Device Parameters\EDID_OVERRIDE` | E | I, T |

Everything volatile (LUID, `\\.\DISPLAYn`, PnP instance path, desktop rectangle) is a session cache, never a key.

### 2.2 VRR signals the probe parses

| Signal | Where | Rule |
|---|---|---|
| Base-block range limits (0xFD) | EDID 128-byte block | Counts as a VRR range only if EDID revision >= 4, continuous-frequency flag (byte 24 bit 0) set, and descriptor flag = "range limits only" (0x01); +255 Hz offsets apply in 1.4 [V Linux `drm_edid.c`]. Otherwise it is a timing envelope. |
| DisplayID 2.x dynamic video timing range limits (tag 0x25) | EDID ext 0x70 | min/max V Hz plus "seamless dynamic video timing change" bit [V edid-decode] |
| AMD FreeSync vendor block (OUI 00-00-1A) | CTA, or CTA inside DisplayID (tag 0x81) | version, min Hz, max Hz, flags; layout is reverse-engineered by edid-decode, which says any 0xE6 flag bit means the range is switched over DDC/CI MCCS [V]; amdgpu does read a FreeSync VCP code over DDC/CI for such sinks [V] |
| HDMI Forum VSDB/SCDB (OUI C4-5D-D8, or ext tag 0x79) | CTA | VRRmin = byte5 & 0x3F, VRRmax = (byte5 & 0xC0) << 2 or byte6 [V edid-decode]; absent when the block is short (HDMI 2.0 sinks) |
| DisplayID 2.1 Adaptive-Sync block | DisplayID | not decoded by the edid-decode snapshot I read; probe records unknown tags only |

### 2.3 Schema sketch (`panorama.room/1`)

```jsonc
{
  "schema": "panorama.room/1",
  "units": "mm, deg, Hz as {num,den}. Frame = seat: right-handed, +x right, +y up, +z away from the eye toward the screens, origin = eye point",
  "seat": { "gaze_yaw_deg": 0, "gaze_pitch_deg": 0, "source": "aligned | default (700 mm in front of the anchor monitor)" },
  "monitors": [{
    "id": "m1",
    "identity": {                                  // detected at launch, saved for matching
      "stable_id": "ZZY21500000000000001_23_07E9_9C",
      "edid_sha256": "68f9...", "edid_id": "ZZY2150", "name": "EGM22F100H", "serial_is_placeholder": true,
      "last_seen_on": { "tech": "HDMI", "gpu": "VEN_10DE&DEV_2F18", "target_id": 4352, "desktop_order": 1 }
    },
    "panel": {                                     // detected, sanity-checked; size may be overridden by alignment
      "native_px": [1920, 1080], "active_area_mm": [480, 270],
      "size_source": "dtd + square-pixel rule", "size_flags": ["dtd_size_is_whole_cm", "non_square_pixels_gt_1.5pct"],
      "curvature_radius_mm": null
    },
    "pose": {                                      // aligned: the physical truth
      "centre_mm": [380, -40, 800], "yaw_deg": -12.0, "pitch_deg": 0.0, "roll_deg": 0.0,
      "bezel_mm": { "left": 7, "right": 7, "top": 9, "bottom": 14 }, "source": "aligned"
    },
    "timing": {
      "modes_builtin": [ { "px": [1920,1080], "rate": [100,1], "pixel_clock_hz": 223000000, "totals": [2000,1115] } ],
      "modes_custom_verified": [ { "px": [1920,1080], "rate": [96,1], "made_by": "CRU", "round_trip_ok": true, "measured_hz": 95.9998 } ],
      "link": { "tech": "HDMI", "max_tmds_mhz": null, "frl_code": 0 },
      "vrr": { "declared_hz": [48,100], "declared_by": ["amd_freesync_vsdb"], "usable_on_link": false, "enabled": null, "verified_by": null },
      "latency_ms": { "value": null, "relative_to": "m1", "method": null }
    },
    "color": {
      "edid": { "primaries_xy": { "r":[0.6396,0.3301], "g":[0.2998,0.5996], "b":[0.1504,0.0596] }, "white_xy": [0.3125,0.3291], "gamma": 2.2, "nits": { "max": 400, "min": 0.0039 } },
      "windows": { "acm": false, "hdr": false, "sdr_white_nits": 80 },   // re-read at launch; a change invalidates `correction`
      "correction": { "gain_rgb": [1,1,1], "gamma": 2.2, "black_lift": 0.0, "source": "by_eye" }
    }
  }],
  "derived": { "seams": [ { "a": "m1", "b": "m2", "gap_mm": 18.2, "stagger_mm": 3.1, "hinge_deg": 12.0 } ] }   // recomputed, never edited
}
```

Rules: any number of monitors, any rotation (portrait = roll 90); bezels are the empty space between panel rectangles, so ADR 0014 holds (the planner never subtracts a bezel). A per-edge `bezel_mm` larger than the aligned gap allows is flagged as an inconsistency. Rates are stored as pixel clock plus totals plus a rational; the true rate is measured at runtime (the spike measured 239.9993 and 99.9997 Hz against nominal 240.001 and 100).

**Precedents (structure borrowed, nothing depended on):**

| Precedent | What to borrow | Evidence |
|---|---|---|
| Unreal nDisplay (UE 5.8 config reference) | Separate objects: `screen` (physical rectangle: `loc` of the centre, `rot` pitch/yaw/roll in degrees, `size` in metres, pivot at the midpoint, parent transforms), `viewport` (pixel rectangle in a window), `camera` (the eye, `eye_dist`), `scene_node` (parents). Metres and degrees for space, pixels for screen space; right-handed, Z-up. | [V] |
| VESA MPCDI (2013, v2.0 2015) | Idea of keeping the screen description, the design eye and the warp separate; per-region files for warp and blend; XML header with profile/level. Exact element names not read. | [S] |
| Sim-racing triple screens | Per-monitor toe-in angle from the monitor's visual angle; bezel compensation adds border + gap to the angle budget; FOV-axis confusion between sims. | [V] 10-10 bezels report |
| GPU-driver bezel correction (NVIDIA Surround, AMD Eyefinity) | One scalar per seam in pixels: a bezel-corrected resolution that renders extra virtual pixels behind the bezels, widened until lines align. Too coarse for mixed sizes and angles; our poses replace it. | [S] NVIDIA help page, WSGF |

### 2.4 Identity matching: saved room vs current desk

| Key | Survives reboot | Port change | Driver update / GPU route | Verdict |
|---|---|---|---|---|
| `StableMonitorId` | yes | yes [V doc] | yes | **primary** |
| EDID base-block SHA-256 | yes | yes | yes (unless CRU override) | primary check, used if WinRT is unavailable |
| Serial text/u32 | yes | yes | yes | only if not a placeholder |
| Container id | yes | yes | yes | secondary; identical for duplicates (Windows derives it from the same EDID [K]) |
| Target id (`UID4352`) | yes for the same GPU [V doc] | no | may change | tie-break only |
| PnP device path | no: the same laptop panel has `1&8713bca&0&UID0`, `5&22923448&0&UID512`, `5&2bc6f227&1&UID4353` [L] | no | no | never |
| Adapter LUID | no (valid until reboot [K]) | n/a | no | session handle only |
| `\\.\DISPLAYn` | renumbers | renumbers | renumbers | never |

Algorithm: (1) list current monitors with the keys above. (2) Match each saved monitor by `stable_id`; unique hits are accepted. (3) For duplicates (same id twice, or placeholder serial): match by (GPU, target id), then by left-to-right desktop order, else show a big number on each screen and ask once. (4) Saved monitor missing: room is incomplete. New monitor: mark unaligned and run alignment for it only. (5) The room is valid when all saved monitors match; the Windows arrangement may differ (it is only a starting guess for alignment), because the room is physical. (6) Keep a `room_signature` (sorted ids) to pick among several saved rooms.

Caveat: Microsoft's page says "see Remarks for exceptions" to the id's stability; the digest I read did not show them. Likely cases: monitors without a unique EDID. [open question 6]

## 3. Setup A probe summary, Setup B facts

### 3.1 Setup A (probe run, ~1 s, no errors; script `spikes/room-probe/room-probe.py`)

| | Laptop panel | External |
|---|---|---|
| Panel / link / GPU | AUO7DB2 (model text B160QAN02.3), eDP, RTX 5070 Ti Laptop | ZZY2150 "EGM22F100H", HDMI, same GPU |
| Stable id | `AUO7DB20_0A_07E8_F5` (matches WinRT) | `ZZY21500000000000001_23_07E9_9C` (matches WinRT) |
| Serial | none (u32 = 0) | placeholder `0000000000001`, u32 = 1 |
| Desktop rect | 0,0 2560x1600, primary, 150% DPI | 2560,0 1920x1080, 100% |
| Exact timing now | 1,175,045,000 Hz / (2720 x 1800); CCD 240001/1000 (true 240.00102) | 223,000,000 / (2000 x 1115) = 100/1 |
| DXGI modes at native | 240.001, 60/1 | 100/1, 75.002, 60/1, 59.94 (60000/1001), 50/1 |
| EDID-declared | DisplayID Type VII 1,175,045 kHz = what Windows runs; first DTD 60 Hz | DTDs 60, 75.0017, 100 Hz; VICs 1080p60/50, 720p60/50, 480p, 576p |
| VRR declared | 60-240 Hz: DisplayID seamless flag + AMD block v3 + EDID 1.4 range (valid by the Linux rule) | 48-100 Hz: AMD block v1.1 only (flags 0xE6 = MCCS); EDID 1.3 range limits not valid as VRR |
| VRR usable | eDP: yes in principle; enabled: unknown | HDMI + AMD-only block on an NVIDIA GPU: no [S] |
| Size | DTD 344 x 215 mm (base block 340 x 220); square pixels; 15.97" | 480 x 260 mm, whole cm, 3.7% non-square; square-pixel rule 480 x 270, 21.7" |
| Colour (EDID) | wide gamut R .679/.316 G .258/.693 B .147/.052; 507.6 nits; SDR gamma only | sRGB-exact primaries; HDR10 PQ declared, 400 nits max, 343.7 avg, 0.0039 min |
| Windows colour state | ACM on (WCG), 8 bpc, SDR white 80 nits | SDR, HDR supported but off |
| Registry fallback mode = current | yes (240 Hz) | yes (100 Hz) |
| System-wide | DXGI tearing allowed = true; EDID override present: no | |

Corrections to the 10-10 report: `PhysicalSizeInInches` is a width x height size (13.54 x 8.46 in for the laptop = 344 x 215 mm), not a diagonal [L]. The pacing report's "the laptop DisplayID has a seamless flag" is confirmed [L].

### 3.2 Setup B: what the registry says (parsed all 5 EDIDs, current and past)

Registry history on this PC: the laptop panel (3 instances), the EGM22F100H (first read 2026-02-01), and one more monitor:

| Fact | Value |
|---|---|
| Model | LG UltraFine, EDID id GSM5CBB, name "LG ULTRAFINE"; EDID 1.3; dated 2025 week 1; real serial text (so a usable key) |
| Size | 600 x 340 mm in EDID (27.1"); 27-inch 4K class; 0.74% skew (cm rounding) |
| Native | 3840x2160 @ 60 Hz (594 MHz, CTA VIC 97). Also DTDs: 4K30, 2560x1440 @ 59.95, 1920x1080 @ 59.93 |
| Declared video formats | VIC 93-97 = 4K 24/25/30/50/60; 1080p 24/30/50/60; 720p60; SD |
| Range limits | 40-60 Hz, 30-135 kHz, <= 600 MHz; EDID 1.3 + default GTF flag = a timing envelope, not VRR |
| Link | HDMI: LLC block 300 MHz; HDMI Forum block v1: 600 MHz TMDS, SCDC, no FRL, no VRR fields = plain HDMI 2.0 |
| VRR | none declared |
| HDR / gamut | HDR10 (PQ) declared, 400 nits max and avg, 0.39 min; BT.2020 colorimetry; nominal primaries R .684/.314 G .280/.664 B .150/.060 (DCI-P3-class) |
| Windows | `AutoColorManagementSupported=1`, no enabled flag; DPI value 0 (default) |
| Seen | first EDID read 2026-02-04 04:33 UTC; last enumerated 2026-02-16 15:29 UTC; on this laptop's HDMI connector (target 4352), the same one the EGM22F100H uses; a vendor interface `LGMonitorApp` is registered under it |

Timing options it implies [D]: built-in 4K24 exists (VIC 93), so 24p content has an exact built-in rate on this screen at 4K; 48 Hz sits inside the declared envelope but is unverified; 25 fps maps to 50 Hz (VIC 96); no refresh above 60.

**The other two Setup B screens are not in the registry.** `GraphicsDrivers\Configuration` and `Connectivity` would show past multi-monitor layouts (positions, rotation, rates) but need elevation (access denied here). The probe tries them when run elevated and dumps them raw (untested path).

**Only the user can tell:** the other two models and refresh rates (or run the probe on Setup B, elevated once); each screen's physical size if the EDID looks wrong; orientation (portrait or landscape; Windows rotation only shows what is configured); gaps, bezel widths, vertical stagger, depth, toe-in, curvature; which physical port each cable uses (Windows reports only the connector kind); the seat position; whether two of the screens are identical models (decides how matching works).

## 4. Display settings

### 4.1 Semantics found (Windows 11 25H2, build 26200)

| Call | Microsoft docs [V] | Measured here [L] |
|---|---|---|
| `ChangeDisplaySettingsEx`, flags 0 | "changed dynamically". NULL devmode + flags 0 = "the easiest way to return to the default mode after a dynamic mode change" (values in the registry are used). Page updated 2024-11-20. | Survived `TerminateProcess` of the caller; saved mode untouched; NULL/0 reset restored 100 Hz |
| `ChangeDisplaySettingsEx`, `CDS_FULLSCREEN` | "temporary in nature. If you change to and from another desktop, this mode will not be reset." Chen (2008): a temporary change is made by a program that went fullscreen and undoes it when it lets go of the screen; Windows and Explorer do not move windows or icons for it. | **Reverted by itself**, seen at the first read 0.45 s after `TerminateProcess` |
| `SetDisplayConfig` without `SDC_SAVE_TO_DATABASE` | Scenario table: apply + use-supplied = "temporary display configuration (not saved)"; `SDC_APPLY + SDC_USE_DATABASE_CURRENT` = "return from a temporary mode to the last saved display configuration". Fails with `ERROR_ACCESS_DENIED` for a caller without access to the console session or desktop. Page updated 2024-02-22. | Survived `TerminateProcess`; saved mode untouched; NULL/0 reset also undid it |
| `CDS_UPDATEREGISTRY`, `SDC_SAVE_TO_DATABASE` | persist | never used |
| DXGI exclusive fullscreen mode switch | `ALLOW_MODE_SWITCH` lets the swap chain change the mode; the swap chain leaves fullscreen when occluded or when an in-process watchdog thread sees the app stop presenting (`HKCU\Software\Microsoft\DXGI\DisableFullscreenWatchdog`); do not release a swap chain while fullscreen. Page updated 2025-09-11. Silent on process death. | not tested (fullscreen windows not allowed); kernel cleanup on process death is [K] |
| On desktop switch, logoff, sleep, hot-plug | Only the `CDS_FULLSCREEN` sentence above (implies flags 0 can be reset by a desktop switch). Nothing else found. | **not tested** |

Also: DEVMODE carries an integer refresh. Kodi works around Windows 8+ picking the wrong 24/48/60 Hz variant by temporarily writing the registry (4.3) and rebuilds fractional rates from integers [V]; exact rationals (59.94, 96.000) need CCD `refreshRate` or a custom mode. My trials used integer 60 and CCD read back 60/1.

### 4.2 The kill test (exact steps, `spikes/display-restore/display-restore-kill-test.py`)

**Hold notice:** the coordinator asked me to hold this test while the user calibrates by eye. It had already run, once, 13:24-13:25 (+0700), before that message arrived. No refresh rate has been switched since, and none will be until released: the counter file (`state.json`, 3 of 3) blocks a rerun, and the 4.5 check was not run. Every later command was read-only (the probe).

Rules kept: only the monitor whose EDID name is EGM22F100H (aborts otherwise); built-in 100 -> 60 Hz at 1920x1080; no `CDS_UPDATEREGISTRY`, no `SDC_SAVE_TO_DATABASE`; 3 kill trials, counter file blocks a fourth; original state written to disk before any change; always restored with read-back.

1. Preflight: find the monitor via CCD target name; require HDMI, 1920x1080, current and saved mode both 100 Hz, 60 Hz offered; validate every change without applying (`CDS_TEST` = 0x2 for flags 0 and for `CDS_FULLSCREEN`; `SDC_VALIDATE`; all returned 0). Dry run changed nothing.
2. Per trial: spawn a child process with one small ordinary window on the laptop panel (as a player has); the child applies the change (flags 0, `CDS_FULLSCREEN`, or `SetDisplayConfig` apply + use-supplied + allow-changes, target refresh 60/1 with Windows choosing the timing) and prints `APPLIED rc=0`.
3. Wait 3 s, read back (GDI current, CCD refresh, saved mode), check the laptop panel is unchanged.
4. `Popen.kill()` (= `TerminateProcess`, exit code 1), then poll every 0.25 s for 10 s.
5. Restore with `ChangeDisplaySettingsEx(dev, NULL, NULL, 0, NULL)` if still 60 Hz; read back; check the other display.

| Trial | After apply | Saved mode | After kill | Restore |
|---|---|---|---|---|
| 1 `ChangeDisplaySettingsEx` flags 0 | 60 Hz (CCD 60/1) | 100 | stayed 60 Hz, 10 s | NULL/0 reset: 100 Hz (API ~0.3 s plus HDMI resync) |
| 2 `CDS_FULLSCREEN` | 60 Hz | 100 | **100 Hz at the first read, 0.45 s** | none needed |
| 3 `SetDisplayConfig` no save (Windows chose the timing: "auto" variant validated) | 60 Hz | 100 | stayed 60 Hz, 10 s | NULL/0 reset: 100 Hz |

End state: external 100 Hz, saved mode 100 Hz, laptop panel 240.001 Hz at the same position, no stray processes (checked). Results: `spikes/display-restore/results-2026-10-11/kill-test.json`, `original-state.json`, `state.json`.

Limits: n = 1 per method; child had a window (a windowless child could behave differently for `CDS_FULLSCREEN`; the windowed child is the best case for an OS revert, so "flags 0 and SDC do not revert" is the robust finding); normal exit of the caller was not tested separately; 0.45 s is an upper bound because the first poll came after `Popen.wait`.

### 4.3 How real players do it (checked 2026-10-11)

| Player | Mechanism | Restore | Evidence | Status |
|---|---|---|---|---|
| MPC-BE | `CDS_FULLSCREEN` when "restore resolution after exit" is on; otherwise writes the new mode to the registry (`CDS_UPDATEREGISTRY + CDS_NORESET`) | at shutdown `SetDispMode(launch mode, forceRegistry)` | [V] `MainFrm.cpp`; repo commit 2026-10-10 | maintained |
| Kodi | `CDS_FULLSCREEN`; for 24/48/60 Hz a "Windows 8 workaround": write the mode into the registry unapplied, apply with a NULL devmode + `CDS_FULLSCREEN`, write the old values back | `RestoreDesktopResolution` when leaving fullscreen; crash relies on the OS revert | [V] `WinSystemWin32.cpp`; commit 2026-10-10 | maintained |
| madVR 0.92.17 | switch on fullscreen or playback start | "restore original mode when leaving fullscreen"; tracker #648 (opened 2020-07-13, last update 2022-07-01, open): "refresh rate doesn't change back", sometimes restored only when the player closes | [V] bug tracker | **legacy**: unresolved, no fix found |
| mpv scripts | `mpv-changerefresh` (52 stars) calls nircmd; autospeedwin similar | reverts "when mpv exits" or on a hotkey; no crash handling stated; warns the saved rate may be wrong when the window spans displays | [V] README, [S] | community scale |
| JRiver MC | "Enable Automatic Display Settings changing" | revert behaviour not documented in what I found | [S] wiki | unknown |
| Plex HTPC | | | not checked | |

Pattern: nobody ships an out-of-process watchdog; the maintained players rely on the OS temporary-mode revert plus an explicit restore on clean exit. The Kodi workaround window (registry briefly holds the wrong mode) is a persistence risk if the process dies inside it: avoid copying it.

### 4.4 Recommended design (ADR 0005 consequence)

Invariant: after any way the player can stop, every monitor is in the mode the user had, and the player never leaves a mode the user did not choose.

| Lever | Verdict |
|---|---|
| Temporary by construction: `CDS_FULLSCREEN` called by the **long-lived** process (player or its launcher; the OS reverts when that process dies) | **Use for every change it can express** (built-in integer-Hz modes, custom modes once they exist in the list). The caller must outlive playback: a short-lived helper that sets and exits gets its change reverted. |
| Never persist (no `CDS_UPDATEREGISTRY`, no save) | **Always.** Reboot and logoff then start from the user's mode, and a crash cannot leave a persisted one. |
| Dirty snapshot on disk before the first change: owner PID, per-device current and saved mode, CCD paths, state `dirty` (atomic write) | **Yes.** Cheap; covers every failure the other levers miss. |
| Guardian process: tiny, detached from the player's job object, waits on the player's process handle, then resets with `ChangeDisplaySettingsEx(dev, NULL, NULL, 0, NULL)` per changed device (tested) or `SDC_APPLY + SDC_USE_DATABASE_CURRENT` (documented), verifies by read-back, clears the snapshot | **Only for changes that survive process death** (SDC rational rates, primary-display change, rotation, HDR/ACM toggles). It must run in the interactive session: `SetDisplayConfig` fails with `ERROR_ACCESS_DENIED` otherwise, so not a service. |
| Recovery at next launch (and guardian start): snapshot dirty and no live owner -> compare and restore | **Yes.** Handles guardian death. |
| Job objects | Use for the player's own children (`KILL_ON_JOB_CLOSE`); keep the guardian out of that job. [K] |
| Scheduled task at logon | **Not needed:** non-persisted changes do not survive logon (SDC docs: "not saved"). Only useful if something ever persists. |
| Restart Manager | **Not useful:** it coordinates installers shutting applications down; it does not run on a crash. [K] |
| DXGI exclusive fullscreen per output | Option for the own renderer: process-bound and takes rational rates, but legacy against flip-model borderless; not evaluated. |

Rules: switch only at a break (start, seek, pause) as in the pacing report (a mode change blanks an HDMI monitor for about 3 s [L]); restore the *saved* mode, not a remembered value; touch only the field we changed (rate); verify by read-back and log. Untested: desktop switch (lock screen, UAC), sleep, logoff, hot-plug, primary-display change: treat as unknown, re-assert the mode on unlock for non-`CDS_FULLSCREEN` changes, and run the kill test again on each new Windows build.

### 4.5 Existing script: `spikes/mixed-rate-pacing/display-refresh.py`

`CDS_TEST, DISP_CHANGE_SUCCESSFUL = 0x4, 0` (line 20). Per the SDK header: `CDS_TEST` = 0x2, `CDS_FULLSCREEN` = 0x4 [V]. `set_refresh` loops over `(CDS_TEST, 0)`: the first call really applies a `CDS_FULLSCREEN` mode, the second asks for flags 0 with the same mode, which may be a no-op (`CDS_RESET` exists for "apply even if unchanged" [V doc]). `run-span-trial.py` and `watch-viewing-clip.py` call it as `subprocess.run([... "set" ...])` and play afterwards, so if the process-bound ownership stays, the mode returns when the setter exits (~3 s after the call). Not touched (out of scope); fix = `0x2`. Check, 1 minute: `python display-refresh.py set \\.\DISPLAY2 60`, wait 5 s, `python display-refresh.py list`, then `restore`. Needs the user's go-ahead (it is another display change).

## 5. Custom modes (96/72 Hz and similar)

| Route | How | Persistence and removal | Needs | Evidence |
|---|---|---|---|---|
| EDID override (CRU style), works on NVIDIA, AMD and Intel | writes a software EDID override into the registry; `restart64.exe` restarts the driver (F8 within 15 s = recovery mode that unloads overrides); delete + reboot, or `reset-all.exe` + reboot | persistent until deleted | admin (UAC) once; driver restart blanks all displays a few seconds | [V] CRU thread, post edited 2026-01-26, v1.5.3 |
| NVIDIA NVAPI | `NvAPI_DISP_TryCustomDisplay` (set without saving) -> test -> `NvAPI_DISP_SaveCustomDisplay`; `NvAPI_DISP_RevertCustomDisplayTrial`; `NvAPI_DISP_DeleteCustomDisplay`; save options "this output only" / "this monitor only"; Windows 10+ | trial is unsaved; saved ones live in the registry until deleted | NVAPI (MIT-licensed header, repo commit 2026-09-18); no admin documented | [V] `nvapi.h` |
| AMD ADLX | interface `IADLXDisplayCustomResolution`: `CreateNewResolution`, `DeleteResolution`, `GetResolutionList`, `GetCurrentAppliedResolution`, `IsSupported` | not documented in what I read | ADLX SDK | [V] method names only; older `ADL_Display_ModeTimingOverride*` [S] |
| Intel IGCL | `ctlGetSetCustomMode` add (single), remove (one or many), get; but the mode struct only has `SourceX`, `SourceY`: **no refresh or timing** | n/a | IGCL (header v298, repo commit 2026-08-18) | [V] `igcl_api.h` |
| Windows itself | no API to add a timing | | | [V] none found |

Do the modes need removing after playback? **No technical need.** An override or saved mode only adds an entry to the monitor's list; selecting it is the per-playback, reversible part. Removing and re-adding by EDID override costs a driver restart each time. So: create once during room setup (needs one elevation, a tension with ADR 0011's "simple setup": make it an opt-in step), verify, store under `modes_custom_verified`. Use NVAPI's unsaved trial to test a timing before saving it. If the same GPU/driver is later replaced the override may be ignored: re-verify at launch by looking for the mode in the DXGI list.

Risks: no picture (CRU: F8 within 15 s, or `reset-all.exe` in safe mode [V]; NVAPI: `RevertCustomDisplayTrial` [V]); NVIDIA can hang at boot with several displays and an override present, and ignores overrides when DSC is active above its single-head pixel clock [V CRU]; HDMI is treated as single-link DVI unless the override defines an HDMI data block (HDMI 2.0 needs both blocks) [V CRU]; low vertical blanking can block memory-clock reduction or corrupt the picture [V CRU]; panels may reject timings outside their EDID envelope (EGM22F100H: 48-100 Hz, <= 255 kHz; the 223 MHz DTD already exceeds its declared 200 MHz pixel-clock field, so that field is not a hard limit [L]); modes can differ from the exact multiple needed (95.904 vs 96.000, pacing report).

Numbers for the external [D]: keep the 100 Hz CVT-RB v2 totals (2000 x 1115). 96.000 Hz = 214.08 MHz (H 107.04 kHz); 72.000 Hz = 160.56 MHz. Verify each by CCD read-back (`pixelRate / totals`) and a 10 s present-log before storing it as verified.

## 6. Sources (fetched 2026-10-11 unless dated)

Microsoft:
- ChangeDisplaySettingsExW, page updated 2024-11-20: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-changedisplaysettingsexw
- SetDisplayConfig, updated 2024-02-22: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setdisplayconfig
- DXGI overview, updated 2025-09-11: https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/d3d10-graphics-programming-guide-dxgi
- DisplayTarget class (StableMonitorId, AdapterRelativeId), updated 2026-01-15: https://learn.microsoft.com/en-us/uwp/api/windows.devices.display.core.displaytarget
- Raymond Chen, "What does it mean when a display change is temporary?", 2008-01-04: https://devblogs.microsoft.com/oldnewthing/?p=23923
- Windows SDK 10.0.26100.0 headers on this PC: `wingdi.h` (CCD structs, `GET_ADVANCED_COLOR_INFO_2`, `QDC_VIRTUAL_REFRESH_RATE_AWARE`), `winuser.h` (CDS_*), `dxgi*.h` (vtable slots and IIDs used by the probe).

EDID / VRR (raw source reads):
- edid-decode `parse-cta-block.cpp`, `parse-displayid-block.cpp` (AOSP mirror, master): https://github.com/aosp-mirror-neo/platform_external_edid-decode (the canonical git.linuxtv.org sits behind a bot wall)
- Linux `drm_edid.c` (monitor range rule, HF-VSDB): https://github.com/torvalds/linux/blob/master/drivers/gpu/drm/drm_edid.c
- Linux amdgpu `amdgpu_dm_helpers.c` (MCCS FreeSync VCP): https://github.com/torvalds/linux/tree/master/drivers/gpu/drm/amd/display/amdgpu_dm
- NVIDIA/nvapi `nvapi.h` (custom display, `NvAPI_Disp_GetVRRInfo`, adaptive sync; repo commit 2026-09-18): https://github.com/NVIDIA/nvapi
- Intel `igcl_api.h` (v298, 2026-08-18): https://github.com/intel/drivers.gpu.control-library
- NVIDIA HDMI VRR announcement coverage, 2019 [S]: https://overclock3d.net/news/gpu-displays/nvidias-bringing-hdmi-2-1-vrr-support-to-its-rtx-20-series-gpus/

Players:
- MPC-BE `MainFrm.cpp` (commit 2026-10-10): https://github.com/Aleksoid1978/MPC-BE
- Kodi `WinSystemWin32.cpp` (commit 2026-10-10): https://github.com/xbmc/xbmc/blob/master/xbmc/windowing/windows/WinSystemWin32.cpp
- madVR bug 648 (2020-07-13, updated 2022-07-01): https://bugs.madshi.net/view.php?id=648
- mpv-changerefresh: https://github.com/CogentRedTester/mpv-changerefresh
- JRiver wiki "Video Setup" [S]: https://wiki.jriver.com/index.php/Video_Setup

Custom modes and precedents:
- CRU (monitortests.com, v1.5.3, first post edited 2026-01-26): https://www.monitortests.com/forum/Thread-Custom-Resolution-Utility-CRU
- AMD ADLX `IADLXDisplayCustomResolution`: https://gpuopen.com/manuals/adlx/adlx-sdk-references/adlx-interfaces/display/iadlxdisplaycustomresolution
- Unreal nDisplay configuration reference (UE 5.8): https://dev.epicgames.com/documentation/en-us/unreal-engine/ndisplay-configuration-file-reference-for-unreal-engine
- VESA MPCDI [S]: https://vesa.org/featured-articles/vesa-completes-specifications-for-new-multiple-projector-common-data-interchange-standard-mpcdi/
- NVIDIA Surround bezel correction help [S]: https://www.nvidia.com/content/Control-Panel-Help/vLatest/en-us/mergedProjects/nv3d/To_configure_my_displays_for_Surround.htm ; WSGF bezel correction [S]: https://www.wsgf.org/node/21290

Project: `plans/reports/researcher-261010-1846-setup-calibration-and-color-matching.md`, `...bezels-perception-and-geometry-model.md`, `research-261011-1000-frame-pacing-judder-and-seek-swap-concepts.md`, `spike-261011-1034-one-window-across-mixed-refresh-rates.md`, ADRs 0005, 0007, 0009, 0011, 0014.

Local evidence: `spikes/room-probe/room-probe.py` (run on Setup A; reusable on Setup B), `spikes/display-restore/display-restore-kill-test.py` and `results-2026-10-11/`.

Method notes: 23 WebSearch/WebFetch calls plus 12 raw-source reads (curl + grep, which avoids the page-digest model); about 10 of the searches returned snippets only and are tagged [S]. The edid-decode copy is an AOSP mirror whose snapshot date I could not read (repo last pushed 2026-06-01), so newer upstream blocks may be missing. Not covered: Plex HTPC, DSC and link bandwidth, multi-GPU setups, macOS, HDR toggle restore, DisplayID 2.1 Adaptive-Sync block layout, MPCDI element names.

## 7. Unresolved questions

1. Does `display-refresh.py set` (with the `CDS_TEST` typo) revert when its process exits? One-minute check above; decides whether the pending 96/72 Hz trials are valid.
2. Behaviour of flags 0, SDC and `CDS_FULLSCREEN` changes on lock/unlock, UAC, sleep, logoff, hot-plug: untested. Needs a user-attended test, not a kill test.
3. Does a windowless caller of `CDS_FULLSCREEN` revert on kill like the windowed child did, and does a normal exit revert it? (A launcher without a window is the likely product shape.)
4. Is VRR actually on for the laptop panel? NVIDIA Control Panel "Set up G-SYNC" (E1) or `NvAPI_Disp_GetVRRInfo`; I could not call NVAPI from stdlib Python (function ids are not in the public header).
5. Can `SetDisplayConfig` pick an exact rational (59.94, 96.000) by `refreshRate`, and does Windows keep the choice? Not tested (would be another display change).
6. What are the "exceptions" to `StableMonitorId`'s stability (the doc's Remarks)? Likely identical EDIDs; confirm with two identical monitors if Setup B has them.
7. Do custom modes appear in the DXGI list and survive a driver update, per vendor? Needs approval (ADR 0005 user step; E2).
8. Primary-display switching (the spike found the main display sets the pace of a spanning window): is it restorable with `SDC_USE_DATABASE_CURRENT`, and is it also reverted by an OS mechanism? Not tested (rules forbade changing the main display).
9. Setup B: the other two monitors, orientation, sizes, gaps, ports, seat; whether any two are identical models; whether Setup B runs from the same GPU. Run `python room-probe.py > room-report.json` on Setup B, once normally and once from an elevated shell.
10. DXGI exclusive fullscreen: what the OS does with the mode when the process dies is not stated on the page I read (only app-level exit, occlusion and the unresponsive-app watchdog). Probably the same temporary-mode machinery as `CDS_FULLSCREEN` [K]; untested, and only matters if the own renderer uses exclusive mode.
11. When the coordinator releases the hold: the 4.5 check (set via subprocess, wait, read, restore) is the one display change worth making next; it decides whether the 96/72 Hz trials are valid.
