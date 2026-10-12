// Smoke test: every pass once on a small synthetic canvas, results written as PNGs for a look.
#include "test-canvas.h"
#include "test-main.h"

int test_smoke(struct tctx *t)
{
    printf("== smoke\n");
    struct fp_env *env = &t->dev.env;
    const int W = 1280, H = 800;
    pl_tex canvas = tu_canvas(env, W, H, "canvas");
    pl_tex filled = tu_canvas(env, W, H, "filled");
    pl_tex out8 = tu_target8(env, W, H, false, "out8");
    tu_check(canvas && filled && out8, "textures created");
    if (!canvas || !filled || !out8)
        return 1;

    struct tc_shot shot = {
        .rect = { 0, 133, W, 667 }, .left = { 0.8f, 0.45f, 0.1f }, .right = { 0.1f, 0.3f, 0.7f },
        .shade = 0.4f, .grain = 0.02f, .flash = 0.0f, .texture = 0.12f,
    };
    pl_dispatch_reset_frame(env->dp);
    tu_check(tc_gen_shot(env, canvas, &shot, 0), "canvas generated");

    struct fp_surround *s = fp_surround_create(env, W, H, t->cell_px);
    tu_check(s != NULL, "surround created");
    if (!s)
        return 1;
    struct fp_surround_look look = { .mode = FP_SURROUND_GLOW, .gain = 0.8f, .falloff_px = 250.0f, .saturation = 1.0f,
                                     .max_level = 1.0f };
    struct fp_surround_time tm = { .tau_steady_s = 0.4f, .cut_rule = FP_CUT_SNAP };
    struct fp_surround_frame fr = { .dt_s = 1.0 / 240.0, .reset = true };
    tu_check(fp_surround_apply(s, canvas, filled, &look, &tm, &fr), "surround applied");

    float *f = tu_read_f32(env, filled, W, H);
    tu_check(f != NULL, "filled canvas read back");
    if (f) {
        char path[512];
        snprintf(path, sizeof(path), "%s/smoke-surround.png", t->out);
        tu_check(tu_png_write_linear(path, f, W, H, 1.0f), "wrote %s", path);
        free(f);
    }

    struct fp_finish *fin = fp_finish_create(env);
    struct fp_color_monitor mon = { .transfer = FP_TRC_SRGB, .gamut_owner = FP_GAMUT_OS };
    struct fp_color_gpu cg;
    fp_color_make(&mon, &cg);
    pl_dispatch_reset_frame(env->dp);
    tu_check(fp_finish_run(fin, filled, out8, &cg, FP_DITHER_BLUE_NOISE), "finish applied");
    uint8_t *u = tu_read_u8(env, out8, W, H);
    if (u) {
        char path[512];
        snprintf(path, sizeof(path), "%s/smoke-finish.png", t->out);
        tu_check(tu_png_write_rgba8(path, u, W, H), "wrote %s", path);
        free(u);
    }

    // fused 2+3 in one pass
    pl_dispatch_reset_frame(env->dp);
    fr.reset = false;
    tu_check(fp_surround_update(s, canvas, &look, &tm, &fr), "surround updated (fused)");
    tu_check(fp_finish_run_fused(fin, s, canvas, out8, &look, &cg, FP_DITHER_BLUE_NOISE), "fused pass applied");

    fp_finish_destroy(&fin);
    fp_surround_destroy(&s);
    pl_tex_destroy(env->gpu, &canvas);
    pl_tex_destroy(env->gpu, &filled);
    pl_tex_destroy(env->gpu, &out8);
    return 0;
}
