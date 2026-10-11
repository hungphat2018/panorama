#!/bin/bash
# Windowed playback measurements (a normal ~1280x800 window on the laptop panel, never fullscreen).
# Each run starts the PowerShell load sampler first, then the player. Run inside the MSYS2 UCRT64
# shell:   bash run-matrix.sh [master|packaged|lgpl] [group...]   (flavor = which bin/perf0-<flavor>.exe)
# Groups: judas hotel (= hotel-d3d11 + hotel-vulkan) judas-hq sync own-present stallhunt cold-warm
#         packaged (flavor packaged) lgpl (flavor lgpl)
# (default: judas hotel judas-hq; stallhunt and cold-warm run without the sampler)
# Results go to results-2026-10-11/; per-refresh CSVs were zipped afterwards (per-refresh-csv.zip).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
FLAVOR="${1:-master}"
shift || true
GROUPS_TO_RUN="${*:-judas hotel judas-hq}"
RES="$HERE/results-2026-10-11"
EXE="$HERE/bin/perf0-$FLAVOR.exe"
PROC="perf0-$FLAVOR"
case "$FLAVOR" in
master|lgpl) export PATH="$HOME/p0/prefix/libplacebo-master/bin:$PATH" ;;
esac
[ "$FLAVOR" = lgpl ] && export PATH="$HOME/p0/prefix/ffmpeg-lgpl/bin:$PATH"
JUDAS="C:/Users/tamla/Downloads/[Judas] Nama-Anaru - S01E02.mkv"
HOTEL="C:/Users/tamla/Downloads/260723_HOTEL_AMAN_43_V3_MAIN_50s_MASTER_16x9_VA_H264.mp4"
SAMPLER="$(cygpath -w "$HERE/sample-gpu-cpu.ps1")"

# run ID FILE SECONDS BACKEND DECODE [extra perf0 args...]  (with the load sampler)
run() {
    local id=$1 file=$2 secs=$3 backend=$4 decode=$5
    shift 5
    local out="$RES/$id"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$SAMPLER" -ProcessName "$PROC" \
        -OutCsv "$(cygpath -w "$out-load.csv")" > /dev/null 2>&1 &
    local spid=$!
    sleep 3   # let PowerShell finish its own start-up before the player starts
    "$EXE" --file "$file" --backend "$backend" --decode "$decode" --adapter NVIDIA --seconds "$secs" \
           --out "$out" --id "$id" --log-level 3 "$@" > "$out.stdout.log" 2> "$out.stderr.log"
    local rc=$?
    wait $spid
    echo "$id rc=$rc"
}

# run-bare: same without the sampler (first-frame time runs must not share the machine with PowerShell start-up)
run_bare() {
    local id=$1 file=$2 secs=$3 backend=$4 decode=$5
    shift 5
    local out="$RES/$id"
    "$EXE" --file "$file" --backend "$backend" --decode "$decode" --adapter NVIDIA --seconds "$secs" \
           --out "$out" --id "$id" --log-level 3 "$@" > "$out.stdout.log" 2> "$out.stderr.log"
    echo "$id rc=$?"
}

