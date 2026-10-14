"""Hidden-sides test: give a candidate only the centre of real frames, let it redraw the sides, compare with the truth.

Geometry: the truth is the full frame (W x H). For side share s the visible centre is W / (1 + 2 s) wide and each hidden side
is s times the visible width, so the canvas is (1 + 2 s) times the picture's width, as on the real screens (s = 0.5 is
16:9 on 32:9). Widths are rounded to even pixels; the truth canvas is the full frame cut to the same total width.

Scores, on the hidden sides only, 0-255 code values of 8-bit sRGB:
  err_near   mean absolute error in the band next to the seam (1/8 of the side's width): does the picture continue
  err_far    the same in the outer half of each side: do colour and brightness stay right far from the picture
  seam_step  mean jump across the seam divided by the truth's jump at the same place (1.0: as smooth as the truth)
  flicker    mean frame-to-frame change of the sides minus the truth's (0: no extra flicker; > 0: the fill pumps or boils)
A low error is not the goal by itself: a plausible but different continuation is fine. The by-eye check uses the truth as
its anchor (compose_sheet puts candidate and truth one above the other).
"""
import json
import os
import shutil
import subprocess

import cv2
import numpy as np


def geometry(w, s):
    """(visible width, side width, canvas width) in pixels for a picture w wide and side share s."""
    vis = int(round(w / (1.0 + 2.0 * s) / 2.0)) * 2
    side = (w - vis) // 2
    return vis, side, vis + 2 * side


def read_frames(path, max_frames=None):
    """All frames of a clip as RGB uint8 arrays (H, W, 3)."""
    cap = cv2.VideoCapture(path)
    frames = []
    while max_frames is None or len(frames) < max_frames:
        ok, bgr = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    cap.release()
    if not frames:
        raise RuntimeError(f"no frames read from {path}")
    return frames


def split(frame, s):
    """(truth canvas, visible centre, side width) for one full frame."""
    w = frame.shape[1]
    vis, side, canvas = geometry(w, s)
    x0 = (w - canvas) // 2
    truth = frame[:, x0:x0 + canvas]
    return truth, truth[:, side:side + vis], side


