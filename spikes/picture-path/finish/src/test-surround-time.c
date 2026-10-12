// Pass 2, the temporal study: film sequences rendered at 240 Hz refreshes through the real passes, the surround's
// brightness recorded on every refresh (rows read back from the filled canvas), and written as CSV for
// analyze-flicker.py, which defines and computes the flicker metrics.
//
// A film frame is 1/24 s, so the picture changes every 10th refresh; the surround is updated and drawn on every refresh
// (that is what the performer does), with the film-time step dt = 1/240 s and a cut flag on the first refresh of a shot.
#include "test-canvas.h"
#include "test-main.h"
#include "test-surround.h"

#define W 2560
#define H 1600
#define RECT_TOP 264
#define RECT_BOTTOM 1336
#define REFRESH_HZ 240
#define FILM_FPS 24

static double row_luma(const struct fp_env *env, pl_tex tex, int y)
{
    static float buf[W * 4];
    if (!pl_tex_download(env->gpu, pl_tex_transfer_params(.tex = tex, .rc = { 0, y, 0, W, y + 1, 1 }, .ptr = buf)))
        return -1.0;
    double sum = 0;
    for (int x = 0; x < W; x++)
        sum += tc_luma(&buf[4 * x]);
    return sum / W;
}

// The picture of a scenario at film frame `f`.
static struct tc_shot picture_at(const struct tl_scenario *sc, int f, double t, int *shot_index, bool *cut_frame)
{
    double acc = 0;
    int idx = 0;
    for (int i = 0; i < sc->n_shots; i++) {
        if (t + 1e-9 >= acc)
            idx = i;
        acc += sc->shots[i].len_s;
    }
    *shot_index = idx;
    (void) cut_frame;
    const struct tl_shot *s = &sc->shots[idx];
    struct tc_shot p = { .rect = { 0, RECT_TOP, W, RECT_BOTTOM }, .shade = 0.3f, .grain = sc->grain };
    memcpy(p.left, s->left, sizeof(p.left));
    memcpy(p.right, s->right, sizeof(p.right));
    if (sc->dissolve_s > 0 && idx + 1 < sc->n_shots) {
        // a cross-dissolve into the next shot over the last dissolve_s seconds of this one
        double start = 0;
        for (int i = 0; i <= idx; i++)
            start += sc->shots[i].len_s;
        double a = (t - (start - sc->dissolve_s)) / sc->dissolve_s;
        if (a > 0) {
            const struct tl_shot *n = &sc->shots[idx + 1];
            for (int c = 0; c < 3; c++) {
                p.left[c] = (float) (s->left[c] * (1 - a) + n->left[c] * a);
                p.right[c] = (float) (s->right[c] * (1 - a) + n->right[c] * a);
            }
        }
    }
    if (sc->flash_frames > 0 && f >= sc->flash_frame && f < sc->flash_frame + sc->flash_frames)
        p.flash = 1.0f;
    if (sc->strobe_frames > 0 && (f / sc->strobe_frames) % 2 == 1)
        for (int c = 0; c < 3; c++) {
            p.left[c] *= 3.0f;
            p.right[c] *= 3.0f;
        }
    return p;
}

void tl_series_free(struct tl_series *s)
{
    free(s->t);
    free(s->pic);
    free(s->top_near);
    free(s->top_far);
    free(s->bot_near);
    memset(s, 0, sizeof(*s));
}

bool tl_run(struct tctx *t, const struct tl_scenario *sc, const struct tl_cfg *cfg, const char *csv_path,
            struct tl_series *out)
{
    struct fp_env *env = &t->dev.env;
    pl_tex canvas = tu_canvas(env, W, H, "tl canvas"), filled = tu_canvas(env, W, H, "tl filled");
    struct fp_surround *s = fp_surround_create(env, W, H, t->cell_px);
    FILE *csv = fopen(csv_path, "w");
    if (!canvas || !filled || !s || !csv) {
        fprintf(stderr, "finish-test: temporal run '%s' could not start\n", cfg->name);
        return false;
    }
    double total = 0;
    for (int i = 0; i < sc->n_shots; i++)
        total += sc->shots[i].len_s;
    int n_ref = (int) (total * REFRESH_HZ);
    fprintf(csv, "# scenario=%s config=%s rule=%d tau_steady=%.4f max_rate=%.4f tau_blend=%.4f blend_window=%.4f hold=%.4f gain=%.3f falloff_px=%.0f flash_frame=%d flash_frames=%d flag_flash_as_cut=%d\n",
            sc->name, cfg->name, (int) cfg->time.cut_rule, cfg->time.tau_steady_s, cfg->time.max_rate, cfg->time.tau_blend_s,
            cfg->time.blend_window_s, cfg->time.hold_s, cfg->look.gain, cfg->look.falloff_px, sc->flash_frame, sc->flash_frames,
            (int) cfg->flag_flash_as_cut);
    fprintf(csv, "k,t,frame,shot,cut,flash,pic_edge,top_near,top_far,bot_near,bot_far\n");

    int prev_shot = -1, prev_frame = -1, prev_flash = 0;
    bool ok = true;
    if (out) {
        memset(out, 0, sizeof(*out));
        out->n = n_ref;
        out->t = calloc((size_t) n_ref, sizeof(double));
        out->pic = calloc((size_t) n_ref, sizeof(double));
        out->top_near = calloc((size_t) n_ref, sizeof(double));
        out->top_far = calloc((size_t) n_ref, sizeof(double));
        out->bot_near = calloc((size_t) n_ref, sizeof(double));
    }
    for (int k = 0; k < n_ref && ok; k++) {
        double tk = (double) k / REFRESH_HZ;
        int f = (int) floor(tk * FILM_FPS + 1e-9);
        int shot = 0;
        bool dummy = false;
        struct tc_shot pic = picture_at(sc, f, tk, &shot, &dummy);
        int flash = pic.flash > 0.5f;
        if (f != prev_frame || shot != prev_shot) {
            pl_dispatch_reset_frame(env->dp);
            ok = tc_gen_shot(env, canvas, &pic, f);
            prev_frame = f;
        }
        bool cut = k > 0 && shot != prev_shot;
        if (cfg->flag_flash_as_cut && k > 0 && flash != prev_flash)
            cut = true;
        struct fp_surround_frame fr = { .dt_s = 1.0 / REFRESH_HZ, .cut = cut, .reset = k == 0 };
        pl_dispatch_reset_frame(env->dp);
        ok = ok && fp_surround_apply(s, canvas, filled, &cfg->look, &cfg->time, &fr);
        double pic_edge = row_luma(env, canvas, RECT_TOP + 8);
        double tn = row_luma(env, filled, RECT_TOP - 24), tf = row_luma(env, filled, 40);
        double bn = row_luma(env, filled, RECT_BOTTOM + 24), bf = row_luma(env, filled, H - 40);
        fprintf(csv, "%d,%.6f,%d,%d,%d,%d,%.6f,%.6f,%.6f,%.6f,%.6f\n", k, tk, f, shot, cut, flash, pic_edge, tn, tf, bn, bf);
        if (out) {
            out->t[k] = tk;
            out->pic[k] = pic_edge;
            out->top_near[k] = tn;
            out->top_far[k] = tf;
            out->bot_near[k] = bn;
        }
        prev_shot = shot;
        prev_flash = flash;
    }
    fclose(csv);
    fp_surround_destroy(&s);
    pl_tex_destroy(env->gpu, &canvas);
    pl_tex_destroy(env->gpu, &filled);
    return ok;
}
