#!/bin/bash
# Compile perf0 inside the portable MSYS2 UCRT64 shell.
#   bash compile-perf0.sh master      # libplacebo master built from source (API 375, ~/p0/prefix/libplacebo-master) + MSYS2's FFmpeg 9.0.2 (GPL build)
#   bash compile-perf0.sh packaged    # MSYS2 package mingw-w64-ucrt-x86_64-libplacebo 7.360.1 + MSYS2's FFmpeg
#   bash compile-perf0.sh lgpl        # libplacebo master + FFmpeg 9.0.2 built from source with --disable-gpl (~/p0/prefix/ffmpeg-lgpl)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FLAVOR="${1:-master}"

case "$FLAVOR" in
master|lgpl)
    export PKG_CONFIG_PATH="$HOME/p0/prefix/libplacebo-master/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    ;;
esac
if [ "$FLAVOR" = lgpl ]; then
    export PKG_CONFIG_PATH="$HOME/p0/prefix/ffmpeg-lgpl/lib/pkgconfig:$PKG_CONFIG_PATH"
fi

PKGS="libplacebo glfw3 libavcodec libavformat libavutil"
mkdir -p "$HERE/bin"
gcc -std=gnu11 -O2 -g0 -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers \
    -o "$HERE/bin/perf0-$FLAVOR.exe" \
    "$HERE"/src/perf0-main.c "$HERE"/src/perf0-gpu.c "$HERE"/src/perf0-decode.c "$HERE"/src/perf0-stats.c \
    $(pkg-config --cflags $PKGS) $(pkg-config --libs $PKGS) \
    -ld3d11 -ldxgi -ldxguid -lpsapi -lwinmm
echo "built $HERE/bin/perf0-$FLAVOR.exe against libplacebo $(pkg-config --modversion libplacebo), libavcodec $(pkg-config --modversion libavcodec)"
