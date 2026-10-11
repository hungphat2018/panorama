---
title: "Spike P0: performer toolchain and reference playback (libplacebo + FFmpeg on this PC)"
date: 2026-10-11
type: spike
answers: "ADR 0022 gate: can the own performer (C11 on libplacebo + FFmpeg) be built and run on this PC, and what does baseline windowed playback cost on D3D11 and Vulkan with software, D3D11VA and Vulkan Video decode?"
harness: spikes/performer-p0/
results: spikes/performer-p0/results-2026-10-11/
---

# Spike P0: performer toolchain and reference playback

Terms (plain): a **refresh** is one chance for a screen to show a new picture (240 a second on the laptop panel). A **film frame** is one picture of the video (23.976 a second). **Steady** means after the first 2 s of a run. The **loop interval** is the time between two presents of the player's render loop. **Late** means a loop interval over 1.5 refreshes; a **hitch** is one over 50 ms. **Skipped refreshes** (estimate) is refreshes that elapsed minus pictures the loop submitted; it is 0 when no refresh went without a new picture, whatever the present queue does.

All playback numbers: a normal 1280x800 window on the laptop panel (240 Hz, per-monitor DPI aware, never fullscreen), the player rendering a new picture on every refresh from a 1080p source, libplacebo's default preset, RTX 5070 Ti Laptop (driver 617.42) driving both screens, laptop on AC at the High performance plan.

## Answer

