#!/bin/bash
# Rebuilds the FROZEN sources of bin/perf1-lgpl.exe (the first P1 binary, as it was on 2026-10-11 19:14) into
# src-perf1-frozen/bin/perf1-frozen-lgpl.exe. It never touches ../bin. Same MSYS2 UCRT64 shell as ../compile.ps1.
#   bash compile-perf1.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
P0_PREFIX="${P0_PREFIX:-/home/tamla/p0/prefix}"
export PKG_CONFIG_PATH="$P0_PREFIX/libplacebo-master/lib/pkgconfig:$P0_PREFIX/ffmpeg-lgpl/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
PKGS="libplacebo libavcodec libavformat libavutil"
mkdir -p "$HERE/bin"
# shellcheck disable=SC2046
gcc -std=gnu11 -O2 -g0 -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers -Wno-override-init     -I "$HERE/../../per-monitor-presenter" -o "$HERE/bin/perf1-frozen-lgpl.exe"     "$HERE/perf1-main.c" "$HERE/perf1-decode.c" "$HERE/perf1-clock.c" "$HERE/perf1-stats.c"     $(pkg-config --cflags $PKGS) $(pkg-config --libs $PKGS)     -ld3d11 -ldxgi -ldxguid -lavrt -lwinmm -luser32
echo "compiled $HERE/bin/perf1-frozen-lgpl.exe"
