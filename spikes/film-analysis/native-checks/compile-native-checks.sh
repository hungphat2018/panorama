#!/bin/bash
# Compiles the small C programs of this folder inside the portable MSYS2 UCRT64 shell (started by
# compile-native-checks.ps1, which sets MSYSTEM=UCRT64), against the same LGPL FFmpeg the performer uses.
# Output: bin/frame-times.exe and bin/dovi-probe.exe (git-ignored).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
P0_PREFIX="${P0_PREFIX:-/home/tamla/p0/prefix}"
mkdir -p "$HERE/bin"
export PKG_CONFIG_PATH="$P0_PREFIX/ffmpeg-lgpl/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
PKGS="libavcodec libavformat libavutil"
for prog in frame-times dovi-probe; do
    # shellcheck disable=SC2046  # pkg-config output is a list of flags
    gcc -std=gnu11 -O2 -g0 -Wall -Wextra -o "$HERE/bin/$prog.exe" "$HERE/$prog.c" \
        $(pkg-config --cflags $PKGS) $(pkg-config --libs $PKGS) -lm
    echo "compiled $HERE/bin/$prog.exe against libavcodec $(pkg-config --modversion libavcodec)"
done
