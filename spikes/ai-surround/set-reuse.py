"""Set reuse and real-pixel coverage on one episode: how many plates the live path needs if one plate serves every shot
of a set, how much of each shot's 32:9 sides real pixels (its own camera path, other shots of its set) can fill, and how
many new drawings need the generator.

A set = shots that show the same background, found by registering shots pairwise (connected components). A plate
painted for a set's first shot can serve the later ones; their real pixels can also fill each other's sides. Stages
(sub-commands, run in order; all CPU, BelowNormal priority, 2 threads; --rest-before lets the PC rest between heavy runs):

  scan      one sequential decode of the film at 480x270 (ffmpeg -threads 2, no hwaccel; the camera path and the GEN test
            need every frame, so one pass decodes less than one seek per shot). Per shot:
              camera offsets: scroll-plate.camera_offsets (flow-mode step, chained, relative to the middle frame)
              GEN test per step on the outer 25% band of each side: U = share of band pixels that differ by more than
              16/255 from the previous frame under every hypothesis (hold; slide by the camera step; slide x a gain).
              Camera step = the flow mode, or the content model's phase-correlation shift (signals dx, dy), or both.
              representative frames: start, middle, end, plus one per 1/4 picture width (or height) of camera travel
            -> CACHE/shot-NNN.npz
  register  SIFT on the representative frames (keypoints outside the person/head/face boxes of subjects.json), ratio
            test, RANSAC similarity (cv2.estimateAffinePartial2D); candidates = shots within +-WINDOW s plus, elsewhere,
            the TOP_K most similar shots by a colour-layout descriptor. Accepted pairs -> sets -> OUT/x5-sets.json
  sheet     contact sheets of accepted and rejected pairs for the by-eye check -> OUT/x5-sheet-*.png
  coverage  per shot, share of its side area (2 x 960x1080 picture px, at 240x270 each) that real pixels cover, from its
            own camera path (gated: steps with flow agreement < 0.3 or > 1/4 width per frame count as no motion) and
            from the registered shots of its set (upper bound: characters count as covered; and with person boxes
            removed); which earlier plate (if any) covers >= 80% / 95% of its sides; GEN drawings per shot and per
            film-second; story / ED split (--ed-from-shot, by eye) -> OUT/x5-coverage.json, x5-gen-frames.json

    python -I set-reuse.py scan FILM CONTENT_DIR CACHE
    python -I set-reuse.py register CONTENT_DIR CACHE OUT [--window 90] [--top-k 8] [--workers 4] [--ed-from-shot 653]
                                    [--from-results] [--min-inliers 20] [--min-spread 0.03] [--max-angle 20]
    python -I set-reuse.py sheet CONTENT_DIR CACHE OUT
    python -I set-reuse.py coverage CONTENT_DIR CACHE OUT [--ed-from-shot 653]
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), os.path.join(HERE, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W, H = 480, 270                                                   # working size: the picture at a quarter of 1920x1080
SIDE = W // 2                                                     # each 32:9 side is half the picture's width
BAND = W // 4                                                     # outer 25% band of the picture, each side
PIX = (16, 19)                                                    # per-pixel thresholds: 16 full-range, ~16 video-range
HYPS = ("hold", "flow", "pc", "all")                              # camera-step sources tried by the GEN test


def low_priority(threads=2):
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    cv2.setNumThreads(threads)
    cv2.ocl.setUseOpenCL(False)                                   # CPU only: the GPU belongs to another job


def json_default(o):
    """numpy scalars and arrays in JSON output."""
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def rest(seconds):
    if seconds > 0:
        print(f"resting {seconds} s before this run", flush=True)
        time.sleep(seconds)


def load_shots(content_dir):
    return json.load(open(os.path.join(content_dir, "shots.json"), encoding="utf-8"))["shots"]


def signal(content_dir, name):
    """One column of the content model's signals.bin as a numpy array (one value per frame)."""
    meta = json.load(open(os.path.join(content_dir, "signals.json"), encoding="utf-8"))
    col = next(c for c in meta["columns"] if c["name"] == name)
    raw = np.fromfile(os.path.join(content_dir, meta["file"]), np.uint8)
    n = meta["n_frames"] * col["width"]
    return np.frombuffer(raw.tobytes(), np.dtype(col["dtype"]), count=n, offset=col["offset"]).reshape(meta["n_frames"], -1).squeeze()


def decode(film, start_s, threads=2):
    """RGB frames at W x H from start_s (0: the file's first frame), CPU decode, BelowNormal priority."""
    ffmpeg = os.environ.get("PANORAMA_FFMPEG") or shutil.which("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", str(threads)]
    if start_s > 0:
        cmd += ["-ss", f"{start_s:.4f}"]
    cmd += ["-i", film, "-map", "0:v:0", "-fps_mode", "passthrough", "-vf", f"scale={W}:{H}:flags=area,format=rgb24",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=W * H * 3 * 8,
                         creationflags=getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0))
    size = W * H * 3
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    finally:
        p.stdout.close()
        p.terminate()
        p.wait()


