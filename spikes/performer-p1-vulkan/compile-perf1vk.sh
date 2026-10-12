#!/bin/bash
# Compile the Vulkan spike programs inside the portable MSYS2 UCRT64 shell (run it with MSYSTEM=UCRT64, see compile.ps1).
#   bash compile-perf1vk.sh [lgpl|master] [all|caps|probe|perf1vk|jsontest]
#     lgpl    libplacebo master + the self-built LGPL FFmpeg 9.0.2 (default)
#     master  libplacebo master + MSYS2's FFmpeg 9.0.2 (GPL build)
# Programs (bin/):
#   vk-caps.exe          headless capability probe: layers, GPUs, extensions, features (no window)
#   vk-timing-probe.exe  windowed probe of VK_EXT_present_timing / present_id2 / present_wait2 on a Win32 surface
#   perf1vk-<flavor>.exe two-window presenter on libplacebo's Vulkan backend (derived from perf1)
#   test-own-swap-json.exe, test-probe-json.exe  unit tests of the log blocks (target "jsontest"; run by tests/*.py)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FLAVOR="${1:-lgpl}"
WHAT="${2:-all}"
# Where P0 installed libplacebo master and the LGPL FFmpeg (an MSYS2 path; $HOME differs between shells, so it is explicit).
P0_PREFIX="${P0_PREFIX:-/home/tamla/p0/prefix}"

export PKG_CONFIG_PATH="$P0_PREFIX/libplacebo-master/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
if [ "$FLAVOR" = lgpl ]; then
    export PKG_CONFIG_PATH="$P0_PREFIX/ffmpeg-lgpl/lib/pkgconfig:$PKG_CONFIG_PATH"
fi

mkdir -p "$HERE/bin"
# Arrays, not strings: the project path has a space in it.
CFLAGS=(-std=gnu11 -O2 -g0 -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers -Wno-override-init)
# -I: row-timing.h and scanline-clock.h are shared with the per-monitor presenter spike (header-only, C11).
SHARED=(-I "$HERE/../per-monitor-presenter" -I "$HERE/src")
PKGS="libplacebo libavcodec libavformat libavutil"

if [ "$WHAT" = all ] || [ "$WHAT" = caps ]; then
    gcc "${CFLAGS[@]}" "${SHARED[@]}" -o "$HERE/bin/vk-caps.exe" "$HERE/src/vk-caps.c" \
        -lvulkan-1 -ldxgi -ldxguid -luser32 -lpsapi
    echo "compiled $HERE/bin/vk-caps.exe"
fi

if [ "$WHAT" = all ] || [ "$WHAT" = probe ]; then
    if [ -f "$HERE/src/vk-timing-probe.c" ]; then
        gcc "${CFLAGS[@]}" "${SHARED[@]}" -o "$HERE/bin/vk-timing-probe.exe" "$HERE/src/vk-timing-probe.c" \
            -lvulkan-1 -ldxgi -ldxguid -luser32 -lwinmm -lavrt -lpsapi
        echo "compiled $HERE/bin/vk-timing-probe.exe"
    fi
fi

if [ "$WHAT" = all ] || [ "$WHAT" = jsontest ]; then
    gcc "${CFLAGS[@]}" "${SHARED[@]}" -o "$HERE/bin/test-own-swap-json.exe" "$HERE/tests/test-own-swap-json.c" \
        $(pkg-config --cflags libplacebo) $(pkg-config --libs libplacebo) -lvulkan-1 -luser32 -lpsapi
    echo "compiled $HERE/bin/test-own-swap-json.exe"
    gcc "${CFLAGS[@]}" "${SHARED[@]}" -o "$HERE/bin/test-probe-json.exe" "$HERE/tests/test-probe-json.c"         -lvulkan-1 -ldxgi -ldxguid -luser32 -lwinmm -lavrt -lpsapi
    echo "compiled $HERE/bin/test-probe-json.exe"
fi

if [ "$WHAT" = all ] || [ "$WHAT" = perf1vk ]; then
    if [ -f "$HERE/src/perf1vk-main.c" ]; then
        gcc "${CFLAGS[@]}" "${SHARED[@]}" -o "$HERE/bin/perf1vk-$FLAVOR.exe" \
            "$HERE"/src/perf1vk-main.c "$HERE"/src/perf1vk-gpu.c "$HERE"/src/perf1vk-decode.c \
            "$HERE"/src/perf1vk-clock.c "$HERE"/src/perf1vk-stats.c "$HERE"/src/perf1vk-own-swap.c "$HERE"/src/perf1vk-hwdevice.c \
            $(pkg-config --cflags $PKGS) $(pkg-config --libs $PKGS) \
            -lvulkan-1 -ldxgi -ldxguid -lavrt -lwinmm -luser32 -lpsapi
        echo "compiled $HERE/bin/perf1vk-$FLAVOR.exe against libplacebo $(pkg-config --modversion libplacebo), libavcodec $(pkg-config --modversion libavcodec)"
    fi
fi
