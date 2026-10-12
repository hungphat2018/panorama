// Source pictures: PPM in, GPU frame out. See pp-source.h.
#include "pp-source.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include <libplacebo/utils/upload.h>

#include "pp-util.h"

const char *pp_src_fmt_name(enum pp_src_fmt f)
{
    static const char *const names[] = { "rgb8", "yuv420p8", "yuv420p10" };
    return names[f];
}

bool pp_src_fmt_from_name(const char *name, enum pp_src_fmt *out)
{
    for (int i = 0; i < 3; i++) {
        if (!strcmp(name, pp_src_fmt_name((enum pp_src_fmt) i))) {
            *out = (enum pp_src_fmt) i;
            return true;
        }
    }
    return false;
}

static int next_token(const uint8_t *d, size_t n, size_t *pos)
{
    for (;;) {
        while (*pos < n && isspace(d[*pos]))
            (*pos)++;
        if (*pos < n && d[*pos] == '#') {
            while (*pos < n && d[*pos] != '\n')
                (*pos)++;
            continue;
        }
        break;
    }
    int v = 0;
    bool any = false;
    while (*pos < n && isdigit(d[*pos])) {
        v = v * 10 + (d[*pos] - '0');
        (*pos)++;
        any = true;
    }
    return any ? v : -1;
}

uint8_t *pp_ppm_load(const char *path, int *w, int *h)
{
    size_t n;
    char *raw = pp_read_file(path, &n);
    const uint8_t *d = (const uint8_t *) raw;
    if (n < 10 || d[0] != 'P' || d[1] != '6') {
        fprintf(stderr, "picture-path: %s is not a binary PPM\n", path);
        free(raw);
        return NULL;
    }
    size_t pos = 2;
    int ww = next_token(d, n, &pos), hh = next_token(d, n, &pos), maxval = next_token(d, n, &pos);
    pos++; // the single whitespace after maxval
    if (ww <= 0 || hh <= 0 || maxval != 255 || pos + (size_t) ww * hh * 3 > n) {
        fprintf(stderr, "picture-path: %s: only 8-bit PPMs of the stated size are supported\n", path);
        free(raw);
        return NULL;
    }
    uint8_t *out = pp_xcalloc((size_t) ww * hh * 3, 1);
    memcpy(out, d + pos, (size_t) ww * hh * 3);
    free(raw);
    *w = ww;
    *h = hh;
    return out;
}

// RGB8 (sRGB codes) -> planar 4:2:0 BT.709 limited range. Chroma is the mean of each 2x2 block (centre siting).
// bits = 8: uint8 planes; bits = 10: uint16 planes, values in the low 10 bits.
static void rgb_to_yuv420(const uint8_t *rgb, int w, int h, int bits, uint8_t *planes[3])
{
    const double kr = 0.2126, kb = 0.0722, kg = 1.0 - kr - kb;
    const double scale = bits == 10 ? 4.0 : 1.0;
    const int cw = w / 2, ch = h / 2;
    #define PUT(plane, idx, v) do { \
        long q = lround(v); \
        if (bits == 10) ((uint16_t *) planes[plane])[idx] = (uint16_t) q; else planes[plane][idx] = (uint8_t) q; } while (0)
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            const uint8_t *p = rgb + ((size_t) y * w + x) * 3;
            double yy = kr * p[0] / 255.0 + kg * p[1] / 255.0 + kb * p[2] / 255.0;
            PUT(0, (size_t) y * w + x, (16.0 + 219.0 * yy) * scale);
        }
    }
    for (int y = 0; y < ch; y++) {
        for (int x = 0; x < cw; x++) {
            double cb = 0.0, cr = 0.0;
            for (int dy = 0; dy < 2; dy++) {
                for (int dx = 0; dx < 2; dx++) {
                    const uint8_t *p = rgb + ((size_t) (2 * y + dy) * w + 2 * x + dx) * 3;
                    double r = p[0] / 255.0, g = p[1] / 255.0, b = p[2] / 255.0;
                    double yy = kr * r + kg * g + kb * b;
                    cb += (b - yy) / (2.0 * (1.0 - kb));
                    cr += (r - yy) / (2.0 * (1.0 - kr));
                }
            }
            PUT(1, (size_t) y * cw + x, (128.0 + 224.0 * cb / 4.0) * scale);
            PUT(2, (size_t) y * cw + x, (128.0 + 224.0 * cr / 4.0) * scale);
        }
    }
    #undef PUT
}

