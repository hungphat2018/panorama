"""content.json: the index of one content model folder, rewritten atomically after every step.

A reader opens content.json first: it says which tables exist, in which state, at which analyzer version, and which frames the
full scan has covered (`scan.done`, as half-open [first, last) frame intervals of the frame table).
"""
from __future__ import annotations

import datetime
import json
import os
import platform
from typing import Optional

from . import SCHEMA_ID, WORKER_VERSION
from .signals_store import write_json_atomic

NAME = "content.json"


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def new_manifest(file_key: dict, file_name: str, n_frames: int) -> dict:
    return {
        "schema": SCHEMA_ID, "worker_version": WORKER_VERSION, "created_at": now_iso(), "updated_at": now_iso(),
        "file": {"key": file_key, "name": file_name},
        "tools": {}, "decode_path": None,
        "tables": {},
        "scan": {"state": "not-started", "n_frames": n_frames, "done": [], "failed": [], "playhead_s": 0.0,
                 "started_at": None, "finished_at": None, "timing": {}},
        "warnings": [],
    }


def load(folder: str) -> Optional[dict]:
    path = os.path.join(folder, NAME)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(folder: str, manifest: dict) -> None:
    manifest["updated_at"] = now_iso()
    write_json_atomic(os.path.join(folder, NAME), manifest)


def set_table(manifest: dict, name: str, file: Optional[str], version: str, state: str, **extra) -> None:
    entry = {"file": file, "version": version, "state": state, "updated_at": now_iso()}
    entry.update(extra)
    manifest["tables"][name] = entry


# Tables whose content refers to shot ids and boundaries: when `shots` is made again they are made again with it.
FOLLOW_SHOTS = ("slide-paths", "subjects")


def stale_tables(manifest: dict, current: dict, folder: Optional[str] = None) -> list:
    """Tables the folder holds (any state) whose stored analyzer version differs from `current` (name -> version), or, when
    `folder` is given, whose file is gone. Tables the folder never held are not stale: the scan makes those the first time."""
    out = []
    for name, version in current.items():
        entry = manifest.get("tables", {}).get(name)
        if entry is None:
            continue
        gone = folder is not None and entry.get("file") and not os.path.isfile(os.path.join(folder, entry["file"]))
        if entry.get("version") != version or gone:
            out.append(name)
    return out


def tables_to_make(stale: list) -> set:
    """The stale tables plus the ones that follow them (a new shots table moves the ids the others refer to)."""
    want = set(stale)
    if "shots" in want:
        want.update(FOLLOW_SHOTS)
    return want


def tools_info(ffmpeg_version: str, ffprobe_version: str, extra: dict) -> dict:
    info = {"ffmpeg": ffmpeg_version, "ffprobe": ffprobe_version, "python": platform.python_version(),
            "platform": platform.platform()}
    info.update(extra)
    return info
