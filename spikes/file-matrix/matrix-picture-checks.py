"""Picture checks on frame grabs (perf0's rendered frame dump, mpv's window screenshot) and contact sheets.

All checks work on what ended up on a window-sized RGB image with a black background, so the same code
judges both players: where the picture sits (bounds, aspect), whether interlace combing is visible, which
scene is on screen, what colours the known patches came out as, and whether tone-mapped HDR steps stay ordered.
Imported through importlib by run-matrix.py (the file name has a dash).
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFont

BLACK_THRESHOLD = 14   # 8-bit level above which a pixel counts as picture rather than window background


def load(path):
    return np.asarray(Image.open(path).convert("RGB"))


def picture_bbox(img, min_frac=0.004):
    """(x0, y0, x1, y1) of the non-black area; rows/columns need a few lit pixels so noise does not widen it."""
    h, w, _ = img.shape
    lit = img.max(axis=2) > BLACK_THRESHOLD
    rows = np.flatnonzero(lit.sum(axis=1) > max(2, min_frac * w))
    cols = np.flatnonzero(lit.sum(axis=0) > max(2, min_frac * h))
    if len(rows) == 0 or len(cols) == 0:
        return None
    return int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1


def aspect_of(b):
    return (b[2] - b[0]) / (b[3] - b[1]) if b else None


def luma(img):
    return img[..., 0] * 0.2126 + img[..., 1] * 0.7152 + img[..., 2] * 0.0722


def comb_score(img, b, t=30.0):
    """Percentage of pixels in the picture that look like interlace comb teeth: a row that differs strongly from
    both neighbours while the two neighbours agree with each other (the idet-style test). Progressive test
    patterns score near 0, combed ones several percent. Only meaningful when the picture is shown near 1:1."""
    if not b:
        return None
    y = luma(img[b[1]:b[3], b[0]:b[2]].astype(np.float32))
    if y.shape[0] < 5:
        return None
    a = np.abs(y[1:-1] - y[:-2])
    c = np.abs(y[1:-1] - y[2:])
    d = np.abs(y[:-2] - y[2:])
    return float(((a > t) & (c > t) & (d < t / 2)).mean() * 100.0)


def rel_patch(img, b, x0, x1, y0, y1, shrink=0.2):
    """Mean RGB of a rectangle given in picture-relative coordinates (0..1), shrunk to stay off its edges."""
    bw, bh = b[2] - b[0], b[3] - b[1]
    dx, dy = (x1 - x0) * shrink, (y1 - y0) * shrink
    xa, xb = b[0] + (x0 + dx) * bw, b[0] + (x1 - dx) * bw
    ya, yb = b[1] + (y0 + dy) * bh, b[1] + (y1 - dy) * bh
    xa, xb, ya, yb = int(round(xa)), max(int(round(xb)), int(round(xa)) + 1), int(round(ya)), max(int(round(yb)), int(round(ya)) + 1)
    return [float(v) for v in img[ya:yb, xa:xb].reshape(-1, 3).mean(axis=0)]


def hdr_card_patches(img, b, tools):
    """12 grey patches and 12 colour patches of the HDR card as mean RGB each (picture-relative sampling)."""
    g0, g1 = tools.CARD_GREY_ROW
    c0, c1 = tools.CARD_COLOUR_ROW
    grey, colour = [], []
    for i in range(12):
        x0, x1 = (60 + i * 150) / 1920, (60 + i * 150 + 130) / 1920
        grey.append(rel_patch(img, b, x0, x1, g0 / 1080, g1 / 1080))
        colour.append(rel_patch(img, b, x0, x1, c0 / 1080, c1 / 1080))
    return grey, colour


def colour_card_patches(img, b, tools):
    """8 colour bars and 8 grey steps of the SDR colour card: measured mean RGB and the expected sRGB values."""
    got, want = [], []
    for i in range(8):
        x0, x1 = 0.06 + i * 0.11, 0.06 + (i + 1) * 0.11
        got.append(rel_patch(img, b, x0, x1, 0.12, 0.50))
        want.append(list(tools.COLOUR_CARD_BARS[i]))
    for i in range(8):
        x0, x1 = 0.06 + i * 0.11, 0.06 + (i + 1) * 0.11
        got.append(rel_patch(img, b, x0, x1, 0.56, 0.80))
        g = round(16 + (235 - 16) * i / 7)
        want.append([g, g, g])
    return got, want


def scene_colour(img, b):
    """Dominant colour of the picture's left bar (testsrc2 + hue shift: red 0, green 120, blue 240 degrees)."""
    r, g, bl = rel_patch(img, b, 0.03, 0.12, 0.40, 0.60, shrink=0.1)
    m = max(r, g, bl)
    if m < 80:
        return "dark"
    return "red" if m == r else "green" if m == g else "blue"


# ---------------------------------------------------------------------------------------------
# thumbnails and contact sheets (what the report links to, so the pictures can be judged by eye)
# ---------------------------------------------------------------------------------------------

def _font(size):
    for f in (r"C:\Windows\Fonts\consola.ttf", r"C:\Windows\Fonts\arial.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def thumb(path, size=(480, 270)):
    """Fit an image into `size` on a dark grey canvas, keeping its aspect."""
    canvas = Image.new("RGB", size, (40, 40, 40))
    if not path:
        return canvas
    try:
        im = Image.open(path).convert("RGB")
    except (OSError, ValueError):
        return canvas
    im.thumbnail(size, Image.LANCZOS)
    canvas.paste(im, ((size[0] - im.width) // 2, (size[1] - im.height) // 2))
    return canvas


def contact_sheet(rows, out_path, cols_titles, size=(480, 270)):
    """rows: [(label, [image path or None, ...])] -> one PNG, a header line per row and a title per column."""
    font, small = _font(15), _font(13)
    pad, head = 6, 22
    n = len(cols_titles)
    w = n * size[0] + (n + 1) * pad
    row_h = head + size[1] + pad
    sheet = Image.new("RGB", (w, 22 + len(rows) * row_h + pad), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    for j, t in enumerate(cols_titles):
        d.text((pad + j * (size[0] + pad), 3), t, fill=(180, 220, 255), font=font)
    for i, (label, paths) in enumerate(rows):
        y = 22 + i * row_h
        d.text((pad, y + 3), label, fill=(255, 255, 255), font=small)
        for j, p in enumerate(paths):
            sheet.paste(thumb(p, size), (pad + j * (size[0] + pad), y + head))
    sheet.save(out_path)
