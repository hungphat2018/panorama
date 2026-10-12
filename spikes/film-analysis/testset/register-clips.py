"""Put the real clips of real-clips.json into manifest.json with the facts read from the files themselves.

Usage: python -I register-clips.py [--only ID ...]

For every entry: probe the video stream, count frames from the packet table, hash the file (the worker's sample key and the full
SHA-256), and copy licence and source details from sources.json for public sources. Labels already recorded for a clip are kept.
Public sources must already be downloaded (fetch-sources.py). A file whose size or hash changed since the last registration is
reported, because its labels no longer apply.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testset_common as tc  # noqa: E402


def licence_of(src: dict, sources: dict) -> dict:
    if src.get("source_id"):
        allsrc = {s["id"]: s for s in sources["sources"] + sources.get("windows", [])}
        s = allsrc[src["source_id"]]
        lic = s["licence"]
        return {"id": lic["id"], "url": lic["url"], "credit": lic["credit"], "checked": lic["checked"], "evidence_url": lic["evidence_url"],
                "source_title": s["title"], "source_url": (s.get("url") or (s.get("files") or [{}])[0].get("url"))}
    return {"id": src.get("licence", "unknown")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None)
    a = ap.parse_args()
    plan = tc.load_json(tc.HERE / "real-clips.json")
    sources = tc.load_json(tc.SOURCES)
    man = tc.load_manifest()
    for c in plan["clips"]:
        if a.only and c["id"] not in a.only:
            continue
        p = Path(c["path"])
        full = p if p.is_absolute() else tc.MEDIA / p
        if not full.exists():
            print(f"[{c['id']}] missing file {full}; fetch it first")
            continue
        facts = tc.describe_clip(full)
        old = next((x for x in man["clips"] if x["id"] == c["id"]), None)
        if old and old.get("sha256") and old["sha256"] != facts["sha256"]:
            print(f"[{c['id']}] WARNING: the file changed since it was registered; labels may not apply")
        entry = {"id": c["id"], "kind": "real", "domain": c["domain"], "path": c["path"] if p.is_absolute() else str(p).replace("\\", "/"),
                 "source": dict(c["source"], licence=licence_of(c["source"], sources)), "tests": c["tests"], "notes": c.get("notes", "")}
        entry.update(facts)
        tc.upsert_clip(man, entry)
        v = facts["video"]
        print(f"[{c['id']}] {v['codec']} {v['width']}x{v['height']} {v['fps']} {v['frames']} frames {v['duration_s']:.1f} s  key {facts['identity']['sample_sha256'][:12]}")
    tc.save_json(tc.MANIFEST, man)
    return 0


if __name__ == "__main__":
    sys.exit(main())
