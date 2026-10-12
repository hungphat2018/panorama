#!/usr/bin/env python3
"""Download the few test files that cannot be generated, each into its own new folder.

Everything here comes from the FFmpeg project's own sample servers (FATE suite and
samples.ffmpeg.org), plus the official MKVToolNix portable archive (the test-media muxer),
and is treated as untrusted data:
  * one new folder per download under downloads/<name>/ (nothing is extracted or run there);
  * the script only streams bytes to disk and hashes them (it never parses a media file);
  * URL, size and SHA-256 are written to downloads/<name>/source.json and collected in
    downloads/manifest.json. A second run keeps the first hash and fails when it changes.
Run with:  python -I fetch-samples.py          (from any folder; -I = no user site, no cwd imports)
"""
import hashlib
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS = os.path.join(HERE, "downloads")
MEDIA = os.path.join(HERE, "media")

FATE = "https://fate-suite.ffmpeg.org/"
SAMPLES = "https://samples.ffmpeg.org/"

# name -> (url, why this file, file name used in media/)
SAMPLES_TO_FETCH = {
    "ffmpeg-samples-pgs-supsample": (
        SAMPLES + "sub/PGS/supsample.mkv",
        "real Blu-ray PGS (image subtitles) in MKV, 23 KB",
        "dl-pgs-supsample.mkv"),
    "fate-dovi-p5": (
        FATE + "mov/dovi-p5.mp4",
        "Dolby Vision profile 5 (IPTPQc2 base layer, no HDR10 fallback), 4 KB",
        "dl-dovi-p5.mp4"),
    "fate-dovi-p81": (
        FATE + "mov/dovi-p81.mp4",
        "Dolby Vision profile 8.1 (HDR10-compatible base layer), 4 KB",
        "dl-dovi-p81.mp4"),
    "fate-dovi-p7-hvce": (
        FATE + "mkv/dovi-p7-hvce.mkv",
        "Dolby Vision profile 7 (UHD Blu-ray dual layer), 657 KB",
        "dl-dovi-p7.mkv"),
    "fate-h264-paff": (
        FATE + "h264-conformance/camp_mot_picaff0_full.26l",
        "JVT conformance stream with picture-adaptive frame/field (PAFF) coding, raw H.264",
        "dl-h264-paff.26l"),
    "ffmpeg-samples-4khdr-leopard-4k": (
        SAMPLES + "4khdr/PE2_Leopard_4K.mkv",
        "real HDR10 4K HEVC from a UHD Blu-ray (Planet Earth II), 24 MB",
        "dl-hdr10-leopard-4k.mkv"),
    "fate-hdr10-plus": (
        FATE + "hevc/hdr10_plus_h265_sample.hevc",
        "HDR10+ (ST 2094-40 dynamic metadata) raw HEVC, 76 KB",
        "dl-hdr10plus.hevc"),
    "fate-mov-empty-edit": (
        FATE + "mov/empty_edit_5s.mp4",
        "MP4 whose edit list starts with a 5 s empty edit",
        "dl-mp4-empty-edit-5s.mp4"),
    "fate-mov-neg-first-pts": (
        FATE + "mov/mov_neg_first_pts_discard.mov",
        "MOV with negative first pts and frames the edit list discards",
        "dl-mov-neg-first-pts.mov"),
}


MKVTOOLNIX_VERSION = "102.0"
MKVTOOLNIX_ARCHIVE = f"mkvtoolnix-64-bit-{MKVTOOLNIX_VERSION}.7z"
MKVTOOLNIX_BASE = f"https://mkvtoolnix.download/windows/releases/{MKVTOOLNIX_VERSION}/"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "panorama-file-matrix/1 (spike)"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)


