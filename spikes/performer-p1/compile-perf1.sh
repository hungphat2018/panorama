#!/bin/bash
# Compile inside the portable MSYS2 UCRT64 shell (run it with MSYSTEM=UCRT64, see compile.ps1).
#   bash compile-perf1.sh [FLAVOR] [PROGRAM] [OUTPUT_NAME]
#
#   FLAVOR   lgpl (default): libplacebo master + the self-built LGPL FFmpeg 9.0.2
#            master:         libplacebo master + MSYS2's FFmpeg 9.0.2 (GPL build)
#   PROGRAM  p3 (default)    bin/perf1-p3-FLAVOR.exe, the performer with cue sheets, the per-shot latch, seeking,
#                            hysteresis / re-aim / last-due picking and the control channel
#            latch-test      bin/latch-sim.exe and bin/cue-selftest.exe, the headless test (no libplacebo, no FFmpeg)
#            audio-test      bin/audio-selftest.exe, the sound's decoder and converter against a ramp clip (FFmpeg only)
#            picture-test    bin/picture-selftest.exe, the picture path's CPU maths (room, map, slide, blend, colour; no GPU)
#   OUTPUT_NAME  p3 only: another file name inside bin/ (default perf1-p3-FLAVOR.exe)
#
# bin/perf1-lgpl.exe, the first P1 binary, is never written by this script: its sources are frozen in
# src-perf1-frozen/ and the name is refused below. (Paths contain a space: everything is an array or quoted.)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
FLAVOR="${1:-lgpl}"
PROGRAM="${2:-p3}"
# Where P0 installed libplacebo master and the LGPL FFmpeg (an MSYS2 path; $HOME differs between shells, so it is explicit).
P0_PREFIX="${P0_PREFIX:-/home/tamla/p0/prefix}"
mkdir -p "$HERE/bin"
CFLAGS=(-std=gnu11 -O2 -g0 -Wall -Wextra -Wno-unused-parameter -Wno-missing-field-initializers -Wno-override-init)
# row-timing.h and scanline-clock.h are shared with the per-monitor presenter spike (header-only, C11).
SHARED=(-I "$HERE/../per-monitor-presenter")
CORE=("$HERE/src/perf1-json.c" "$HERE/src/perf1-cue-sheet.c" "$HERE/src/perf1-shot-latch.c" "$HERE/src/perf1-frame-index.c"
      "$HERE/src/perf1-engine.c" "$HERE/src/perf1-engine-log.c")

# The sound (perf1-audio.h): own demuxer + decoder, converter, sinks (WASAPI and the --audio-null stand-in), sync, thread, log.
AUDIO_LIB=("$HERE/src/perf1-audio-open.c" "$HERE/src/perf1-audio-decode.c" "$HERE/src/perf1-audio-pipe.c" "$HERE/src/perf1-audio-pipe-feed.c")
AUDIO=("${AUDIO_LIB[@]}" "$HERE/src/perf1-audio-sync.c" "$HERE/src/perf1-audio-null.c" "$HERE/src/perf1-audio-wasapi.c"
       "$HERE/src/perf1-audio.c" "$HERE/src/perf1-audio-play.c" "$HERE/src/perf1-audio-log.c")

# The picture path (perf1-picture.h): room, shot plan, frame preparation, the one resample, then surround, colour and
# subtitles (the finish passes ported from spikes/picture-path/finish). PICTURE_MATH is the part without a GPU or libass.
PICTURE_MATH=("$HERE/src/perf1-room.c" "$HERE/src/perf1-picture-map.c" "$HERE/src/perf1-picture-plan.c"
              "$HERE/src/perf1-finish-color-math.c" "$HERE/src/perf1-finish-surround-clock.c" "$HERE/src/perf1-finish-subs-time.c")
PICTURE=("${PICTURE_MATH[@]}" "$HERE/src/perf1-picture-shader.c" "$HERE/src/perf1-picture-pass1.c"
         "$HERE/src/perf1-picture-prepare.c" "$HERE/src/perf1-picture-look.c" "$HERE/src/perf1-picture-args.c"
         "$HERE/src/perf1-picture-setup.c" "$HERE/src/perf1-picture-shot.c" "$HERE/src/perf1-picture-timing.c"
         "$HERE/src/perf1-picture-render.c" "$HERE/src/perf1-picture-dump.c" "$HERE/src/perf1-subs-load.c"
         "$HERE/src/perf1-finish-common.c" "$HERE/src/perf1-finish-surround.c" "$HERE/src/perf1-finish-color.c"
         "$HERE/src/perf1-finish-subs.c")

