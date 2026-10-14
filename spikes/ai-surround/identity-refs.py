"""Identity references (phase 4 of plans/261013-0718-ai-surround-live-path): who the episode's characters are, and what
each one looks like whole, so the painter and the refiner see the real character when the picture's edge cuts one off
(the plate and A14B invented a scarf for a blood stain and a ponytail for short hair).

From the content model's person and head tracks (subjects.json: boxes every 12 frames):
  embed     each person track's best few boxes (largest, most confident) cut from the film, through CCIP (deepghs
            ccip-caformer-24-randaug-pruned, ONNX: RGB 384x384, CLIP's normalisation; the feature model and its metric
            model, which gives the pairwise difference; same character at <= 0.1785)
  cluster   the tracks whose best embedded box is at least MIN_BODY_H tall, by that box's feature: complete linkage (a
            track joins a cluster only if it is within SAME of every member; largest boxes first); a track alone stays
            its own cluster. DBSCAN (the model card's dbscan_2, eps 0.1292)
            chained 722 of JJK's 744 tracks into one cluster through small crops of effects, crowds and partial bodies
            that sit near everything, so small tracks take no part at all.
  cache     the embeddings (features, owners, boxes) go to <out>/embeddings.npz, keyed by the recipe: clustering again
            needs no decoding (embedding JJK's 1620 boxes took ~10 min on the CPU)
  crops     per cluster, body crops from boxes the picture's edge does not cut (4 px inside the shot's crop), largest and
            sharpest first, and head crops from the head tracks inside them: RGB PNG, both sides a multiple of 32,
            0.1-0.3 MP (the painter's extra_refs, live/painter_jobs.py)
  cut       per shot (the content model's span) and side: the clusters of person tracks a box of which touches that edge of
            the crop (within 8 px)
Writes <out>/clusters.json and <out>/c<k>/<n>.png:
  {"version": 1, "clusters": {"c<k>": {"crops": [{"path", "kind": "body"|"head", "frame", "box": [x0, y0, x1, y1],
   "score"}, ...]}}, "cut_by_seam": [{"first", "n", "side", "clusters": ["c<k>", ...]}, ...], "facts": {...}}
Paths are relative to <out>; boxes are source px; crops best first. cut_by_seam's spans are the content model's shots: the
worker splits some at hidden cuts, so its spans lie inside these (a lookup matches a worker span by overlap).

    python -I -X utf8 identity-refs.py FILM [--content DIR] [--out DIR] [--model DIR] [--cuda] [--per-track 3] [--threads 4]
Default --out: out/live/cache/<film key folder>/identity (the worker's cache); CPU unless --cuda; below-normal priority.
Needs onnxruntime and Pillow (CCIP's own resize: PIL bilinear, which antialiases when it shrinks).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "spikes", "film-analysis"))
from film_analysis import file_key  # noqa: E402
from film_analysis.proc import lower_own_priority  # noqa: E402

MODEL_DIR = os.path.join(REPO, "tools", "models", "ccip-caformer-24-randaug-pruned")
MEAN = np.float32([0.48145466, 0.4578275, 0.40821073])
STD = np.float32([0.26862954, 0.26130258, 0.27577711])
SIZE = 384
SAME = 0.14       # stricter than the model's same-character 0.1785 (imgutils): JJK's action crops (two characters, effects,
                  # partial bodies) passed as one character there; at 0.14 a character spreads over several clusters (its
                  # poses), which a per-shot lookup does not mind, and few clusters mix characters (contact sheets 04:58)
STEP = 12                                                         # subjects.json samples every 12th frame
EDGE_IN, EDGE_CUT = 4.0, 8.0                                      # px: inside the crop (body crops) / touching an edge
MIN_BODY_H = 160                                                  # source px: smaller person boxes are not references
CROPS_PER_CLUSTER = 6


def video_size(content: str) -> tuple:
    """The film's frame size (w, h), from the content model's file facts (its boxes are in these px)."""
    with open(os.path.join(content, "file-facts.json"), encoding="utf-8") as f:
        v = json.load(f)["video"]
    return int(v["width"]), int(v["height"])


def frames_of(film, wanted: set, w: int, h: int):
    """(frame index, RGB) of the wanted frames (all multiples of STEP), decoded in one pass of every STEP-th frame."""
    if not wanted:
        return
    ffmpeg = os.environ.get("PANORAMA_FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg not found (PATH or PANORAMA_FFMPEG)")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "4", "-i", film, "-map", "0:v:0",
           "-vf", f"select='not(mod(n\\,{STEP}))'", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-"]
    size = w * h * 3
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=size * 4)
    k, last = 0, max(wanted)
    try:
        while k * STEP <= last:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            if k * STEP in wanted:
                yield k * STEP, np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            k += 1
    finally:
        p.kill()
        p.wait()


def ccip_input(rgb: np.ndarray) -> np.ndarray:
    """imgutils' _preprocess_image: RGB resized to 384x384 by PIL's bilinear, /255, CLIP's mean and std, CHW float32."""
    x = np.asarray(Image.fromarray(rgb).resize((SIZE, SIZE), resample=Image.BILINEAR), np.float32) / 255.0
    return ((x - MEAN) / STD).transpose(2, 0, 1)


