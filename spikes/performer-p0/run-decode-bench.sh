#!/bin/bash
# Decode-only throughput (no rendering, hidden window, nothing is shown on any screen):
# how many times faster than real time each decoder runs, and what it costs in CPU.
# Run inside the MSYS2 UCRT64 shell:   bash run-decode-bench.sh [master|packaged]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
FLAVOR="${1:-master}"
RES="$HERE/results-2026-10-11/decode-bench"
EXE="$HERE/bin/perf0-$FLAVOR.exe"
[ "$FLAVOR" = master ] && export PATH="$HOME/p0/prefix/libplacebo-master/bin:$PATH"
mkdir -p "$RES"
JUDAS="C:/Users/tamla/Downloads/[Judas] Nama-Anaru - S01E02.mkv"
HOTEL="C:/Users/tamla/Downloads/260723_HOTEL_AMAN_43_V3_MAIN_50s_MASTER_16x9_VA_H264.mp4"

bench() {   # id file backend decode
    local id=$1 file=$2 backend=$3 decode=$4
    "$EXE" --file "$file" --backend "$backend" --decode "$decode" --adapter NVIDIA --bench-decode --seconds 12 \
           --id "$id" --log-level 3 > "$RES/$id.json" 2> "$RES/$id.stderr.log"
    echo "$id rc=$?"
}

for name in judas hotel; do
    file="$JUDAS"; [ "$name" = hotel ] && file="$HOTEL"
    bench "$name-sw"       "$file" d3d11  sw
    bench "$name-d3d11va"  "$file" d3d11  d3d11va
    bench "$name-vulkan"   "$file" vulkan vulkan
done
