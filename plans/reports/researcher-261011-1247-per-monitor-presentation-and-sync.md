---
title: "Research: per-monitor presentation and sync, what keeps every screen even and both sides of a bezel in step"
date: 2026-10-11
type: research
answers: "roadmap M1 question 'Pace' (phase 01); research-261011-1000 unresolved questions 1-2; coordinator addendum (IDXGISwapChainMedia, scanline/DWM timing, nearest-refresh scheduling)"
sources: "55 retrievals: 29 page fetches (9 of them returned a 404, a redirect or an empty page), 12 web searches, 14 file/API pulls into the scratchpad (NVAPI, IGCL, ADLX headers, PresentMon, the 2011 paper PDF, OpenAlex). Plus read-only probes of this PC (CCD, EDID, registry, DXGI, D3DKMT, DWM, Vulkan, NVAPI, swapchain support checks). About 25 over the ~30-call target: the addendum added scope, and I replaced search snippets with vendor headers and probes."
---

# Per-monitor presentation and sync (M1 Pace, phase 01)

Run 2026-10-11, Asia/Bangkok. Nothing on the PC was changed: no display mode, no setting, no fullscreen window. Probes were read-only (CCD, EDID, DXGI, `D3DKMTGetScanLine` reads, NVAPI getters, Vulkan extension lists, two tiny never-shown windows).

Evidence tags: **[V]** verified from a primary source or source code read this pass. **[S]** search snippet or community post only. **[K]** prior knowledge, not re-verified. **[L]** local probe on this PC. **[D]** my derivation or simulation.

## 0. Terms

- **Refresh**: one chance for a monitor to show a picture (240 Hz = 240 per second). **Pace**: refreshes per film frame (24 fps on 240 Hz = 10 each, even; on 100 Hz = 4,4,4,4,4,5, uneven).
- **Scanout**: a monitor draws its picture row by row, top to bottom, once per refresh. Example: the laptop takes 3.7 ms, the external 9.7 ms.
- **Vblank**: the short pause between the last row of one refresh and the first row of the next. New pictures switch in there. Laptop: 0.46 ms every 4.17 ms.
- **Present**: the app hands Windows a finished picture to show at an upcoming refresh.
- **DWM**: the Windows desktop compositor. It builds the desktop picture once per refresh of the *main display*.
- **Independent Flip**: Windows lets the app's picture go straight to the screen and skips the DWM. One fewer clock in the path.
- **Phase**: where a monitor is in its refresh cycle at a given instant. Two screens at the same rate can still be a few ms apart in phase.
- **Master and follower**: the master monitor's refreshes carry each film-frame change. Each follower switches on its own refresh nearest that moment.
- **Genlock / frame lock**: hardware that makes all screens start refreshes at the same instant (pro cards).
- **VRR**: variable refresh, the monitor waits for the next picture. **LFC**: when a VRR monitor is fed slower than its minimum rate, the driver repeats frames to stay in range.

## 1. Answer

1. **Pace is decided by each monitor's own refresh rate, so each picture screen needs a whole multiple of the playback rate.** At 24 fps that is 48/72/96/120/240 Hz. Setup A: laptop 240 Hz = 10 per frame, already even. The external at 100 Hz can never be even for 24p; a custom 96 Hz (4 per frame) can [D].
2. **One spanning window cannot control the seam. One Independent-Flip window per monitor can.** Windows has one composition clock, the main display's. `DwmGetCompositionTimingInfo` only accepts NULL and reports 239.998 Hz here [V doc, L]. The other screen shows the newest composed picture at its own refresh, so the cross-bezel offset is a constant anywhere from 0 to one refresh of that screen (up to 10.4 ms at 96 Hz) [D]. With one window per monitor the app chooses which refresh shows each film frame. No document says two Independent-Flip windows can coexist on two monitors. The sibling spike (`spike-261011-1247-one-window-per-monitor.md`, 13:27, not my run) saw both windows report the hardware "overlay" path at the same time on this PC, 20 s per mode, read through `GetFrameStatisticsMedia`; there is no PresentMon cross-check yet [L, sibling]. Hardware-composition flags here: laptop windowed+fullscreen, external fullscreen only [L].
3. **Both heads already run on locked clocks. Only the phase is arbitrary, and software can aim at it.** Measured over 30 s: the 240 Hz and 100 Hz heads hold a 12:5 ratio to 0.5 ppm; their relative phase drifts 0.5 µs/s, one laptop period per ~2.3 hours; five fixed phase classes sit 0.83 ms apart; vblank waits wake 72-90 µs after vblank start with sd 30 µs [L]. So: slowest monitor = master; every faster monitor switches on its own refresh nearest the master's. Offset is at most half the faster period (±2.08 ms at 240 Hz) [D, simulated over 2 h]. Your derivation holds, with one condition: the follower's pace stays even only if its rate is a whole multiple of the film rate. At 0.5 ppm mismatch one frame in ~2 h lasts 9 refreshes instead of 10; hide that at a cut [D]. The sibling spike ran the scheme on hardware at 20 fps (both native rates even): a steady 0.28 ms offset in that run (luck of the phase; the guarantee is ±2.1 ms) against −1.7..+6.7 ms for the spanning window [L, sibling].
4. **Scanout skew, not refresh, dominates for slow panels.** Scan times: laptop 3.70 ms; external 9.69 ms at 100 Hz, 10.09 ms at 96 Hz; LG 4K 16.0 ms at 60 Hz, 20.0 ms at 48 Hz [L CCD+EDID, D]. The frame-change offset slides 6.0-6.4 ms from top to bottom on Setup A and 12-16 ms when the LG is involved. Levers: aim the schedule at the seam-crossing row (mid-height: ±3.2 ms at top and bottom on Setup A), matched timings. VRR does not slow the scan.
5. **Perception: the only study tested 8.3 ms and up, so 2-4 ms is untested.** Deshpande & Daly 2011 (20 viewers, 120 Hz simulation): with a 0.54° gap, two tiles scored 4.6-4.9 of 5 at 8.3 ms and 3.3-4.6 at 25 ms. With no gap, 3.8-4.5 at 8.3 ms. Tolerance rose with gap width and with speed [V PDF, figure values read by eye]. Our gap is wider (about 1.4-1.9° at 20 mm and 0.6-0.8 m, assumed) [D]. Your seam-sim clip (offsets 0, 4, 8, 13, 17 ms): the larger ones "flicker crazy", no threshold named [spike report]. This is an input to an anchored by-eye ladder, not a verdict.
6. **Only two APIs take a target time per monitor, and this PC has both.** `IPresentationManager::SetTargetTime` (Windows build 22000+; works on the NVIDIA and AMD adapters here) [V, L]. `VK_EXT_present_timing` (ratified Nov 2025; NVIDIA 617.42 on Windows exposes it with all three feature bits; the AMD 610M iGPU does not) [S, V, L]. DXGI has no target time; build the schedule from per-monitor vblank waits (`D3DKMTWaitForVerticalBlankEvent` and `IDXGIOutput::WaitForVBlank` both work) and read each head's phase with `D3DKMTGetScanLine` (user mode, no privilege, ±1 µs after a line-number fit) [L].
7. **Several tempting levers do not pan out here.** (i) `IDXGISwapChainMedia` present durations: the API answers, but the driver reports (0,0) for every rate on both screens [V doc says integrated panels only; L]. The docs tie its `CompositionMode` to decode swapchains (YUV-to-RGB), but the sibling spike read it on plain flip swapchains and got OVERLAY while both windows covered their monitors, so it works as an indicative, PresentMon-free check on this PC [V doc, L sibling]. (ii) VRR: NVAPI reports VRR not possible and not enabled on either screen right now [L], so a VRR follower is unproven until the NVIDIA Control Panel is checked. The external cannot (FreeSync-over-HDMI is not supported by NVIDIA per community sources [S]; NVAPI reads 0 too, weak evidence because the laptop reads 0 as well [L]). (iii) GeForce Surround/Mosaic: one resolution and refresh for the whole group, bezel correction only. Frame lock and genlock are Quadro Sync hardware [V]. Pro systems (nDisplay, WATCHOUT, disguise) use hardware sync and avoid mixed rates [V, S].
8. **An app can add a custom refresh mode.** NVAPI has `TryCustomDisplay`, `RevertCustomDisplayTrial`, `SaveCustomDisplay`, `DeleteCustomDisplay`, `EnumCustomDisplay` (public header dated 2026-09-02) [V]. On the external's own 2000×1115 totals, 96.000 Hz is exactly 214.08 MHz, a multiple of NVAPI's 10 kHz step [D]. AMD ADLX 2.0 (2026-09-20) has `CreateNewResolution` [V]. Intel's `ctlGetSetCustomMode` is resolution-only [V]. It needs your approval and an out-of-process watchdog (ADR 0005).

