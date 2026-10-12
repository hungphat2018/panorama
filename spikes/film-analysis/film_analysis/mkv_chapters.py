"""Does a Matroska file ask for a different timeline than its own? (ordered chapters, linked segments)

FFmpeg's matroska demuxer ignores `EditionFlagOrdered` and `ChapterSegmentUUID` (the file-matrix spike read the source), so the
performer plays such a file as its plain linear timeline while mpv builds the edit timeline from the chapters. The content model
describes the linear timeline either way; this reader only reports whether the file wants more, so nothing downstream is
silently wrong. It walks the top-level elements of the Segment (skipping cluster payloads with a seek: a few hundred seeks for a
feature film) and parses the Chapters element wherever it sits.
"""
from __future__ import annotations

import os
from typing import Optional

ID_SEGMENT = 0x18538067
ID_CLUSTER = 0x1F43B675
ID_CHAPTERS = 0x1043A770
ID_EDITION = 0x45B9
ID_EDITION_ORDERED = 0x45DD
ID_ATOM = 0xB6
ID_ATOM_SEGMENT_UUID = 0x6E67
ID_ATOM_START = 0x91
ID_ATOM_END = 0x92
ID_ATOM_ENABLED = 0x98


def _read_id(f) -> Optional[int]:
    b = f.read(1)
    if not b:
        return None
    first = b[0]
    length = 1
    mask = 0x80
    while length <= 4 and not (first & mask):
        length += 1
        mask >>= 1
    if length > 4:
        return None
    rest = f.read(length - 1)
    if len(rest) != length - 1:
        return None
    return int.from_bytes(bytes([first]) + rest, "big")


def _read_size(f) -> Optional[int]:
    """Element data size; -1 for 'unknown'."""
    b = f.read(1)
    if not b:
        return None
    first = b[0]
    length = 1
    mask = 0x80
    while length <= 8 and not (first & mask):
        length += 1
        mask >>= 1
    if length > 8:
        return None
    rest = f.read(length - 1)
    if len(rest) != length - 1:
        return None
    value = first & (mask - 1)
    for byte in rest:
        value = (value << 8) | byte
    return -1 if value == (1 << (7 * length)) - 1 else value


def _children(data: bytes):
    """(id, payload) pairs of the elements packed in `data`."""
    pos = 0
    import io
    f = io.BytesIO(data)
    while pos < len(data):
        f.seek(pos)
        eid = _read_id(f)
        size = _read_size(f)
        if eid is None or size is None or size < 0:
            return
        start = f.tell()
        yield eid, data[start:start + size]
        pos = start + size


def read_chapters(path: str, max_bytes: int = 64 << 20) -> Optional[dict]:
    """None when the file is not Matroska or has no Chapters element, else a summary of the editions."""
    try:
        with open(path, "rb") as f:
            if _read_id(f) != 0x1A45DFA3:
                return None
            size = _read_size(f)
            if size is None or size < 0:
                return None
            f.seek(size, 1)
            if _read_id(f) != ID_SEGMENT:
                return None
            _read_size(f)
            file_size = os.path.getsize(path)
            chapters = None
            steps = 0
            while chapters is None and steps < 200000:
                steps += 1
                pos = f.tell()
                eid = _read_id(f)
                sz = _read_size(f)
                if eid is None or sz is None:
                    break
                if eid == ID_CHAPTERS:
                    if sz < 0 or sz > max_bytes:
                        break
                    chapters = f.read(sz)
                    break
                if sz < 0:                            # unknown-size element (live stream): cannot skip it
                    if eid == ID_CLUSTER:
                        break
                    continue
                if pos + sz > file_size:
                    break
                f.seek(sz, 1)
    except OSError:
        return None
    if chapters is None:
        return {"present": False, "editions": 0, "ordered_editions": 0, "atoms": 0, "atoms_linking_other_segments": 0}
    editions = ordered = atoms = linked = 0
    for eid, payload in _children(chapters):
        if eid != ID_EDITION:
            continue
        editions += 1
        for cid, cpay in _children(payload):
            if cid == ID_EDITION_ORDERED and int.from_bytes(cpay, "big") == 1:
                ordered += 1
            elif cid == ID_ATOM:
                atoms += 1
                for aid, apay in _children(cpay):
                    if aid == ID_ATOM_SEGMENT_UUID and any(apay):
                        linked += 1
    return {"present": True, "editions": editions, "ordered_editions": ordered, "atoms": atoms, "atoms_linking_other_segments": linked}
