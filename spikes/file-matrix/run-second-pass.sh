#!/bin/bash
# Second pass of the supported-file matrix (what produced the "@fixes" runs): reruns of cases whose generation or settings were
# fixed after pass 1, the two concat-script timelines, then perf0 with all six switches on for the cases where one can matter.
# Needs the same tools as run-matrix.py. Pacing numbers from this pass are only meaningful on a quiet machine.
PY=/c/Users/tamla/AppData/Local/Programs/Python/Python311/python.exe
cd "/c/Users/tamla/Downloads/Telegram Desktop/panorama/spikes/file-matrix"
FIX="--deinterlace bwdif --rotation --sw-fallback --skip-bad-packets --synth-pts --sd-passthrough"
echo "== plain reruns (subtitle tracks flagged default, HDR10+ settings)"
$PY -u run-matrix.py --only srt-mkv pgs-generated-mkv h264-1080p24-mp4 dl-hdr10plus
echo "== interlace group plain (native-size window, mpv video-mode grab)"
$PY -u run-matrix.py --group interlace
echo "== concat-script timelines"
$PY -u run-matrix.py --only ordered-internal-via-ffconcat ordered-linked-via-ffconcat
echo "== perf0 with all switches: interlace geometry colour broken"
$PY -u run-matrix.py --variant fixes "$FIX" --group interlace
$PY -u run-matrix.py --variant fixes "$FIX" --group geometry
$PY -u run-matrix.py --variant fixes "$FIX" --group colour
$PY -u run-matrix.py --variant fixes "$FIX" --group broken
echo "== perf0 with all switches: selected others"
$PY -u run-matrix.py --variant fixes "$FIX" --only h264-1080p24-mkv hevc-main10-1080p24-mkv h264-hi10p-1080p24-mkv h264-422-10bit-1080p24-mkv h264-444-1080p24-mkv mpeg4-asp-480p25-avi hevc-main10-2160p60-mkv vfr-24-30-60-mkv mp4-trimmed-edit-list hdr10-pq-card-mkv dl-h264-paff dl-hdr10plus dl-mp4-empty-edit-5s dl-pgs-supsample real-judas-hevc-main10-mkv
echo "== done"
