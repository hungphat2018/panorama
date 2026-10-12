"""File identity: the cache key of a content model.

Rule (scheme "size+3x1MiB/sha256/0"): SHA-256 over
    b"panorama.filekey/0\\0"  +  size as 8 bytes little-endian  +  samples
where the samples are the whole file when it is at most 3 MiB, otherwise three blocks of 1 MiB read at offset 0, at
size//2 - 512 KiB (the middle) and at size - 1 MiB. The name, folder and modification time are not part of the key, so a
renamed or moved copy keeps its model; any change in size or in those three places gives a new key. A remux is a new key.
The full hash (SHA-256 of every byte) is a separate, slower, later value; it is stored next to the sample hash when known.
"""
from __future__ import annotations

import hashlib
import os
import struct

MIB = 1 << 20
SCHEME = "size+3x1MiB/sha256/0"
_PREFIX = b"panorama.filekey/0\0"


def sample_key(path: str) -> dict:
    size = os.path.getsize(path)
    h = hashlib.sha256()
    h.update(_PREFIX)
    h.update(struct.pack("<Q", size))
    with open(path, "rb") as f:
        if size <= 3 * MIB:
            h.update(f.read())
        else:
            for offset in (0, size // 2 - MIB // 2, size - MIB):
                f.seek(offset)
                block = f.read(MIB)
                if len(block) != MIB:
                    raise OSError(f"short read at offset {offset} of {path}")
                h.update(block)
    return {"scheme": SCHEME, "size_bytes": size, "sample_sha256": h.hexdigest()}


def full_sha256(path: str, chunk: int = 8 * MIB) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                return h.hexdigest()
            h.update(block)


def same_file(a: dict, b: dict) -> bool:
    """True when two keys name the same file: scheme, size and sample hash equal. The optional full hash is not compared (one key
    may have it and the other not yet); when both have it, it must agree."""
    if (a["scheme"], a["size_bytes"], a["sample_sha256"]) != (b["scheme"], b["size_bytes"], b["sample_sha256"]):
        return False
    return a.get("full_sha256") is None or b.get("full_sha256") is None or a["full_sha256"] == b["full_sha256"]


def cache_folder_name(key: dict) -> str:
    """Folder name for a model under a cache root: the first 32 hex digits of the sample hash and the size."""
    return f"{key['sample_sha256'][:32]}-{key['size_bytes']}"