def fetch_mkvtoolnix():
    """The test-media muxer: official portable 7z, checked against the sums the site publishes (same origin, so a transfer
    check, not a signature), extracted to tools/mkvtoolnix. Needs a 7z command (7-Zip) on PATH for the extraction."""
    folder = os.path.join(DOWNLOADS, "mkvtoolnix")
    archive, sums = os.path.join(folder, MKVTOOLNIX_ARCHIVE), os.path.join(folder, "sha256sums.txt")
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(archive):
        download(MKVTOOLNIX_BASE + MKVTOOLNIX_ARCHIVE, archive)
    download(MKVTOOLNIX_BASE + "sha256sums.txt", sums)
    published = next(l.split()[0] for l in open(sums, encoding="utf-8") if l.split()[-1].lstrip("*") == MKVTOOLNIX_ARCHIVE)
    digest = sha256_of(archive)
    if digest != published:
        raise SystemExit(f"MKVToolNix archive hash {digest} differs from the published {published}")
    meta = {"name": "mkvtoolnix", "url": MKVTOOLNIX_BASE + MKVTOOLNIX_ARCHIVE, "file": MKVTOOLNIX_ARCHIVE,
            "why": "mkvmerge for ordered chapters, segment UIDs, attachments, display size and HDR colour elements",
            "size_bytes": os.path.getsize(archive), "sha256": digest, "published_sha256_matches": True,
            "first_downloaded_utc": datetime.fromtimestamp(os.path.getmtime(archive), timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
    old = os.path.join(folder, "source.json")
    if os.path.exists(old):
        meta["first_downloaded_utc"] = json.load(open(old, encoding="utf-8"))["first_downloaded_utc"]
    with open(old, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    target = os.path.join(HERE, "tools", "mkvtoolnix")
    if not os.path.exists(os.path.join(target, "mkvmerge.exe")):
        tmp = os.path.join(HERE, "tools", "_mkvtoolnix-extract")
        os.makedirs(tmp, exist_ok=True)
        import subprocess
        subprocess.run(["7z", "x", "-y", f"-o{tmp}", archive], check=True, capture_output=True)
        shutil.move(os.path.join(tmp, "mkvtoolnix"), target)
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"ok   {'mkvtoolnix':34s} {meta['size_bytes']:>10d} B  {digest[:16]}...  -> tools/mkvtoolnix")
    return meta


def main():
    os.makedirs(DOWNLOADS, exist_ok=True)
    os.makedirs(MEDIA, exist_ok=True)
    manifest = {}
    failed = False
    for name, (url, why, media_name) in SAMPLES_TO_FETCH.items():
        folder = os.path.join(DOWNLOADS, name)
        fname = url.rsplit("/", 1)[1]
        dest = os.path.join(folder, fname)
        meta_path = os.path.join(folder, "source.json")
        if not os.path.exists(dest):
            os.makedirs(folder, exist_ok=True)  # always a new folder: one download per folder
            try:
                download(url, dest)
            except Exception as e:  # network or HTTP error: report, keep going with the rest
                print(f"FAIL {name}: {e}")
                failed = True
                continue
        digest = sha256_of(dest)
        meta = {"name": name, "url": url, "file": fname, "why": why, "size_bytes": os.path.getsize(dest),
                "sha256": digest, "first_downloaded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")}
        if os.path.exists(meta_path):
            old = json.load(open(meta_path, encoding="utf-8"))
            if old["sha256"] != digest:
                print(f"HASH CHANGED {name}: recorded {old['sha256']}, now {digest}")
                failed = True
                continue
            meta["first_downloaded_utc"] = old["first_downloaded_utc"]
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)
        manifest[name] = meta
        # the matrix plays a copy under media/, the original stays untouched in its folder
        shutil.copyfile(dest, os.path.join(MEDIA, media_name))
        print(f"ok   {name:34s} {meta['size_bytes']:>10d} B  {digest[:16]}...  -> media/{media_name}")
    try:
        manifest["mkvtoolnix"] = fetch_mkvtoolnix()
    except Exception as e:      # no network, no 7z: the samples above are still usable
        print(f"FAIL mkvtoolnix: {e}")
        failed = True
    with open(os.path.join(DOWNLOADS, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
