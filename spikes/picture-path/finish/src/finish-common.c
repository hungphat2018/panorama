// Helpers shared by the finish passes (see finish-internal.h).
#include "finish-internal.h"

pl_fmt fp_find_fmt(pl_gpu gpu, const char *const *names, int n, enum pl_fmt_caps caps)
{
    for (int i = 0; i < n; i++) {
        pl_fmt f = pl_find_named_fmt(gpu, names[i]);
        if (f && (f->caps & caps) == caps)
            return f;
    }
    return NULL;
}

pl_tex fp_make_target(const struct fp_env *env, int w, int h, pl_fmt fmt, bool storable, const char *what)
{
    if (!fmt) {
        FP_ERR(env, "no suitable texture format for %s", what);
        return NULL;
    }
    pl_tex t = pl_tex_create(env->gpu, pl_tex_params(
        .w = w, .h = h, .format = fmt, .sampleable = true, .renderable = true,
        .storable = storable && (fmt->caps & PL_FMT_CAP_STORABLE),
        .blit_src = true, .blit_dst = true, .host_readable = (fmt->caps & PL_FMT_CAP_HOST_READABLE) != 0,
        .host_writable = true,
    ));
    if (!t)
        FP_ERR(env, "cannot create the %dx%d %s texture (%s)", w, h, fmt->name, what);
    return t;
}

bool fp_append_canvas_fetch(pl_shader sh, pl_tex canvas)
{
    struct pl_shader_desc d = fp_desc_tex("cv_canvas", canvas, false);
    return pl_shader_custom(sh, &(struct pl_custom_shader) {
        .description = "canvas fetch",
        .input = PL_SHADER_SIG_NONE,
        .output = PL_SHADER_SIG_COLOR,
        .descriptors = &d,
        .num_descriptors = 1,
        .body = "color = texelFetch(cv_canvas, ivec2(gl_FragCoord.xy), 0);",
    });
}