case "$PROGRAM" in
audio-test)
    # bin/audio-selftest.exe: the sound's decoder and converter against a clip with a known ramp (needs FFmpeg's DLLs on PATH to run)
    export PKG_CONFIG_PATH="$P0_PREFIX/ffmpeg-lgpl/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    ATEST_PKGS="libavcodec libavformat libavutil libswresample"
    # shellcheck disable=SC2046
    gcc "${CFLAGS[@]}" -o "$HERE/bin/audio-selftest.exe" "$HERE/audio-selftest.c" "$HERE/src/perf1-audio-sync.c" "${AUDIO_LIB[@]}" \
        $(pkg-config --cflags $ATEST_PKGS) $(pkg-config --libs $ATEST_PKGS) -lm
    echo "compiled $HERE/bin/audio-selftest.exe"
    ;;
picture-test)
    # bin/picture-selftest.exe: the picture path's CPU maths (room, map, slide, blend weights, colour, surround rule); no GPU
    export PKG_CONFIG_PATH="$P0_PREFIX/libplacebo-master/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    # shellcheck disable=SC2046
    gcc "${CFLAGS[@]}" "${SHARED[@]}" -o "$HERE/bin/picture-selftest.exe" "$HERE/picture-selftest.c" \
        "$HERE/src/perf1-json.c" "$HERE/src/perf1-cue-sheet.c" "$HERE/src/perf1-picture-look.c" "${PICTURE_MATH[@]}" \
        $(pkg-config --cflags libplacebo) -lm
    echo "compiled $HERE/bin/picture-selftest.exe"
    ;;
latch-test)
    # The engine is compiled with ENGINE_FAULTS here only: the deliberate defects of the negative controls.
    gcc "${CFLAGS[@]}" "${SHARED[@]}" -DENGINE_FAULTS -o "$HERE/bin/latch-sim.exe" "$HERE/latch-sim.c" "${CORE[@]}" -lm -luser32
    gcc "${CFLAGS[@]}" "${SHARED[@]}" -o "$HERE/bin/cue-selftest.exe" "$HERE/cue-selftest.c" \
        "$HERE/src/perf1-json.c" "$HERE/src/perf1-cue-sheet.c" "$HERE/src/perf1-shot-latch.c" "$HERE/src/perf1-layout.c" -lm
    echo "compiled $HERE/bin/latch-sim.exe and $HERE/bin/cue-selftest.exe"
    ;;
p3)
    OUT_NAME="${3:-perf1-p3-$FLAVOR.exe}"
    if [ "$OUT_NAME" = "perf1-lgpl.exe" ]; then
        echo "refusing to write bin/perf1-lgpl.exe: it is the binary the controller runs full screen" >&2
        exit 2
    fi
    export PKG_CONFIG_PATH="$P0_PREFIX/libplacebo-master/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
    if [ "$FLAVOR" = lgpl ]; then
        export PKG_CONFIG_PATH="$P0_PREFIX/ffmpeg-lgpl/lib/pkgconfig:$PKG_CONFIG_PATH"
    fi
    PKGS="libplacebo libavcodec libavformat libavutil libswresample libass"
    # shellcheck disable=SC2046  # pkg-config output is a list of flags
    gcc "${CFLAGS[@]}" "${SHARED[@]}" -o "$HERE/bin/$OUT_NAME" \
        "$HERE/src/perf1-main.c" "$HERE/src/perf1-args.c" "$HERE/src/perf1-display.c" "$HERE/src/perf1-render.c" \
        "$HERE/src/perf1-headless.c" "$HERE/src/perf1-control.c" "$HERE/src/perf1-layout.c" "$HERE/src/perf1-log.c" \
        "$HERE/src/perf1-decode.c" "$HERE/src/perf1-clock.c" "$HERE/src/perf1-stats.c" "${CORE[@]}" "${AUDIO[@]}" "${PICTURE[@]}" \
        $(pkg-config --cflags $PKGS) $(pkg-config --libs $PKGS) \
        -ld3d11 -ldxgi -ldxguid -lavrt -lwinmm -luser32 -lole32 -lm
    echo "compiled $HERE/bin/$OUT_NAME against libplacebo $(pkg-config --modversion libplacebo), libavcodec $(pkg-config --modversion libavcodec)"
    ;;
*)
    echo "unknown program $PROGRAM (p3 | latch-test | audio-test | picture-test)" >&2
    exit 2
    ;;
esac
