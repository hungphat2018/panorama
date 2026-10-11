"""Compare two binary PPM (P6) frames: used to check that the hardware-decode import paths
draw the same picture as software decoding.

    python -I compare-ppm.py REFERENCE.ppm OTHER.ppm [--png-prefix OUT]

Prints one JSON object: size, mean/max absolute difference (8-bit levels), PSNR, share of
pixels differing by more than 2 and by more than 8 levels, per-channel mean levels.
"""
import json
import sys

import numpy as np


def read_ppm(path):
    with open(path, "rb") as f:
        data = f.read()
    # header: P6 <w> <h> 255 <whitespace> then raw RGB
    parts = data.split(maxsplit=4)
    if parts[0] != b"P6" or parts[3] != b"255":
        raise ValueError(f"{path}: not an 8-bit P6 PPM")
    w, h = int(parts[1]), int(parts[2])
    raw = parts[4]
    px = np.frombuffer(raw[: w * h * 3], dtype=np.uint8).reshape(h, w, 3)
    return px


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        print(__doc__)
        return 2
    a = read_ppm(args[0]).astype(np.int16)
    b = read_ppm(args[1]).astype(np.int16)
    if a.shape != b.shape:
        print(json.dumps({"error": f"size mismatch {a.shape} vs {b.shape}"}))
        return 1
    d = np.abs(a - b)
    mse = float(np.mean((a - b).astype(np.float64) ** 2))
    psnr = float("inf") if mse == 0 else 10.0 * np.log10(255.0 * 255.0 / mse)
    out = {
        "size": [int(a.shape[1]), int(a.shape[0])],
        "mean_abs_diff": round(float(d.mean()), 4),
        "max_abs_diff": int(d.max()),
        "psnr_db": round(psnr, 2),
        "pct_pixels_diff_gt2": round(100.0 * float(np.mean(d.max(axis=2) > 2)), 3),
        "pct_pixels_diff_gt8": round(100.0 * float(np.mean(d.max(axis=2) > 8)), 3),
        "mean_rgb_reference": [round(float(x), 2) for x in a.reshape(-1, 3).mean(axis=0)],
        "mean_rgb_other": [round(float(x), 2) for x in b.reshape(-1, 3).mean(axis=0)],
    }
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