for grp in $GROUPS_TO_RUN; do
    case $grp in
    judas)   # 60 s of the HEVC Main10 episode: the four primary combinations, plus the default Vulkan queue depth
        run judas-d3d11-sw           "$JUDAS" 60 d3d11  sw
        run judas-d3d11-d3d11va      "$JUDAS" 60 d3d11  d3d11va
        run judas-vulkan-sw-depth1   "$JUDAS" 60 vulkan sw     --vk-depth 1
        run judas-vulkan-vk-depth1   "$JUDAS" 60 vulkan vulkan --vk-depth 1
        run judas-vulkan-sw-depth3   "$JUDAS" 20 vulkan sw
        run judas-vulkan-vk-depth3   "$JUDAS" 20 vulkan vulkan
        ;;
    hotel|hotel-d3d11)   # the H.264 clip once, to the end (about 63 s)
        run hotel-d3d11-sw           "$HOTEL" 0 d3d11  sw
        run hotel-d3d11-d3d11va      "$HOTEL" 0 d3d11  d3d11va
        [ "$grp" = hotel-d3d11 ] || {
        run hotel-vulkan-sw-depth1   "$HOTEL" 0 vulkan sw     --vk-depth 1
        run hotel-vulkan-vk-depth1   "$HOTEL" 0 vulkan vulkan --vk-depth 1
        }
        ;;
    hotel-vulkan)
        run hotel-vulkan-sw-depth1   "$HOTEL" 0 vulkan sw     --vk-depth 1
        run hotel-vulkan-vk-depth1   "$HOTEL" 0 vulkan vulkan --vk-depth 1
        ;;
    judas-hq)   # moving source crop (defeats the frame cache, as a pan does): the whole pipeline on every refresh
        run judas-hq-worst-d3d11-sw      "$JUDAS" 20 d3d11  sw      --preset hq --worst-case
        run judas-hq-worst-d3d11-d3d11va "$JUDAS" 20 d3d11  d3d11va --preset hq --worst-case
        run judas-hq-worst-vulkan-sw     "$JUDAS" 20 vulkan sw      --preset hq --worst-case --vk-depth 1
        run judas-hq-worst-vulkan-vk     "$JUDAS" 20 vulkan vulkan  --preset hq --worst-case --vk-depth 1
        run judas-default-worst-d3d11-sw  "$JUDAS" 20 d3d11  sw      --worst-case
        run judas-default-worst-vulkan-sw "$JUDAS" 20 vulkan sw      --worst-case --vk-depth 1
        ;;
    packaged)   # same player source built against the MSYS2 package libplacebo 7.360.1:  run-matrix.sh packaged packaged
        run packaged-judas-d3d11-sw      "$JUDAS" 20 d3d11  sw
        run packaged-judas-d3d11-d3d11va "$JUDAS" 20 d3d11  d3d11va
        run packaged-judas-vulkan-vk     "$JUDAS" 20 vulkan vulkan --vk-depth 1
        ;;
    lgpl)   # same player against FFmpeg 9.0.2 built with --disable-gpl:  run-matrix.sh lgpl lgpl
        run lgpl-judas-d3d11-sw      "$JUDAS" 20 d3d11  sw
        run lgpl-judas-d3d11-d3d11va "$JUDAS" 20 d3d11  d3d11va
        run lgpl-judas-vulkan-sw     "$JUDAS" 20 vulkan sw     --vk-depth 1
        run lgpl-judas-vulkan-vk     "$JUDAS" 20 vulkan vulkan --vk-depth 1
        run lgpl-hotel-d3d11-d3d11va "$HOTEL" 20 d3d11  d3d11va
        ;;
    sync)   # pl_gpu_finish after every render: wall time of the render including the GPU, per film-frame class
        run judas-sync-d3d11-sw      "$JUDAS" 20 d3d11  sw      --sync-render
        run judas-sync-d3d11-d3d11va "$JUDAS" 20 d3d11  d3d11va --sync-render
        ;;
    own-present)   # D3D11: our own waitable swapchain (max latency 1) wrapped by libplacebo, our own Present1
        run judas-own-present-d3d11-sw      "$JUDAS" 20 d3d11 sw      --own-present
        run judas-own-present-d3d11-d3d11va "$JUDAS" 20 d3d11 d3d11va --own-present
        ;;
    stallhunt)   # no load sampler: do Vulkan hitches of 300+ ms reproduce, and does D3D11 show any?
        for n in 1 2 3; do
            run_bare stallhunt-vulkan-sw-depth1-run$n "$JUDAS" 60 vulkan sw --vk-depth 1
        done
        run_bare stallhunt-d3d11-sw-run1 "$JUDAS" 60 d3d11 sw
        ;;
    cold-warm)   # first-frame time: cold = no pl_cache file yet; warm = the next runs reuse it. 5 runs each.
        for cfg in "d3d11 sw" "d3d11 d3d11va" "vulkan sw" "vulkan vulkan"; do
            set -- $cfg
            cache="$RES/cache-$1-$2.bin"
            rm -f "$cache"
            for n in 1 2 3 4 5; do
                run_bare "firstframe-$1-$2-run$n" "$JUDAS" 1 $1 $2 --vk-depth 1 --cache "$cache"
            done
        done
        ;;
    esac
done