**Recommendation.** Product: one process, one Independent-Flip window per monitor, each at a multiple of the playback rate, slowest monitor as master, nearest-refresh scheduling plus a seam-row shift. Demo bridge: stock mpv spanning window with a custom 96 Hz on the external. VRR follower only if the by-eye test shows a visible leftover offset. Spike order: (c) custom 96 Hz next; then the remaining half of (a), which is the sibling presenter rerun at 24 fps on 240+96 plus the PresentMon cross-check, a 10-minute drift run and the knock-out matrix; (b) only if G-SYNC can be switched on and the by-eye ladder asks for it.

## 2. Invariant and lever map

**Invariant.** On every screen carrying the picture: (1) each film frame stays up the same number of refreshes; (2) each film-frame change reaches all screens at the same moment, judged at the rows where motion crosses the seam. Every lever below bends one of: the refresh rate, the refresh phase, the scan speed, or which refresh shows which frame.

| # | Lever | Bends | Setup A (laptop 240 + external 100) | Setup B (3 screens, LG 4K 60) | Evidence |
|---|---|---|---|---|---|
| 1 | Built-in multiples | rate | laptop 240 even; external 50/59/60/75/100 none even for 24p | LG 60 not even | [L] modes (prior report), [D] |
| 2 | Custom modes (NVAPI, ADLX) | rate, scan speed | external 96 (also 72, 48), exact 96.000 | LG 48 exact (475.2 MHz); other screens unknown | [V] headers, [D], [L] EDID ranges |
| 3 | Film speed nudge (`display-resample`) | film rate to master | in use, +0.1% | same | prior [V] mpv docs |
| 4 | Pan mode, blending | hides pace error | fallback, ADR 0016 | fallback | prior reports |
| 5 | One Independent-Flip window per monitor | removes the DWM clock; per-monitor present control | works: both windows in the hardware path at once (sibling spike, 20 s runs) | untested | [V] MS Learn, [L] flags, [L] sibling |
| 6 | Slowest-master + nearest-refresh | phase, ±T_follower/2 | external 96 master, laptop ±2.08 ms | LG 48 master; choose master per seam | [D] simulated |
| 7 | Seam-row time shift | phase vs scan skew | ±3.2 ms at top/bottom if mid-row aligned | ±8.2 ms (LG 48 vs laptop) | [D] |
| 8 | VRR follower (present at the master's vblank) | phase to sub-ms | NVAPI says not possible now | LG, others: none known | [L] |
| 9 | Re-roll phase at modeset (measure, repeat modeset until good) | phase | unverified | unverified | [D] |
| 10 | Locked heads (shared GPU reference) | phase stability, free | measured 0.5 µs/s | likely same-GPU only | [L] |
| 11 | Hardware genlock / frame lock | phase to ~µs | not on GeForce | not on GeForce | [V] NVIDIA, nDisplay |
| 12 | Surround / Mosaic | one timing for the group | rejected | rejected | [V] NVAPI, CPL help |
| 13 | Vendor clock tricks (Intel IGCL genlock, Intel SW Genlock) | phase | Intel only | Intel only | [V] header, [S] abstract |
| 14 | Present-duration request (`IDXGISwapChainMedia`, `SetPreferredPresentDuration`) | refresh follows film, integrated panels | (0,0) on both screens | n/a | [V] docs, [L] |
| 15 | Match scan speeds (fast panel on a slow timing) | scan skew | laptop only has 60/240; 60 Hz loses 24p pace | n/a | [D] |
| 16 | Software rolling cut (stagger the change by rows to mimic the slow scan) | scan skew | rejected: puts a tear line on the fast screen | rejected | [D] |
| 17 | Planner: slip events and offset changes only at cuts | hides rare slips | yes | yes | [D], ADR 0014 |

## 3. Findings

### Q1. Per-monitor presentation from one process

**1.1 Independent Flip and MPO on several monitors**

- Rule [V, MS Learn "For best performance, use DXGI flip model", updated 2026-04-15]: DirectFlip when the swapchain buffers match the screen and the window's client area covers it; a panel-fitter variant when buffers are within a hardware scale range; an MPO variant with a hardware overlay plane. Once flipped, the DWM sleeps. That is Independent Flip. Present statistics work in windowed mode. The waitable object gives one frame of latency in Independent Flip.
- Multi-monitor: **no document states it either way.** One targeted search (2026-10-11) found nothing. Per-output design hints: `CheckHardwareCompositionSupport` is per output [V doc], and PresentMon logs `VidPnSourceId` and `LayerIndex` per present [V]. Evidence on this PC comes from the sibling spike, not from me: one process, one borderless top-most window per monitor, exact fit, `FLIP_DISCARD`, 2 buffers, waitable object; both windows reported OVERLAY in nearly every poll, in every mode, at the same time (e.g. 4869 + 2027 polls overlay, 3 + 6 "none" at start-up) [L, sibling]. Still open: a PresentMon cross-check that OVERLAY means "Hardware: Independent Flip" or "Hardware Composed: Independent Flip", the knock-out matrix, long runs, and the real renderer (decode, libplacebo, subtitles).
- This PC [L]: laptop flags = FULLSCREEN + WINDOWED (windowed MPO possible). External flags = FULLSCREEN + CURSOR_STRETCHED (a panel fitter exists; no windowed overlay). So an exact-fit window on the external can DirectFlip but never get MPO. `DXGI_FEATURE_PRESENT_ALLOW_TEARING` is supported.
- The process must be per-monitor-V2 DPI aware. The laptop runs at 150% scaling: an unaware process sees 1707×1067 [L]. Buffers must be native (2560×1600).
- What knocks it out [V]: window not covering the screen, buffer size outside the panel-fitter range, something on top that cannot be reverse-composed or overlaid. [K, untested]: topmost overlays and toasts, Desktop Duplication or Graphics Capture running, non-flip swapchains, HDR/SDR mismatch. Do not use Desktop Duplication as the measuring tool while testing this.
- How to verify:
  - PresentMon **2.6.0** (2026-09-21; 2.5.1 2026-06-29; 2.4.1 2026-01-16) [V GitHub API]. `PresentMode` values: "Hardware: Independent Flip", "Hardware Composed: Independent Flip" (overlay plane), "Composed: Flip" (DWM), legacy ones [V README]. `--write_display_metadata` adds `VidPnSourceId`, `LayerIndex`, `PresentId`. It needs "Performance Log Users" membership or admin [V].
  - In process: composition-swapchain statistics `PresentStatisticsKind_IndependentFlipFrame` with `GetDisplayedTime` and `GetPresentDuration` [V doc, V SDK 10.0.26100 header]. NVIDIA only: `NvAPI_D3D_GetSleepStatus.bFullscreenIFlip` [V header]. Per-display flip token, time and refresh count: `NvAPI_DISP_GetAdaptiveSyncData`; both displays return live values here [V header, L].
  - `IDXGISwapChainMedia::GetFrameStatisticsMedia().CompositionMode`: the docs define it for decode swapchains (`COMPOSED` = the DWM does YUV-to-RGB once per output refresh, `OVERLAY` = hardware does it once per video frame) [V]. On plain RGB flip swapchains the sibling spike got OVERLAY while the windows ran in the hardware path and COMPOSED in a tearing case, so it is a usable indicator here but undocumented for this use [L sibling]. Treat it as indicative until PresentMon agrees.

**1.2 Which API can schedule a frame for a target time on a specific monitor**

| API | Target time | Per monitor | Status on this PC | Notes |
|---|---|---|---|---|
| DXGI flip: `Present(SyncInterval)` + waitable object | No. MS says DXGI has no PresentAt [V]. Holds N vblanks. | per swapchain | works | SyncInterval is 1-4 only [K]; at 240 Hz repeat presents. Build the schedule yourself from vblank waits. |
| `IDXGIOutput::WaitForVBlank`, `D3DKMTWaitForVerticalBlankEvent` | clock source, not a scheduler | per output | both work; wake = vblank start + 72-90 µs, sd 27-31 µs, p99 90-135 µs [L] | `hDevice=0` works for the D3DKMT call [L] |
| `D3DKMTGetScanLine` | read-only phase | per VidPnSource | works unprivileged; call 17.5 µs p50 [L] | line 0..vtotal-1 plus a vblank flag; MS Learn page 404, header semantics only |
| `IDXGISwapChain::GetFrameStatistics` | after the fact | per swapchain | [K] | available in windowed mode [V] |
| Composition swapchain (`presentation.h`) | **Yes**: `SetTargetTime(SystemInterruptTime)`, `SetPreferredPresentDuration` | per presentation manager (a DComp-hosted window) | supported on NVIDIA and AMD, incl. independent flip [L] | Windows build 22000+; docs dated 2021, updated 2024-02; no public samples found [V, S]. Device needs `D3D11_CREATE_DEVICE_PREVENT_INTERNAL_THREADING_OPTIMIZATIONS` [S]. |
| Vulkan `VK_KHR_present_wait`, `present_id` | wait for a present to land, no target | per swapchain | NVIDIA yes (present_wait feature = 1); AMD 610M no [L] | |
| **`VK_EXT_present_timing`** | **Yes**: absolute or relative target time, real present times back, refresh duration, FRR/VRR flag | per swapchain | NVIDIA 617.42: spec rev 3, features presentTiming / AtAbsoluteTime / AtRelativeTime all = 1; AMD 610M: no [L]. Spec status Ratified [V], in Vulkan 1.4.335 (Nov 2025) [S]. | Young: no field reports on Windows found. |
| NVAPI `GetAdaptiveSyncData` | feedback only (last flip time, refresh count, token) | per display | live on both [L] | NVIDIA only; time units unknown |
| `IDXGISwapChainMedia` | request a custom refresh | integrated panels only | (0,0) on both screens [L] | see 1.4 |
| `DwmGetCompositionTimingInfo` | no | **no**: NULL only since Windows 8.1 [V] | 239.998 Hz = main display [L] | one global composition clock |

**1.3 Reading each monitor's phase from one process [L, addendum item 2]**

- `D3DKMTGetScanLine` returns `ScanLine` in 0..vtotal-1 (laptop 0-1799, external 0-1114, matching CCD totals) and `InVerticalBlank`. Each call costs about 17.5 µs (p95 27.5, max 341). Polling edges is noisy (±40 µs), but **frame start = sample time minus line × line time** is precise: over 30 s the fit residual was 1.2 µs (laptop) and 0.3 µs (external).
- `D3DKMTWaitForVerticalBlankEvent` (`hDevice=0`) and `IDXGIOutput::WaitForVBlank` wake at vblank start, 72 µs (laptop) and 90 µs (external) later, jitter sd 27-31 µs. Use event waits to run the loop and the scanline fit to calibrate and track drift (a 20 ms burst every few seconds is enough, since drift is 0.5 µs/s).
- `DwmGetCompositionTimingInfo(NULL)`: rateRefresh 240 Hz, the main display's. It cannot be asked per window or per monitor [V doc, L]. So it cannot give per-monitor phase.
- Honest limits: this measures refresh starts, not light. Panel processing delay and true scan direction need a camera.

**1.4 `IDXGISwapChainMedia` and the composition swapchain (addendum item 1)**

- Documented [V, MS Learn, 2024-02-22]: lets a media app ask for a seamless change to a custom refresh (example: 23.997 fps asks for 24 or 48 Hz, no mode switch). "Seamless changes to custom framerates can only be done on integrated panels. Custom frame rates cannot be applied to external displays", `CheckPresentDurationSupport` returns (0,0) there. `SetPresentDuration` takes 100 ns units. Approval must be re-checked each frame through `ApprovedPresentDuration`. Windows 8.1+.
- This PC, Windows 11 25H2, NVIDIA 617.42 [L]: `QueryInterface(IDXGISwapChainMedia)` succeeds on flip-model HWND swapchains on **both** monitors and on a composition swapchain. `CheckPresentDurationSupport` returns S_OK with (0,0) for 24, 23.976, 30, 48, 60, 72, 96, 100 and 240 Hz on all three, including the laptop's embedded panel. Caveat: the windows were hidden and never presented. `GetFrameStatisticsMedia` before any present returns DXGI_ERROR_FRAME_STATISTICS_DISJOINT. Verdict: **no usable "screen follows the film" lever here.** Retest once with a presenting window in spike (a).
- Composition swapchain [V]: `IsPresentationSupported` and `IsPresentationSupportedWithIndependentFlip` are both true on the NVIDIA and AMD adapters here [L]. It adds `SetTargetTime`, `SetPreferredPresentDuration(preferred, deviationTolerance)`, per-present statistics including independent-flip displayed time. Not tested: whether `SetPreferredPresentDuration` makes the OS retime the display.

### Q2. Clock and phase levers

**2.1 VRR and G-SYNC with windowed flip apps, and the VRR follower**

- Windows OS VRR toggle (build 1903): VRR for DX11 **fullscreen** games, requires WDDM 2.6 drivers; off by default [V, DirectX blog]. "Optimizations for windowed games" (Windows 11 22H2, announced 2022-02-16) upgrades DX10/11 blt windowed or borderless apps to flip model so VRR and Auto HDR can apply [S]. Our own flip-model windows do not need the upgrade.
- NVIDIA G-SYNC windowed: Control Panel "windowed and full screen" option [S]. One thread reports that with a second display G-SYNC worked only in exclusive fullscreen until that option was enabled [S, single anecdote]. No NVIDIA document found.
- This PC [L, NVAPI 2026-10-11, driver 617.42]: `NvAPI_Disp_GetVRRInfo` gives enabled=0, possible=0, requested=0 for **both** displays. `GetMonitorCapabilities` also gives supportVRR=0 for both, but its `connectorType` came back 0 (the struct may not be filled correctly), so I do not rely on it. The laptop panel's EDID does carry a FreeSync block (60-240 Hz), yet the driver does not offer VRR now. Cannot tell "panel not VRR-capable" from "G-SYNC switched off in the Control Panel": your 1-minute E1 check decides. The sibling spike's "follow" mode (present with tearing allowed at the master's refresh) tore on the fixed 240 Hz grid instead of refreshing in step, which fits VRR being inactive for windowed apps [L, sibling].
- VRR follower concept [D]: a VRR monitor starts a refresh when a present arrives, within [1/max, 1/min] of the last one. Presenting to it right after the master's vblank (plus a fixed offset) lines both screens up. Present at the master's cadence (96 Hz, inside 60-240) to avoid LFC. If fed the film rate (24 Hz, below 60) the driver repeats frames at its own timing (frame splitting; `NV_SET_ADAPTIVE_SYNC_DATA.bDisableFrameSplitting` exists [V header]), so only the first refresh of each film frame is under our control [K]. Scan speed stays at the maximum-rate speed, a fixed 3.7 ms on the laptop [S Blur Busters G-SYNC 101, D]. So VRR fixes quantisation (±2.08 ms to sub-ms), not scan skew.
- Precedent for a software VRR follower: **none found.** Adjacent: Intel "Software Genlock" (SID 2026, Mehmood & Singh) aligns vblank across machines to ±100-500 µs by modulating the display PLL; it needs PTP/Chrony time sync and Intel hardware [S abstract]. nDisplay's "Ethernet" policy is a software vblank barrier and warns of residual tearing [V]. HDMI QMS uses the VRR mechanism to switch to the media rate [prior report].

**2.2 NVIDIA and FreeSync over HDMI**

- No NVIDIA statement found. Community and retail sources: G-SYNC Compatible is DisplayPort Adaptive-Sync, plus HDMI 2.1 VRR [S]. The external's EDID has an AMD FreeSync block and no HDMI 2.1 VRR block (prior report). NVAPI says VRR not possible on it [L, but the laptop reads the same, so this is weak evidence].
- Expect no VRR on the external. Check whether it has a DisplayPort input (E1).

**2.3 Same-GPU head sync on consumer GPUs**

| Vendor | What exists | Locks heads? | Mixed rates or sizes | Bezel correction | Evidence |
|---|---|---|---|---|---|
| NVIDIA GeForce Surround (Mosaic API) | `NvAPI_Mosaic_*`: grids with **one** `NV_MOSAIC_DISPLAY_SETTING` (width, height, bpp, freq) per grid; control panel: "Specify the Surround resolution and refresh rate" | no sync claim anywhere | no: one setting for the group | yes: per-display overlap/gap, `applyWithBezelCorrect` adds corrected resolutions | [V] header, [V] CPL help |
| NVIDIA frame lock / swap sync | `NvAPI_GSync_*` and the NVIDIA frame-lock page cover **Quadro** hardware only; all cards must run "the same pixel rate"; GeForce not mentioned | yes, with Quadro Sync boards | no | n/a | [V] |
| AMD Eyefinity | ADLX: "Displays running in duplicate or Eyefinity mode do not support custom resolutions"; WATCHOUT guide: AMD needs the S400 sync module for frame-accurate sync even on one card | needs S400 | n/a | n/a | [V] ADLX, [S] Dataton |
| Intel | IGCL public header: `ctlGetSetDisplayGenlock` (validate, enable, disable, topology), `ctlGetSetCombinedDisplay` (up to 16 displays, 4 per GPU), a per-child-target vblank-timestamp query (`ctl_vblank_ts_args_t`) | yes, when the config allows | not stated | n/a | [V] header (master, 2026-10-11) |
| Any (measured) | two heads on one NVIDIA GPU, no Surround | **frequency yes (0.5 ppm), phase no** | yes | n/a | [L] |

- Answer to "any GeForce API for frame lock?": none found in the public NVAPI header; `NvAPI_GSync_*` are Quadro Sync devices [V]. The free lock we do have (frequency) comes from the shared reference; phase is set at modeset.
- Surround/Mosaic is the wrong tool: it forbids mixed rates, which is exactly the freedom panorama needs. Its bezel correction is the same idea as ADR 0009.

**2.4 Custom refresh modes at runtime**

- **NVIDIA** [V, NVIDIA/nvapi `nvapi.h`, file dated 2026-09-02, repo pushed 2026-09-18; present since release 313]: `NvAPI_DISP_EnumCustomDisplay`, `TryCustomDisplay` ("without saving the configuration"), `RevertCustomDisplayTrial`, `SaveCustomDisplay`, `DeleteCustomDisplay`. `NV_TIMING` carries `pclk` in 10 kHz units, full porches and syncs, and `etc.rrx1k` (physical refresh in 0.001 Hz). `hwModeSetOnly` means a hardware modeset without OS update: **do not set it**, or the OS and the hardware disagree. Read-only query on this PC: no saved custom displays on either screen [L].
- **Exactness** [D]: the external's own 100 Hz timing is 2000×1115 at 223.000 MHz. Any integer rate N needs pclk = 2,230,000 × N, always a multiple of 10 kHz. So 96.000, 72.000 and 48.000 Hz are exact. Fractional rates (95.904) can only be approximated to about ±23 ppm at this pclk, not needed. The external's EDID range is 48-100 Hz (H 30-255 kHz), a timing envelope, not a VRR range. Its Range Limits descriptor lists a 200 MHz pixel-clock maximum, yet its own 100 Hz timing runs 223 MHz, so the descriptor is looser than the real limit. 96 Hz at 214.08 MHz is below what it already takes [L EDID]. The HDMI link limit is not an issue.
- Laptop panel [L EDID]: native 2560×1600 at 60 Hz is 2720×1800 at 293.76 MHz; the 240 Hz mode is exactly four times that pixel clock (1,175.04 MHz = 240.000 Hz). Windows reports 240.001 because it rounds the pixel rate (1,175,045,000). The measured head ratio (2.4000012) agrees with 240.000. This corrects the earlier pacing research, which took 4 ppm from the rounded 240.001 figure and so predicted a stumble every ~40 minutes; the measured mismatch is 0.5 ppm.
- **AMD** [V, ADLX v2.0, 2026-09-20]: `IADLXDisplayCustomResolution::CreateNewResolution/DeleteResolution` with a full `ADLX_TimingInfo` and pixel clock. Not on Eyefinity or duplicate. **Intel** [V]: `ctlGetSetCustomMode` takes only source X/Y: no custom refresh. **CRU** [K]: user-level EDID override plus driver restart; not an app mechanism.
- Can an app do this "without the user"? API-wise yes. Unknown: whether `SaveCustomDisplay` needs admin, whether a trial survives a window move, how the OS mode list reflects a trial. All spike (c).
- Safety: the Try/Revert pair is the safety valve, but the app must also run an out-of-process watchdog (ADR 0005). The laptop panel stays the screen that always works.

**2.5 Windows 11 per-display refresh and Dynamic Refresh Rate**

- Insider Canary build 25915 (2023-07-27) [V]: "improved refresh rate logic to allow different refresh rates on different monitors, depending on the refresh rate for each monitor and content shown on the screen". Nothing says the DWM composes per monitor. GA build unconfirmed [S].
- On 25H2 (build 26200) the DWM reports one composition clock, 239.998 Hz, the main display's [L]. The earlier spike also saw composition once per main-display refresh. So no independent composition clocks in practice.
- Dynamic Refresh Rate (laptops with VRR panels) can retime the panel mid-playback; battery saver forces the low rate [V Insider note]. This laptop lists only 60 and 240 Hz (prior report) and NVAPI says VRR is not possible, so DRR is probably not offered [D]. Pin a fixed rate during playback anyway.

### Q3. Scanout timing

Numbers [L CCD `QueryDisplayConfig` and EDID unless marked D]:

| Screen and mode | Total (px) | Line time | Active scanout | Vblank | Period |
|---|---|---|---|---|---|
| Laptop 240 Hz (native timing) | 2720×1800 | 2.315 µs | **3.704 ms** (88.9%) | 0.463 ms | 4.167 ms |
| External 100 Hz (current) | 2000×1115 | 8.969 µs | **9.686 ms** (96.9%) | 0.314 ms | 10.000 ms |
| External 96 Hz custom (same totals, 214.08 MHz) [D] | 2000×1115 | 9.342 µs | **10.090 ms** | 0.327 ms | 10.417 ms |
| LG 4K 60 Hz (EDID native, Setup B) | 4400×2250 | 7.407 µs | **16.0 ms** | 0.667 ms | 16.667 ms |
| LG 4K 48 Hz custom (475.2 MHz) [D] | 4400×2250 | 9.259 µs | **20.0 ms** | 0.833 ms | 20.833 ms |

- A frame change shows at row y (0 top, 1 bottom) at time `t_refresh + y × T_scan`. For a seam between screens A and B: offset(y) = q + (y − y*) × S + c. Here q is the refresh quantisation, y* the row the schedule is aimed at, c the calibrated panel-latency difference, and S = T_scan(slow) − T_scan(fast). It assumes both screens scan top to bottom and no frame buffer inside the panel [D, camera needed].
- Setup A: S = 5.98 ms (100 Hz) or 6.39 ms (96 Hz). With y* = 0.5: ±3.2 ms at the extreme rows, ±1.9 ms over the middle 60%. Adding q ≤ 2.08 ms (240 Hz follower): worst case 5.3 ms at the extreme rows, 4.0 ms over the middle 60%, 2.1 ms at mid-height.
- Setup B: LG 60 vs laptop S = 12.3 ms, LG 48 vs laptop S = 16.3 ms. With y* = 0.5 that is ±6.2 / ±8.2 ms at the extreme rows. This is the largest unsolved term. A portrait monitor scans along its native axis, so in the viewer's frame its skew runs left to right [D]: the room model must record rotation and scan direction.
- Levers: (1) per-monitor time shift aimed at the seam row, chosen by the planner where motion crosses (ADR 0009 keeps faces off bezels, so crossings are mostly background and pans); (2) matched timings (needs custom modes on both screens; the laptop panel has only 60/240 built in); (3) VRR does not help, scan stays at max-rate speed [S]; (4) software rolling cut rejected (a tear line on the fast screen); (5) refresh quantisation q cut by a faster follower or by VRR.

### Q4. Perception

Paper: Deshpande & Daly, "Quality of Experience for Large Ultra-High-Resolution Tiled Displays with Synchronization Mismatch", EURASIP J. Image and Video Processing 2011, DOI 10.1155/2011/647591 [V, full PDF read, figures read by eye ±0.2].

- Method: a 120 Hz single panel simulates tiles. Viewing distance 3 picture heights. 20 imaging engineers, 5-point impairment scale (5 imperceptible, 4 perceptible but not annoying, 3 slightly annoying, 2 annoying, 1 very annoying). Mismatch tested: 8.33, 16.7, 25 ms only. Moving-blob tests at 6, 12, 24 deg/s with gaps 0-0.9°.
- Natural clips, two tiles (1×2 or 2×1), score at 8.3 / 16.7 / 25 ms [Fig. 10]:

| Clip | Gap 0.54° | No gap |
|---|---|---|
| Diving | 4.7 / 4.7-4.9 / 4.5-4.6 | 4.4-4.5 / 4.3-4.6 / 3.9-4.2 |
| Basketball | 4.6-4.7 / 4.6-4.8 / 4.1-4.5 | 4.2-4.3 / 2.2-3.0 / 1.8 |
| Fence (hardest) | 4.8-4.9 / 4.3-4.6 / 3.3-4.5 | 3.8-4.1 / 2.6-3.1 / 2.2 |

- Moving blob, no gap, 8.3 ms: 2.0 (6 deg/s), 2.8 (12), 3.6 (24). With a 0.36° gap: 4.2, 4.5, 4.8 [Fig. 11]. The tolerance threshold at score 4 rose with gap width and, unexpectedly, with speed: roughly 12-21 ms (6 deg/s), 25-31 ms (12), 27-42 ms (24) for gaps 0.36-0.9° [Fig. 12, interpolated from three mismatch values, coarse]. More tiles lowered tolerance (3×3 worst).
- What it does not give us: nothing below 8.33 ms; smooth 120 Hz motion, not 24 fps film where each frame change moves the picture by a whole step (a 960 px/s pan moves 40 px per film frame); expert viewers; no scan-row skew.
- Our offsets: refresh quantisation ≤ 2.1 ms plus up to ±3 ms row skew, below the paper's lowest step. The paper says one frame at 120 Hz was mostly near-imperceptible with a gap wider than 0.5°. Your own seam-sim clip (0, 4, 8, 13, 17 ms, 24 fps pan) drew "flicker crazy" on the larger offsets, with no threshold named. Both fit a threshold somewhere between 4 and 13 ms for film cadence; that is a guess to be settled by the ladder.
- Newer work: none found on inter-display offset visibility. OpenAlex searches (3 queries) surfaced Intel's SW Genlock, which claims ±100-500 µs "sufficient to eliminate perceptible refresh misalignment" [S abstract, vendor claim, not a perception study], and an RIT 2014 thesis on spatial artifacts [S].
- Use as an input to the by-eye test [D]: ladder 0, 1, 2, 3, 4, 6 ms between two real screens with 24 fps film pans and a fixed grey-bar reference across the seam, per the roadmap rule on anchored checks. Spike (a) can set the offsets from software.

### Q5. What pro systems do without sync cards

| System | How they sync outputs of one GPU | Without hardware sync | Source |
|---|---|---|---|
| Unreal nDisplay | Policies: **None** (0), **Ethernet** (1, VBlank barrier over the network), **NVIDIA** (2, hardware framelock via SwapLock), Custom. Mosaic/Eyefinity make outputs share one clock "when GPUs are properly connected using Quadro Sync II or FirePro S400" | "Expect at least a one-frame hardware delay"; Ethernet: "tearing can still be possible among the displays" (Windows DWM and driver settings); genlock alone "has not proven to be 100% reliable"; best = genlock + framelock | [V] Epic doc |
| NVIDIA frame lock / swap sync | Hardware locks pixel scanning to a sync source; swap sync aligns buffer swaps; Quadro only | n/a | [V] NVIDIA page |
| WATCHOUT 7 | Multi-output on one card (up to 6; same resolution and refresh); software sync by network time at "millisecond-level"; hardware sync (Quadro Sync II, FirePro S400, WATCHPAX) for LED walls; AMD needs S400 even on one card, NVIDIA generally not | software sync adequate for most setups | [S] Dataton guide and forum |
| disguise | "Framelock" keeps outputs of one server in step, "genlock" locks servers to a clock; tearing between outputs means sync is wrong; has an Output Sync Test | n/a | [S] disguise docs |
| TouchDesigner | Docs advise one canvas across monitors; no sync mechanism found in what I read | n/a | [S] |
| Resolume | not covered | | |
| Splash (GPL-3.0, now on GitLab) | up to 8 outputs on 2 GPUs, one computer; README says nothing on sync | n/a | [V] README |

None of them mixes refresh rates: the pro world requires identical timing on every output (NVIDIA: "same pixel rate"; WATCHOUT: same resolution and refresh). panorama's per-monitor multiples with software scheduling is new ground, so there is no quality claim to borrow [D]. The only software-sync numbers found are millisecond-level (WATCHOUT) and Intel's ±100-500 µs with PLL control.

### Addendum item 3. Nearest-refresh scheduling: checked

Claim: slowest picture monitor = master; every faster monitor switches on its own refresh nearest that moment; offset ≤ half the faster period; the faster monitor's pace stays even while the clocks keep a fixed ratio.

- **Offset bound holds** [D, simulated 2 h, 172,800 film frames]. For any real moment the nearest refresh of a grid with period T is within T/2: max 2.083 ms at 240 Hz.
- **Pace holds with two conditions** [D].
  - The follower's rate must be a whole multiple of the film rate. Counter-test: 25 fps on a 100 Hz master (4 per frame) with a 240 Hz follower (9.6 needed) gave 9 and 10 mixed (7,200 × 9, 10,799 × 10 over 12 min).
  - "Fixed ratio" is never perfect. Follower refreshes per film frame over 2 h for the Setup A pair (master 96 Hz, follower 240 Hz): 0 ppm → 10 every frame; **0.5 ppm (measured here) → one 9 in 2 h**; 4.2 ppm → seven 9s; 20 ppm → 35 9s. Each is a 10% short frame. Put them at cuts (the cue sheet knows cuts).
- Offsets of the simulation: |offset| p50 0.56-1.2 ms, p95 ≤ 2.0 ms, max 2.083 ms. Against the one-sided "first refresh at or after" rule (what a shared timeline gives): 240 Hz follower p50 2.08, p95 3.96, max 4.17 ms; 96 Hz follower p50 5.2, p95 9.9, max 10.4 ms.
- **Two followers of one master**: their mutual offset is up to T_follower1/2 + T_follower2/2 (example: two 240 Hz heads, 1.57 ms in the simulation, bound 4.17 ms). So the master must be chosen per seam, not just "the slowest". For three screens L-C-R, an edge master gives the far seam up to T_C/2 + T_R/2. The planner should pick the master and the time shifts to minimise the worst adjacent pair. A coarse follower (a 96 Hz screen, ±5.2 ms) is the worst follower, so a good default is the coarsest grid as master (its own offset is zero by definition); the planner can pick otherwise when that shrinks the worst seam.
- Measured support [L]: head frequencies are locked to 0.5 ppm and the phase classes are stable, so a calibration at start plus a check every few seconds is enough. Needs re-measuring for 240 vs 96 after the custom mode exists: the ratio depends on the new pixel clock's divider.
- **Hardware confirmation (sibling spike, 20 fps, 20 s per mode)** [L, sibling]: nearest-refresh gave offset −0.28 ms (p5-p95 −0.28 to −0.27) over 380 frames with an even pace on both screens (laptop 12 per frame, external 5); the "own" rule (first refresh at or after) gave −1.1 ms in that run and +2.2 ms in an earlier one, an arbitrary steady value, as predicted for the one-sided rule. The −0.28 ms is luck of the phase; the guarantee stays ±2.1 ms.
- **Implementation rule learned there:** derive the film clock from the master's refresh *count*, never compare it against the master's refresh times. With a free-running film clock, 57 of 380 laptop frames stumbled, because frame starts fell within jitter of a master refresh and the master's switch flipped between two refreshes [L, sibling].
- **Slip interval, refined:** the sibling spike estimated a nearest-refresh slip about every 17 min from the CCD-reported rates (240.001 vs 100.000, 4 ppm). Windows rounds the pixel rate; the measured head ratio mismatch is 0.5 ppm, so about every 2.3 h [L, D]. Re-check after the custom 96 Hz mode, since its divider is new.
- Needs per frame: the master's next vblank time (known), the follower's grid (known), the film clock. The follower's present must be issued ahead of the master's refresh when the nearest follower refresh is earlier.

## 4. Ranked candidate presentation architectures

Adoption risk is stated per row. Ranks are for the M1 decision.

| Rank | Architecture | Setup A | Setup B | Pace | Seam offset | What it needs from the player engine | Risk |
|---|---|---|---|---|---|---|---|
| **1** (product) | One process, one Independent-Flip window per monitor. Each screen at a multiple of the playback rate (custom modes allowed). Slowest = master, nearest-refresh scheduling plus seam-row shift. D3D11/12 flip swapchains with waitable objects and vblank events as the baseline; `IPresentationManager` or `VK_EXT_present_timing` as optional precision. | external custom 96 (master), laptop 240; ≤ ±2.1 ms plus row term, stable for hours | LG 48 (or 96/240 for others); master per seam; LG skew term 12-16 ms | even on every screen at a multiple | ±T_follower/2, row term | Own renderer. N swapchains on one device per adapter. A *pull* API: "frame plus sub-frame position for target scanout time T" instead of push-at-vsync. Per-monitor clock map from one film clock. Atomic flush on seek. Cue-sheet lookup by PTS. Pan mode (ADR 0016) needs the per-refresh position. mpv's single VO window cannot do this; an mpv fork or a libplacebo-based renderer can (engine researcher decides). | Medium: co-existing Independent Flip seen on Setup A in short synthetic runs only (sibling spike); not yet with the real renderer, long runs or PresentMon; custom modes are vendor APIs (NVAPI, ADLX); Intel has none; present-timing is young. |
| 2 (demo bridge) | One spanning window in stock mpv, custom multiples on every screen, fastest screen as main. | 240 + custom 96: pace even on both (predicted [D]); seam offset constant but uncontrolled, 0-10.4 ms; "re-roll" phase lever unverified | needs all three screens at multiples; compose clock = main display | even if each rate is a multiple | uncontrolled | none (stock mpv) | Low effort, cannot steer the seam. The pending 240+96 trial tells if by-eye is acceptable. |
| 3 (conditional) | #1 plus a VRR follower on VRR-capable screens | only if G-SYNC can be turned on for the laptop panel; gain ±2.1 ms to sub-ms; scan skew unchanged | LG and the others have none known | as #1 | sub-ms | #1 plus present-on-demand | High: NVAPI says VRR not possible now; windowed G-SYNC with two monitors has only anecdotes |
| 4 (today, fallback) | Spanning window, native modes | external 4,4,4,4,4,5; offset −1.7..+6.7 ms [L spike] | worse | uneven | uncontrolled | none | Fails the by-eye bar per the earlier spike |
| 5 | Hardware genlock, Surround, Mosaic | n/a on GeForce; one mode for the group | same | n/a | n/a | n/a | Reject |

Notes for the engine researcher: the product cannot use the libmpv render API (OpenGL only, per the earlier report) and needs frames as GPU textures on the same device as the swapchains. The engine report's plan to own `Present` per monitor is what this schedule needs: libplacebo's D3D11 swapchain hard-codes `Present(1,0)` and exposes no present timing, so wrap our own backbuffers (`pl_d3d11_wrap`) or call `Present` ourselves. Setup A's outputs are all on the NVIDIA adapter [L], so no cross-adapter copy. Setup B's wiring is unknown; if two screens sit on different adapters, per-adapter devices or copies are needed [K].

## 5. Spike designs for this PC

My scripts live in the session scratchpad, not the project: `...\scratchpad\probe\sync-probe\` (Rust + C++, modes 1-8), `ccd_probe.py`, `edid_probe.py`, `nearest_refresh_sim.py`. Reuse or rewrite. The sibling presenter harness is in the project: `spikes/per-monitor-presenter/`.

### (a) One-process per-monitor presenter: Independent Flip check and flip timing logs

**Status.** The core was already built and run by the sibling spike (`spikes/per-monitor-presenter/`, results in `spike-261011-1247-one-window-per-monitor.md`): C++17, D3D11, one window and one device per monitor, `FLIP_DISCARD`, 2 buffers, waitable object, modes own / nearest / follow, a `WaitForVBlank` grid fit and `GetFrameStatistics` for present times. Section 1 lists what it showed. This spike is the rest of the job.

**Remaining build** (extend that harness, do not rebuild it):
- A scanline-phase sampler thread using my probe's line-number fit (`D3DKMTGetScanLine` bursts every 100 ms), so offsets are computed from refresh starts at chosen rows and drift is logged.
- A seam-row setting `y*` and a calibration offset `c` per monitor in the schedule.
- PresentMon 2.6.0 beside it (`--process_name ... --write_display_metadata`) to confirm that OVERLAY in `GetFrameStatisticsMedia` means "Hardware: Independent Flip" or "Hardware Composed: Independent Flip", per `VidPnSourceId`.
- Variants: V2 windows 1 px smaller (forces DWM composition, the control); V4 composition swapchain with `SetTargetTime` and independent-flip statistics (`GetDisplayedTime`, `GetPresentDuration`); V5 Vulkan `VK_EXT_present_timing` (optional).
- 24 fps on 240 + 96 Hz once spike (c) exists. The sibling used 20 fps so both native rates were already even.
- Knock-out matrix: another window over a corner, a topmost toast, Game Bar, Alt+Tab, taskbar auto-hide, 100% vs 150% scaling, HDR toggle, Desktop Duplication or a screen recorder running.
- Later, the same with the real renderer (decode, libplacebo, subtitles, on-screen controls).

**Measure:** share of presents per mode per window (PresentMon and `GetFrameStatisticsMedia` side by side); pace histogram per monitor; refresh-start offset between monitors at y*, 0.25 and 0.75 of the height, for the nearest schedule and a one-sided control; phase slope over 10 min (expect about 0.5 µs/s); CPU per presenter thread; each knock-out's effect on mode, pace and offset.

**Pass:** both windows in the hardware path at the same time for 5 min, at least 99.5% of presents, agreed by PresentMon; laptop 10 refreshes per frame at least 99.9% (24 fps); offset within T_follower/2 + 0.3 ms for at least 99% of frame changes; phase slope at most 1 µs/s; no missed present. Optional camera (phone, 240 fps) for ground truth.

**Needs from you:** two fullscreen-size borderless windows for 1-5 min per run (takes over both screens); PresentMon needs admin or "Performance Log Users" membership. No display-mode change.

### (b) VRR follower on the laptop panel

**Gate 0 (1 minute, you):** NVIDIA Control Panel, "Set up G-SYNC": is the laptop panel listed; turn on "Enable G-SYNC, G-SYNC Compatible" and the windowed option; note if the external has a DisplayPort input. Then I rerun probe mode 5. **If `bIsVRRPossible` stays 0, stop: (b) is dead on this driver.**

**Build:** the sibling harness already has a `follow` mode (the laptop presents with tearing allowed at the master's refresh times). Reuse it with the settable offset; external at a fixed rate. Present at 96 Hz (in range) as the main case; also a film-rate (24 Hz) case to see LFC.

**Measure:** laptop-vs-external refresh-start offset (external by scanline fit; laptop by vblank exits or NVAPI flip timestamps, since vtotal varies in VRR); `bIsDisplayInVRRMode` over 10 min; flip cadence at 24, 60, 96, 144 Hz; brightness flicker by eye on a grey reference; the same with the OS VRR toggle on and off, with the external active.

**Pass:** offset p99 within ±0.5 ms for 10 min; VRR never drops; no visible flicker; at least 3× better than nearest-refresh (±2.1 ms). Otherwise drop (b).

**Run only if** (a)'s residual offset shows by eye, or content needs a non-multiple (25p on the laptop at 200 Hz).

**Needs from you:** the Control Panel setting; fullscreen takeover of both screens. No display-mode change.

### (c) Custom 96 Hz on the external

**Build:** a small NVAPI tool in the probe's style. `NvAPI_Initialize` → `GetDisplayIdByDisplayName("\\.\DISPLAY2")` → `NV_CUSTOM_DISPLAY`: 1920×1080; timing HVisible 1920, HTotal 2000, front porch 48, sync width 32, back porch 0 (as the monitor's own 100 Hz timing), VVisible 1080, VTotal 1115, front porch 3, sync 5; sync polarity from the EDID flags; `pclk` 21408 (214.08 MHz); `etc.rrx1k` 96000; `hwModeSetOnly` **off**. Flow: `TryCustomDisplay`, watch the screen, `RevertCustomDisplayTrial` on failure. On success `SaveCustomDisplay` (this monitor only) so Windows lists the mode, switch with `ChangeDisplaySettingsEx(..., CDS_FULLSCREEN)` (96 Hz is an integer rate, so DEVMODE can ask for it). The sibling kill test (`spikes/display-restore/`, n=1 per method, not elevated) saw that kind revert 0.45 s after the caller was killed, while flags 0 and `SetDisplayConfig` stayed until reset. Finish with `DeleteCustomDisplay`. An out-of-process watchdog still reverts after 15 s unless confirmed.

**Measure:** CCD `vSyncFreq` and `pixelRate`; probe mode 6 on the new pair: the external's fitted rate, the ratio to the laptop (expect 2.5 ± 1 ppm), the phase-class slope over 10 min; picture stable for 10 min with no HDMI dropout; the mode listed in Windows; restore after killing the tool. Then the pending spike trials (`run-span-trial.py ... --refresh \\.\DISPLAY2=96`, and 72) become possible. Repeat for 72 and for the LG at 48 Hz when Setup B is connected.

**Pass:** stable 10 min; CCD reports 96 Hz exactly; ratio drift ≤ 1 µs/s; kill-and-restore in ≤ 5 s; nothing left persisted after cleanup.

**Needs from you:** approval for a display-mode change (screen blanks briefly; if the monitor rejects the timing it can stay blank until the revert fires; the laptop panel stays on) and for a saved driver configuration (removed afterwards). Whether `SaveCustomDisplay` needs admin is unknown until tried.

## 6. Sources (fetched 2026-10-11 unless noted)

Microsoft:
- Composition swapchain overview (doc updated 2025-03-11): https://learn.microsoft.com/en-us/windows/win32/comp_swapchain/comp-swapchain-portal
- `IPresentationManager` (Windows build 22000): https://learn.microsoft.com/en-us/windows/win32/api/presentation/nn-presentation-ipresentationmanager
- `SetTargetTime`: https://learn.microsoft.com/en-us/windows/win32/api/presentation/nf-presentation-ipresentationmanager-settargettime
- `ICompositionFramePresentStatistics`: https://learn.microsoft.com/en-us/windows/win32/api/presentation/nn-presentation-icompositionframepresentstatistics
- `PresentStatisticsKind`: https://learn.microsoft.com/en-us/windows/win32/api/presentationtypes/ne-presentationtypes-presentstatisticskind
- Local header `presentation.h` in Windows SDK 10.0.26100 [L]
- DXGI flip model guidance (updated 2026-04-15): https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/for-best-performance--use-dxgi-flip-model
- `IDXGISwapChainMedia` (2024-02-22): https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_3/nn-dxgi1_3-idxgiswapchainmedia ; `SetPresentDuration`: .../nf-dxgi1_3-idxgiswapchainmedia-setpresentduration ; `DXGI_FRAME_STATISTICS_MEDIA`: .../ns-dxgi1_3-dxgi_frame_statistics_media ; `DXGI_FRAME_PRESENTATION_MODE`: .../ne-dxgi1_3-dxgi_frame_presentation_mode
- `DwmGetCompositionTimingInfo` (2025-07-01): https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/nf-dwmapi-dwmgetcompositiontiminginfo
- OS variable refresh rate (Windows 1903): https://devblogs.microsoft.com/directx/os-variable-refresh-rate/
- Windowed-game optimizations (2022-02-16) [S]: https://devblogs.microsoft.com/directx/updates-in-graphics-and-gaming/
- Windows Insider build 25915 (2023-07-27): https://blogs.windows.com/windows-insider/2023/07/27/announcing-windows-11-insider-preview-build-25915/
- Composition swapchain code examples [S]: https://learn.microsoft.com/en-us/windows/win32/comp_swapchain/comp-swapchain-examples

Khronos and vendors:
- `VK_EXT_present_timing` (Ratified, rev 3): https://docs.vulkan.org/refpages/latest/refpages/source/VK_EXT_present_timing.html
- Vulkan 1.4.335 [S]: https://www.phoronix.com/news/Vulkan-1.4.335-Released ; NVIDIA Linux beta 595.45.04 [S]: https://www.gamingonlinux.com/2026/03/nvidia-beta-driver-595-45-04-released-for-linux-looks-to-be-an-exciting-one/
- NVAPI (header dated 2026-09-02, repo pushed 2026-09-18): https://github.com/NVIDIA/nvapi (`nvapi.h`, `nvapi_interface.h`)
- Intel IGCL (master header): https://github.com/intel/drivers.gpu.control-library
- AMD ADLX v2.0 (2026-09-20): https://github.com/GPUOpen-LibrariesAndSDKs/ADLX
- NVIDIA frame lock: https://www.nvidia.com/en-us/drivers/IO-10794
- NVIDIA Surround control-panel help: https://www.nvidia.com/content/Control-Panel-Help/vLatest/en-us/mergedProjects/nv3d/To_configure_my_displays_for_Surround.htm
- PresentMon 2.6.0 (2026-09-21): https://github.com/GameTechDev/PresentMon ; README: https://github.com/GameTechDev/PresentMon/blob/main/README-ConsoleApplication.md

Pro systems and papers:
- Unreal nDisplay synchronization: https://dev.epicgames.com/documentation/unreal-engine/synchronization-in-ndisplay-in-unreal-engine
- WATCHOUT hardware sync [S]: https://www.dataton.com/watchout-users-guide/installation/hardware-synchronization (the fetch 404'd; snippet only)
- disguise output sync [S]: https://help.disguise.one/designer/networking/output-sync-test
- TouchDesigner multiple monitors [S]: https://derivative.ca/UserGuide/Multiple_Monitors
- Splash: https://github.com/paperManu/splash
- Deshpande & Daly 2011: https://doi.org/10.1155/2011/647591 (PDF read from https://link.springer.com/content/pdf/10.1155/2011/647591.pdf)
- Intel Software Genlock, SID 2026 [S abstract]: https://doi.org/10.1002/sdtp.70003_257
- Blur Busters G-SYNC 101 [S]: https://www.blurbusters.com/gsync/gsync101-input-lag-tests-and-settings/6/ ; dual-monitor G-SYNC thread [S]: https://forums.blurbusters.com/viewtopic.php?p=111726
- Windows 11 G-SYNC/FreeSync-over-HDMI community sources [S]: https://evezone.evetech.co.za/ez/g-sync-compatible-meaning-freesync-monitors

Project: README, PDR, roadmap, ADRs 0005/0014/0016/0019, `research-261011-1000-...`, `spike-261011-1034-...`, `researcher-261010-1846-windows-rendering-...`. Read after my probes, not edited: `spike-261011-1247-one-window-per-monitor.md` (sibling presenter spike, 13:27) and `researcher-261011-1247-performer-engine-and-picture-path.md` (engine research, 13:30; it also finds `VK_EXT_present_timing` rev 3 on this driver and `Present(1,0)` hard-coded in libplacebo's D3D11 swapchain).

Local probes [L], all read-only, 2026-10-11, Windows 11 Pro 25H2 (26200), NVIDIA driver 617.42: CCD `QueryDisplayConfig`; EDID from the registry; DXGI adapters, outputs, `CheckHardwareCompositionSupport`; `D3DKMTGetScanLine`, `D3DKMTWaitForVerticalBlankEvent`, `IDXGIOutput::WaitForVBlank` for 30 s and 6 s runs; `DwmGetCompositionTimingInfo`; hidden-window swapchains for `IDXGISwapChainMedia`; `IsPresentationSupported`; Vulkan extension and feature lists; NVAPI getters. Also `reg_probe.py` (no `DirectXUserGlobalSettings` value set, no DWM `OverlayTestMode`).

## 7. Limits and unresolved questions

Limits of this pass:
- I presented no frames myself. Independent Flip evidence on this PC is the sibling spike's (short synthetic runs, `GetFrameStatisticsMedia` only). VRR, LFC, `VK_EXT_present_timing` behaviour and `IDXGISwapChainMedia` with a presenting window are untested. Those need fullscreen windows.
- The phase and pace numbers are refresh-start times. Light output, panel delay and scan direction need a camera or photodiode.
- Local runs were 30 s (phase) and 6 s (waits). The 0.5 µs/s drift needs a 10-minute rerun before it is relied on for a 2-hour film.
- Two MS Learn pages 404'd (`D3DKMTGetScanLine`, `CompositionFrameDisplayInstance`); the Dataton page 404'd. WebFetch output is a model-written digest; figure values from the 2011 paper were read by eye.
- Web budget ran 25 calls over target (see the header).

Unresolved:
1. Does the sibling's OVERLAY reading mean real Independent Flip (PresentMon agreement), and does it survive the knock-outs, long runs and the real renderer? Spike (a), remaining half. (Two windows in the hardware path at once is already seen on Setup A.)
2. Why does NVAPI say VRR is not possible on the laptop panel: panel, driver, or a Control Panel setting? E1.
3. Does `SaveCustomDisplay` need admin, and does the mode list show it? Does a trial survive? Spike (c).
4. After the custom 96 Hz, is the 240/96 head ratio as tight (≤ 1 ppm) as 240/100?
5. Panel latency and true scan direction of both screens, the LG's skew, portrait monitors in Setup B. Camera.
6. Is the phase at modeset random, and is a re-roll lever worth the flicker?
7. How visible is a 1-4 ms offset on 24 fps film across the real bezel? By-eye ladder.
8. Does `VK_EXT_present_timing` give usable per-monitor target times on NVIDIA Windows in practice? What do AMD discrete and Intel Arc drivers expose on Windows?
9. Does an `IDXGISwapChainMedia` retest with a presenting window change the (0,0) answer?
10. Is there an NVIDIA primary statement on FreeSync-over-HDMI, and on windowed G-SYNC with several monitors?
11. Setup B facts: adapters per monitor, orientation, the third screen's timing range and VRR.
12. Resolume, disguise and TouchDesigner specifics were not read at source.