static bool upload_rgba8(struct pp_source *s, const struct pp_gpu *g)
{
    size_t npx = (size_t) s->w * s->h;
    uint8_t *rgba = pp_xcalloc(npx, 4);
    for (size_t i = 0; i < npx; i++) {
        rgba[4 * i + 0] = s->rgb[3 * i + 0];
        rgba[4 * i + 1] = s->rgb[3 * i + 1];
        rgba[4 * i + 2] = s->rgb[3 * i + 2];
        rgba[4 * i + 3] = 255;
    }
    struct pl_plane_data pd = {
        .type = PL_FMT_UNORM, .width = s->w, .height = s->h,
        .component_size = { 8, 8, 8, 8 }, .component_map = { 0, 1, 2, 3 },
        .pixel_stride = 4, .pixels = rgba,
    };
    struct pl_plane plane = {0};
    bool ok = pl_upload_plane(g->gpu, &plane, &s->tex[0], &pd);
    free(rgba);
    if (!ok) {
        fprintf(stderr, "picture-path: uploading the RGBA8 source failed\n");
        return false;
    }
    plane.components = 3; // the alpha byte is padding
    s->frame.num_planes = 1;
    s->frame.planes[0] = plane;
    s->frame.repr = (struct pl_color_repr) { .sys = PL_COLOR_SYSTEM_RGB, .levels = PL_COLOR_LEVELS_FULL, .alpha = PL_ALPHA_NONE };
    return true;
}

static bool upload_yuv420(struct pp_source *s, const struct pp_gpu *g, int bits)
{
    size_t ysz = (size_t) s->w * s->h, csz = (size_t) (s->w / 2) * (s->h / 2);
    size_t bytes = bits == 10 ? 2 : 1;
    uint8_t *buf = pp_xcalloc(ysz + 2 * csz, bytes);
    uint8_t *planes[3] = { buf, buf + ysz * bytes, buf + (ysz + csz) * bytes };
    rgb_to_yuv420(s->rgb, s->w, s->h, bits, planes);
    for (int p = 0; p < 3; p++) {
        struct pl_plane_data pd = {
            .type = PL_FMT_UNORM, .width = p ? s->w / 2 : s->w, .height = p ? s->h / 2 : s->h,
            .component_size = { bits == 10 ? 16 : 8 }, .component_map = { p },
            .pixel_stride = bytes, .pixels = planes[p],
        };
        struct pl_plane plane = {0};
        if (!pl_upload_plane(g->gpu, &plane, &s->tex[p], &pd)) {
            fprintf(stderr, "picture-path: uploading plane %d of the 4:2:0 source failed\n", p);
            free(buf);
            return false;
        }
        s->frame.planes[p] = plane;
    }
    free(buf);
    s->frame.num_planes = 3;
    s->frame.repr = (struct pl_color_repr) {
        .sys = PL_COLOR_SYSTEM_BT_709, .levels = PL_COLOR_LEVELS_LIMITED, .alpha = PL_ALPHA_NONE,
        .bits = { .sample_depth = bits == 10 ? 16 : 8, .color_depth = bits },
    };
    pl_frame_set_chroma_location(&s->frame, PL_CHROMA_CENTER);
    return true;
}

bool pp_source_load(struct pp_source *s, const struct pp_gpu *g, const char *ppm_path, enum pp_src_fmt fmt, int expect_w,
                    int expect_h)
{
    static uint64_t next_signature = 0x1000;
    memset(s, 0, sizeof(*s));
    s->signature = next_signature++;
    s->fmt = fmt;
    s->rgb = pp_ppm_load(ppm_path, &s->w, &s->h);
    if (!s->rgb)
        return false;
    if ((expect_w > 0 && s->w != expect_w) || (expect_h > 0 && s->h != expect_h)) {
        fprintf(stderr, "picture-path: %s is %dx%d, the scenario says %dx%d\n", ppm_path, s->w, s->h, expect_w, expect_h);
        return false;
    }
    if (fmt != PP_SRC_RGB8 && ((s->w | s->h) & 1)) {
        fprintf(stderr, "picture-path: 4:2:0 needs even dimensions\n");
        return false;
    }
    bool ok = fmt == PP_SRC_RGB8 ? upload_rgba8(s, g) : upload_yuv420(s, g, fmt == PP_SRC_YUV420P10 ? 10 : 8);
    if (!ok)
        return false;
    // The pattern's codes are sRGB-coded BT.709 primaries, whichever way they were packed.
    s->frame.color = (struct pl_color_space) { .primaries = PL_COLOR_PRIM_BT_709, .transfer = PL_COLOR_TRC_SRGB,
                                               .hdr = { .min_luma = PL_COLOR_HDR_BLACK, .max_luma = PL_COLOR_SDR_WHITE } };
    s->frame.crop = (pl_rect2df) { 0, 0, (float) s->w, (float) s->h };
    return true;
}

void pp_source_destroy(struct pp_source *s, const struct pp_gpu *g)
{
    for (int i = 0; i < 3; i++)
        pl_tex_destroy(g->gpu, &s->tex[i]);
    free(s->rgb);
    s->rgb = NULL;
}
