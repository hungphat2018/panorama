"""Self-test of the set atlas (live/set_atlas.py) on synthetic shots of one known world: the registration finds the
similarity between two shots (one panning, one zoomed and shifted), a third shot of another place stays alone, and the
atlas puts both shots' real pixels where they belong (their overlap agrees, and the frames' transforms into the set's
world sample the atlas where the picture is). CPU, a few seconds.

    python -I -X utf8 live-sets-selftest.py
"""
import os
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from live import mosaic_t1, set_atlas  # noqa: E402
from live.analyse_v0 import ShotAnalysis  # noqa: E402

SRC_W, SRC_H, DEC_W = 1920, 1080, 960
DS = SRC_W / DEC_W                                                # source px per decoded px
FAILS = []


def check(ok, what):
    print(f"  {'ok  ' if ok else 'FAIL'} {what}", flush=True)
    if not ok:
        FAILS.append(what)


def world_image(seed, w=4200, h=1600):
    """A textured 'place': blurred noise plus edges and blobs, so SIFT finds structure everywhere."""
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur(rng.integers(0, 255, (h // 8, w // 8, 3), dtype=np.uint8), (0, 0), 1.2)
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_CUBIC)
    for _ in range(400):
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        x, y, r = int(rng.integers(0, w)), int(rng.integers(0, h)), int(rng.integers(8, 60))
        if rng.random() < 0.5:
            cv2.circle(img, (x, y), r, c, -1)
        else:
            cv2.rectangle(img, (x, y), (x + r * 2, y + r), c, 3)
    return img


def render(world, cx, cy, zoom):
    """The source frame (1920x1080) of a camera centred on world (cx, cy) at `zoom` (world px per source px = 1/zoom),
    returned at the decode size."""
    a = 1.0 / zoom
    m = np.float64([[a, 0, cx - a * SRC_W / 2], [0, a, cy - a * SRC_H / 2]])   # source px -> world px
    m_dec = m @ np.float64([[DS, 0, 0.5 * DS - 0.5], [0, DS, 0.5 * DS - 0.5], [0, 0, 1]])   # decoded px centre
    return cv2.warpAffine(world, m_dec, (DEC_W, int(SRC_H / DS)), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,
                          borderMode=cv2.BORDER_REFLECT)


def make_shot(shot_id, world, centres, zoom):
    """A ShotAnalysis of frames looking at the world at `centres` (one per frame); offsets are the true camera path in
    source px (content of frame 0 at p sits at p + offset in frame i: the camera moved by -offset)."""
    n = len(centres)
    c0 = np.float64(centres[0])
    offsets = np.array([(c0 - np.float64(c)) * zoom for c in centres], np.float64)
    kept = [(i, render(world, *centres[i], zoom)) for i in sorted(set([0, n // 3, 2 * n // 3, n - 1]))]
    return ShotAnalysis(shot_id=shot_id, first=1000 * shot_id, n=n, crop=(0.0, 0.0, float(SRC_W), float(SRC_H)),
                        decode_scale=DS, from_content_model=True, offsets=offsets, agree=np.ones(n, np.float32),
                        luma=np.full(n, 0.2, np.float32), new_drawing=np.zeros(n, bool), kept=kept)


def shot_to_world(c0, zoom):
    """The true map of a shot's world px (= its first frame's source px) to the place's world px."""
    a = 1.0 / zoom
    return np.float64([[a, 0, c0[0] - a * SRC_W / 2], [0, a, c0[1] - a * SRC_H / 2]])


def main():
    print("set atlas self-test")
    w1, w2 = world_image(1), world_image(2)
    a_centres = [(1500 + 6 * i, 800) for i in range(40)]              # shot A pans right 240 world px
    b_centres = [(1900, 760 + 2 * i) for i in range(30)]              # shot B: zoomed in 1.25, tilts down a little
    sa = make_shot(1, w1, a_centres, 1.0)
    sb = make_shot(2, w1, b_centres, 1.25)
    sc = make_shot(3, w2, [(2000, 800)] * 20, 1.0)                    # another place
    fa, fb, fc = (set_atlas.features_of(s, None, start_s=float(i)) for i, s in enumerate((sa, sb, sc)))
    check(all(f is not None and len(f.pts) > 200 for f in (fa, fb, fc)), "features on every shot")

    r = set_atlas.match(fa, fb)
    truth = set_atlas.compose(set_atlas.invert(shot_to_world(a_centres[0], 1.0)), shot_to_world(b_centres[0], 1.25))
    check(r["accepted"], f"A-B accepted ({r['inliers']} inliers)")
    if "M" in r:
        err = np.abs(set_atlas.box_through(r["M"], (0, 0, SRC_W, SRC_H)) -
                     np.float64(set_atlas.box_through(truth, (0, 0, SRC_W, SRC_H)))).max()
        check(err < 8.0, f"A-B similarity within 8 px of the truth at the frame's corners ({err:.1f} px; scale "
                         f"{r['scale']:.3f} vs {1 / 1.25:.3f})")
    rc = set_atlas.match(fa, fc)
    check(not rc["accepted"], f"A-C (another place) rejected ({rc['inliers']} inliers)")

    reg = set_atlas.SetRegistry(window_s=90.0)
    margins = (832.0, 0.0, 1504.0, 288.0)
    boxes = {}
    for s, f in ((sa, fa), (sb, fb), (sc, fc)):
        wx0, wy0, sc_, tw, th = mosaic_t1.canvas_geometry(s, margins, 2.0, 64 << 20)
        boxes[s.shot_id] = (wx0, wy0, wx0 + tw * sc_, wy0 + th * sc_)
    sid_a, ta, _ = reg.register(fa, boxes[1])
    sid_b, tb, best = reg.register(fb, boxes[2])
    sid_c, tc, _ = reg.register(fc, boxes[3])
    check(sid_a == 1 and sid_b == 1 and sid_c == 3, f"B joins A's set, C opens its own (sets {sid_a}, {sid_b}, {sid_c})")

    # atlas: A's and B's real pixels in the set's world agree where both saw the place
    ma = mosaic_t1.build(sa, margins, 2.0, 64 << 20)
    mb = mosaic_t1.build(sb, margins, 2.0, 64 << 20)
    at = set_atlas.atlas([(ma, set_atlas.IDENTITY), (mb, tb)], 2.0, 64 << 20)
    alone_b = set_atlas.atlas([(mb, tb)], 2.0, 64 << 20)
    # sample B's atlas-alone pixels at A-atlas texels: same world px, so overlapping texels should match
    x0 = int(round((alone_b.world_x0 - at.world_x0) / at.scale))
    y0 = int(round((alone_b.world_y0 - at.world_y0) / at.scale))
    h, w = alone_b.tex_h, alone_b.tex_w
    region = at.rgb[y0:y0 + h, x0:x0 + w].astype(np.float32)
    cov_a = np.zeros_like(at.coverage)
    a_only = set_atlas.atlas([(ma, set_atlas.IDENTITY)], 2.0, 64 << 20)
    ax0 = int(round((a_only.world_x0 - at.world_x0) / at.scale))
    ay0 = int(round((a_only.world_y0 - at.world_y0) / at.scale))
    cov_a[ay0:ay0 + a_only.tex_h, ax0:ax0 + a_only.tex_w] = a_only.coverage
    both = cov_a[y0:y0 + h, x0:x0 + w] & alone_b.coverage[:region.shape[0], :region.shape[1]]
    if both.sum() > 1000:
        diff = np.abs(region[both[:region.shape[0], :region.shape[1]]] -
                      alone_b.rgb[:region.shape[0], :region.shape[1]][both[:region.shape[0], :region.shape[1]]].astype(np.float32))
        check(float(np.median(diff)) < 12.0, f"A's and B's pixels agree in the set's world (median diff "
                                            f"{float(np.median(diff)):.1f} codes over {int(both.sum())} texels)")
    else:
        check(False, f"A and B overlap in the atlas ({int(both.sum())} texels)")

    # frame transforms: B's frame 15 source px of the picture's centre lands where A's world shows the same place
    xf = set_atlas.frame_transforms(sb, tb)                       # (n, 8): the store's projective h
    i = 15
    p = np.float64([SRC_W / 2, SRC_H / 2])
    q = np.r_[xf[i].astype(np.float64), 1.0].reshape(3, 3) @ np.r_[p, 1.0]
    wset = q[:2] / q[2]
    true_world = shot_to_world(b_centres[i], 1.25) @ np.r_[p, 1.0]
    a_world = set_atlas.invert(shot_to_world(a_centres[0], 1.0)) @ np.r_[true_world, 1.0]
    err = float(np.abs(wset - a_world).max())
    check(err < 8.0, f"B's frame transform maps into A's world within 8 px ({err:.1f} px)")

    print(f"{'PASS' if not FAILS else 'FAIL'}: {len(FAILS)} failed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
