"""Download the public sources named in sources.json, each into its own new folder under tools/testset/downloads/.

Usage (Python 3.11, standard library only):
  python -I fetch-sources.py list
  python -I fetch-sources.py fetch ID [ID ...]        # whole-file downloads (resumable), zip members extracted
  python -I fetch-sources.py window ID [ID ...]       # windows of a remote file read by HTTP range (ffmpeg), own folder each
  python -I fetch-sources.py ledger                   # bytes fetched so far against the budget

Rules kept (project rules for downloads):
  * every source gets a NEW folder; an existing folder is only reused to resume the same file;
  * downloaded files are data only: nothing is run, zip members are listed and checked before extraction;
  * the SHA-256 and byte count of every file go into tools/testset/downloads/ledger.json, which also enforces the
    byte budget from sources.json (the user's cap of about 4 GB; the script stops instead of going over it).
A window ("remote-window" in sources.json) is read with ffmpeg -ss/-t straight from the URL, so only that part of the file crosses the
network: mode "copy" keeps the streams as they are (the cut starts at the keyframe at or before in_s), mode "transcode" re-encodes
to the 1080p H.264 given in the entry. The ledger counts the media bytes the window transferred (bit rate x seconds, an estimate).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
DOWNLOADS = REPO / "tools" / "testset" / "downloads"
LEDGER = DOWNLOADS / "ledger.json"
SOURCES = HERE / "sources.json"
CHUNK = 1 << 20
TZ = timezone(timedelta(hours=7))


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def load_sources() -> dict:
    return json.loads(SOURCES.read_text(encoding="utf-8"))


def load_ledger() -> dict:
    if LEDGER.exists():
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    return {"ledger_format": "panorama-testset-ledger/1", "entries": []}


def save_ledger(led: dict) -> None:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(led, indent=1), encoding="utf-8")
    tmp.replace(LEDGER)


def ledger_total(led: dict) -> int:
    return int(sum(int(e.get("bytes_transferred", 0)) for e in led["entries"]))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def open_url(req: urllib.request.Request, timeout: int = 60, tries: int = 6):
    """urlopen that waits out HTTP 429 (the mirror's rate limit) for as long as Retry-After says."""
    for k in range(tries):
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code != 429 or k == tries - 1:
                raise
            wait = int(e.headers.get("Retry-After", "60")) + 5
            print(f"    rate limited (429); waiting {wait} s", flush=True)
            time.sleep(wait)
    raise RuntimeError("unreachable")


def head(url: str) -> dict:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "panorama-testset/1"})
    with open_url(req) as r:
        return {"length": int(r.headers.get("Content-Length", "-1")), "etag": r.headers.get("ETag"),
                "last_modified": r.headers.get("Last-Modified"), "accept_ranges": r.headers.get("Accept-Ranges")}


def download(url: str, dest: Path, expect_bytes: int | None, retries: int = 4) -> int:
    """Resumable download to dest (via dest.part). Returns bytes transferred in this call."""
    part = dest.with_name(dest.name + ".part")
    transferred = 0
    for attempt in range(1, retries + 1):
        have = part.stat().st_size if part.exists() else 0
        headers = {"User-Agent": "panorama-testset/1"}
        if have:
            headers["Range"] = f"bytes={have}-"
        req = urllib.request.Request(url, headers=headers)
        try:
            with open_url(req) as r:
                status = r.status
                total = int(r.headers.get("Content-Length", "-1"))
                if status == 200 and have:        # server ignored the range: start over
                    have = 0
                    part.unlink()
                mode = "ab" if have else "wb"
                want = (have + total) if total >= 0 else expect_bytes
                last = time.time()
                with part.open(mode) as f:
                    while True:
                        b = r.read(CHUNK)
                        if not b:
                            break
                        f.write(b)
                        transferred += len(b)
                        have += len(b)
                        if time.time() - last > 10 and want:
                            print(f"    {dest.name}: {have / 1e6:8.1f} / {want / 1e6:.1f} MB", flush=True)
                            last = time.time()
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            print(f"    attempt {attempt} failed: {e}", flush=True)
            time.sleep(3 * attempt)
    else:
        raise RuntimeError(f"download failed after {retries} attempts: {url}")
    size = part.stat().st_size
    if expect_bytes is not None and size != expect_bytes:
        raise RuntimeError(f"{dest.name}: got {size} bytes, sources.json says {expect_bytes}")
    part.replace(dest)
    return transferred


def safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    out = []
    for m in zf.infolist():
        name = m.filename.replace("\\", "/")
        if name.startswith("/") or ".." in name.split("/") or ":" in name.split("/")[0]:
            raise RuntimeError(f"unsafe zip member name: {m.filename!r}")
        if (m.external_attr >> 16) & 0o170000 == 0o120000:
            raise RuntimeError(f"symlink in zip: {m.filename!r}")
        out.append(m)
    return out


def write_licence_note(folder: Path, src: dict) -> None:
    lic = src["licence"]
    text = (f"{src['title']}\n"
            f"Licence: {lic['id']}  {lic['url']}\n"
            f"Read from: {lic['evidence_url']} on {lic['checked']}\n"
            f"Credit: {lic['credit']}\n"
            f"Note: {lic.get('note', '')}\n"
            "Used only to cut local test clips for film-analysis accuracy tests; clips are never uploaded.\n")
    (folder / "LICENCE-NOTE.txt").write_text(text, encoding="utf-8")


def fetch_source(src: dict, budget: int, led: dict) -> None:
    folder = DOWNLOADS / src["folder"]
    resume = folder.exists() and any(folder.glob("*.part"))
    if folder.exists() and not resume and any(folder.iterdir()):
        print(f"[{src['id']}] folder {folder} already holds files: refusing to reuse it (delete it to fetch again)")
        return
    folder.mkdir(parents=True, exist_ok=True)
    write_licence_note(folder, src)
    for fdesc in src["files"]:
        dest = folder / fdesc["name"]
        info = head(fdesc["url"])
        expect = fdesc.get("bytes") if fdesc.get("bytes") is not None else info["length"]
        if info["length"] >= 0 and expect is not None and info["length"] != expect:
            raise RuntimeError(f"{fdesc['url']}: server says {info['length']} bytes, sources.json says {expect}")
        remaining = (expect or 0) - (dest.with_name(dest.name + '.part').stat().st_size if dest.with_name(dest.name + '.part').exists() else 0)
        if ledger_total(led) + remaining > budget:
            raise RuntimeError(f"budget: {ledger_total(led) / 1e9:.2f} GB used + {remaining / 1e9:.2f} GB for {dest.name} would pass {budget / 1e9:.1f} GB; "
                               "ask the controller before going over")
        print(f"[{src['id']}] {fdesc['name']}: {(expect or 0) / 1e6:.1f} MB", flush=True)
        time.sleep(1.5)          # polite pace: the mirror rate-limits bursts
        t0 = time.time()
        got = download(fdesc["url"], dest, expect)
        dt = max(time.time() - t0, 1e-6)
        entry = {"source_id": src["id"], "file": str(dest.relative_to(DOWNLOADS)).replace("\\", "/"), "url": fdesc["url"],
                 "bytes_transferred": got, "bytes_on_disk": dest.stat().st_size, "sha256": sha256_of(dest),
                 "etag": info["etag"], "http_last_modified": info["last_modified"], "fetched": now(),
                 "seconds": round(dt, 1)}
        led["entries"].append(entry)
        save_ledger(led)
        print(f"    done {got / 1e6:.1f} MB in {dt:.0f} s ({got / 1e6 / dt:.1f} MB/s) sha256 {entry['sha256'][:16]}", flush=True)
        if fdesc.get("extract") == "zip":
            out = folder / "extracted"
            out.mkdir(exist_ok=True)
            with zipfile.ZipFile(dest) as zf:
                members = safe_members(zf)
                print("    zip members:", [(m.filename, m.file_size, m.compress_type) for m in members])
                for m in members:
                    zf.extract(m, out)
            for p in sorted(out.rglob("*")):
                if p.is_file():
                    led["entries"].append({"source_id": src["id"], "file": str(p.relative_to(DOWNLOADS)).replace("\\", "/"),
                                           "extracted_from": dest.name, "bytes_transferred": 0, "bytes_on_disk": p.stat().st_size,
                                           "sha256": sha256_of(p), "fetched": now()})
            save_ledger(led)


