#!/bin/bash
# Compile the finish passes (surround, finish, subtitles) and their test driver inside the portable MSYS2 UCRT64 shell
# (run it with MSYSTEM=UCRT64, see compile.ps1).
#   bash compile-finish.sh [TARGET]
#
#   TARGET  test (default)  bin/finish-test.exe: offscreen tests and measurements (needs libplacebo, libass, zlib)
#           unit            bin/finish-unit.exe: the CPU-only checks (temporal rule, colour maths, libass time)
#           all             both of the above
#
# The by-eye checks (near-black ramp, grey bars, surround at cuts) moved to the thin prototype (M1 phase 8), so this
# spike has no viewer.
#
# libplacebo master (API 375) comes from the P0 prefix; libass 0.17.x, zlib and the font libraries from MSYS2 UCRT64.
# Nothing is installed anywhere. The project path contains a space, so every list below is a bash array.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
TARGET="${1:-test}"
P0_PREFIX="${P0_PREFIX:-/home/tamla/p0/prefix}"
mkdir -p "$HERE/bin"
export PKG_CONFIG_PATH="$P0_PREFIX/libplacebo-master/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
CFLAGS=(-std=gnu11 -O2 -g0 -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers -Wno-override-init)

# The passes: the part another harness links (src/finish-passes.h is their whole API).
PASSES=("$HERE/src/finish-common.c" "$HERE/src/finish-surround.c" "$HERE/src/finish-surround-clock.c"
        "$HERE/src/finish-color.c" "$HERE/src/finish-color-math.c" "$HERE/src/finish-subs.c" "$HERE/src/finish-subs-time.c")
DEVICE=("$HERE/src/finish-device.c")
TESTS=("$HERE/src/test-main.c" "$HERE/src/test-util.c" "$HERE/src/test-canvas.c" "$HERE/src/test-smoke.c"
       "$HERE/src/test-surround.c" "$HERE/src/test-surround-time.c" "$HERE/src/test-color.c" "$HERE/src/test-subs.c" "$HERE/src/test-bench.c")

make_test() {
    # shellcheck disable=SC2046  # pkg-config output is a list of flags
    gcc "${CFLAGS[@]}" -I "$HERE/src" -o "$HERE/bin/finish-test.exe" "${TESTS[@]}" "${DEVICE[@]}" "${PASSES[@]}" \
        $(pkg-config --cflags libplacebo libass zlib) $(pkg-config --libs libplacebo libass zlib) \
        -ld3d11 -ldxgi -ldxguid -lvulkan-1 -lm
    echo "compiled $HERE/bin/finish-test.exe against libplacebo $(pkg-config --modversion libplacebo), libass $(pkg-config --modversion libass)"
}

make_unit() {
    # CPU only: no GPU is touched; libplacebo's headers are read but nothing is linked.
    # shellcheck disable=SC2046
    gcc "${CFLAGS[@]}" -I "$HERE/src" -o "$HERE/bin/finish-unit.exe" "$HERE/src/test-unit.c" \
        "$HERE/src/finish-surround-clock.c" "$HERE/src/finish-color-math.c" "$HERE/src/finish-subs-time.c" \
        $(pkg-config --cflags libplacebo) -lm
    echo "compiled $HERE/bin/finish-unit.exe"
}

case "$TARGET" in
test) make_test ;;
unit) make_unit ;;
all) make_test; make_unit ;;
*)
    echo "unknown target $TARGET (test | unit | all)" >&2
    exit 2
    ;;
esac