def score(truth_frames, cand_frames, side):
    """Scores of one candidate clip against the truth clip (lists of equal-size canvases)."""
    t = np.stack(truth_frames).astype(np.float32)
    c = np.stack(cand_frames).astype(np.float32)
    w = t.shape[2]
    near = max(2, side // 8)
    sides = np.r_[0:side, w - side:w]
    near_cols = np.r_[side - near:side, w - side:w - side + near]
    far_cols = np.r_[0:side // 2, w - side // 2:w]
    err = np.abs(c - t)
    # Seam jump: difference between the last visible column and the first side column, both sides.
    def jump(x):
        return (np.abs(x[:, :, side] - x[:, :, side - 1]).mean() + np.abs(x[:, :, w - side - 1] - x[:, :, w - side]).mean()) / 2
    seam = jump(c) / max(1e-6, jump(t))
    if len(t) > 1:
        flick = np.abs(np.diff(c[:, :, sides], axis=0)).mean() - np.abs(np.diff(t[:, :, sides], axis=0)).mean()
    else:
        flick = 0.0
    return {"err_near": round(float(err[:, :, near_cols].mean()), 2), "err_far": round(float(err[:, :, far_cols].mean()), 2),
            "seam_step": round(float(seam), 2), "flicker": round(float(flick), 2)}


def write_video(frames, path, fps):
    """Frames (RGB uint8) to an H.264 file through ffmpeg on PATH (or PANORAMA_FFMPEG)."""
    ffmpeg = os.environ.get("PANORAMA_FFMPEG") or shutil.which("ffmpeg")
    h, w = frames[0].shape[:2]
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
           "-r", f"{fps:.5f}", "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for f in frames:
        p.stdin.write(np.ascontiguousarray(f).tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise RuntimeError(f"ffmpeg failed writing {path}")


def colour_fit(model_centre, real_centre, shrink=8):
    """(4, 3) matrix M with real ~ [r, g, b, 1] @ M of a model's own redraw of the visible centre: the model's colour shift
    (a dropped haze, a warmer tint). Least squares on area-shrunk copies, so line art need not line up. Images may be one
    canvas (H, W, 3) or a stack of them (N, H, W, 3)."""
    m_all, r_all = [], []
    for m_img, r_img in zip(np.reshape(model_centre, (-1,) + model_centre.shape[-3:]),
                            np.reshape(real_centre, (-1,) + real_centre.shape[-3:])):
        h, w = r_img.shape[:2]
        size = (max(8, w // shrink), max(8, h // shrink))
        m_all.append(cv2.resize(m_img.astype(np.float32), size, interpolation=cv2.INTER_AREA).reshape(-1, 3))
        r_all.append(cv2.resize(r_img.astype(np.float32), size, interpolation=cv2.INTER_AREA).reshape(-1, 3))
    m, r = np.vstack(m_all).astype(np.float64), np.vstack(r_all).astype(np.float64)
    M, *_ = np.linalg.lstsq(np.hstack([m, np.ones((len(m), 1))]), r, rcond=None)
    return M.astype(np.float32)


def apply_fit(img, M):
    """Float image with the colour matrix from colour_fit applied (not clipped)."""
    h, w = img.shape[:2]
    flat = np.hstack([img.reshape(-1, 3).astype(np.float32), np.ones((h * w, 1), np.float32)])
    return (flat @ M).reshape(h, w, 3)


def flow_step(dis, g_prev, g_cur, stride=1):
    """The camera's step between two grey images of one size, in their pixels: (step (dx, dy), agreement). Dense optical
    flow (DIS) from g_prev to g_cur; the step is the most common flow vector (the background wins by area, not by
    contrast: phase correlation locked onto a character the camera follows), refined as the median of the vectors within
    1.5 px of it; agreement is the share of pixels within 1.5 px. Content moved by +step. stride > 1 votes with every
    stride-th vector in each direction (faster, the same answer on smooth fields)."""
    flow = dis.calc(g_prev, g_cur, None)[8:-8:stride, 8:-8:stride].reshape(-1, 2)  # where each pixel of g_prev went in g_cur
    lo, hi = np.floor(flow.min(0)) - 1, np.ceil(flow.max(0)) + 1
    bins = [max(1, int((hi[i] - lo[i]) / 0.5)) for i in (0, 1)]
    hist, ex, ey = np.histogram2d(flow[:, 0], flow[:, 1], bins=bins, range=[[lo[0], hi[0]], [lo[1], hi[1]]])
    i, j = np.unravel_index(np.argmax(hist), hist.shape)
    mode = np.array([(ex[i] + ex[i + 1]) / 2, (ey[j] + ey[j + 1]) / 2])
    near = np.abs(flow - mode).max(1) <= 1.5
    step = np.median(flow[near], axis=0) if near.any() else mode
    return step, float(near.mean())


def live_seam(canvas, vis, side, band=4, sigma=24.0, falloff=0.25):
    """canvas: float canvas (a plate, or one frame of a video fill); vis: this frame's real centre. Returns the canvas with
    each side pulled toward the picture's edge: jump = mean of `band` picture columns at the edge minus mean of `band` side
    columns outside it, per row, blurred along the edge (sigma px, so line art crossing the seam leaves no streak), faded by
    exp(-distance / (falloff x side)). What a performer shader can do per film frame."""
    vis_w = vis.shape[1]
    v = vis.astype(np.float32)
    jl = v[:, :band].mean(1) - canvas[:, side - band:side].mean(1)                      # (H, 3)
    jr = v[:, vis_w - band:].mean(1) - canvas[:, side + vis_w:side + vis_w + band].mean(1)
    k = 2 * int(np.ceil(3 * sigma)) + 1
    jl = cv2.GaussianBlur(jl, (1, k), 0, sigmaY=sigma)
    jr = cv2.GaussianBlur(jr, (1, k), 0, sigmaY=sigma)
    fade = np.exp(-np.arange(side, dtype=np.float32) / max(1.0, falloff * side))        # 1 next to the seam
    out = canvas.copy()
    out[:, :side] += jl[:, None, :] * fade[::-1][None, :, None]
    out[:, side + vis_w:] += jr[:, None, :] * fade[None, :, None]
    return out


def write_stacked(truth_frames, cand_frames, side, path, fps, width=1280):
    """Truth above candidate, seams marked, one video: the by-eye check of motion and flicker with the truth as anchor."""
    h, w = truth_frames[0].shape[:2]
    size = (width, int(round(2 * h * width / w / 2)) * 2)
    write_video([cv2.resize(np.vstack([mark_seams(t, side), mark_seams(c, side)]), size, interpolation=cv2.INTER_AREA)
                 for t, c in zip(truth_frames, cand_frames)], path, fps)


def finish_video_fill(model_frames, parts, base, runs_dir, scene, fps, s, info, scores, sheet_name):
    """A video model's whole canvas per frame (uint8, any size) -> two fills, `base` as drawn and `base`-fit with one
    colour matrix per clip (its own redraw of the centre mapped onto the real centre, every 8th frame), the real centre
    pasted back in both. Writes runs_dir/<fill>/<scene>/s<s>.mp4 and -with-truth.mp4, adds the scores (plus `info`) to
    `scores`, and a sheet of the middle frame to runs_dir/sheets/<sheet_name>. parts = [split(frame, s) ...]."""
    truth = [p[0] for p in parts]
    side, vis_w = parts[0][2], parts[0][1].shape[1]
    h, w = truth[0].shape[:2]
    model = [cv2.resize(f, (w, h), interpolation=cv2.INTER_LANCZOS4) for f in model_frames]
    M = colour_fit(np.stack([m[:, side:side + vis_w] for m in model[::8]]), np.stack([p[1] for p in parts[::8]]))
    cands = {base: [], base + "-fit": []}
    for m, (_, vis, _) in zip(model, parts):
        for name, img in ((base, m.astype(np.float32)), (base + "-fit", apply_fit(m, M))):
            c = np.clip(img, 0, 255).round().astype(np.uint8)
            c[:, side:side + vis_w] = vis                         # the real picture is never the model's
            cands[name].append(c)
    mid = len(truth) // 2
    rows, labels = [mark_seams(truth[mid], side)], [f"{scene}  s={s}  TRUTH (frame {mid})"]
    for name, cand in cands.items():
        sc = score(truth, cand, side)
        sc.update(info)
        scores.setdefault(name, {}).setdefault(scene, {})[f"s{s}"] = sc
        odir = os.path.join(runs_dir, name, scene)
        os.makedirs(odir, exist_ok=True)
        write_video(cand, os.path.join(odir, f"s{s}.mp4"), fps)
        write_stacked(truth, cand, side, os.path.join(odir, f"s{s}-with-truth.mp4"), fps)
        rows.append(mark_seams(cand[mid], side))
        labels.append(f"{name}  {info.get('seconds_per_frame', '?')} s/frame  near {sc['err_near']}  far {sc['err_far']}  "
                      f"seam x{sc['seam_step']}  flicker {sc['flicker']:+}")
        print(f"{name:34s} {scene:18s} {sc}", flush=True)
    os.makedirs(os.path.join(runs_dir, "sheets"), exist_ok=True)
    compose_sheet(rows, labels, os.path.join(runs_dir, "sheets", sheet_name), width=1280)


def mark_seams(img, side, colour=(255, 255, 0)):
    """Thin dashed lines where the visible picture ends, so the eye knows what was given."""
    out = img.copy()
    h, w = out.shape[:2]
    for x in (side, w - side - 1):
        for y in range(0, h, 12):
            out[y:y + 6, x] = colour
    return out


def compose_sheet(rows, labels, out_path, width=1920):
    """Stack canvases (same size) vertically with a label strip each; scaled to `width`."""
    tiles = []
    for img, label in zip(rows, labels):
        scale = width / img.shape[1]
        im = cv2.resize(img, (width, int(round(img.shape[0] * scale))), interpolation=cv2.INTER_AREA)
        strip = np.zeros((22, width, 3), np.uint8)
        cv2.putText(strip, label, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        tiles += [strip, im]
    cv2.imwrite(out_path, cv2.cvtColor(np.vstack(tiles), cv2.COLOR_RGB2BGR))


def save_json(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