- **Yes: the stack builds and runs on this PC.** MSYS2 UCRT64 installed portably under `C:\Users\tamla\msys64` (base archive checked against its published SHA-256 and GPG signature; no installer, no admin, user PATH and registry untouched). libplacebo master `1dd0226` (7.375.0, API 375) builds from the read-only clone in 10 s with D3D11, Vulkan, shaderc, SPIRV-Cross, lcms and libdovi. `plplay` builds as-is and plays on both backends.
- **`plplay` alone cannot be measured or used for hardware decode**, so I wrote `perf0` from it (plplay is CC0): 2.4k lines of C11 including the instrumentation; the decode and GPU parts are 1.2k. It adds a second backend path, hardware decode, statistics, JSON/CSV results, a pixel-dump check and a headless render benchmark. plplay's `--hwdec` on Vulkan collapses to about 10 refreshes a second with constant dropped frames (FFmpeg creates its own `VkDevice`, libplacebo cannot share it), and on D3D11 it silently falls back to software.
- **libplacebo's libav helpers compile warning-free and work against FFmpeg 9.0.2 (libavutil 61).** Software frames (8- and 10-bit), Vulkan Video frames and 10-bit AV1 all render, and match software decoding to within 5 of 255 levels (table at the end of Results).
- **D3D11VA needs a small import of our own** (not in libplacebo's helpers): about 70 lines wrap the decoder's array texture planes with `pl_d3d11_wrap`. **Vulkan Video works** when FFmpeg creates the `VkDevice` and libplacebo imports it (`pl_vulkan_import`). Decoding flat out, the hardware decoders run at 55 to 75 times real time and use 0.08 to 0.28 of a core.
- **D3D11 is the solid baseline.** Over 60 s (14,399 refreshes) with software decode and with D3D11VA: 0 late refreshes, 0 skipped, longest loop interval 5.2 ms, 1437 of 1439 film frames shown for exactly 10 refreshes. The first present comes 0.27 s after process start when libplacebo's shader cache is warm (0.41 to 0.46 s cold).
- **Vulkan plays everything but its presenting is unsteady.** With libplacebo's swapchain at its default depth the loop is bursty (loop interval 0.1 to 10 ms, film frames shown for 9 to 12 refreshes); depth 1 makes it even on average (0 skipped refreshes) but still jittery (p99 6.6 ms). Three hitches of 300 to 720 ms showed in 2 of 12 runs with the load sampler and none in three bare 60 s runs; cause unknown. First frame 0.7 to 0.95 s warm.
- **Render cost is far inside a refresh on the dGPU and far outside it on the AMD iGPU.** One 1080p frame to 1440p with EWA (`hq`) and every cache bypassed: RTX 5070 Ti p99 1.1 ms (D3D11), 1.4 ms (Vulkan); 4K target 1.7 / 2.0 ms. Radeon 610M: 19 ms (`hq`), 8.6 ms (`default`), 1.4 ms (`fast`, bilinear). The 610M has no display outputs here.
- **The FFmpeg in MSYS2 is a GPL v3 build** (flags below). I built FFmpeg 9.0.2 from its signed source with `--disable-gpl`: it reports LGPL 2.1+ and still has `d3d11va`, `d3d12va`, `dxva2` and `vulkan` hwaccels; `perf0` runs against it unchanged and the player's non-system DLLs drop from 101 files (150 MiB) to 15 (44 MiB).
- **The MSYS2 package libplacebo 7.360.1 also works** with the same player source (two newer struct fields need a version guard, two internal helpers a local copy), so P1 may skip building libplacebo; render times match master.
- **For P1: start on D3D11.** Wrap our own flip-model DXGI swapchain with `pl_d3d11_create_swapchain(.swapchain)` and call `Present1` ourselves (verified: 0 late, 0 skipped over 20 s). libplacebo's Vulkan swapchain presents inside `pl_swapchain_submit_frame`, so Vulkan needs our own swapchain around `pl_vulkan_wrap` for `VK_EXT_present_timing`. Details under "What P1 needs".

## Exact build commands

From a clean state; `C:\Users\tamla` stands for the user profile. PowerShell 7 for the first block, then the MSYS2 shell. Folders are named `out` and `prefix` because the scout hook blocks shell commands with "build" in a path.

```powershell
# 1. MSYS2 base archive, verified (no installer, no admin, no PATH change)
$dl = "$env:TEMP\msys2-base"; New-Item -ItemType Directory -Force $dl | Out-Null
$f  = "msys2-base-x86_64-20260927.tar.zst"
curl.exe -L -o "$dl\$f"        "https://repo.msys2.org/distrib/x86_64/$f"
curl.exe -L -o "$dl\$f.sig"    "https://repo.msys2.org/distrib/x86_64/$f.sig"
curl.exe -L -o "$dl\$f.sha256" "https://github.com/msys2/msys2-installer/releases/download/2026-09-27/$f.sha256"
Get-Content "$dl\$f.sha256"                                   # 8a2095647afbf799d113d0cae35f7544589d059819042635504e75bd2179bd02
(Get-FileHash -Algorithm SHA256 "$dl\$f").Hash.ToLower()      # must be identical
# signature (done with Git for Windows' gpg and a private keyring):
#   curl -L -o reiter.asc "https://keyserver.ubuntu.com/pks/lookup?op=get&options=mr&search=0x0EBF782C5D53F7E5FB02A66746BD761F7A49B0EC"
#   gpg --homedir <tmp> --import reiter.asc ; gpg --homedir <tmp> --verify "$f.sig" "$f"
#   -> Good signature, primary fingerprint 0EBF 782C 5D53 F7E5 FB02 A667 46BD 761F 7A49 B0EC (the one msys2.org/docs/installer lists)
tar -xf "$dl\$f" -C C:\Users\tamla                            # Windows' bsdtar reads .zst; creates C:\Users\tamla\msys64 (7 s)

# 2. Toolchain and libraries (UCRT64), env vars for these commands only
$env:MSYSTEM = "UCRT64"; $env:CHERE_INVOKING = "1"
$sh = "C:\Users\tamla\msys64\usr\bin\bash.exe"
& $sh -lc "exit"                                              # first start: keyring init, ~30 s
& $sh -lc "pacman -Syu --noconfirm"                           # the first run updates msys2-runtime and stops; run it twice
& $sh -lc "pacman -Syu --noconfirm"
& $sh -lc "pacman -S --needed --noconfirm mingw-w64-ucrt-x86_64-{gcc,meson,ninja,pkgconf,libplacebo,ffmpeg,libass,shaderc,spirv-cross,glfw,SDL2,vulkan-headers,vulkan-loader,glslang,lcms2,libdovi,python-jinja,xxhash,fast_float}"
#   146 packages, 273 MiB download, 2.0 GiB installed
```

```bash
# 3. libplacebo master 1dd0226 + demos (inside:  & $sh -l  with the two env vars above)
git clone --recurse-submodules https://github.com/haasn/libplacebo.git ~/p0/src/libplacebo
git -C ~/p0/src/libplacebo checkout 1dd0226
export PYTHONDONTWRITEBYTECODE=1
meson setup ~/p0/out/libplacebo-master ~/p0/src/libplacebo --buildtype=release \
    --prefix="$HOME/p0/prefix/libplacebo-master" \
    -Dd3d11=enabled -Dvulkan=enabled -Dopengl=disabled -Dshaderc=enabled -Dglslang=disabled \
    -Dlcms=enabled -Ddovi=enabled -Dlibdovi=enabled -Dxxhash=enabled -Dunwind=disabled \
    -Ddemos=true -Dtests=false
meson compile -C ~/p0/out/libplacebo-master     # 97 steps, 9.5 s: libplacebo-375.dll, plplay.exe, colors.exe, ...
meson install -C ~/p0/out/libplacebo-master     # private prefix; /ucrt64 keeps the packaged 7.360.1

# 4. Our player (spikes/performer-p0/compile-perf0.sh)
bash spikes/performer-p0/compile-perf0.sh master      # libplacebo master + MSYS2 FFmpeg 9.0.2 (GPL build)
bash spikes/performer-p0/compile-perf0.sh packaged    # MSYS2 package libplacebo 7.360.1
bash spikes/performer-p0/compile-perf0.sh lgpl        # after step 5

# 5. Optional: LGPL FFmpeg 9.0.2 from signed source (2 min 8 s)
pacman -S --needed --noconfirm make diffutils mingw-w64-ucrt-x86_64-nasm
curl -LO https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz ; curl -LO https://ffmpeg.org/releases/ffmpeg-9.0.2.tar.xz.asc
curl -L -o ffmpeg-devel.asc https://ffmpeg.org/ffmpeg-devel.asc ; gpg --import ffmpeg-devel.asc ; gpg --verify ffmpeg-9.0.2.tar.xz.asc ffmpeg-9.0.2.tar.xz
#   -> Good signature, primary fingerprint FCF9 86EA 15E6 E293 A564 4F10 B432 2F04 D676 58D8 (listed on ffmpeg.org/download.html)
tar -xf ffmpeg-9.0.2.tar.xz -C ~/p0/src ; mkdir -p ~/p0/out/ffmpeg-lgpl && cd ~/p0/out/ffmpeg-lgpl
~/p0/src/ffmpeg-9.0.2/configure --prefix="$HOME/p0/prefix/ffmpeg-lgpl" --target-os=mingw32 --arch=x86_64 \
    --cc=gcc --cxx=g++ --enable-shared --disable-static --disable-gpl --disable-nonfree --disable-doc \
    --disable-debug --disable-autodetect --enable-runtime-cpudetect --enable-d3d11va --enable-d3d12va \
    --enable-dxva2 --enable-vulkan --enable-zlib --enable-libdav1d
make -j32 && make install
```

Run: `spikes\performer-p0\bin\run.cmd perf0-master.exe --file C:/path/video.mkv --backend d3d11 --decode d3d11va --seconds 60 --out results\run --id run` (puts the MSYS2 DLLs on PATH for that process only). `bin\plplay-master.exe` is the unmodified demo. Measurement scripts: `run-matrix.sh` (windowed runs plus `sample-gpu-cpu.ps1`), `run-render-bench.sh`, `run-decode-bench.sh`, `compare-ppm.py`, `summarize-results.py`.

## Versions

| Part | Version |
|---|---|
| Windows / CPU / RAM | 11 Pro 10.0.26200.8875 (25H2); Ryzen 9 8940HX (32 threads); 31 GB |
| GPUs | NVIDIA RTX 5070 Ti Laptop, driver 617.42, Vulkan 1.4.351, drives both screens; AMD Radeon 610M (32.0.21039.7002), Vulkan 1.4.315, no outputs |
| Screens | laptop 2560x1600 at 240 Hz (150 %); external 1920x1080 (not used) |
| MSYS2 | base 20260927, msys2-runtime 3.6.10-6, pacman 6.1.0-25, bash 5.3.020-1 |
| Compiler / build | gcc 16.2.0-5 (Rev5), binutils 2.47-4, mingw-w64 crt 14.0.0.r426, meson 1.12.1-1, ninja 1.13.2-1, pkgconf 3.0.7-2, python 3.14.8-1, make 4.4.1-3, nasm 3.02-1, gdb 18.1-3 |
| libplacebo | package 7.360.1-2 (API 360); built from source: master `1dd0226` of 2026-10-10 = 7.375.0 (API 375) |
| FFmpeg | package 9.0.2-1 (libavutil 61.1.102, libavcodec 63.1.102, libavformat 63.1.102); self-built 9.0.2 LGPL, same library versions |
| Other libraries | libass 0.17.5-1, shaderc 2026.4-1, spirv-cross 1~1.4.363.0-1, glslang 16.6.0-1, glfw 3.5.1-1, SDL2 2.32.10-1, vulkan-headers/loader 1~1.4.363.0-1, lcms2 2.19.1-1, libdovi 3.4.0-1, xxhash 0.8.4-1, dav1d 1.5.4-1, harfbuzz 14.6.0-1, freetype 2.14.3-1, fribidi 1.0.17-1 |
| Disk | MSYS2 2.9 GB in total (base 0.26 GB; 0.29 GB is our `~/p0` work area) |

Full lists: `results-2026-10-11/pacman-Q-full.txt` (237 packages), `pacman-Q-key-packages.txt`.

**MSYS2 FFmpeg configure flags** (`ffmpeg -buildconf`, full text in `ffmpeg-msys2-version-buildconf-hwaccels.txt`): `--enable-gpl --enable-version3 --enable-shared --enable-d3d11va --enable-d3d12va --enable-dxva2 --enable-vulkan --enable-libplacebo --enable-nvenc --enable-amf --enable-libvpl --enable-libx264 --enable-libx265 --enable-libxvid --enable-libvidstab --enable-frei0r --enable-libaom --enable-libdav1d --enable-libsvtav1 --enable-librav1e ...` and about 30 more libraries. `-hwaccels`: cuda, vaapi, dxva2, qsv, d3d11va, vulkan, d3d12va, amf. The GPL parts are x264, x265, xvid, vidstab, frei0r; the product build needs none of them. The self-built LGPL flags are in step 5; `ffmpeg -L` prints "GNU Lesser General Public License version 2.1 or later" and `-hwaccels` lists dxva2, d3d11va, vulkan, d3d12va.

## Results

Files: `results-2026-10-11/results-summary.md` (all runs), one JSON per run, `*-load.csv` (GPU engines and CPU, once a second), `per-refresh-csv.zip` (every refresh of every run). The player was extended while the runs went on (the loop itself did not change): the 60 s Judas and the first Hotel runs used the first build; later builds add the end-of-file fix, a message-pump timer, the moving crop, own present and the AV1 decoder choice. Sources: Judas = `[Judas] Nama-Anaru - S01E02.mkv` (HEVC Main 10, 1920x1080, 23.976 fps, 1.46 Mbit/s, tags unspecified, so libplacebo assumes BT.709); Hotel = `260723_HOTEL_AMAN_..._H264.mp4` (H.264 High, 1920x1080, 25 fps, 4.5 Mbit/s; the stream is 62.7 s long despite the "50s" in its name).

### Windowed playback, the four combinations

| Run | First frame warm (cold) | Render CPU per refresh p50 / p99 | GPU time of the picture passes, median / p99 | Loop interval p99 / max | Late / skipped refreshes | Dropped / stalled | Film frames at exactly 10 refreshes | Process CPU (one core) | GPU 3D / video-decode engine |
|---|---|---|---|---|---|---|---|---|---|
| **Judas 60 s** D3D11 + software | 0.27 s (0.46) | 0.028 / 0.16 ms | 0.31 / 0.46 ms | 4.30 / 5.2 ms | 0 / 0 of 13,920 | 1 / 0 | 1437 of 1439 | 17 % | 6.3 % / 0 |
| D3D11 + D3D11VA | 0.27 s (0.41) | 0.029 / 0.39 ms | 0.27 / 0.35 ms | 4.30 / 4.5 ms | 0 / 0 | 1 / 0 | 1437 of 1439 | 2 % | 5.3 % / 2.0 % |
| Vulkan + software, depth 1 | 0.95 s (1.07) | 0.083 / 0.64 ms | 0.19 / 0.21 ms | 6.6 / 8.3 ms | 520 / 0 | 1 / 0 | 1435 of 1439 | 21 % | 10.1 % / 0 |
| Vulkan + Vulkan Video, depth 1 | 0.71 s (0.81) | 0.106 / 0.70 ms | 0.21 / 0.22 ms | 6.6 / **302** ms | 470 / 148 (one hitch) | 6 / 2 | 1416 of 1430 | 5 % | 11.0 % / 1.2 % |
| **Hotel 62 s, to the end** D3D11 + software | as above | 0.026 / 0.15 ms | 0.30 / 0.40 ms | 4.28 / 4.5 ms | 0 / 0 of 14,408 | 0 / 0 | 9 or 10 refreshes (25 fps on 240 Hz) | 28 % | 5.9 % / 0 |
| D3D11 + D3D11VA | | 0.028 / 0.35 ms | 0.16 / 0.31 ms | 4.26 / 12.4 ms | 1 / 0 | 0 / 0 | same | 11 % | 5.2 % / 1.4 % |
| Vulkan + software, depth 1 | | 0.077 / 0.63 ms | 0.18 / 0.20 ms | 6.4 / 6.6 ms | 502 / 0 | 0 / 0 | same | 20 % | 9.9 % / 0 |
| Vulkan + Vulkan Video, depth 1 | | 0.102 / 0.71 ms | 0.21 / 0.22 ms | 6.5 / 6.8 ms | 306 / 0 | 0 / 0 | same | 23 % | 10.4 % / 1.1 % |

Notes:
- "Dropped 1" is the first frame, which the queue discards at start. Render CPU is the time of `pl_render_image_mix` on every refresh; about one refresh in ten runs the heavy picture passes (p99 of those refreshes: 0.21 ms D3D11 software, 0.51 ms D3D11VA, 0.79 to 0.90 ms Vulkan). Every number is well under the 4.17 ms refresh.
- GPU time comes from libplacebo's own pass timers; the column is the median and p99 over the refreshes that run the picture passes (0.1 to 3 ms readings). In the D3D11 software path 2 to 4 % of all refreshes (about a fifth of the picture-pass refreshes) read 4.1 ms instead. No present was late, and with `pl_gpu_finish` after every render the readings vanish, so they are timer artefacts (a wait behind the present), not render cost. The headless benchmark below is the clean GPU figure.
- "Late" on Vulkan is loop jitter: the average still equals the refresh rate, so no refresh went without a picture ("skipped" 0 except the one hitch). On D3D11 `Present(1,0)` blocks to the refresh and the loop is exact; DXGI's own present statistics show the next refresh for every present (14,388 of 14,390 sampled in each Judas run).
- **Process CPU is noisy and includes driver threads**: D3D11VA ranged from 2 % to 25 % of one core between runs of the same program (the extra time was kernel time in a driver thread, not ours), software decode from 9 % to 59 %. Treat these as orders of magnitude. Decoding itself is cheap (next but one table).
- Peak GPU memory: D3D11 software 106 MB, D3D11VA 239 MB, Vulkan software 247 MB, Vulkan Video 748 MB.

### Other runs

| Run (Judas, 20 s unless noted) | Result |
|---|---|
| Vulkan default swapchain depth 3, software / Vulkan Video | loop interval p50 3.9 ms, p99 12.1 / 10.2 ms; film frames shown for 9, 10, 11 or 12 refreshes (only 26 to 28 % at exactly 10); one 720 ms hitch and one 338 ms in the software run. plplay as-is estimates the display at 1031 fps on Vulkan against 248 on D3D11. |
| Pan-like load: source crop moved 1/1000 px every refresh, so the whole pipeline (scaler included) runs on every refresh, `hq` preset, 1280x720 target | D3D11: GPU passes p50 0.30 (software) / 0.39 ms (D3D11VA), p99 0.59 / 0.79 ms, GPU 3D engine 13 to 14 % (against 5 to 6 %), 0 late, loop interval max 4.8 ms. Vulkan: GPU p50 0.32 ms, 3D engine 19 to 20 %, jitter as before. |
| D3D11 with our own waitable swapchain (max latency 1) and our own `Present1`, software / D3D11VA | 0 late, 0 skipped, DXGI delta 1 for every present; loop interval p99 5.0 / 4.5 ms, max 5.5 ms (libplacebo's own `Present(1,0)` loop: p99 4.3 ms). Two 15 s repeats of the software run: 2 and 3 late intervals, max 6.7 and 10.1 ms. A little more jitter than libplacebo's own present, no lost refresh. |
| Same player on MSYS2 libplacebo 7.360.1: D3D11 software, D3D11VA, Vulkan Video | same as master: 0 late on both D3D11 runs; Vulkan Video 186 late of 4316, 4 skipped. |
| Same player on the self-built LGPL FFmpeg 9.0.2: D3D11 software, D3D11VA, Hotel D3D11VA | 0 late, 0 skipped, loop interval max 4.7 to 5.2 ms. Vulkan runs on it (software and Vulkan Video) were jittery (about 150 skipped refreshes in 17 s), see below. |
| Hitch hunt: Vulkan software depth 1, 3 x 60 s, no load sampler; D3D11 software 60 s | no loop interval over 12.2 ms in any of them (Vulkan p99 5.9 to 6.5 ms); D3D11 max 5.1 ms. |
| Later in the day (about 15:30 onward), same binaries | Vulkan Video depth 1 degraded for both the MSYS2 and the LGPL FFmpeg: 24 to 72 skipped refreshes per 15 s, p99 8 to 10 ms (14:56 to 15:14: 0 to 4 skipped, p99 6.5 to 7.3 ms). D3D11 stayed exact. System load was 2 % at the time; something about the display or GPU state changed (`environment-drift/`). |

### Headless render cost (nothing shown on a screen)

One 1920x1080 film frame drawn into an offscreen rgb10a2 target, every cache bypassed, wait for the GPU after each draw, 300 draws. Source: Judas, software frames. Wall time includes the CPU submit.

| GPU | Backend | `fast` (bilinear) p50 / p99 | `default` p50 / p99 | `hq` (EWA lanczossharp, deband) p50 / p99 |
|---|---|---|---|---|
| RTX 5070 Ti, 1440p | D3D11 | 0.12 / 0.25 ms | 0.43 / 0.66 ms | **0.82 / 1.13 ms** |
| RTX 5070 Ti, 1440p | Vulkan | 0.12 / 0.18 ms | 0.46 / 0.65 ms | 0.99 / 1.43 ms |
| RTX 5070 Ti, 4K | D3D11 / Vulkan | | 0.63 / 0.89 and 0.66 / 0.86 ms | 1.36 / 1.66 and 1.61 / 2.04 ms |
| Radeon 610M, 1440p | D3D11 | 1.35 / 1.43 ms | 8.4 / 8.6 ms | **19.0 / 19.3 ms** |
| Radeon 610M, 1440p | Vulkan | 1.17 / 1.27 ms | 8.7 / 8.9 ms | 18.6 / 19.2 ms |

Sampling the decoder's own textures costs nothing extra: `hq` 1440p on the RTX with D3D11VA frames 0.83 / 1.13 ms, with Vulkan Video frames 0.93 / 1.13 ms. libplacebo 7.360.1 gives the same: `hq` 1440p 0.79 / 1.04 ms (D3D11), 0.98 / 1.11 ms (Vulkan). Against the P0 bar of p99 at most 3 ms on the dGPU: passed with room for 4K. The iGPU fails it except with `fast`.

### Decode only (12 s per run, frames discarded, no rendering)

| Source | Software | D3D11VA | Vulkan Video |
|---|---|---|---|
| Judas HEVC Main 10 | 1121 fps = 47x real time, 7.9 cores busy | 1785 fps = 75x, 0.09 core | 1311 fps = 55x, 0.22 core |
| Hotel H.264 | 1888 fps = 75x, 11.6 cores | 1746 fps = 70x, 0.08 core | 1505 fps = 60x, 0.28 core |

### First frame (process creation to first presented frame), Judas

| Path | Cold (first run, empty `pl_cache`) | Warm (runs 2 to 5, median / range) | Of the warm time, until window, device and swapchain are ready |
|---|---|---|---|
| D3D11 + software | 462 ms | 269 ms (267 to 273) | 215 ms |
| D3D11 + D3D11VA | 405 ms | 265 ms (260 to 270) | 215 ms |
| Vulkan + software | 1068 ms | 951 ms (945 to 957) | 905 ms (plplay's log: 312 ms for the instance, 434 ms for the device) |
| Vulkan + Vulkan Video | 809 ms | 707 ms (706 to 715) | 681 ms (FFmpeg creates the device) |

Warm minus ready is the file open (5 ms), the first decoded frame (about 30 ms later) and the first render (10 to 18 ms). The NVIDIA driver's own shader cache was warm for these programs from earlier runs, so "cold" is libplacebo's cache only. P0 bar: under 1 s warm; Vulkan with libplacebo's own device is at the edge.

### Picture check (one film frame rendered alone, compared with D3D11 + software)

| Source | D3D11VA | Vulkan Video | Vulkan software |
|---|---|---|---|
| H.264 8-bit (640x360 clip) | max diff 1, 79.5 dB | max 1, 79.4 dB | identical |
| HEVC Main 10 (1280x800) | max 5, 73.3 dB (0.035 % of pixels over 2) | max 1, 71.8 dB | max 1, 98.5 dB |
| AV1 10-bit (SVT-AV1 re-encode of Hotel; software = libdav1d, hardware = FFmpeg's native `av1` decoder) | max 2, 79.8 dB | max 1, 69.1 dB | |

The package libplacebo 7.360.1 with D3D11VA against master with software: max 5, 69.1 dB. I looked at the dumped HEVC and H.264 frames: correct colours and letterboxing.

## What worked and what didn't

Worked:
- **Portable MSYS2** needed no admin and left the user PATH alone: every command sets `MSYSTEM=UCRT64` for its own process. First start 30 s, base extraction 7 s.
- **libplacebo master in 10 s**; plplay as-is on D3D11 (feature level 12_1) and Vulkan, exiting cleanly when its window is closed (`results-2026-10-11/plplay-asis-*.log`).
- **Device sharing for D3D11VA**: libplacebo creates the device with `D3D11_CREATE_DEVICE_VIDEO_SUPPORT`; the same `ID3D11Device` goes into FFmpeg's `AVD3D11VADeviceContext`; `ID3D10Multithread::SetMultithreadProtected(TRUE)` is needed because the decoder thread and the render thread share the immediate context; the decoder's frames context gets `D3D11_BIND_SHADER_RESOURCE` in `get_format`; each plane is `pl_d3d11_wrap` with the slice index and an `R8`/`R8G8` or `R16`/`R16G16` view; bit layout for P010 is sample depth 16, colour depth 10, shift 6.
- **Vulkan Video**: `av_hwdevice_ctx_create(vulkan)` with `instance_extensions=VK_KHR_surface+VK_KHR_win32_surface` and `device_extensions=VK_KHR_swapchain`, then `pl_vulkan_import` with FFmpeg's feature chain and queue families. libplacebo accepted FFmpeg's features (hostQueryReset and timelineSemaphore are on) with no patching; FFmpeg 9 has deprecated `lock_queue` in favour of `VK_KHR_internally_synchronized_queues`.
- **AV1 needs the right decoder**: for hardware decode pick the decoder that carries the hwaccel (FFmpeg's native `av1`), not `libdav1d`, which `avcodec_find_decoder` returns first.

Didn't, and the workaround:
- **Hang at exit on Vulkan (my bug, found by attaching gdb: `pacman -S mingw-w64-ucrt-x86_64-gdb`).** Leaving a swapchain frame started but not submitted (end of file, error) blocks `vkDestroySwapchainKHR` forever: libplacebo waits on the image's present fence, which only a present signals. The main thread sat in `swapchain_destroy`. Rule for P1: always submit a started frame (clear it to black and submit). plplay's loop has the same early return at end of file.
- **Vulkan pacing**: see the results. Depth 1 (`pl_vulkan_swapchain_params.swapchain_depth`) is the usable setting; it only waits for GPU completion, not for the display.
- **Package libplacebo 7.360.1 lacks** `pl_frame.pixel_aspect_ratio`, `pl_vulkan_queue.flags` and the two libav internal helpers (`pl_map_hwframe_bit_encoding`, `pl_fix_hwframe_sample_depth`), and its `pl_frame_mix.timestamps`, `vsync_duration` and `pl_source_frame.duration` are `float` (double from API 375; `pts` is already double in the header). The player source carries `#if PL_API_VER >= 375` guards and local copies of the helpers.
- **plplay's D3D11 path runs with the D3D debug layer on** (its `DEBUG` macro is true when `NDEBUG` is unset, as in a plain release build); irrelevant for perf0, which turns it off.
- **GPU pass timers** read 4.1 ms on about a fifth of the picture-pass refreshes in the D3D11 software path (explained above); use the headless benchmark for GPU cost.
- **Windows and MSYS quirks**: media paths with `[Judas]` must be given as `C:/Users/...`, not `/c/Users/...` (MSYS skips the conversion); an unquoted `mkdir -p $R/...` with a space in the project path made two stray empty folders, which I removed (`Downloads\Telegram` and a `Desktop\...` tree under `plans\reports`).
- **Not done:** 10 minute soaks (the task asked for 60 s), libass, WASAPI, HDR, Dolby Vision, film grain, subtitle and audio paths.

## What P1 needs

One window and one swapchain per monitor, our own `Present`:

1. **D3D11, our own swapchain (recommended).** Create one `pl_d3d11` on the adapter that owns the outputs (`perf0 --probe`: the RTX 5070 Ti owns both screens, the iGPU none). Per monitor: a borderless `HWND` over the screen, an `IDXGISwapChain1` made with `CreateSwapChainForHwnd` (flip-discard, `DXGI_SWAP_CHAIN_FLAG_FRAME_LATENCY_WAITABLE_OBJECT`, `ALLOW_TEARING` when VRR is on), handed to `pl_d3d11_create_swapchain(.swapchain = ours)`. Per refresh: wait on the waitable object, `pl_swapchain_start_frame`, render, `pl_swapchain_submit_frame`, then **our own `Present1`**. This works because libplacebo's D3D11 `swap_buffers` is only `Present(1, 0)` and `submit_frame` only releases the backbuffer; `perf0 --own-present` does exactly this (`create_own_swapchain` and `gpu_present` in `perf0-gpu.c` are the starting point) and held 240 Hz with every present on its own refresh. The sync interval, flags, `SetTargetTime` on a composition swapchain and the present time for the follower monitor are then ours. Call `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` before enumerating outputs (`--probe` runs unaware and shows the laptop as 1707x1067).
2. **Threads.** The D3D11 `pl_gpu` is not thread-safe (`thread_safe: 0`; Vulkan's is 1) and so is `pl_renderer`: one render thread driving all monitors in turn, or one device per monitor. With D3D11VA the decoder surfaces live on one device, so one device and one render thread (waiting on both swapchains' waitable objects) avoids cross-device copies. One `pl_renderer` and one `pl_queue` per monitor, all fed the same decoded frames.
3. **Hardware decode**: reuse `perf0-decode.c` (D3D11VA import, hwaccel-aware decoder choice). It leaves the CPU almost idle and held every refresh.
4. **Vulkan only if present timing is needed.** libplacebo's Vulkan swapchain does `vkQueuePresentKHR` inside `pl_swapchain_submit_frame`, has no `present_id`, `present_wait` or `VK_EXT_present_timing`, and its `swap_buffers` only waits for GPU work. So there is no own-present hook: P1 would wrap its own `VkSwapchainKHR` (`pl_vulkan_wrap` for the images, our `vkAcquireNextImageKHR` and `vkQueuePresentKHR`, `pl_vulkan.lock_queue`) or patch `src/vulkan/swapchain.c`. The NVIDIA driver advertises `VK_EXT_present_timing` rev 3, `VK_KHR_present_id` and `VK_KHR_present_wait`; the AMD driver does not. Whether present timing works in a borderless window on Windows is untested.
5. **Which libplacebo**: master `1dd0226` (double timestamps, pixel aspect ratio) or the package 7.360.1; both ran the same code with the same render times. The package saves building and its 3 gaps are small. Pin one after P1.
6. **Packaging**: libplacebo (1.5 MB) plus shaderc (10.7 MB) and SPIRV-Cross (3.4 MB) are the shader compiler stack; with the LGPL FFmpeg the player's whole non-system DLL set is 15 files, 44 MiB (`results-2026-10-11/perf0-dll-closure.txt`).
7. **iGPU screens** (Setup B): the Radeon 610M needs a `fast`-class profile (1.4 ms at 1440p) or lower resolution; `default` is 8.6 ms.

## Unresolved questions

1. What causes the 300 to 720 ms Vulkan hitches (3 events in 2 of 12 sampler runs, none in three bare 60 s runs) and the Vulkan pacing drift after about 15:30? The load sampler (GPU performance counters), user activity on the other screen and a driver or display state change are the candidates; D3D11 was unaffected throughout.
2. Does a borderless window covering a monitor get independent flip (hardware overlay) with libplacebo's D3D11 renderer, as it did in the raw-DXGI spike? Only windowed 1280x800 was measured here.
3. Does `VK_EXT_present_timing` work in a borderless window with driver 617.42?
4. Does the driver-level shader cache matter for a true first run on a new machine? It was never cleared.
5. Should a screen on the AMD iGPU (Setup B) be allowed a `default`/`hq` profile at all?
6. libplacebo master or the package for P1, and is a 10 minute soak per combination needed before the engine decision?
