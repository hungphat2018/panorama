// One refresh of one monitor through the picture path, see perf1-picture.h.
#include "perf1-picture-internal.h"

struct pic_mon g_pm[MAX_MONITORS];
bool g_have_subs;

static pl_dispatch g_dp;            // the frame preparation's (it has no uniforms)
static struct pic_pass1 g_pass1;
static struct pic_prep g_prep;
static bool g_ready;
static struct subs_track g_track;

// ---- life cycle -------------------------------------------------------------------------------------------------

static bool init_shared(void)
{
    if (g_ready)
        return true;
    g_dp = pl_dispatch_create(A.gpu.log, A.gpu.gpu);
    if (!g_dp || !pic_pass1_init(&g_pass1, A.gpu.gpu, PC.kernel) ||
        !pic_prep_init(&g_prep, A.gpu.gpu, g_dp, PC.chroma_filter))
        return false;
    g_ready = true;
    return true;
}

bool picture_init_monitor(struct monitor *m)
{
    if (A.cfg.headless_hz) // the dummy GPU compiles no shaders: nothing is drawn
        return true;
    struct pic_mon *pm = &g_pm[m->slot];
    if (!init_shared())
        return false;
    pl_fmt f16 = pl_find_named_fmt(A.gpu.gpu, "rgba16f");
    pm->canvas = f16 ? pl_tex_create(A.gpu.gpu, pl_tex_params(.w = m->sc_w, .h = m->sc_h, .format = f16, .sampleable = true,
                                                              .renderable = true, .storable = true, .blit_src = true,
                                                              .blit_dst = true, .host_readable = true,
                                                              .debug_tag = PL_DEBUG_TAG))
                     : NULL;
    pm->t_resample = pl_timer_create(A.gpu.gpu);
    pm->t_surround = pl_timer_create(A.gpu.gpu);
    pm->t_finish = pl_timer_create(A.gpu.gpu);
    pm->dp = pl_dispatch_create(A.gpu.log, A.gpu.gpu);
    pm->env = (struct fp_env) { .log = A.gpu.log, .gpu = A.gpu.gpu, .dp = pm->dp };
    pm->surround = fp_surround_create(&pm->env, m->sc_w, m->sc_h, 0);
    pm->finish = fp_finish_create(&pm->env);
    pm->subs = fp_subs_create(&pm->env, m->sc_w, m->sc_h);
    if (!pm->dp || !pm->canvas || !pm->surround || !pm->finish || !pm->subs) {
        fprintf(stderr, "perf1: %s: the picture path's GPU objects could not be created\n", m->name);
        return false;
    }
    return true;
}

bool picture_init_subs(void)
{
    if (PC.no_subs)
        return true;
    const char *path = PC.sub_file ? PC.sub_file : A.cfg.file;
    char err[300] = "";
    if (!subs_load(path, PC.sub_file ? -1 : PC.sub_stream, PC.sub_file == NULL, &g_track, err, sizeof(err))) {
        if (PC.sub_file || PC.sub_stream >= 0) {
            fprintf(stderr, "perf1: subtitles: %s\n", err);
            return false;
        }
        printf("subtitles: none (%s)\n", err);
        return true;
    }
    for (int i = 0; i < A.nmon; i++)
        if (g_pm[i].subs && !subs_apply(&g_track, g_pm[i].subs)) {
            fprintf(stderr, "perf1: subtitles: libass refused the track\n");
            return false;
        }
    g_have_subs = true;
    printf("subtitles: %zu events (%s) and %zu fonts from %s in %.2f s; %zu packets skipped\n", g_track.n_ev, g_track.codec,
           g_track.n_font, g_track.source, g_track.seconds, g_track.skipped);
    return true;
}

void picture_destroy(void)
{
    for (int i = 0; i < MAX_MONITORS; i++) {
        struct pic_mon *pm = &g_pm[i];
        if (A.gpu.gpu) {
            fp_subs_destroy(&pm->subs);
            fp_finish_destroy(&pm->finish);
            fp_surround_destroy(&pm->surround);
            pl_timer_destroy(A.gpu.gpu, &pm->t_resample);
            pl_timer_destroy(A.gpu.gpu, &pm->t_surround);
            pl_timer_destroy(A.gpu.gpu, &pm->t_finish);
            pl_tex_destroy(A.gpu.gpu, &pm->canvas);
            pl_dispatch_destroy(&pm->dp);
        }
        for (int c = 0; c < COST_COUNT; c++)
            samples_free(&pm->cost[c]);
    }
    if (g_ready) {
        pic_prep_destroy(&g_prep);
        pic_pass1_destroy(&g_pass1);
        pl_dispatch_destroy(&g_dp);
        g_ready = false;
    }
    subs_free(&g_track);
}

