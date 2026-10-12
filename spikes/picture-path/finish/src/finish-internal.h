// Helpers shared by the finish passes' source files (not part of the narrow API in finish-passes.h).
#pragma once

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libplacebo/shaders/custom.h>
#include <libplacebo/shaders/dithering.h>

#include "finish-passes.h"

// libplacebo has no public logging call for host code: errors of the passes go to stderr.
#define FP_ERR(env, fmt, ...) fprintf(stderr, "finish: " fmt "\n", ##__VA_ARGS__)

// Small immutable-per-call shader parts. `pl_shader_custom` copies the variable data, so pointers to locals are fine.
static inline struct pl_shader_var fp_var_f(const char *name, const float *v)
{
    return (struct pl_shader_var) { .var = pl_var_float(name), .data = v, .dynamic = true };
}
static inline struct pl_shader_var fp_var_i(const char *name, const int *v)
{
    return (struct pl_shader_var) { .var = pl_var_int(name), .data = v, .dynamic = true };
}
static inline struct pl_shader_var fp_var_v2(const char *name, const float *v)
{
    return (struct pl_shader_var) { .var = pl_var_vec2(name), .data = v, .dynamic = true };
}
static inline struct pl_shader_var fp_var_iv2(const char *name, const int *v)
{
    return (struct pl_shader_var) { .var = pl_var_ivec2(name), .data = v, .dynamic = true };
}
static inline struct pl_shader_var fp_var_v3(const char *name, const float *v)
{
    return (struct pl_shader_var) { .var = pl_var_vec3(name), .data = v, .dynamic = true };
}
// 3x3 matrix given row-major in C; GLSL's mat3 is column-major, so the data is transposed here.
static inline struct pl_shader_var fp_var_m3_rowmajor(const char *name, const float *rm, float *scratch9)
{
    for (int r = 0; r < 3; r++)
        for (int c = 0; c < 3; c++)
            scratch9[c * 3 + r] = rm[r * 3 + c];
    return (struct pl_shader_var) { .var = pl_var_mat3(name), .data = scratch9, .dynamic = true };
}

static inline struct pl_shader_desc fp_desc_tex(const char *name, pl_tex tex, bool linear)
{
    return (struct pl_shader_desc) {
        .desc = { .name = name, .type = PL_DESC_SAMPLED_TEX },
        .binding = { .object = tex, .address_mode = PL_TEX_ADDRESS_CLAMP,
                     .sample_mode = linear ? PL_TEX_SAMPLE_LINEAR : PL_TEX_SAMPLE_NEAREST },
    };
}

// Runs a finished fragment shader into `target` (whole texture), recording GPU time if env->timer is set.
static inline bool fp_dispatch(const struct fp_env *env, pl_shader *sh, pl_tex target)
{
    return pl_dispatch_finish(env->dp, pl_dispatch_params(.shader = sh, .target = target, .timer = env->timer));
}

// The first format of these names that the GPU has with all the given capabilities.
pl_fmt fp_find_fmt(pl_gpu gpu, const char *const *names, int n, enum pl_fmt_caps caps);

// Creates a render-target texture that the passes can also sample.
pl_tex fp_make_target(const struct fp_env *env, int w, int h, pl_fmt fmt, bool storable, const char *what);