def band_u(prev, cur, steps):
    """U per hypothesis set, threshold and side: share of the outer-band pixels of `cur` (grey) that differ by more than
    the threshold from `prev` under every hypothesis of the set. steps = {"flow": (dx, dy), "pc": (dx, dy)}: content moved
    by +step from prev to cur. Sets: hold only; hold + slide + slide x gain for the flow step, the pc step, or both."""
    p32, c32 = prev.astype(np.float32), cur.astype(np.float32)
    hold = np.abs(c32 - p32)
    slid_err = {}
    for name, s in steps.items():
        m = np.float32([[1, 0, s[0]], [0, 1, s[1]]])
        slid = cv2.warpAffine(p32, m, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=np.nan)
        err = np.abs(c32 - slid)
        for cols in (slice(0, BAND), slice(W - BAND, W)):          # one gain per band (ratio of means)
            ok = np.isfinite(slid[:, cols])
            if ok.sum() > 50:
                g = float(np.clip(c32[:, cols][ok].mean() / max(1.0, slid[:, cols][ok].mean()), 0.5, 2.0))
                err[:, cols] = np.fmin(err[:, cols], np.abs(c32[:, cols] - g * slid[:, cols]))
        slid_err[name] = err
    best = {"hold": hold, "flow": np.fmin(hold, slid_err["flow"]), "pc": np.fmin(hold, slid_err["pc"])}
    best["all"] = np.fmin(best["flow"], slid_err["pc"])
    out = np.zeros((len(HYPS), len(PIX), 2), np.float32)
    for i, h in enumerate(HYPS):
        for j, thr in enumerate(PIX):
            out[i, j, 0] = (best[h][:, :BAND] > thr).mean()
            out[i, j, 1] = (best[h][:, W - BAND:] > thr).mean()
    return out


