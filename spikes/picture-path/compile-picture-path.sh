#!/bin/bash
# Compile the picture-path harness inside the portable MSYS2 UCRT64 shell (run it with MSYSTEM=UCRT64, see compile.ps1).
#   bash compile-picture-path.sh [TARGET]
#
#   TARGET  gpu-probe          bin/gpu-probe.exe: lists adapters, creates every backend x adapter offscreen
#           harness (default)  bin/picture-path.exe: the offscreen harness (render, bench, selftest)
#           geometry-test      bin/geometry-test.exe: the room-model and map code alone (no GPU, no libplacebo)
#
# libplacebo master (API 375) and its dependencies come from the P0 prefix (an MSYS2 path; $HOME differs between
# shells, so it is explicit). Nothing is installed anywhere; the paths contain a space, so everything is an array.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-harness}"
P0_PREFIX="${P0_PREFIX:-/home/tamla/p0/prefix}"
mkdir -p "$HERE/bin"
CFLAGS=(-std=gnu11 -O2 -g0 -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers -Wno-override-init)
GEOM=("$HERE/src/pp-json.c" "$HERE/src/pp-room.c" "$HERE/src/pp-geometry.c" "$HERE/src/pp-scenario.c")

case "$TARGET" in
gpu-probe)
    export PKG_CONFIG_PATH="$P0_PREFIX/libplacebo-master/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    # shellcheck disable=SC2046
    gcc "${CFLAGS[@]}" -I "$HERE/src" -o "$HERE/bin/gpu-probe.exe" "$HERE/src/gpu-probe.c" "$HERE/src/pp-gpu.c" \
        $(pkg-config --cflags libplacebo) $(pkg-config --libs libplacebo) -ld3d11 -ldxgi -ldxguid -lvulkan-1 -lm
    echo "compiled $HERE/bin/gpu-probe.exe"
    ;;
download-test)
    export PKG_CONFIG_PATH="$P0_PREFIX/libplacebo-master/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    # shellcheck disable=SC2046
    gcc "${CFLAGS[@]}" -I "$HERE/src" -o "$HERE/bin/download-test.exe" "$HERE/src/download-test.c" "$HERE/src/pp-gpu.c"         $(pkg-config --cflags libplacebo) $(pkg-config --libs libplacebo) -ld3d11 -ldxgi -ldxguid -lvulkan-1 -lm
    echo "compiled $HERE/bin/download-test.exe"
    ;;
geometry-test)
    gcc "${CFLAGS[@]}" -I "$HERE/src" -o "$HERE/bin/geometry-test.exe" "$HERE/src/geometry-test.c" "${GEOM[@]}" -lm
    echo "compiled $HERE/bin/geometry-test.exe"
    ;;
harness)
    export PKG_CONFIG_PATH="$P0_PREFIX/libplacebo-master/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    # shellcheck disable=SC2046  # pkg-config output is a list of flags
    gcc "${CFLAGS[@]}" -I "$HERE/src" -o "$HERE/bin/picture-path.exe" \
        "$HERE/src/pp-main.c" "$HERE/src/pp-gpu.c" "$HERE/src/pp-source.c" "$HERE/src/pp-timing.c" \
        "$HERE/src/pp-s1.c" "$HERE/src/pp-s2.c" "$HERE/src/pp-output.c" "$HERE/src/pp-run.c" "$HERE/src/pp-s1-hook.c" "${GEOM[@]}" \
        $(pkg-config --cflags libplacebo) $(pkg-config --libs libplacebo) \
        -ld3d11 -ldxgi -ldxguid -lvulkan-1 -lm
    echo "compiled $HERE/bin/picture-path.exe against libplacebo $(pkg-config --modversion libplacebo)"
    ;;
*)
    echo "unknown target $TARGET (gpu-probe | download-test | harness | geometry-test)" >&2
    exit 2
    ;;
esac
