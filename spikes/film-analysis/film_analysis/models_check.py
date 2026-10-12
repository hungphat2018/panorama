"""models-manifest.json: look up a model's hash, verify the files in tools/models."""
from __future__ import annotations

import hashlib
import json
import os


def _manifest(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return {m["id"]: m for m in json.load(f)["models"]}


def model_sha256(manifest_path: str, model_id: str) -> str:
    return _manifest(manifest_path)[model_id]["sha256"]


def model_hashes(manifest_path: str) -> dict:
    """model id -> sha-256 recorded in the manifest, for every model the worker loads."""
    return {mid: m["sha256"] for mid, m in _manifest(manifest_path).items()}


def verify(models_dir: str, manifest_path: str) -> list:
    """Problems found (empty list: every file is there with the manifest's size and sha-256)."""
    problems = []
    for mid, m in _manifest(manifest_path).items():
        p = os.path.join(models_dir, m["path"])
        if not os.path.isfile(p):
            problems.append(f"{mid}: {p} is missing (source: {m['source_url']})")
            continue
        if os.path.getsize(p) != m["size_bytes"]:
            problems.append(f"{mid}: size {os.path.getsize(p)} differs from {m['size_bytes']}")
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for block in iter(lambda: f.read(1 << 22), b""):
                h.update(block)
        if h.hexdigest() != m["sha256"]:
            problems.append(f"{mid}: sha-256 {h.hexdigest()} differs from the manifest's {m['sha256']}")
    return problems