// ---- passes 2 to 4 ---------------------------------------------------------------------------------------------

// The monitor that shows dialogue during a shot: the cue sheet's, else --sub-monitor, else the monitor that shows the most
// of the shot's picture.
static int dialogue_monitor(const struct pic_refresh *r, const struct pic_shot *shot)
{
    if (r->cue->subtitle_monitor >= 0)
        return r->cue->subtitle_monitor;
    if (PC.sub_monitor >= 0)
        return PC.sub_monitor;
    int best = 0;
    long best_area = -1;
    for (int i = 0; i < A.nmon; i++) {
        const struct lay_share *s = &shot->lay.share[i];
        long area = s->has_picture ? (long) (s->tx1 - s->tx0) * (s->ty1 - s->ty0) : 0;
        if (area > best_area) {
            best_area = area;
            best = i;
        }
    }
    return best;
}

bool pic_finish_into(struct pic_mon *pm, const struct pic_refresh *r, const struct pic_shot *shot, pl_tex out, int out_bits)
{
    struct fp_color_gpu color;
    pic_color_make(picture_color(r->m->slot), out_bits, &color);
    pm->env.timer = pm->t_finish;
    if (!fp_finish_run_fused(pm->finish, pm->surround, pm->canvas, out, &shot->look.look, &color, FP_DITHER_BLUE_NOISE))
        return false;
    if (g_have_subs && dialogue_monitor(r, shot) == r->m->slot) {
        struct fp_subs_result res;
        if (!fp_subs_draw(pm->subs, out, r->pts, &res))
            return false;
        pm->n_sub_draws += res.drawn;
        pm->n_sub_overflow += res.overflow;
    }
    return true;
}

// ---- one refresh ------------------------------------------------------------------------------------------------

static uint64_t drain(pl_timer t)
{
    uint64_t sum = 0, ns;
    while ((ns = pl_timer_query(A.gpu.gpu, t)))
        sum += ns;
    return sum;
}

static void note_cost(struct pic_mon *pm, enum pic_cost c, uint64_t ns)
{
    if (ns)
        samples_push(&pm->cost[c], (double) ns / 1e6);
}

// The prepared texture a pass reads: the sigmoid copy for a monitor that magnifies, else the linear one.
static pl_tex pick_texture(const struct pic_prepared *p, bool sigmoid)
{
    return sigmoid ? p->sig : p->lin;
}

// The surround's per-refresh inputs: film seconds since this monitor's previous refresh, a cut, a restart.
static struct fp_surround_frame surround_frame(struct pic_mon *pm, const struct pic_refresh *r, const struct pic_shot *shot)
{
    bool glow = shot->look.look.mode == FP_SURROUND_GLOW;
    bool reset = !r->timed || !pm->primed || r->first_of_epoch || r->epoch != pm->last_epoch || (glow && !pm->last_glow);
    double speed = A.master_period > 0.0 ? A.step_s / A.master_period : 1.0; // film seconds per wall second
    struct fp_surround_frame f = {
        .dt_s = reset ? 0.0 : (r->wake - pm->last_wake) * speed,
        .cut = !reset && r->shot_id != pm->last_shot,
        .reset = reset,
    };
    pm->primed = true;
    pm->last_glow = glow;
    pm->last_epoch = r->epoch;
    pm->last_shot = r->shot_id;
    pm->last_wake = r->wake;
    return f;
}

