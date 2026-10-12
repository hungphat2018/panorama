"""Writes the scenario files (format in pp_reference.py) into scenarios/. One scenario = one room, one surface model, one
source picture and placement, one kernel; the harness and reference-render.py read the same file.

  python -I make-scenarios.py            (writes scenarios/*.json)

Names: <surface>-<pattern> for the main checks; identity-*, shear-*, blend-*, plain-* (one monitor, fit to the monitor),
kernel-* (T7 on one monitor with each kernel), bench-* (the cost matrix: one monitor, picture fitted).
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "scenarios")
KAPPA = 1.0 / 700.0                      # the cylinder around the eye at the seat distance


def src(pattern, w=1920, h=1080):
    return {"file": "patterns/%s-%dx%d.ppm" % (pattern, w, h), "width": w, "height": h, "sar": 1.0}


def scenario(name, room, surface, source, height_mm, **extra):
    s = {"room": "room/%s.json" % room, "surface": surface, "source": source,
         "cue": {"centre_mm": [0.0, 0.0], "height_mm": height_mm}, "kernel": "ewa_lanczossharp", "domain": "auto"}
    s.update(extra)
    return name, s


def build():
    fold = {"model": "fold"}
    curve = {"model": "curve", "curvature": KAPPA}
    flat = {"model": "curve", "curvature": 0.0}
    out = []
    for pat in ("t1", "t7"):
        out.append(scenario("fold-" + pat, "setup-a-default", fold, src(pat), 215.0))
        out.append(scenario("curve-" + pat, "setup-a-default", curve, src(pat), 200.0))
    out.append(scenario("curveflat-t1", "setup-a-default", flat, src("t1"), 215.0))
    for room in ("toe15", "toe30", "laptop-nearer"):
        out.append(scenario("curve-%s-t1" % room, "setup-a-" + room, curve, src("t1"), 200.0))
        out.append(scenario("fold-%s-t1" % room, "setup-a-" + room, fold, src("t1"), 215.0))
    # identity: one monitor, 1:1 (the picture's pixel pitch equals the monitor's)
    out.append(scenario("identity-laptop", "single-laptop", fold, src("noise", 2560, 1600), 215.0))
    out.append(scenario("identity-external", "single-external", fold, src("noise"), 270.0))
    # a camera slide at 300 mm/s to the right; every row drawn for its own light time (laptop 3.704 ms, external 9.69 ms)
    out.append(scenario("shear-fold", "setup-a-default", fold, src("shear"), 215.0,
                        slide={"velocity_mm_s": [300.0, 0.0], "t_ref_ms": 0.0, "t_top_ms": {"laptop": 0.0, "external": 0.0}}))
    # rolling blend: the laptop shows frame B growing down the screen, the external a ramp of its own
    out.append(scenario("blend-fold", "setup-a-default", fold, src("t1"), 215.0, source_b=src("t7"),
                        blend={"laptop": {"wa": 1.05, "wb": -6.0e-4}, "external": {"wa": -0.2, "wb": 1.2e-3}}))
    out.append(scenario("blend-flat", "setup-a-default", fold, src("flat-a"), 215.0, source_b=src("flat-b"),
                        blend={"laptop": {"wa": 0.0, "wb": 6.25e-4}, "external": {"wa": 0.0, "wb": 9.259e-4}}))
    # plain single monitors, picture fitted: the laptop upscales 1.333x, the external downscales 0.741x
    out.append(scenario("plain-laptop-t7", "single-laptop", fold, src("t7"), 193.5))
    out.append(scenario("plain-external-t7", "single-external", fold, src("t7"), 200.0))
    # the same on the cylinder (curvature 1/700): the picture lies on the cylinder, each monitor shows its part
    out.append(scenario("plain-curve-laptop-t7", "single-laptop", curve, src("t7"), 160.0))
    out.append(scenario("plain-curve-external-t7", "single-external", curve, src("t7"), 200.0))
    for mon, room, h in (("laptop", "single-laptop", 193.5), ("external", "single-external", 200.0)):
        name, s = scenario("plain-%s-t7-ar" % mon, room, fold, src("t7"), h)      # lanczossharp with anti-ringing 0.8
        s["antiring"] = 0.8
        out.append((name, s))
    for kern in ("ewa_lanczossharp", "ewa_lanczos", "ewa_lanczos4sharpest", "ewa_robidoux", "ewa_mitchell", "ewa_hermite"):
        for mon in ("laptop", "external"):
            h = 193.5 if mon == "laptop" else 200.0
            name, s = scenario("kernel-%s-%s" % (kern, mon), "single-" + mon, fold, src("t7"), h)
            s["kernel"] = kern
            out.append((name, s))
    # the cost matrix: one monitor of the stated size, the picture fitted so the scale is as noted
    pitch = 0.1344
    bench = [
        ("bench-2560x1600-src1080-up1.33", "bench-2560x1600", src("t1"), 2560 * pitch * 9 / 16),
        ("bench-1920x1080-src1080-down0.8", "bench-1920x1080", src("t1"), 1080 * pitch * 0.8),
        ("bench-3840x2160-src1080-up2.0", "bench-3840x2160", src("t1"), 2160 * pitch),
        ("bench-3840x2160-src2160-down0.9", "bench-3840x2160", src("t1", 3840, 2160), 2160 * pitch * 0.9),
        ("bench-2560x1600-src2160-down0.67", "bench-2560x1600", src("t1", 3840, 2160), 2560 * pitch * 9 / 16),
    ]
    for name, room, source, h in bench:
        out.append(scenario(name, room, fold, source, h))
    out.append(scenario("bench-2560x1600-curve-src1080", "bench-2560x1600", curve, src("t1"), 160.0))
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, s in build():
        with open(os.path.join(OUT, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(s, f, indent=1)
            f.write("\n")
    print("wrote %d scenarios to %s" % (len(build()), OUT))


if __name__ == "__main__":
    sys.exit(main())
