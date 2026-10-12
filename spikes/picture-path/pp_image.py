"""Image and canvas file helpers shared by the picture-path scripts: binary PPM (P6, 8 or 16 bit) in and out,
raw RGBA16F / float32 canvases written by the harness, and 8-bit sRGB previews."""
import json
import os

import numpy as np


def read_ppm(path):
    """Returns an (H, W, 3) uint8 or uint16 array."""
    with open(path, "rb") as f:
        data = f.read()
    pos = 0
    tokens = []
    while len(tokens) < 4:
        while data[pos:pos + 1].isspace():
            pos += 1
        if data[pos:pos + 1] == b"#":
            while data[pos:pos + 1] != b"\n":
                pos += 1
            continue
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        tokens.append(data[pos:end])
        pos = end
    pos += 1                                  # the single whitespace after maxval
    if tokens[0] != b"P6":
        raise ValueError("%s is not a binary PPM" % path)
    w, h, maxval = int(tokens[1]), int(tokens[2]), int(tokens[3])
    dtype = np.dtype(">u2") if maxval > 255 else np.uint8
    arr = np.frombuffer(data, dtype=dtype, count=w * h * 3, offset=pos).reshape(h, w, 3)
    return arr.astype(np.uint16 if maxval > 255 else np.uint8)


def write_ppm(path, arr):
    arr = np.asarray(arr)
    h, w, c = arr.shape
    if c != 3 or arr.dtype not in (np.uint8, np.uint16):
        raise ValueError("write_ppm wants (H, W, 3) uint8 or uint16")
    maxval = 255 if arr.dtype == np.uint8 else 65535
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n%d\n" % (w, h, maxval))
        f.write((arr if arr.dtype == np.uint8 else arr.astype(">u2")).tobytes())


def read_canvas(prefix):
    """Reads a canvas the harness (or reference-render.py) wrote: <prefix>.json (+ <prefix>.f16 or .f32).
    Returns (H, W, 4) float32 and the metadata dict."""
    with open(prefix + ".json", "r", encoding="utf-8") as f:
        meta = json.load(f)
    w, h = meta["width"], meta["height"]
    fmt = meta["format"]
    if fmt == "rgba16f":
        raw = np.fromfile(prefix + ".f16", dtype=np.float16)
    elif fmt == "rgba32f":
        raw = np.fromfile(prefix + ".f32", dtype=np.float32)
    else:
        raise ValueError("unknown canvas format " + fmt)
    return raw.reshape(h, w, 4).astype(np.float32), meta


def write_canvas_f32(prefix, canvas, meta):
    os.makedirs(os.path.dirname(os.path.abspath(prefix)), exist_ok=True)
    h, w, _ = canvas.shape
    m = dict(meta, width=w, height=h, format="rgba32f")
    canvas.astype(np.float32).tofile(prefix + ".f32")
    with open(prefix + ".json", "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)


def linear_to_code(x):
    """Linear light -> 8-bit sRGB code value as float (0..255, not rounded), clamped to the displayable range."""
    from pp_resample import linear_to_srgb
    return 255.0 * linear_to_srgb(x)


def write_preview_png(path, canvas_rgba):
    """sRGB-encoded 8-bit preview of a linear canvas; the picture coverage is shown against a dark checkerboard."""
    from PIL import Image
    c = np.asarray(canvas_rgba, dtype=np.float64)
    rgb = linear_to_code(c[..., :3])
    img = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    Image.fromarray(img, "RGB").save(path)
