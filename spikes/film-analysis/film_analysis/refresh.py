"""Making stale tables again: a stored table whose analyzer version is not the current one is rebuilt, the others stay as they are.

The cache key of a content model is the file's identity (file_key.py) for the folder and the analyzer versions for what inside
it may be reused. When the signals' versions agree, the raw signals are never touched here: a changed rule for slides, a
changed crop policy or a new subtitle classifier costs seconds (or, for subtitles, one pass of libass), not a new decode.
  shots, slide-paths, subjects   derived from signals.bin and subjects-raw.jsonl (derive_tables.py); a new `shots` table moves the
                                 ids the other two name, so they are made again with it (manifest.tables_to_make)
  file-facts                     read again from the container; the parts that come from the scan are kept
  subtitles                      every track analysed again
"""
from __future__ import annotations

import os
import time

from . import derive_tables, file_facts, manifest as mf, prescan, subjects_faces as sf, subtitles_events, versions
from .signals_store import write_json_atomic


def refresh_stale(ctx, stale: list, log=None, subtitle_threads: int = 2) -> dict:
    """Rebuilds the tables named in `stale` (and the ones that follow them). Returns {table: seconds} for what was rebuilt."""
    log = log or (lambda msg: None)
    want = mf.tables_to_make(stale)
    took = {}
    if "file-facts" in want:
        t0 = time.perf_counter()
        _remake_file_facts(ctx)
        took["file-facts"] = round(time.perf_counter() - t0, 3)
        log(f"file-facts made again ({took['file-facts']} s)")
    l1 = want & derive_tables.ALL_TABLES
    if l1:
        t0 = time.perf_counter()
        raw_path = os.path.join(ctx.out_dir, sf.RAW_NAME)
        raw = sf.load_raw(ctx.out_dir) if os.path.isfile(raw_path) else None
        prescan.write_l1(ctx, raw, l1)
        dt = round(time.perf_counter() - t0, 3)
        for name in sorted(l1):
            took[name] = dt
        log(f"{', '.join(sorted(l1))} made again from the signals ({dt} s)")
    if "subtitles" in want:
        t0 = time.perf_counter()
        remake_subtitles(ctx, subtitle_threads, log)
        took["subtitles"] = round(time.perf_counter() - t0, 3)
    mf.save(ctx.out_dir, ctx.manifest)
    return took


def remake_subtitles(ctx, threads: int, log=None) -> dict:
    """Analyses every subtitle stream and records the table in the manifest."""
    subs = subtitles_events.run_all(ctx, ctx.out_dir, threads, log)
    failed = any(t["status"] == "failed" for t in subs["tracks"])
    mf.set_table(ctx.manifest, "subtitles", None, versions.SUBTITLES, "partial" if failed else "complete",
                 tracks=subs["tracks"], seconds=subs["seconds"])
    return subs


def _remake_file_facts(ctx) -> None:
    """The container facts again (streams, colour, HDR, timeline), keeping what the scan added to them."""
    first_frame = prescan.hdr_first_frame(ctx.path, ctx.vstream)
    vers = {"worker": versions.table_versions(), "signals": {g: v["version"] for g, v in ctx.store.header["groups"].items()}}
    new = file_facts.build_file_facts(os.path.basename(ctx.path), ctx.key, ctx.info, ctx.vstream, ctx.table, first_frame,
                                      ctx.decode["path"], vers)
    prescan.attach_mkv_chapters(new, ctx.path)
    for field in ("black_level", "crop_vocabulary", "text_in_bars", "slide_region_of_interest"):
        new[field] = ctx.facts.get(field)
    ctx.facts.clear()
    ctx.facts.update(new)
    write_json_atomic(os.path.join(ctx.out_dir, "file-facts.json"), ctx.facts)
    mf.set_table(ctx.manifest, "file-facts", "file-facts.json", versions.FILE_FACTS, "complete")