def fetch_window(src: dict, budget: int, led: dict, parents: dict) -> None:
    folder = DOWNLOADS / src["folder"]
    if folder.exists() and any(folder.iterdir()):
        print(f"[{src['id']}] folder {folder} already holds files: refusing to reuse it")
        return
    par = parents[src["parent"]]
    est = int(src.get("estimated_bytes", 0))
    if ledger_total(led) + est > budget:
        raise RuntimeError(f"budget: {ledger_total(led) / 1e9:.2f} GB used + about {est / 1e9:.2f} GB for {src['id']} would pass {budget / 1e9:.1f} GB; "
                           "ask the controller before going over")
    folder.mkdir(parents=True, exist_ok=True)
    write_licence_note(folder, src)
    info = head(src["url"])
    out = folder / f"{src['id']}.mkv"
    dur = float(src["out_s"]) - float(src["in_s"])
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{float(src['in_s']):.3f}", "-i", src["url"],
           "-t", f"{dur:.3f}"]
    if src.get("mode", "copy") == "copy":
        for m in src.get("maps", ["0:v:0"]):
            cmd += ["-map", m]
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        t = src["transcode"]
        cmd += ["-map", "0:v:0", "-an", "-sn", "-vf", t["vf"], "-c:v", "libx264", "-crf", str(t.get("crf", 12)), "-preset", t.get("preset", "slow"),
                "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                "-threads", "8", "-g", "48"]
    cmd += [str(out)]
    print(f"[{src['id']}] {src['in_s']} s .. {src['out_s']} s of {par['id']} (about {est / 1e6:.0f} MB to read)", flush=True)
    t0 = time.time()
    import subprocess
    r = subprocess.run(cmd, capture_output=True, creationflags=0x4000 if sys.platform == "win32" else 0)
    dt = time.time() - t0
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg failed for {src['id']}: {r.stderr.decode('utf-8', 'replace')[-600:]}")
    entry = {"source_id": src["id"], "file": str(out.relative_to(DOWNLOADS)).replace("\\", "/"), "url": src["url"],
             "window_s": [src["in_s"], src["out_s"]], "mode": src.get("mode", "copy"), "bytes_transferred": est,
             "bytes_transferred_note": "estimate: whole-file bit rate x window seconds + 3 MB container overhead",
             "bytes_on_disk": out.stat().st_size, "sha256": sha256_of(out), "etag": info["etag"], "http_last_modified": info["last_modified"],
             "fetched": now(), "seconds": round(dt, 1)}
    led["entries"].append(entry)
    save_ledger(led)
    print(f"    done in {dt:.0f} s, {out.stat().st_size / 1e6:.1f} MB on disk, sha256 {entry['sha256'][:16]}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("ledger")
    f = sub.add_parser("fetch")
    f.add_argument("ids", nargs="+")
    w = sub.add_parser("window")
    w.add_argument("ids", nargs="+")
    a = ap.parse_args()
    data = load_sources()
    led = load_ledger()
    budget = int(data["budget"]["total_bytes"])
    if a.cmd == "list":
        for s in data["sources"] + data.get("windows", []):
            print(f"{s['id']:28s} {s['kind']:15s} {s['licence']['id']:10s} {s['domain']:6s} {s['title']}")
        return 0
    if a.cmd == "ledger":
        for e in led["entries"]:
            print(f"{e.get('bytes_transferred', 0) / 1e6:9.1f} MB  {e['file']}  {e.get('sha256', '')[:12]}")
        print(f"total transferred {ledger_total(led) / 1e9:.3f} GB of {budget / 1e9:.1f} GB")
        return 0
    if a.cmd == "window":
        wins = {s["id"]: s for s in data.get("windows", [])}
        parents = {s["id"]: s for s in data.get("remote_files", [])}
        for sid in a.ids:
            if sid not in wins:
                print(f"unknown window id {sid}; known: {sorted(wins)}")
                return 2
            fetch_window(wins[sid], budget, led, parents)
        print(f"ledger: {ledger_total(led) / 1e9:.3f} GB of {budget / 1e9:.1f} GB")
        return 0
    by_id = {s["id"]: s for s in data["sources"]}
    for sid in a.ids:
        if sid not in by_id:
            print(f"unknown source id {sid}; known: {sorted(by_id)}")
            return 2
        fetch_source(by_id[sid], budget, led)
    print(f"ledger: {ledger_total(led) / 1e9:.3f} GB of {budget / 1e9:.1f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
