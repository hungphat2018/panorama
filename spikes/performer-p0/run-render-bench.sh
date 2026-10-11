#!/bin/bash
# Headless render benchmark (no window, nothing is shown on any screen):
# one 1920x1080 film frame drawn into an offscreen target, every cache bypassed, GPU awaited after
# each draw. Run inside the MSYS2 UCRT64 shell:   bash run-render-bench.sh [master|packaged]
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
FLAVOR="${1:-master}"
RES="$HERE/results-2026-10-11/render-bench"
EXE="$HERE/bin/perf0-$FLAVOR.exe"
[ "$FLAVOR" = master ] && export PATH="$HOME/p0/prefix/libplacebo-master/bin:$PATH"
mkdir -p "$RES"
JUDAS="C:/Users/tamla/Downloads/[Judas] Nama-Anaru - S01E02.mkv"

bench() {   # id backend decode adapter preset target
    local id=$1 backend=$2 decode=$3 adapter=$4 preset=$5 target=$6
    "$EXE" --file "$JUDAS" --backend "$backend" --decode "$decode" --adapter "$adapter" --preset "$preset" \
           --bench-render "$target" --bench-iters 300 --id "$id" --log-level 3 \
           > "$RES/$id.json" 2> "$RES/$id.stderr.log"
    echo "$id rc=$?"
}

for backend in d3d11 vulkan; do
    for adapter in NVIDIA AMD; do
        for preset in fast default hq; do
            bench "$backend-$adapter-$preset-sw-2560x1440" $backend sw $adapter $preset 2560x1440
        done
    done
done
# 4K target on the discrete GPU
for backend in d3d11 vulkan; do
    for preset in default hq; do
        bench "$backend-NVIDIA-$preset-sw-3840x2160" $backend sw NVIDIA $preset 3840x2160
    done
done
# hardware-decoded source frames (sampled straight from the decoder's textures)
bench "d3d11-NVIDIA-hq-d3d11va-2560x1440" d3d11 d3d11va NVIDIA hq 2560x1440
bench "vulkan-NVIDIA-hq-vulkan-2560x1440" vulkan vulkan NVIDIA hq 2560x1440
