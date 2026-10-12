// Preparing source frames for the resample pass. libplacebo maps the decoded frame (D3D11VA or uploaded), decodes its
// colour to linear light BT.709 and writes it 1:1 at source size into an RGBA16F texture: no scaling, no dither, no
// peak detection. The pass-1 shader then does the only resample. The black point is declared (min luminance = true black):
// without it libplacebo infers a 1000:1 display and moves near-black by about 3 codes.
//
// A frame is prepared once however many monitors and refreshes show it (a 240 Hz screen shows one frame for ten
// refreshes): the cache is keyed by the frame's timestamp. For monitors that magnify the picture the pass samples a copy
// in libplacebo's sigmoid domain (less ringing), made on demand from the linear one.
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include <libplacebo/dispatch.h>
#include <libplacebo/gpu.h>
#include <libplacebo/renderer.h>

#define PIC_PREP_SLOTS 5

struct pic_prepared {
    bool used;
    double pts;
    unsigned long long stamp;   // last use, for eviction
    int w, h;
    pl_tex lin;                 // linear light, 1.0 = SDR white
    pl_tex sig;                 // the same in the sigmoid domain (allocated when a magnifying monitor first asks)
    bool has_sig;               // sig holds this frame's sigmoid copy
};

struct pic_prep {
    pl_gpu gpu;
    pl_dispatch dp;
    struct pl_render_params params;
    struct pic_prepared slot[PIC_PREP_SLOTS];
    unsigned long long tick;
    pl_timer sigmoid_timer;
    uint64_t gpu_ns;            // libplacebo's GPU timers of the conversion passes, summed since the last take
    uint32_t hits, misses;
    char *header;
};

// chroma_filter: libplacebo filter name for chroma planes, NULL = libplacebo's cheap built-in.
bool pic_prep_init(struct pic_prep *pp, pl_gpu gpu, pl_dispatch dp, const char *chroma_filter);
void pic_prep_destroy(struct pic_prep *pp);
// Drops every prepared frame (a seek or pause: the queues were reset).
void pic_prep_flush(struct pic_prep *pp);

// The prepared form of `frame` (a decoded frame with timestamp pts and a visible area of w x h pixels), converted now
// with `rr` unless it is cached. Returns NULL (with a message) if the conversion fails. The pointer stays valid until the
// next calls: the cache holds PIC_PREP_SLOTS frames and drops the least recently used.
const struct pic_prepared *pic_prep_get(struct pic_prep *pp, pl_renderer rr, double pts, const struct pl_frame *frame,
                                        int w, int h, bool want_sigmoid);

// The prepared form of the frame with timestamp pts if it is still cached (it is not touched: no conversion is run), else
// NULL. A sigmoid copy is made on demand.
const struct pic_prepared *pic_prep_find(struct pic_prep *pp, double pts, int w, int h, bool want_sigmoid);

// GPU time of the sigmoid copies made since the last call, in ns (0 if none finished yet).
uint64_t pic_prep_take_gpu_ns(struct pic_prep *pp);
