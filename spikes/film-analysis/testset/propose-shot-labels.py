"""Agent-proposed shot labels for real clips: candidates from several detectors, contact sheets, then a decisions file.

Usage:
  python -I propose-shot-labels.py candidates CLIP_ID      # list candidates, auto-accept consensus cuts, write review sheets for the rest
  python -I propose-shot-labels.py build CLIP_ID           # merge consensus + decisions/<clip>-shots.json into labels/<clip>/shots.json (+ sheets of every boundary)

Candidates are clusters of events (within 3 frames) from TransNetV2 (strong >0.5, weak >0.15, many-hot spans), scdet (>=10 strong, >=6 weak),
PySceneDetect Content and Adaptive, and keyframes. A TransNetV2 hard cut that one independent signal confirms is accepted as a hard cut
("consensus"; the detectors being measured later are not the only judges, but this set is biased towards what they find, so the review
set below also contains everything they disagree on). Everything else is shown on a contact sheet row (frames b-2, b-1, b, b+1 or a
five-frame strip across a span) and decided by eye. Decisions use short codes:
  h            hard cut at the candidate frame            d:A-B  cross-dissolve over frames A..B-1      fb:A-B  dip to black     fw:A-B  dip to white
  w:A-B        wipe / slide / other synthetic transition  n:flash | n:held | n:overlay | n:other   not a layout change (flash, a held-drawing pop,
               a graphics/overlay change inside one shot, anything else)       u   cannot tell (kept out of the labels, counted)
Output: labels/<clip>/shots.json (status agent-proposed; every boundary carries review = consensus | agent-viewed and its sheet cell),
decisions/<clip>-shots.json (the decisions, committed), sheets under tools/testset/sheets/<clip>/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import testset_common as tc  # noqa: E402
from testset_sheets import event_row, grab_frames, stack_rows, write_sheet  # noqa: E402

_EVAL = str(Path(__file__).resolve().parent.parent / "eval")
if _EVAL not in sys.path:
    sys.path.insert(0, _EVAL)
from fa_eval import cache  # noqa: E402
from fa_eval.methods import shots_policy as sp  # noqa: E402

DEC_DIR = tc.HERE / "decisions"
TW, TH = 192, 108
PER_SHEET = 22


def candidates(sig: dict) -> list[dict]:
    """Clusters of detector events. Each: frame (best estimate of the first frame of the new shot), span or None, votes, tn (max prob)."""
    ev = []   # (frame, source, strength, span)
    for b in sp.tn_single(sig["tn_single"], 0.5):
        ev.append((b.frame, "tn", b.score, b.span))
    for b in sp.tn_single(sig["tn_single"], 0.15):
        if not any(abs(b.frame - e[0]) <= 1 for e in ev if e[1] == "tn"):
            ev.append((b.frame, "tn_weak", b.score, b.span))
    for b in sp.tn_any(sig["tn_single"], sig["tn_many"], 0.5, 0.4):
        if b.span:
            ev.append((b.span[0], "tn_span", b.score, b.span))
    sc = sig["scdet"]
    for b in sp.scdet(sc, 10.0):
        ev.append((b.frame, "scdet", b.score, None))
    for b in sp.scdet(sc, 6.0):
        if sc[b.frame] < 10.0:
            ev.append((b.frame, "scdet_weak", b.score, None))
    for k, name in (("psd_content", "psdc"), ("psd_adaptive", "psda")):
        if k in sig:
            for f in sig[k]:
                ev.append((int(f), name, 1.0, None))
    ev.sort(key=lambda e: e[0])
    clusters: list[dict] = []
    for f, src, strength, span in ev:
        if clusters and f - clusters[-1]["last"] <= 3:
            c = clusters[-1]
        else:
            c = {"frames": [], "votes": {}, "span": None, "last": f}
            clusters.append(c)
        c["frames"].append((f, src))
        c["votes"][src] = max(c["votes"].get(src, 0.0), strength)
        c["last"] = max(c["last"], span[1] if span else f)
        if span and (c["span"] is None or span[1] - span[0] > c["span"][1] - c["span"][0]):
            c["span"] = span
    kf = np.asarray(sig.get("keyframes", []))
    out = []
    for i, c in enumerate(clusters):
        tn_frames = [f for f, s in c["frames"] if s == "tn"]
        pick = tn_frames[0] if tn_frames else int(np.median([f for f, _ in c["frames"]]))
        c["frame"] = int(pick)
        c["kf"] = bool(kf.size and np.abs(kf - c["frame"]).min() <= 1)
        c["id"] = i
        out.append({"id": i, "frame": int(pick), "span": list(c["span"]) if c["span"] else None, "votes": {k: round(float(v), 3) for k, v in c["votes"].items()}, "kf": c["kf"]})
    return out


def auto_decision(c: dict) -> str | None:
    v = c["votes"]
    if "tn" in v and not c["span"]:
        independent = ("scdet" in v) or ("psdc" in v) or ("psda" in v) or c["kf"]
        if independent:
            return "h"
    return None


def vote_text(c: dict) -> str:
    v = c["votes"]
    parts = []
    if "tn" in v:
        parts.append(f"TN{v['tn']:.2f}")
    elif "tn_weak" in v:
        parts.append(f"tn{v['tn_weak']:.2f}")
    if "tn_span" in v:
        parts.append("TNspan")
    if "scdet" in v:
        parts.append(f"S{v['scdet']:.0f}")
    elif "scdet_weak" in v:
        parts.append(f"s{v['scdet_weak']:.0f}")
    if "psdc" in v:
        parts.append("C")
    if "psda" in v:
        parts.append("A")
    if c["kf"]:
        parts.append("K")
    return " ".join(parts)


def frames_for(c: dict) -> tuple[list[int], list[str], int | None]:
    """Frame indices and small labels for the contact-sheet row; mark_gap = column after which the boundary falls."""
    b = c["frame"]
    if c["span"] and c["span"][1] - c["span"][0] >= 3:
        s, e = c["span"]
        mid = (s + e) // 2
        idx = [s - 1, s, mid, e - 1, e]
        return idx, [f"{i}" for i in idx], None
    idx = [b - 2, b - 1, b, b + 1]
    return idx, [f"{i}" for i in idx], 2


def fmt_time(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def make_sheets(clip_id: str, cands: list[dict], prefix: str, path, n_frames: int) -> dict[int, tuple[str, int]]:
    """Write sheets of the candidates; returns {candidate id: (sheet name, cell number)}."""
    want = set()
    plans = {}
    for c in cands:
        idx, labs, gap = frames_for(c)
        idx = [min(max(i, 0), n_frames - 1) for i in idx]
        plans[c["id"]] = (idx, labs, gap)
        want.update(idx)
    imgs = grab_frames(path, want, width=TW * 2)
    tsec = tc.frame_seconds(path, [c["frame"] for c in cands]) if cands else []
    where = {}
    for s in range(0, len(cands), PER_SHEET):
        chunk = cands[s:s + PER_SHEET]
        rows = []
        for k, c in enumerate(chunk):
            idx, labs, gap = plans[c["id"]]
            title = f"#{c['id']} b={c['frame']} {fmt_time(tsec[s + k])} {vote_text(c)}"
            row = event_row(imgs, idx, labs, title, TW, TH, mark_gap=gap)
            if row.shape[1] < TW * 5:
                row = np.hstack([row, np.zeros((row.shape[0], TW * 5 - row.shape[1], 3), np.uint8)])
            rows.append(row)
            where[c["id"]] = (f"{prefix}-{s // PER_SHEET:03d}", k)
        write_sheet(clip_id, f"{prefix}-{s // PER_SHEET:03d}", stack_rows(rows, 2))
    return where


def parse_decision(code: str) -> dict:
    if code == "h":
        return {"kind": "hard"}
    if code == "u":
        return {"kind": "unsure"}
    head, _, rest = code.partition(":")
    if head == "n":
        return {"kind": "not_cut", "why": rest or "other"}
    if head in ("d", "fb", "fw", "w"):
        a, b = (int(x) for x in rest.split("-"))
        return {"kind": {"d": "dissolve", "fb": "fade_black", "fw": "fade_white", "w": "wipe"}[head], "span": [a, b]}
    raise ValueError(f"bad decision {code!r}")


def cmd_candidates(clip_id: str) -> int:
    entry = tc.clip_by_id(tc.load_manifest(), clip_id)
    path = tc.clip_path(entry)
    sig = cache.load_npz("signals", clip_id)
    n = int(sig["n_frames"][0])
    cands = candidates(sig)
    auto = [c for c in cands if auto_decision(c)]
    review = [c for c in cands if not auto_decision(c)]
    print(f"[{clip_id}] {n} frames: {len(cands)} candidates, {len(auto)} consensus hard cuts, {len(review)} to review")
    where = make_sheets(clip_id, review, "shots-review", path, n)
    for c in review:
        c["sheet"], c["cell"] = where[c["id"]]
    cache.dump_json(cache.run_dir("review") / f"{clip_id}-shots-candidates.json", {"clip": clip_id, "n_frames": n, "consensus": auto, "review": review})
    print("review items:", [(c["id"], c["frame"], vote_text(c)) for c in review][:400])
    return 0


def cmd_build(clip_id: str) -> int:
    entry = tc.clip_by_id(tc.load_manifest(), clip_id)
    path = tc.clip_path(entry)
    cand_file = cache.run_dir("review") / f"{clip_id}-shots-candidates.json"
    data = tc.load_json(cand_file)
    dec_file = DEC_DIR / f"{clip_id}-shots.json"
    decisions = tc.load_json(dec_file) if dec_file.exists() else {"decisions": {}}
    dec = decisions.get("decisions", {})
    n = data["n_frames"]
    boundaries, not_cuts, unsure, missing = [], [], [], []
    for c in data["consensus"]:
        boundaries.append({"frame": c["frame"], "kind": "hard", "span": None, "layout_change_point": True, "review": "consensus", "votes": c["votes"], "_id": ("c", c["id"])})
    for c in data["review"]:
        code = dec.get(str(c["id"]))
        if code is None:
            missing.append(c["id"])
            continue
        d = parse_decision(code)
        if d["kind"] == "hard":
            boundaries.append({"frame": c["frame"], "kind": "hard", "span": None, "layout_change_point": True, "review": "agent-viewed", "votes": c["votes"], "_id": ("r", c["id"])})
        elif d["kind"] == "not_cut":
            span = c["span"] or [c["frame"], c["frame"] + 1]
            not_cuts.append({"span": span, "kind": d["why"], "review": "agent-viewed", "votes": c["votes"], "_id": ("r", c["id"])})
        elif d["kind"] == "unsure":
            unsure.append({"frame": c["frame"], "votes": c["votes"]})
        else:
            a, b = d["span"]
            boundaries.append({"frame": a, "kind": d["kind"], "span": [a, b], "layout_change_point": True, "review": "agent-viewed", "votes": c["votes"], "_id": ("r", c["id"])})
    boundaries.sort(key=lambda b: b["frame"])
    not_cuts.sort(key=lambda b: b["span"][0])
    # sheets of every final boundary and not-cut for the user's confirmation
    items = [dict(id=i, frame=b["frame"], span=b["span"] if b["kind"] != "hard" else None, votes=b["votes"], kf=False) for i, b in enumerate(boundaries)]
    where = make_sheets(clip_id, items, "shots", path, n)
    for i, b in enumerate(boundaries):
        b["sheet"], b["cell"] = where[i]
        b.pop("_id", None)
    items_nc = [dict(id=i, frame=b["span"][0], span=b["span"] if b["span"][1] - b["span"][0] >= 3 else None, votes=b["votes"], kf=False) for i, b in enumerate(not_cuts)]
    where_nc = make_sheets(clip_id, items_nc, "shots-notcuts", path, n) if items_nc else {}
    for i, b in enumerate(not_cuts):
        b["sheet"], b["cell"] = where_nc[i]
        b.pop("_id", None)
    basis = (f"agent proposal: candidates from TransNetV2 + scdet + PySceneDetect; {len(data['consensus'])} consensus hard cuts accepted, {len(data['review'])} disagreements "
             f"decided by looking at contact sheets (decisions/{clip_id}-shots.json); {len(unsure)} left out as unsure; {len(missing)} not yet decided")
    fps = entry["video"]["fps"]
    tc.write_labels(clip_id, "shots", {"boundaries": boundaries, "not_cuts": not_cuts, "unsure": unsure, "regions": decisions.get("regions", []),
                                       "undecided_candidates": missing, "reviewed_by": decisions.get("reviewed_by", "agent")},
                    status="agent-proposed", proposed_by="agent", basis=basis, frame_count=n, fps=fps)
    man = tc.load_manifest()
    tc.upsert_clip(man, {"id": clip_id, "labels": {"shots": {"file": f"labels/{clip_id}/shots.json", "status": "agent-proposed",
                                                           "items": len(boundaries) + len(not_cuts), "waiting_for_confirmation": len(boundaries) + len(not_cuts)}}})
    tc.save_json(tc.MANIFEST, man)
    print(f"[{clip_id}] {len(boundaries)} boundaries ({sum(1 for b in boundaries if b['review'] == 'consensus')} consensus), {len(not_cuts)} not-cuts, {len(unsure)} unsure, {len(missing)} undecided")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["candidates", "build"])
    ap.add_argument("clip")
    a = ap.parse_args()
    return cmd_candidates(a.clip) if a.cmd == "candidates" else cmd_build(a.clip)


if __name__ == "__main__":
    sys.exit(main())