def write_clusters(out: str, doc: dict):
    with open(os.path.join(out, "clusters.json.tmp"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    os.replace(os.path.join(out, "clusters.json.tmp"), os.path.join(out, "clusters.json"))


def complete_linkage(diff: np.ndarray, order: list, same: float) -> np.ndarray:
    """Labels 0..k-1: in `order`, each item joins the cluster all of whose members are within `same` of it (the one with
    the lowest mean difference), else starts one. Labels are renumbered by cluster size, largest first."""
    members = []
    for i in order:
        best, best_mean = -1, np.inf
        for c, ms in enumerate(members):
            d = diff[i, ms]
            if d.max() <= same and d.mean() < best_mean:
                best, best_mean = c, float(d.mean())
        if best < 0:
            members.append([i])
        else:
            members[best].append(i)
    labels = np.empty(len(diff), np.int64)
    for k, ms in enumerate(sorted(members, key=len, reverse=True)):
        labels[ms] = k
    return labels


def ref_size(w: float, h: float) -> tuple:
    """Both sides a multiple of 32, about 0.2 MP (0.1-0.3), the box's aspect."""
    s = np.sqrt(0.2e6 / max(1.0, w * h))
    rw, rh = max(32, int(round(w * s / 32)) * 32), max(32, int(round(h * s / 32)) * 32)
    while rw * rh > 0.3e6:
        rw, rh = max(32, rw - 32), max(32, rh - 32)
    return rw, rh


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("film")
    ap.add_argument("--content", default=os.path.join(HERE, "out", "jjk", "content"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--cache", default=os.path.join(HERE, "out", "live", "cache"))
    ap.add_argument("--model", default=MODEL_DIR)
    ap.add_argument("--cuda", action="store_true", help="CCIP on the GPU (default: CPU)")
    ap.add_argument("--per-track", type=int, default=3, help="boxes embedded per person track")
    ap.add_argument("--same", type=float, default=SAME, help="complete linkage's difference limit")
    ap.add_argument("--threads", type=int, default=4, help="CPU threads for CCIP (below-normal priority either way)")
    a = ap.parse_args(argv)
    lower_own_priority()                                          # ffmpeg, started later, inherits it
    t0 = time.perf_counter()
    out = a.out or os.path.join(a.cache, file_key.cache_folder_name(file_key.sample_key(a.film)), "identity")
    os.makedirs(out, exist_ok=True)
    subjects = json.load(open(os.path.join(a.content, "subjects.json"), encoding="utf-8"))
    shots = {s["id"]: s for s in json.load(open(os.path.join(a.content, "shots.json"), encoding="utf-8"))["shots"]}
    vw, vh = video_size(a.content)

    def give_up(why: str, failed: bool = False, **facts):
        """An empty clusters.json (the lookup then finds no crops); exit code 1 when decoding failed."""
        facts.update({"model": os.path.basename(a.model), "same": a.same, "min_body_h": MIN_BODY_H, "clusters": 0,
                      "seconds": round(time.perf_counter() - t0, 1), "empty": why})
        write_clusters(out, {"version": 1, "clusters": {}, "cut_by_seam": [], "facts": facts})
        print(f"no clusters: {why}; wrote {out}", flush=True)
        return 1 if failed else 0

    # person tracks, their boxes (source px), and the head tracks of the same shot
    tracks, heads = [], {}
    for sh in subjects["shots"]:
        crop = shots[sh["shot"]]["crop"]["rect"]
        for t in sh["tracks"]:
            boxes = [b for b in t["boxes"] if b["frame"] % STEP == 0]
            if t["class"] == "person" and boxes:
                tracks.append({"shot": sh["shot"], "crop": crop, "boxes": boxes})
            elif t["class"] == "head":
                heads.setdefault(sh["shot"], []).extend(boxes)
    for tr in tracks:                                             # its best boxes: big, confident, not cut
        def rank(b, crop=tr["crop"]):
            inside = (b["x"] > crop[0] + EDGE_IN and b["y"] > crop[1] + EDGE_IN and b["x"] + b["w"] < crop[2] - EDGE_IN
                      and b["y"] + b["h"] < crop[3] - EDGE_IN)
            return (inside, b["w"] * b["h"] * b["score"])
        tr["best"] = sorted(tr["boxes"], key=rank, reverse=True)[:a.per_track]
    wanted = {b["frame"] for tr in tracks for b in tr["best"]}
    print(f"{len(tracks)} person tracks in {len(subjects['shots'])} shots; decoding {len(wanted)} frames", flush=True)
    if not wanted:
        return give_up("no person tracks", person_tracks=len(tracks))

    prov = ["CUDAExecutionProvider", "CPUExecutionProvider"] if a.cuda else ["CPUExecutionProvider"]
    so = ort.SessionOptions()
    so.intra_op_num_threads = so.inter_op_num_threads = a.threads
    feat = ort.InferenceSession(os.path.join(a.model, "model_feat.onnx"), so, providers=prov)
    metric = ort.InferenceSession(os.path.join(a.model, "model_metrics.onnx"), so, providers=prov)
    by_frame = {}
    for i, tr in enumerate(tracks):
        for j, b in enumerate(tr["best"]):
            by_frame.setdefault(b["frame"], []).append((i, j, b))
    meta, feats, owners = {}, [], []                              # (track, box) -> (box, frame, sharpness); features
    subj = os.path.join(a.content, "subjects.json")
    recipe = (f"{os.path.basename(a.model)}|{file_key.sample_key(a.film)}|subjects-{os.path.getsize(subj)}-"
              f"{int(os.path.getmtime(subj))}|per-track{a.per_track}|rank-inside-area-score|step{STEP}|pil-bilinear")
    cache = os.path.join(out, "embeddings.npz")
    if os.path.isfile(cache):
        with np.load(cache) as z:
            if str(z["recipe"]) == recipe:
                feats = list(z["feats"])
                owners = [tuple(int(v) for v in o) for o in z["owners"]]
                meta = {(int(o[0]), int(o[1])): (tuple(int(v) for v in m[:4]), int(m[4]), float(m[5]))
                        for o, m in zip(z["owners"], z["meta"])}
                print(f"embeddings from {cache}: {len(feats)} boxes", flush=True)
    batch, batch_keys = [], []

    def flush():
        if batch:
            f, = feat.run(["output"], {"input": np.stack(batch)})
            feats.extend(f)
            owners.extend(batch_keys)
            batch.clear()
            batch_keys.clear()

    # pass 1: embed every chosen box (no pixels kept), unless cached
    if not feats:
        for frame, rgb in frames_of(a.film, wanted, vw, vh):
            for i, j, b in by_frame.get(frame, ()):
                x0, y0 = int(max(0, b["x"])), int(max(0, b["y"]))
                x1, y1 = int(min(vw, b["x"] + b["w"])), int(min(vh, b["y"] + b["h"]))
                if x1 - x0 < 16 or y1 - y0 < 16:
                    continue
                c = np.ascontiguousarray(rgb[y0:y1, x0:x1])
                g = cv2.resize(cv2.cvtColor(c, cv2.COLOR_RGB2GRAY), (256, max(16, int(256 * (y1 - y0) / (x1 - x0)))),
                               interpolation=cv2.INTER_AREA)
                meta[(i, j)] = ((x0, y0, x1, y1), frame, float(cv2.Laplacian(g, cv2.CV_32F).var()))
                batch.append(ccip_input(c))
                batch_keys.append((i, j))
                if len(batch) == 16:
                    flush()
        flush()
        print(f"embedded {len(feats)} boxes in {time.perf_counter() - t0:.0f} s", flush=True)
        if feats:
            np.savez(cache + ".tmp.npz", recipe=recipe, feats=np.stack(feats).astype(np.float32),
                     owners=np.int64(owners),
                     meta=np.float64([list(meta[o][0]) + [meta[o][1], meta[o][2]] for o in owners]))
            os.replace(cache + ".tmp.npz", cache)
    if not feats:
        return give_up("no box decoded (ffmpeg failed, or every box under 16 px)", failed=True, person_tracks=len(tracks))

    # one feature per track: its best embedded box (tr["best"] is ranked, so the lowest j); only tracks whose box is
    # tall enough to show who it is take part
    best_of = {}
    for k, (i, j) in enumerate(owners):
        if i not in best_of or j < owners[best_of[i]][1]:
            best_of[i] = k
    tall = {i: k for i, k in best_of.items() if meta[owners[k]][0][3] - meta[owners[k]][0][1] >= MIN_BODY_H}
    if not tall:
        return give_up(f"no person box {MIN_BODY_H} px tall", person_tracks=len(tracks), embedded_boxes=len(feats))
    idx = sorted(tall)
    F = np.stack([feats[tall[i]] for i in idx]).astype(np.float32)
    diff, = metric.run(["output"], {"input": F})

    def size(n):                                                  # the track's best box area (largest first)
        x0, y0, x1, y1 = meta[owners[tall[idx[n]]]][0]
        return -(x1 - x0) * (y1 - y0)

    labels = complete_linkage(diff, sorted(range(len(idx)), key=size), a.same)
    cluster_of = {i: f"c{int(labels[k])}" for k, i in enumerate(idx)}

    # the references per cluster, best first: uncut bodies by size x sharpness, each with the head inside it if any
    by_cluster = {}
    for (i, _j), (box, frame, sharp) in meta.items():
        if i not in cluster_of or (box[3] - box[1]) < MIN_BODY_H:
            continue
        crop = tracks[i]["crop"]
        uncut = (box[0] > crop[0] + EDGE_IN and box[1] > crop[1] + EDGE_IN and box[2] < crop[2] - EDGE_IN
                 and box[3] < crop[3] - EDGE_IN)
        score = (box[3] - box[1]) * np.sqrt(max(sharp, 1.0)) * (1.0 if uncut else 0.25)
        by_cluster.setdefault(cluster_of[i], []).append((score, uncut, i, box, frame))
    chosen = {}                                                   # frame -> [(cluster, rank, kind, box, score, uncut)]
    for cid, items in by_cluster.items():
        items.sort(key=lambda r: -r[0])
        for rank, (score, uncut, i, box, frame) in enumerate(items[:CROPS_PER_CLUSTER]):
            chosen.setdefault(frame, []).append((cid, rank, "body", box, score, uncut))
            for hb in heads.get(tracks[i]["shot"], ()):           # a head box of the same frame inside the body
                hx0, hy0, hx1, hy1 = hb["x"], hb["y"], hb["x"] + hb["w"], hb["y"] + hb["h"]
                if hb["frame"] != frame or hx0 < box[0] - 8 or hy0 < box[1] - 8 or hx1 > box[2] + 8 or hy1 > box[3] + 8:
                    continue
                m = 0.15 * max(hb["w"], hb["h"])                  # a little room around the head
                hbox = (int(max(0, hx0 - m)), int(max(0, hy0 - m)), int(min(vw, hx1 + m)), int(min(vh, hy1 + m)))
                if hbox[3] - hbox[1] >= 64:
                    chosen[frame].append((cid, rank, "head", hbox, score, uncut))
                break
    # pass 2: cut the chosen references from the film
    found = {}
    for frame, rgb in frames_of(a.film, set(chosen), vw, vh):
        for cid, rank, kind, box, score, uncut in chosen[frame]:
            rw, rh = ref_size(box[2] - box[0], box[3] - box[1])
            img = cv2.resize(np.ascontiguousarray(rgb[box[1]:box[3], box[0]:box[2]]), (rw, rh), interpolation=cv2.INTER_AREA)
            found.setdefault(cid, []).append((rank, 0 if kind == "body" else 1, kind, img, frame, box, score, uncut))
    for d in os.listdir(out):                                     # an earlier run's crops
        if d[:1] == "c" and d[1:].isdigit() and os.path.isdir(os.path.join(out, d)):
            shutil.rmtree(os.path.join(out, d))
    clusters = {}
    for cid, items in sorted(found.items(), key=lambda kv: int(kv[0][1:])):
        os.makedirs(os.path.join(out, cid), exist_ok=True)
        entries = []
        for n, (_rank, _, kind, img, frame, box, score, uncut) in enumerate(sorted(items, key=lambda r: (r[0], r[1]))):
            path = f"{cid}/{n}.png"
            cv2.imwrite(os.path.join(out, path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
            entries.append({"path": path, "kind": kind, "frame": frame, "box": [float(v) for v in box],
                            "score": round(float(score), 1), "uncut": bool(uncut)})
        ms = [n for n, i in enumerate(idx) if cluster_of[i] == cid]
        sub = diff[np.ix_(ms, ms)]
        mean_diff = float(sub[np.triu_indices(len(ms), 1)].mean()) if len(ms) > 1 else 0.0
        clusters[cid] = {"crops": entries, "tracks": len(ms), "mean_diff": round(mean_diff, 4)}   # tightness, < SAME

    # who the seam cuts: per shot span and side, the clusters of tracks touching that edge of the crop
    cut = {}
    for i, tr in enumerate(tracks):
        cid = cluster_of.get(i)
        if cid is None or cid not in clusters:
            continue
        x0, y0, x1, y1 = tr["crop"]
        for b in tr["boxes"]:
            for side, touch in (("left", b["x"] <= x0 + EDGE_CUT), ("right", b["x"] + b["w"] >= x1 - EDGE_CUT),
                                ("top", b["y"] <= y0 + EDGE_CUT), ("bottom", b["y"] + b["h"] >= y1 - EDGE_CUT)):
                if touch:
                    cut.setdefault((tr["shot"], side), set()).add(cid)
    cut_by_seam = [{"first": shots[s]["first_frame"], "n": shots[s]["frames"], "side": side,
                    "clusters": sorted(cids, key=lambda c: int(c[1:]))} for (s, side), cids in sorted(cut.items())]
    sizes = np.bincount(labels)
    facts = {"person_tracks": len(tracks), "embedded_boxes": len(feats), "tall_tracks": len(idx),
             "clusters": len(clusters), "clustered_tracks": int(sizes[sizes > 1].sum()),
             "largest_clusters": sorted(sizes.tolist(), reverse=True)[:8], "shot_sides_cut": len(cut_by_seam),
             "model": os.path.basename(a.model), "same": a.same, "min_body_h": MIN_BODY_H,
             "seconds": round(time.perf_counter() - t0, 1)}
    write_clusters(out, {"version": 1, "clusters": clusters, "cut_by_seam": cut_by_seam, "facts": facts})
    print(json.dumps(facts), flush=True)
    print(f"wrote {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