def pick_reps(offsets, max_reps=12):
    """Representative frame indices: start (frame 1, past a cut's first frame), middle, end, plus one each time the camera
    has travelled a quarter of the picture's width or height since the last pick."""
    n = len(offsets)
    idx = {min(1, n - 1), n // 2, max(0, n - 2)}
    last = offsets[min(1, n - 1)]
    for t in range(n):
        if abs(offsets[t, 0] - last[0]) > W / 4 or abs(offsets[t, 1] - last[1]) > H / 4:
            idx.add(t)
            last = offsets[t]
    idx = sorted(idx)
    if len(idx) > max_reps:                                       # keep start, middle, end; thin the rest evenly
        must = {idx[0], n // 2, idx[-1]}
        rest_ = [i for i in idx if i not in must]
        keep = [rest_[int(k)] for k in np.linspace(0, len(rest_) - 1, max_reps - 3)]
        idx = sorted(must | set(keep))
    return idx


def cmd_scan(a):
    rest(a.rest_before)
    low_priority()
    scroll = load_script("scroll-plate")                          # camera_offsets
    shots = load_shots(a.content_dir)
    dx, dy = signal(a.content_dir, "dx"), signal(a.content_dir, "dy")
    os.makedirs(a.cache, exist_ok=True)
    path = lambda sid: os.path.join(a.cache, f"shot-{sid:03d}.npz")
    todo = [s for s in shots if not os.path.isfile(path(s["id"]))]
    if not todo:
        print("scan: cache complete")
        return 0
    first = todo[0]["id"]
    print(f"scan: {len(todo)} shots to do, decoding from shot {first} ({shots[first]['start_s']:.2f} s)", flush=True)
    frames = decode(a.film, shots[first]["start_s"] if first else 0.0)
    t0, done_frames = time.perf_counter(), 0
    for s in shots[first:first + a.limit] if a.limit else shots[first:]:
        buf = []
        for _ in range(s["frames"]):
            f = next(frames, None)
            if f is None:
                break
            buf.append(f)
        if len(buf) < s["frames"]:
            print(f"warning: shot {s['id']} got {len(buf)} of {s['frames']} frames", file=sys.stderr)
            if not buf:
                break
        n = len(buf)
        offsets, agree = scroll.camera_offsets(buf, n // 2)
        grey = [cv2.cvtColor(f, cv2.COLOR_RGB2GRAY) for f in buf]
        diff = np.full(n, np.nan, np.float32)
        u = np.full((n, len(HYPS), len(PIX), 2), np.nan, np.float32)
        for t in range(1, n):
            g = s["first_frame"] + t
            diff[t] = cv2.absdiff(grey[t], grey[t - 1]).mean()
            u[t] = band_u(grey[t - 1], grey[t], {"flow": offsets[t] - offsets[t - 1], "pc": (dx[g] / 4.0, dy[g] / 4.0)})
        reps = pick_reps(offsets)
        tmp = path(s["id"]) + ".tmp.npz"
        np.savez_compressed(tmp, reps=np.stack([buf[i] for i in reps]), rep_idx=np.array(reps, np.int32),
                            offsets=offsets.astype(np.float32), agree=np.array(agree, np.float32), diff=diff, u=u,
                            first_frame=s["first_frame"], n=n)
        os.replace(tmp, path(s["id"]))
        done_frames += n
        if s["id"] % 25 == 0:
            el = time.perf_counter() - t0
            print(f"shot {s['id']}: {done_frames} frames in {el:.0f} s ({done_frames / el:.1f} fps)", flush=True)
    print(f"scan done: {done_frames} frames in {time.perf_counter() - t0:.0f} s", flush=True)
    return 0


# Registration: a pair of shots shows the same place when a similarity maps one's background onto the other's.
RATIO = 0.75                                                      # Lowe ratio test
RANSAC_PX = 3.0                                                   # reprojection tolerance at 480x270
MIN_INLIERS = 20
SCALE_RANGE = (0.4, 2.5)
MAX_ANGLE = 20.0                                                  # degrees; anime cameras rarely roll more
MIN_SPREAD = 0.03                                                 # inliers' bounding box, share of the frame, both frames


def subject_boxes(content_dir):
    """Sampled frame (every 12th) -> person/head/face boxes (x0, y0, x1, y1) at working size, from subjects.json."""
    d = json.load(open(os.path.join(content_dir, "subjects.json"), encoding="utf-8"))
    out = {}
    for sh in d["shots"]:
        for tr in sh["tracks"]:
            for b in tr["boxes"]:
                out.setdefault(b["frame"], []).append((b["x"] / 4, b["y"] / 4, (b["x"] + b["w"]) / 4, (b["y"] + b["h"]) / 4))
    return out


def boxes_at(boxes, frame, lo, hi, stride=12):
    """Boxes of the sampled frame nearest to `frame` within the shot [lo, hi]; [] when the shot has no sampled frame."""
    cands = [f for f in (frame // stride * stride, frame // stride * stride + stride) if lo <= f <= hi]
    if not cands:
        return []
    return boxes.get(min(cands, key=lambda f: abs(f - frame)), [])


def load_cache(cache, sid):
    return np.load(os.path.join(cache, f"shot-{sid:03d}.npz"))


def shot_features(args):
    """SIFT keypoints and descriptors of one shot's representative frames, outside subject boxes and letterbox bars."""
    s, cache, boxes = args
    sift = cv2.SIFT_create(nfeatures=800, contrastThreshold=0.02)  # dark episode: more low-contrast keypoints
    z = load_cache(cache, s["id"])
    x0, y0, x1, y1 = (v / 4 for v in s["crop"]["rect"])
    out = []
    for k, t in enumerate(z["rep_idx"].tolist()):
        grey = cv2.cvtColor(z["reps"][k], cv2.COLOR_RGB2GRAY)
        mask = np.zeros((H, W), np.uint8)
        mask[int(np.ceil(y0)) + 3:int(y1) - 3, int(np.ceil(x0)) + 3:int(x1) - 3] = 255
        for bx0, by0, bx1, by1 in boxes_at(boxes, s["first_frame"] + t, s["first_frame"], s["last_frame"]):
            mask[max(0, int(by0) - 4):max(0, int(by1) + 5), max(0, int(bx0) - 4):max(0, int(bx1) + 5)] = 0
        kp, des = sift.detectAndCompute(grey, mask)
        pts = np.float32([p.pt for p in kp]).reshape(-1, 2)
        out.append((t, pts, np.zeros((0, 128), np.uint8) if des is None else des.astype(np.uint8)))
    return s["id"], out


_FEATS = None


def _init_worker(feats_path):
    global _FEATS
    low_priority(1)
    import pickle
    with open(feats_path, "rb") as f:
        _FEATS = pickle.load(f)


def valid(rec, min_inliers=None, min_spread=None):
    min_inliers = MIN_INLIERS if min_inliers is None else min_inliers
    min_spread = MIN_SPREAD if min_spread is None else min_spread
    return (rec.get("inliers", 0) >= min_inliers and SCALE_RANGE[0] <= rec.get("scale", 0) <= SCALE_RANGE[1]
            and abs(rec.get("angle", 0)) <= MAX_ANGLE and min(rec.get("spread", [0])) >= min_spread)


LOOSE = {"min_inliers": 12, "min_spread": 0.01}                    # candidate pick; acceptance re-applies the strict rule


def match_pair(pair):
    """Best similarity mapping a representative frame of shot b onto one of shot a: the valid one with most RANSAC
    inliers (valid: enough inliers, sane scale and roll, inliers spread over both frames); else the strongest invalid
    one, kept for the near-miss sheet. Matches are one-to-one (the closest b point per a point)."""
    a, b = pair
    bf = cv2.BFMatcher(cv2.NORM_L2)
    best, best_bad = None, {"a": a, "b": b, "inliers": 0, "good": 0}
    for ta, pa, da in _FEATS[a]:
        if len(da) < 8:
            continue
        da32 = da.astype(np.float32)
        for tb, pb, db in _FEATS[b]:
            if len(db) < 8:
                continue
            m = bf.knnMatch(db.astype(np.float32), da32, k=2)
            good = {}
            for x in m:
                if len(x) == 2 and x[0].distance < RATIO * x[1].distance:
                    if x[0].trainIdx not in good or x[0].distance < good[x[0].trainIdx].distance:
                        good[x[0].trainIdx] = x[0]
            good = list(good.values())
            if len(good) < 8:
                continue
            src = pb[[g.queryIdx for g in good]]
            dst = pa[[g.trainIdx for g in good]]
            M, inl = cv2.estimateAffinePartial2D(src, dst, method=cv2.RANSAC, ransacReprojThreshold=RANSAC_PX,
                                                 maxIters=2000, confidence=0.995)
            if M is None:
                continue
            inl = inl.ravel().astype(bool)
            ni = int(inl.sum())
            spread = [float(np.prod(np.ptp(p[inl], axis=0)) / (W * H)) if ni > 1 else 0.0 for p in (dst, src)]
            rec = {"a": a, "b": b, "ta": ta, "tb": tb, "good": len(good), "inliers": ni,
                   "scale": float(np.hypot(M[0, 0], M[1, 0])), "angle": float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))),
                   "spread": [round(x, 4) for x in spread], "M": np.round(M, 5).tolist()}
            if valid(rec, **LOOSE):
                if best is None or ni > best["inliers"]:
                    best = rec
            elif ni > best_bad["inliers"]:
                best_bad = rec
    out = best if best is not None else best_bad
    out["accepted"] = valid(out)
    return out


def colour_descriptor(s, cache, boxes):
    """HSV histogram (8x4x4) of the representative frames outside subject boxes: finds places that recur far apart."""
    z = load_cache(cache, s["id"])
    hist = np.zeros((8, 4, 4), np.float32)
    for k, t in enumerate(z["rep_idx"].tolist()):
        mask = np.full((H, W), 255, np.uint8)
        for bx0, by0, bx1, by1 in boxes_at(boxes, s["first_frame"] + t, s["first_frame"], s["last_frame"]):
            mask[max(0, int(by0)):max(0, int(by1)), max(0, int(bx0)):max(0, int(bx1))] = 0
        hsv = cv2.cvtColor(z["reps"][k], cv2.COLOR_RGB2HSV)
        hist += cv2.calcHist([hsv], [0, 1, 2], mask if mask.any() else None, [8, 4, 4], [0, 180, 0, 256, 0, 256])
    return (hist / max(1.0, hist.sum())).ravel()


def components(n, edges):
    """Union-find over shots: set id per shot (sets numbered by their first shot)."""
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b in edges:
        ra, rb = root(a), root(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return [root(i) for i in range(n)]


def reuse_stats(shots, set_of, keep):
    """Sets, shots per set histogram and reuse factor over the shots for which keep(s) holds."""
    chosen = [s for s in shots if keep(s)]
    sets = {}
    for s in chosen:
        sets.setdefault(set_of[s["id"]], []).append(s["id"])
    sizes = np.array([len(v) for v in sets.values()]) if sets else np.zeros(0, int)
    hist = {str(k): int((sizes == k).sum()) for k in range(1, 6)}
    hist["6-10"] = int(((sizes >= 6) & (sizes <= 10)).sum())
    hist[">10"] = int((sizes > 10).sum())
    secs = sum(s["end_s"] - s["start_s"] for s in chosen)
    return {"shots": len(chosen), "sets": len(sets), "reuse_factor": round(len(chosen) / max(1, len(sets)), 3),
            "film_s": round(secs, 1), "plates_per_film_s": round(len(sets) / max(1e-9, secs), 3),
            "shots_per_set_hist": hist, "largest_sets": sorted(sizes.tolist(), reverse=True)[:8]}


def cmd_register(a):
    global MIN_INLIERS, MIN_SPREAD, MAX_ANGLE
    MIN_INLIERS, MIN_SPREAD, MAX_ANGLE = a.min_inliers, a.min_spread, a.max_angle
    rest(a.rest_before)
    low_priority(2)
    import pickle
    from multiprocessing import Pool
    shots = load_shots(a.content_dir)
    boxes = subject_boxes(a.content_dir)
    t0 = time.perf_counter()
    feats_path = os.path.join(a.cache, "sift-features.pkl")
    results_path = os.path.join(a.cache, "pair-results.pkl")
    if a.from_results:                                            # re-apply the acceptance rule, no matching
        with open(results_path, "rb") as f:
            results = pickle.load(f)
        return summarize_sets(a, shots, results, len(results), t0)
    own_boxes = lambda s: {f: v for f, v in boxes.items() if s["first_frame"] <= f <= s["last_frame"]}
    with Pool(a.workers, initializer=low_priority, initargs=(1,)) as pool:
        feats = dict(pool.map(shot_features, [(s, a.cache, own_boxes(s)) for s in shots], chunksize=8))
    with open(feats_path, "wb") as f:
        pickle.dump(feats, f, protocol=pickle.HIGHEST_PROTOCOL)
    kp = [len(d) for v in feats.values() for _, _, d in v]
    print(f"features: {len(kp)} frames, median {int(np.median(kp))} keypoints, {time.perf_counter() - t0:.0f} s", flush=True)

    # Candidates: every pair within +-window s, plus each shot's top-k most similar shots (colour layout) elsewhere.
    desc = np.stack([colour_descriptor(s, a.cache, boxes) for s in shots])
    pairs = set()
    for i, s in enumerate(shots):
        for j in range(i + 1, len(shots)):
            if shots[j]["start_s"] - s["end_s"] > a.window:
                break
            pairs.add((i, j))
    bc = np.sqrt(desc) @ np.sqrt(desc).T                          # Bhattacharyya coefficient (1 = same histogram)
    n_local = len(pairs)
    for i, s in enumerate(shots):
        far = [j for j in np.argsort(-bc[i]) if j != i and (shots[j]["start_s"] - s["end_s"] > a.window
                                                            or s["start_s"] - shots[j]["end_s"] > a.window)]
        for j in far[:a.top_k]:
            pairs.add((min(i, int(j)), max(i, int(j))))
    pairs = sorted(pairs)
    print(f"candidates: {n_local} within +-{a.window} s, {len(pairs) - n_local} far by colour", flush=True)

    with Pool(a.workers, initializer=_init_worker, initargs=(feats_path,)) as pool:
        results = []
        for k, r in enumerate(pool.imap_unordered(match_pair, pairs, chunksize=64)):
            results.append(r)
            if (k + 1) % 5000 == 0:
                print(f"  {k + 1}/{len(pairs)} pairs, {time.perf_counter() - t0:.0f} s", flush=True)
    results.sort(key=lambda r: (r["a"], r["b"]))
    with open(results_path, "wb") as f:                            # the expensive part, kept before any summary
        pickle.dump(results, f, protocol=pickle.HIGHEST_PROTOCOL)
    return summarize_sets(a, shots, results, len(pairs), t0)


def summarize_sets(a, shots, results, n_candidates, t0):
    """Sets (connected components of the accepted pairs) and their reuse statistics -> OUT/x5-sets.json."""
    ed = a.ed_from_shot
    for r in results:                                             # acceptance with this run's thresholds; ED pairs
        r["accepted"] = valid(r) and (ed is None or max(r["a"], r["b"]) < ed)   # match on its centre overlay only
    acc = [(r["a"], r["b"]) for r in results if r["accepted"]]
    set_of = components(len(shots), acc)
    long_ = lambda s: s["end_s"] - s["start_s"] >= 0.8
    region = lambda s: s["region"]["kind"]
    summary = {
        "all": reuse_stats(shots, set_of, lambda s: True),
        "shots_ge_0.8s": reuse_stats(shots, set_of, long_),
        "story": reuse_stats(shots, set_of, lambda s: region(s) == "normal"),
        "story_ge_0.8s": reuse_stats(shots, set_of, lambda s: region(s) == "normal" and long_(s)),
        "op": reuse_stats(shots, set_of, lambda s: region(s) == "op"),
        "credits": reuse_stats(shots, set_of, lambda s: region(s) == "credits"),
        "far_pairs_accepted": sum(1 for r in results if r["accepted"] and abs(shots[r["b"]]["start_s"] - shots[r["a"]]["end_s"]) > a.window),
    }
    out = {"params": {"window_s": a.window, "top_k": a.top_k, "ratio": RATIO, "ransac_px": RANSAC_PX,
                      "min_inliers": MIN_INLIERS, "scale_range": SCALE_RANGE, "max_angle": MAX_ANGLE,
                      "min_spread": MIN_SPREAD, "ed_from_shot": ed, "working_size": [W, H]},
           "candidates": n_candidates, "accepted": len(acc), "summary": summary, "set_of": set_of,
           "pairs": [r for r in results if r["inliers"] >= 8]}
    with open(os.path.join(a.out, "x5-sets.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=None, separators=(",", ":"), default=json_default)
    print(json.dumps({"candidates": n_candidates, "accepted": len(acc), "summary": summary}, indent=1))
    print(f"register done in {time.perf_counter() - t0:.0f} s", flush=True)
    return 0


def pair_panel(r, cache, label):
    """Shot a's frame | shot b's frame | b warped onto a, blended 50/50: does the background line up?"""
    za, zb = load_cache(cache, r["a"]), load_cache(cache, r["b"])
    fa = za["reps"][za["rep_idx"].tolist().index(r["ta"])] if "ta" in r else za["reps"][len(za["rep_idx"]) // 2]
    fb = zb["reps"][zb["rep_idx"].tolist().index(r["tb"])] if "tb" in r else zb["reps"][len(zb["rep_idx"]) // 2]
    if "M" in r:
        warped = cv2.warpAffine(fb, np.float32(r["M"]), (W, H))
        blend = cv2.addWeighted(fa, 0.5, warped, 0.5, 0)
    else:
        blend = np.zeros_like(fa)
    row = np.hstack([fa, fb, blend])
    bar = np.zeros((22, row.shape[1], 3), np.uint8)
    cv2.putText(bar, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([bar, row])


def cmd_sheet(a):
    """Accepted pairs spread over the inlier range (weakest first) and rejected near misses, for the by-eye check."""
    d = json.load(open(os.path.join(a.out, "x5-sets.json"), encoding="utf-8"))
    rng = np.random.default_rng(a.seed)
    acc = sorted([r for r in d["pairs"] if r["accepted"]], key=lambda r: r["inliers"])
    rej = [r for r in d["pairs"] if not r["accepted"] and r["inliers"] >= 10]
    picks = {"accepted-weak": acc[:a.n], "accepted-random": [acc[i] for i in sorted(rng.choice(len(acc), min(a.n, len(acc)), replace=False))],
             "rejected-near": [rej[i] for i in sorted(rng.choice(len(rej), min(a.n, len(rej)), replace=False))] if rej else []}
    for name, rows in picks.items():
        if not rows:
            continue
        panels = [pair_panel(r, a.cache, f"shots {r['a']}/{r['b']} frames {r.get('ta')}/{r.get('tb')}  inl {r['inliers']} "
                                         f"good {r['good']} s {r.get('scale', 0):.2f} rot {r.get('angle', 0):.1f} "
                                         f"spread {r.get('spread')}  {'ACC' if r['accepted'] else 'rej'}") for r in rows]
        sheet = np.vstack(panels)
        cv2.imwrite(os.path.join(a.out, f"x5-sheet-{name}.png"), cv2.cvtColor(sheet, cv2.COLOR_RGB2BGR))
        print(f"x5-sheet-{name}.png: {len(rows)} pairs")
    return 0


# Coverage: masks on each shot's own key-frame grid (its middle frame's coordinates, extended to every frame's 32:9 canvas).
TAUS = (0.02, 0.10, 0.30)                                         # GEN trigger: worse band's U above tau
REPEAT_BELOW = 0.5                                                # content model diff_before below this: the drawing repeats
PLATE_COVER = (0.8, 0.95)                                         # an earlier plate serves a shot if it covers this share
MAX_PLATE_ZOOM = 1.5                                              # ... and is not magnified more than this


MIN_AGREE = 0.3                                                   # flow agreement below which a step is not trusted
MAX_STEP = W / 4                                                  # px per frame above which a step is not trusted


def gated_offsets(z):
    """The camera path the compositor would follow: untrusted steps (low flow agreement or an implausible jump) count as
    no motion ("do not slide on low agreement"), re-chained and relative to the middle frame again."""
    off = z["offsets"].astype(np.float64)
    if len(off) < 2:
        return off
    steps = np.diff(off, axis=0)
    bad = (z["agree"][1:] < MIN_AGREE) | (np.abs(steps).max(axis=1) > MAX_STEP)
    steps[bad] = 0.0
    acc = np.vstack([np.zeros(2), np.cumsum(steps, axis=0)])
    return acc - acc[len(off) // 2]


def shot_masks(s, z, boxes):
    """Per frame, the picture's origin on the shot's grid; masks on the grid: seen (some frame's picture), seen without
    subject boxes, plate (some frame's 32:9 canvas: what a plate painted for this shot covers)."""
    off = z["offsets"]
    xs, ys = np.round(-off[:, 0]).astype(int), np.round(-off[:, 1]).astype(int)
    ox, oy = int(xs.min()) - SIDE, int(ys.min())
    gw, gh = int(xs.max()) + W + SIDE - ox, int(ys.max()) + H - oy
    px, py = xs - ox, ys - oy
    seen, seen_np, plate = (np.zeros((gh, gw), np.uint8) for _ in range(3))
    ff, lf = s["first_frame"], s["last_frame"]
    for t in range(len(off)):
        seen[py[t]:py[t] + H, px[t]:px[t] + W] = 1
        plate[py[t]:py[t] + H, px[t] - SIDE:px[t] + W + SIDE] = 1
        if t % 2 == 0 or t == len(off) - 1:                         # every other frame is enough for the union
            m = np.ones((H, W), np.uint8)
            for bx0, by0, bx1, by1 in boxes_at(boxes, ff + t, ff, lf):
                m[max(0, int(by0)):max(0, int(np.ceil(by1))), max(0, int(bx0)):max(0, int(np.ceil(bx1)))] = 0
            seen_np[py[t]:py[t] + H, px[t]:px[t] + W] |= m
    return {"px": px, "py": py, "origin": (ox, oy), "size": (gw, gh), "seen": seen, "seen_np": seen_np, "plate": plate}


def side_cover(mask, g):
    """Per frame: share of the two 32:9 sides (SIDE x H each) that `mask` (on the shot's grid) covers."""
    ii = cv2.integral(mask)
    px, py = g["px"], g["py"]
    rs = lambda x0, x1: ii[py + H, x1] - ii[py, x1] - ii[py + H, x0] + ii[py, x0]
    return (rs(px - SIDE, px) + rs(px + W, px + W + SIDE)) / (2.0 * SIDE * H)


def key_transform(r, za, zb):
    """2x3 similarity mapping shot b's key-frame coordinates to shot a's, from the registered frame pair (tb -> ta).
    Key content at p appears at p + offsets[t] in frame t."""
    M = np.float64(r["M"])
    A, c = M[:, :2], M[:, 2]
    d_a, d_b = za["offsets"][r["ta"]].astype(np.float64), zb["offsets"][r["tb"]].astype(np.float64)
    return np.hstack([A, (A @ d_b + c - d_a)[:, None]])


def compose(t1, t2):
    """t1 after t2 (2x3 affine)."""
    return np.hstack([t1[:, :2] @ t2[:, :2], (t1[:, :2] @ t2[:, 2] + t1[:, 2])[:, None]])


def to_grid(t, src_g, dst_g):
    """A key-to-key transform as a grid-to-grid one (each grid starts at its origin in key coordinates)."""
    shift = np.float64([[1, 0, src_g["origin"][0]], [0, 1, src_g["origin"][1]]])
    back = np.float64([[1, 0, -dst_g["origin"][0]], [0, 1, -dst_g["origin"][1]]])
    return compose(back, compose(t, shift))


def time_hist(values, weights, edges=(0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0001)):
    h, _ = np.histogram(values, bins=edges, weights=weights)
    return {f"{edges[i]:.2f}-{min(1.0, edges[i + 1]):.2f}": round(float(x / max(1e-9, weights.sum())), 4) for i, x in enumerate(h)}


def dist(values, weights=None):
    v = np.asarray(values, np.float64)
    w = np.ones_like(v) if weights is None else np.asarray(weights, np.float64)
    order = np.argsort(v)
    cw = np.cumsum(w[order]) / w.sum()
    q = lambda p: round(float(v[order][min(len(v) - 1, np.searchsorted(cw, p))]), 4)
    return {"mean": round(float((v * w).sum() / w.sum()), 4), "p10": q(0.1), "p25": q(0.25), "p50": q(0.5), "p75": q(0.75),
            "p90": q(0.9), "share_ge_0.5": round(float(w[v >= 0.5].sum() / w.sum()), 4),
            "share_ge_0.9": round(float(w[v >= 0.9].sum() / w.sum()), 4), "share_eq_0": round(float(w[v <= 1e-6].sum() / w.sum()), 4)}


def cmd_coverage(a):
    rest(a.rest_before)
    low_priority(2)
    shots = load_shots(a.content_dir)
    reg = json.load(open(os.path.join(a.out, "x5-sets.json"), encoding="utf-8"))
    set_of = reg["set_of"]
    boxes = subject_boxes(a.content_dir)
    diff_before = signal(a.content_dir, "diff_before").astype(np.float32)
    fps = 24000 / 1001
    t0 = time.perf_counter()
    zs = {}
    for s in shots:                                               # the arrays only; the cached frames stay on disk
        z = load_cache(a.cache, s["id"])
        zs[s["id"]] = {k: z[k] for k in ("offsets", "agree", "u", "n", "first_frame")}
        zs[s["id"]]["raw_offsets"] = zs[s["id"]]["offsets"]
        zs[s["id"]]["offsets"] = gated_offsets(zs[s["id"]])
    grids = {s["id"]: shot_masks(s, zs[s["id"]], boxes) for s in shots}
    print(f"masks: {time.perf_counter() - t0:.0f} s", flush=True)
    adj = {s["id"]: [] for s in shots}
    for r in reg["pairs"]:
        if r["accepted"]:
            t_ba = key_transform(r, zs[r["a"]], zs[r["b"]])
            adj[r["a"]].append((r["inliers"], r["b"], t_ba))
            adj[r["b"]].append((r["inliers"], r["a"], cv2.invertAffineTransform(t_ba)))
    long_ = {s["id"]: s["end_s"] - s["start_s"] >= 0.8 for s in shots}
    # Story or ED (identical every episode, cacheable): by eye when --ed-from-shot is given (the content model's
    # chapter regions can be wrong), else the content model's op/credits regions count as ED.
    if a.ed_from_shot is not None:
        region_of = lambda s: "ed" if s["id"] >= a.ed_from_shot else "story"
    else:
        region_of = lambda s: "ed" if s["region"]["kind"] in ("op", "credits") else "story"

    per_shot, frame_rows = [], []
    owners = {(th, skip): [] for th in PLATE_COVER for skip in (False, True)}
    plate_of = {k: {} for k in owners}
    gen_frames = {}
    for s in shots:
        x = s["id"]
        gx = grids[x]
        # Transform of every reachable set member into this shot's key frame: breadth first, strongest pair first.
        tf, queue = {x: np.float64([[1, 0, 0], [0, 1, 0]])}, [x]
        while queue:
            u = queue.pop(0)
            for _, v, t_vu in sorted(adj[u], key=lambda e: -e[0]):
                if v not in tf:
                    tf[v] = compose(tf[u], t_vu)
                    queue.append(v)
        union, union_np = gx["seen"].copy(), gx["seen_np"].copy()
        donors, plate_cover = 0, {}
        for y, t in tf.items():
            if y == x:
                continue
            zoom = float(np.sqrt(abs(np.linalg.det(t[:, :2]))))
            if not SCALE_RANGE[0] <= zoom <= SCALE_RANGE[1]:
                continue
            gy = grids[y]
            m = to_grid(t, gy, gx).astype(np.float32)
            union |= cv2.warpAffine(gy["seen"], m, gx["size"], flags=cv2.INTER_NEAREST)
            union_np |= cv2.warpAffine(gy["seen_np"], m, gx["size"], flags=cv2.INTER_NEAREST)
            donors += 1
            if y < x and zoom <= MAX_PLATE_ZOOM:                   # an earlier shot's plate, mapped into this shot
                plate_cover[y] = float(side_cover(cv2.warpAffine(gy["plate"], m, gx["size"], flags=cv2.INTER_NEAREST), gx).mean())
        cov = {"own": side_cover(gx["seen"], gx), "set": side_cover(union, gx),
               "own_np": side_cover(gx["seen_np"], gx), "set_np": side_cover(union_np, gx)}
        # Plates: a shot inherits the best-covering earlier plate (owner) if it covers >= th; else it owns a new one
        # (unless it is shorter than 0.8 s and short shots skip their plate).
        for (th, skip), own in owners.items():
            best = max(own, key=lambda p: plate_cover.get(p, 0.0), default=None)
            if best is not None and plate_cover.get(best, 0.0) >= th:
                plate_of[(th, skip)][x] = best
            elif skip and not long_[x]:
                plate_of[(th, skip)][x] = None
            else:
                own.append(x)
                plate_of[(th, skip)][x] = x
        # GEN drawings: new drawings (not repeats) whose worse 25% band has U above tau.
        z = zs[x]
        n, ff = int(z["n"]), int(z["first_frame"])
        new = np.zeros(n, bool)
        new[1:] = diff_before[ff + 1:ff + n] >= REPEAT_BELOW
        worse = np.nanmax(z["u"], axis=-1)                         # (n, hyps, thresholds): the worse side decides
        gen = {}
        for hi, hyp in enumerate(HYPS):
            for pj, thr in enumerate(PIX):
                for tau in TAUS:
                    idx = np.nonzero(new & (np.nan_to_num(worse[:, hi, pj]) > tau))[0]
                    gen[f"{hyp}-{thr}-{tau}"] = len(idx)
                    if hyp in ("flow", "pc") and thr == 16:
                        gen_frames.setdefault(f"{hyp}-{thr}-{tau}", {})[x] = idx.tolist()
        side_gen = int((new & (z["u"][:, 1, 0, 0] > 0.10)).sum() + (new & (z["u"][:, 1, 0, 1] > 0.10)).sum())
        dur = s["end_s"] - s["start_s"]
        per_shot.append({"shot": x, "start_s": round(s["start_s"], 3), "end_s": round(s["end_s"], 3), "first_frame": ff, "frames": n,
                         "region": region_of(s), "region_model": s["region"]["kind"], "set": set_of[x],
                         "set_members_reached": len(tf) - 1,
                         "donors": donors, **{f"cover_{k}": round(float(v.mean()), 4) for k, v in cov.items()},
                         "new_drawings": int(new.sum()), "gen": gen, "gen_sides_flow16_0.1": side_gen,
                         "gen_per_s_flow16_0.1": round(gen["flow-16-0.1"] / max(dur, 1e-9), 3),
                         "plate_of": {f"{th}{'-skip' if skip else ''}": plate_of[(th, skip)][x] for th, skip in owners}})
        frame_rows.append(np.stack([cov["own"], cov["set"], cov["own_np"], cov["set_np"]], axis=1))
        if x % 100 == 0:
            print(f"shot {x}: {time.perf_counter() - t0:.0f} s", flush=True)

    # Summaries: coverage over shots and over film time (every frame weighs its display time), GEN rates.
    frames_cov = np.concatenate(frame_rows)
    region_f = np.concatenate([[p["region"]] * p["frames"] for p in per_shot])
    names = ("own", "set", "own_np", "set_np")
    film_s = shots[-1]["end_s"]
    cover = {}
    for reg_name, sel in (("all", np.ones(len(region_f), bool)), ("story", region_f == "story"), ("ed", region_f == "ed")):
        w = np.ones(int(sel.sum()))
        cover[reg_name] = {"film_time": {k: dist(frames_cov[sel, i], w) for i, k in enumerate(names)},
                           "film_time_hist": {k: time_hist(frames_cov[sel, i], w) for i, k in enumerate(names)}}
        ps = [p for p in per_shot if reg_name == "all" or p["region"] == reg_name]
        cover[reg_name]["per_shot"] = {k: dist([p[f"cover_{k}"] for p in ps]) for k in names}
    gen_rate = {}
    for key in per_shot[0]["gen"]:
        total = sum(p["gen"][key] for p in per_shot)
        gen_rate[key] = round(total / film_s, 3)
    new_rate = round(sum(p["new_drawings"] for p in per_shot) / film_s, 3)
    per_sec = {}
    for key, frames in gen_frames.items():
        tbin = np.zeros(int(np.ceil(film_s)), np.int32)
        for x, idx in frames.items():
            for t in idx:
                tbin[min(len(tbin) - 1, int((shots[x]["first_frame"] + t) / fps))] += 1
        per_sec[key] = {"mean": round(float(tbin.mean()), 3), "p50": int(np.percentile(tbin, 50)),
                        "p90": int(np.percentile(tbin, 90)), "p99": int(np.percentile(tbin, 99)), "max": int(tbin.max()),
                        "share_of_seconds_with_none": round(float((tbin == 0).mean()), 3)}
    reg_of = {s["id"]: region_of(s) for s in shots}
    plates = {}
    for (th, skip), own in owners.items():
        k = f"{th}{'-skip' if skip else ''}"
        long_shots = [s for s in shots if long_[s["id"]]]
        plates[k] = {"plates": len(own), "plates_per_film_s": round(len(own) / film_s, 3),
                     "shots_per_plate": round(len(shots) / max(1, len(own)), 3),
                     "shots_without_plate": sum(1 for v in plate_of[(th, skip)].values() if v is None),
                     "story_plates": sum(1 for p in own if reg_of[p] == "story"),
                     "long_shots_per_plate": round(len(long_shots) / max(1, len(own)), 3)}
    sets = {name: reuse_stats(shots, set_of, keep) for name, keep in (
        ("all", lambda s: True), ("ge_0.8s", lambda s: long_[s["id"]]),
        ("story", lambda s: reg_of[s["id"]] == "story"),
        ("story_ge_0.8s", lambda s: reg_of[s["id"]] == "story" and long_[s["id"]]),
        ("ed", lambda s: reg_of[s["id"]] == "ed"))}
    out = {"params": {"working_size": [W, H], "side_px": SIDE, "band_px": BAND, "pixel_thresholds": PIX, "taus": TAUS,
                      "repeat_below": REPEAT_BELOW, "plate_cover": PLATE_COVER, "max_plate_zoom": MAX_PLATE_ZOOM,
                      "ed_from_shot": a.ed_from_shot},
           "film_s": film_s, "sets": sets, "coverage": cover, "new_drawings_per_s": new_rate, "gen_drawings_per_s": gen_rate,
           "gen_per_film_second": per_sec, "plates_by_cover": plates, "shots": per_shot}
    with open(os.path.join(a.out, "x5-coverage.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, default=json_default)
    with open(os.path.join(a.out, "x5-gen-frames.json"), "w", encoding="utf-8") as f:
        json.dump({k: {str(x): v for x, v in d.items()} for k, d in gen_frames.items()}, f, separators=(",", ":"),
                  default=json_default)
    print(json.dumps({k: v for k, v in out.items() if k != "shots"}, indent=1)[:6000])
    print(f"coverage done in {time.perf_counter() - t0:.0f} s", flush=True)
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan")
    p.add_argument("film")
    p.add_argument("content_dir")
    p.add_argument("cache")
    p.add_argument("--limit", type=int, default=0, help="stop after this many shots (a test run)")
    p = sub.add_parser("register")
    p.add_argument("content_dir")
    p.add_argument("cache")
    p.add_argument("out")
    p.add_argument("--window", type=float, default=90.0, help="seconds: every pair of shots this close is a candidate")
    p.add_argument("--top-k", type=int, default=8, help="far candidates per shot, by colour similarity")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--from-results", action="store_true", help="re-apply the acceptance rule to CACHE/pair-results.pkl")
    p.add_argument("--min-inliers", type=int, default=MIN_INLIERS)
    p.add_argument("--min-spread", type=float, default=MIN_SPREAD)
    p.add_argument("--max-angle", type=float, default=MAX_ANGLE)
    p.add_argument("--ed-from-shot", type=int, default=None, help="first shot of the ending (by eye): its pairs match on "
                   "the ending's centre overlay, not on a shared background, and are not accepted")
    p = sub.add_parser("sheet")
    p.add_argument("content_dir")
    p.add_argument("cache")
    p.add_argument("out")
    p.add_argument("--n", type=int, default=14, help="pairs per sheet")
    p.add_argument("--seed", type=int, default=5)
    p = sub.add_parser("coverage")
    p.add_argument("content_dir")
    p.add_argument("cache")
    p.add_argument("out")
    p.add_argument("--ed-from-shot", type=int, default=None, help="first shot of the ending (by eye); later shots = ED")
    for p in sub.choices.values():
        p.add_argument("--rest-before", type=int, default=0, help="seconds to sleep first (let the PC rest between runs)")
    a = ap.parse_args(argv)
    return {"scan": cmd_scan, "register": cmd_register, "sheet": cmd_sheet, "coverage": cmd_coverage}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
