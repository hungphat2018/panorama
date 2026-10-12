// Checks that an RGBA16F canvas can be filled and read back on every backend, for a few sizes: the read-back path the
// harness relies on. bin/download-test.exe [backend adapter]
#include <stdio.h>
#include <string.h>

#include "pp-gpu.h"

static int test(struct pp_gpu *g, int w, int h)
{
    pl_tex t = pp_tex_create_canvas(g, w, h, "download test");
    if (!t)
        return 1;
    struct pl_frame f;
    pp_canvas_frame(&f, t);
    pl_frame_clear_rgba(g->gpu, &f, (const float[4]) { 0.25f, 0.5f, 0.75f, 1.0f });
    size_t bytes = (size_t) w * h * t->params.format->texel_size;
    unsigned short *buf = calloc(bytes, 1);
    bool ok = pl_tex_download(g->gpu, pl_tex_transfer_params(.tex = t, .ptr = buf));
    printf("  %dx%d fmt %s emulated=%d texel_size=%zu: download %s, first texel %04x %04x %04x %04x\n", w, h, t->params.format->name,
           (int) t->params.format->emulated, t->params.format->texel_size, ok ? "ok" : "FAILED", buf[0], buf[1], buf[2], buf[3]);
    fflush(stdout);
    free(buf);
    pl_tex_destroy(g->gpu, &t);
    return ok ? 0 : 1;
}

int main(int argc, char **argv)
{
    enum pp_backend b = PP_BACKEND_D3D11;
    if (argc > 1)
        pp_backend_from_name(argv[1], &b);
    struct pp_gpu g;
    if (!pp_gpu_create(&g, b, argc > 2 ? argv[2] : "nvidia", PL_LOG_WARN))
        return 1;
    pp_gpu_describe(&g, stdout);
    for (int i = 0; i < g.gpu->num_formats; i++) {
        pl_fmt f = g.gpu->formats[i];
        if (strstr(f->name, "16f") || strstr(f->name, "32f"))
            printf("  format %s: comps %d, type %d, texel_size %zu, emulated %d, caps 0x%x, depth %d\n", f->name,
                   f->num_components, (int) f->type, f->texel_size, (int) f->emulated, (unsigned) f->caps,
                   f->component_depth[0]);
    }
    fflush(stdout);
    static const int sizes[][2] = { { 64, 64 }, { 640, 480 }, { 1920, 1080 }, { 2560, 1600 } };
    int bad = 0;
    for (int i = 0; i < 4; i++)
        bad += test(&g, sizes[i][0], sizes[i][1]);
    pp_gpu_destroy(&g);
    return bad ? 1 : 0;
}
