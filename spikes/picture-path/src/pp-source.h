// A source picture on the GPU: the exact test pattern (binary PPM, 8-bit sRGB codes) uploaded as RGBA8, or converted on the
// way in to planar 4:2:0 YCbCr (BT.709 limited range, 8 or 10 bit) like a decoded video frame. The libplacebo frame
// describing it is ready for pl_render_image.
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include <libplacebo/renderer.h>

#include "pp-gpu.h"

enum pp_src_fmt { PP_SRC_RGB8 = 0, PP_SRC_YUV420P8 = 1, PP_SRC_YUV420P10 = 2 };

struct pp_source {
    int w, h;
    enum pp_src_fmt fmt;
    pl_tex tex[3];
    struct pl_frame frame;
    uint8_t *rgb;                   // the original 8-bit RGB, w * h * 3
    uint64_t signature;             // unique per loaded picture: libplacebo's frame cache keys on it
};

const char *pp_src_fmt_name(enum pp_src_fmt f);
bool pp_src_fmt_from_name(const char *name, enum pp_src_fmt *out);

// Reads a P6 8-bit PPM into a heap buffer (caller frees). Returns NULL and prints why on failure.
uint8_t *pp_ppm_load(const char *path, int *w, int *h);

// Loads the picture and uploads it. expect_w/h > 0 must match the file. Returns false (with a message) on failure.
bool pp_source_load(struct pp_source *s, const struct pp_gpu *g, const char *ppm_path, enum pp_src_fmt fmt, int expect_w,
                    int expect_h);
void pp_source_destroy(struct pp_source *s, const struct pp_gpu *g);
