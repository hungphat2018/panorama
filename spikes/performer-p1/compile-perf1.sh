#!/bin/bash
# Compile perf1 inside the portable MSYS2 UCRT64 shell (run it with MSYSTEM=UCRT64, see compile.ps1).
#   bash compile-perf1.sh lgpl      # libplacebo master + the self-built LGPL FFmpeg 9.0.2 (default)
#   bash compile-perf1.sh master    # libplacebo master + MSYS2's FFmpeg 9.0.2 (GPL build)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FLAVOR="${1:-lgpl}"
# Where P0 installed libplacebo master and the LGPL FFmpeg (an MSYS2 path; $HOME differs between shells, so it is explicit).
P0_PREFIX="${P0_PREFIX:-/home/tamla/p0/prefix}"

export PKG_CONFIG_PATH="$P0_PREFIX/libplacebo-master/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
if [ "$FLAVOR" = lgpl ]; then
    export PKG_CONFIG_PATH="$P0_PREFIX/ffmpeg-lgpl/lib/pkgconfig:$PKG_CONFIG_PATH"
fi

PKGS="libplacebo libavcodec libavformat libavutil"
mkdir -p "$HERE/bin"
gcc -std=gnu11 -O2 -g0 -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers -Wno-override-init \
    -o "$HERE/bin/perf1-$FLAVOR.exe" \
    "$HERE"/src/perf1-main.c "$HERE"/src/perf1-decode.c "$HERE"/src/perf1-clock.c "$HERE"/src/perf1-stats.c \
    $(pkg-config --cflags $PKGS) $(pkg-config --libs $PKGS) \
    -ld3d11 -ldxgi -ldxguid -lavrt -lwinmm -luser32
echo "built $HERE/bin/perf1-$FLAVOR.exe against libplacebo $(pkg-config --modversion libplacebo), libavcodec $(pkg-config --modversion libavcodec)"
