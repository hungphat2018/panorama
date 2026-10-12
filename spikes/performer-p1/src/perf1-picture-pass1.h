// Pass 1 of the picture path: the one resample from prepared source frames to a monitor's own pixel grid, with the
// room's geometry (fold or curve), the shot's crop and place, the camera slide of each row and the rolling blend of two
// frames in it. A compute shader on libplacebo's dispatch, so it runs on every libplacebo backend.
//
// Output: an RGBA16F canvas of the monitor's size, linear light, BT.709 primaries, 1.0 = SDR white, premultiplied by
// the picture's coverage in alpha (0 where the picture is not: the leftover area).
#pragma once

#include <stdbool.h>

#include <libplacebo/dispatch.h>
#include <libplacebo/gpu.h>
#include <libplacebo/log.h>

#include "perf1-picture-map.h"
#include "perf1-picture-plan.h"

struct pic_pass1 {
    pl_gpu gpu;
    pl_tex lut;                 // kernel weights over [0, support], 1 x N, R32F
    pl_tex dummy;               // 1 x 1 stand-in for the second frame when nothing blends
    float support, first_zero;  // kernel support and the end of its main lobe, in kernel units
    float antiring;             // the kernel's own anti-ringing strength
    char kernel[48];
    char *header;               // GLSL: sigmoid pair + the resample code
};

// Weight of the named radial kernel at kernel distance t (w may be NULL) and its support radius; false for an unknown
// name. Names: ewa_lanczossharp, ewa_lanczos, ewa_lanczos4sharpest, ewa_robidoux, ewa_catmull_rom, ewa_mitchell,
// ewa_hermite.
bool pic_kernel_weight(const char *name, double t, double *w, double *support);

bool pic_pass1_init(struct pic_pass1 *p, pl_gpu gpu, const char *kernel);
void pic_pass1_destroy(struct pic_pass1 *p);

// The dispatch is the monitor's own: one libplacebo pass is cached per dispatch together with the last values written to its
// uniform buffers, and two monitors alternating on one dispatch (different crops, one refresh apart) were seen to run with a
// mix of each other's uniforms.
struct pic_pass1_job {
    pl_dispatch dp;
    const struct room_geom *geom;     // the output's place on the surface
    const struct pic_view *view;      // the shot's picture
    const struct pic_slide *slide;    // NULL = none
    pl_tex a;                         // prepared frame (linear light, or its sigmoid copy when `sigmoid`)
    pl_tex b;                         // the frame blended in, NULL = no blend
    double wa, wb;                    // share of b in row y: clamp(wa + wb * y, 0, 1)
    bool sigmoid;                     // a holds the sigmoid domain (the picture is magnified here)
    // Frame b's own shot, when it is not the shot of a (a blend across a cut): view_b and slide_b (NULL = none) replace
    // view and slide for b; view_b NULL = b is seen like a. sigmoid_b: b holds the sigmoid domain.
    const struct pic_view *view_b;
    const struct pic_slide *slide_b;
    bool sigmoid_b;
    pl_tex out;                       // RGBA16F, storable, the monitor's size
    pl_timer timer;                   // optional: GPU time of the pass
};
bool pic_pass1_run(struct pic_pass1 *p, const struct pic_pass1_job *job);