bool picture_draw(const struct pic_refresh *r)
{
    struct monitor *m = r->m;
    struct pic_mon *pm = &g_pm[m->slot];
    if (!g_ready || !pm->canvas || r->front < 0 || !r->sf)
        return false;
    const struct pic_shot *shot = picture_shot(r->shot_id, r->cue);
    struct pic_timing tm;
    picture_timing(r, &tm);
    pl_dispatch_reset_frame(g_dp);
    pl_dispatch_reset_frame(pm->dp);

    const bool sigmoid = shot->upscales[m->slot];
    const struct pic_prepared *front = pic_prep_get(&g_prep, m->rr, r->pts, r->mix->frames[r->front], A.src_w, A.src_h, sigmoid);
    if (!front)
        return false;
    const struct pic_prepared *other = NULL;
    // The neighbouring frame, seen through its own shot (it differs from this frame's at a cut) and its own domain.
    const struct pic_shot *shot_b = tm.partner_shot ? tm.partner_shot : shot;
    const bool sigmoid_b = shot_b->upscales[m->slot];
    if (tm.blend.partner) {
        // It is not in this monitor's queue (that keeps the frame on screen and those after it), but it was prepared when a
        // monitor showed it: the previous frame by the time this one started, the next by the master, whose refresh is
        // serviced a whole master refresh before its light. If it is gone, this refresh is drawn unmixed.
        other = pic_prep_find(&g_prep, tm.partner_pts, A.src_w, A.src_h, sigmoid_b);
        if (!other) {
            pm->n_blend_missed++;
            memset(&tm.blend, 0, sizeof(tm.blend));
        }
    }
    // frame a is the earlier one, b the later one; the partner is a when it is the previous frame
    const bool partner_is_a = tm.blend.partner < 0;
    const struct pic_shot *shot_a = partner_is_a ? shot_b : shot;
    const struct pic_shot *shot_bb = partner_is_a ? shot : shot_b;
    const bool sig_a = partner_is_a ? sigmoid_b : sigmoid, sig_b = partner_is_a ? sigmoid : sigmoid_b;
    const struct pic_prepared *prep_a = tm.blend.partner ? (partner_is_a ? other : front) : front;
    const struct pic_prepared *prep_b = tm.blend.partner ? (partner_is_a ? front : other) : NULL;
    const struct pic_slide *slide_a = (partner_is_a ? tm.partner_slide_on : tm.slide_on) ? (partner_is_a ? &tm.partner_slide : &tm.slide) : NULL;
    const struct pic_slide *slide_bb = (partner_is_a ? tm.slide_on : tm.partner_slide_on) ? (partner_is_a ? &tm.slide : &tm.partner_slide) : NULL;
    struct pic_pass1_job job = {
        .dp = pm->dp, .geom = picture_geom(m->slot), .view = &shot_a->view, .slide = slide_a,
        .a = pick_texture(prep_a, sig_a), .b = prep_b ? pick_texture(prep_b, sig_b) : NULL,
        .wa = tm.blend.wa, .wb = tm.blend.wb, .sigmoid = sig_a,
        .view_b = prep_b ? &shot_bb->view : NULL, .slide_b = slide_bb, .sigmoid_b = sig_b,
        .out = pm->canvas, .timer = pm->t_resample,
    };
    if (!pic_pass1_run(&g_pass1, &job))
        return false;

    pm->env.timer = pm->t_surround;
    struct fp_surround_frame sf = surround_frame(pm, r, shot);
    if (!fp_surround_update(pm->surround, pm->canvas, &shot->look.look, &shot->look.time, &sf))
        return false;
    // under the rolling blend the dump waits for a refresh that mixes two frames (the master never does)
    if (PC.dump_dir && !pm->dumped && r->pts >= PC.dump_at_s && (!PC.blend_rolling || tm.blend.partner || m->slot == A.master) &&
        (!PC.dump_cut || m->slot == A.master || (tm.blend.partner && shot_b != shot))) {
        pm->dumped = true;
        picture_dump(pm, r, shot, &job, &tm);
    }
    if (!pic_finish_into(pm, r, shot, r->sf->fbo, r->sf->color_repr.bits.color_depth ? r->sf->color_repr.bits.color_depth : 8))
        return false;

    pm->n_drawn++;
    pm->n_slide += tm.slide_on;
    pm->n_blend += tm.blend.partner != 0;
    pm->n_upscaling += sigmoid;
    uint64_t ns[COST_COUNT] = { pic_prep_take_gpu_ns(&g_prep), drain(pm->t_resample), drain(pm->t_surround),
                                drain(pm->t_finish), pm->subs ? fp_subs_take_gpu_ns(pm->subs) : 0 };
    pm->last_gpu_ms = 0.0;
    for (int c = 0; c < COST_COUNT; c++) {
        note_cost(pm, (enum pic_cost) c, ns[c]);
        pm->last_gpu_ms += (double) ns[c] / 1e6;
    }
    return true;
}

double picture_last_gpu_ms(int slot)
{
    return g_pm[slot].last_gpu_ms;
}

void picture_plan_only(const struct pic_refresh *r)
{
    struct pic_mon *pm = &g_pm[r->m->slot];
    const struct pic_shot *shot = picture_shot(r->shot_id, r->cue);
    struct pic_timing tm;
    picture_timing(r, &tm);
    pm->n_drawn++;
    pm->n_slide += tm.slide_on;
    pm->n_blend += tm.blend.partner != 0;
    pm->n_upscaling += shot->upscales[r->m->slot];
}
