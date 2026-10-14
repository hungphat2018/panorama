// The AI sides, player end: options, the store, the showings and the fill's inputs per refresh (see perf1-ai-sides.h).
// The player record is perf1-ai-sides-record.c, the copies to the GPU perf1-ai-sides-upload.c, the shaders
// perf1-finish-surround-ai.c.
//
// Under --blend rolling the fill follows frame a's picture and slide with the front frame's camera transform: the AI sides
// are not checked with the rolling blend (it is off by default).
#include "perf1-ai-sides-internal.h"

#include <math.h>
#include <sys/stat.h>

#define WORKER_LOST_S 2.0
#define OPEN_RETRY_S 1.0
#define CROP_TOLERANCE_PX 2.0

// T3 (the canvas plus structure patches) is retired as a display tier since X1c (2026-10-14): carried sides break
// within frames on real camera moves. Patch pages are neither copied nor shown unless --ai-patches asks for them.
struct ai_cfg AIC = { .horizon_s = 8.0, .gpu_canvases = 16, .upload_kb = 256, .seam = true, .patches = false };
struct ai_state G_AI = { .uploading = -1 };

// ---- options ------------------------------------------------------------------------------------------------------

void ai_sides_usage(void)
{
    fprintf(stderr,
        "  AI sides (the live path's worker: spikes/ai-surround/live-worker.py):\n"
        "          [--ai-sides STORE] [--ai-horizon-s 8] [--ai-gpu-canvases 16] [--ai-upload-kb 256] [--ai-no-seam]\n"
        "          [--ai-patches]\n"
        "  --ai-sides STORE  sample the sides the worker writes into this side store (opened when it appears; glow until a\n"
        "                   canvas is ready, tiers change only at cuts; OUT/ai-sides.json and OUT/ai-showings.jsonl)\n"
        "  --ai-horizon-s S  copy to the GPU the canvases (and patch pages) of shots starting within S seconds\n"
        "  --ai-gpu-canvases N  canvases and patch pages kept on the GPU (2..24, default 16)\n"
        "  --ai-upload-kb K  bytes copied to the GPU per master refresh\n"
        "  --ai-no-seam      no seam pull (the canvas meets the picture as it is)\n"
        "  --ai-patches      show structure patches (T3, retired: patch pages copied and composited over the canvas;\n"
        "                   off by default; --ai-no-patches is still accepted)\n");
}

int ai_sides_arg(int argc, char **argv, int *i)
{
    const char *a = argv[*i];
    if (!strcmp(a, "--ai-no-seam")) {
        AIC.seam = false;
        return 1;
    }
    if (!strcmp(a, "--ai-patches") || !strcmp(a, "--ai-no-patches")) {
        AIC.patches = !strcmp(a, "--ai-patches");
        return 1;
    }
    bool store = !strcmp(a, "--ai-sides"), horizon = !strcmp(a, "--ai-horizon-s"), gpus = !strcmp(a, "--ai-gpu-canvases"),
         kb = !strcmp(a, "--ai-upload-kb");
    if (!store && !horizon && !gpus && !kb)
        return 0;
    const char *v = *i + 1 < argc ? argv[*i + 1] : NULL;
    if (!v) {
        fprintf(stderr, "perf1: %s needs a value\n", a);
        return -1;
    }
    char *end = NULL;
    double x = store ? 0.0 : strtod(v, &end);
    bool ok = store || (end != v && !*end);
    if (store)
        AIC.store = v;
    else if (horizon && (ok = ok && x >= 0.5 && x <= 120.0))
        AIC.horizon_s = x;
    else if (gpus && (ok = ok && x >= 2 && x <= AI_GPU_MAX && x == floor(x)))
        AIC.gpu_canvases = (int) x;
    else if (kb && (ok = ok && x >= 16 && x <= 65536 && x == floor(x)))
        AIC.upload_kb = (int) x;
    if (!ok) {
        fprintf(stderr, "perf1: %s: bad value '%s'\n", a, v);
        return -1;
    }
    (*i)++;
    return 1;
}

bool ai_sides_on(void)
{
    return AIC.store != NULL;
}

void ai_sides_init_monitor(struct monitor *m, struct fp_surround *surround)
{
    if (!ai_sides_on() || A.cfg.headless_hz)
        return;
    fp_surround_set_ai_fill(surround, ai_fill_append, &G_AI.fill[m->slot]);
    G_AI.hint[m->slot] = -1;
    G_AI.cached_frame[m->slot] = -1;
    G_AI.cached_shot[m->slot] = -1;
    for (int i = 0; i < AI_ACC_MAX; i++)
        if (!G_AI.acc[i].acc)
            G_AI.acc[i].owner = -1;                         // no showing owns a pair yet
    pl_fmt f16 = pl_find_named_fmt(A.gpu.gpu, "rgba16f");
    static const uint16_t transparent[4] = {0};             // the warm-up's fill composites nothing
    for (int i = 0; i < AI_STAND_INS && f16; i++)
        if (!G_AI.stand_in[i])
            G_AI.stand_in[i] = pl_tex_create(A.gpu.gpu, pl_tex_params(.w = 1, .h = 1, .format = f16, .sampleable = true,
                                                                      .initial_data = transparent, .debug_tag = PL_DEBUG_TAG));
    if (!G_AI.showing_log && A.cfg.out_dir) {
        char path[1024];
        snprintf(path, sizeof(path), "%s/ai-showings.jsonl", A.cfg.out_dir);
        G_AI.showing_log = fopen(path, "w");
    }
}

// ---- the store ----------------------------------------------------------------------------------------------------

static void forget_store_contents(void)
{
    ai_gpu_drop_all();
    memset(G_AI.show, 0, sizeof(G_AI.show));
    for (int i = 0; i < AI_ACC_MAX; i++)
        G_AI.acc[i].owner = -1;
    for (int i = 0; i < MAX_MONITORS; i++) {
        G_AI.hint[i] = -1;
        G_AI.cached_frame[i] = -1;
        G_AI.cached_shot[i] = -1;
    }
    for (int i = 0; i < AI_SEAM_CACHE; i++)
        G_AI.seam_gpu[i] = -2;
}

// The store describes this film: the worker's file size and picture size match ours.
static bool store_matches(void)
{
    struct _stat64 st;
    uint64_t size = _stat64(A.cfg.file, &st) == 0 ? (uint64_t) st.st_size : 0;
    volatile struct ai_worker_block *w = G_AI.store.worker;
    if (w->film_bytes == size && w->src_w == (uint32_t) A.src_w && w->src_h == (uint32_t) A.src_h)
        return true;
    snprintf(G_AI.open_err, sizeof(G_AI.open_err), "the store is for another film (%llu bytes, %ux%u)",
             (unsigned long long) w->film_bytes, w->src_w, w->src_h);
    return false;
}

static void store_close(void)
{
    forget_store_contents();
    ai_store_close(&G_AI.store);
    G_AI.open = false;
}

static void store_poll(double now)
{
    if (!G_AI.open) {
        if (now < G_AI.next_open_try)
            return;
        G_AI.next_open_try = now + OPEN_RETRY_S;
        char err[160] = "";
        if (!ai_store_open(&G_AI.store, AIC.store, err, sizeof(err))) {
            if (strcmp(err, G_AI.open_err))
                printf("ai sides: %s: %s; trying again every %.0f s\n", AIC.store, err, OPEN_RETRY_S);
            snprintf(G_AI.open_err, sizeof(G_AI.open_err), "%s", err);
            return;
        }
        if (!store_matches()) {
            printf("ai sides: %s: %s\n", AIC.store, G_AI.open_err);
            ai_store_close(&G_AI.store);
            return;
        }
        G_AI.open = true;
        G_AI.opens++;
        G_AI.store_uid = G_AI.store.store_uid;
        G_AI.worker_beat = ai_store_worker_beat(&G_AI.store);
        G_AI.worker_beat_t = now;
        G_AI.worker_alive = true;
        G_AI.open_err[0] = 0;
        forget_store_contents();
        printf("ai sides: %s open: %u frames, %u heap slots of %.1f MB\n", AIC.store, G_AI.store.max_frames,
               G_AI.store.heap_slots, G_AI.store.heap_slot_bytes / 1048576.0);
        return;
    }
    uint64_t uid = *(const volatile uint64_t *) &G_AI.store.layout->store_uid;
    if (uid != G_AI.store_uid) { // the worker emptied the store for a new run: nothing taken from it is valid any more
        G_AI.store_uid = uid;
        G_AI.uid_changes++;
        forget_store_contents();
        printf("ai sides: the worker restarted the store: canvases dropped; glow until the next cut\n");
        if (!store_matches()) {
            printf("ai sides: %s: %s\n", AIC.store, G_AI.open_err);
            store_close();
            return;
        }
    }
    uint64_t beat = ai_store_worker_beat(&G_AI.store);
    if (beat != G_AI.worker_beat) {
        if (!G_AI.worker_alive)
            printf("ai sides: the worker is back\n");
        G_AI.worker_beat = beat;
        G_AI.worker_beat_t = now;
        G_AI.worker_alive = true;
    } else if (G_AI.worker_alive && now - G_AI.worker_beat_t > WORKER_LOST_S) {
        G_AI.worker_alive = false; // what is on the GPU and in the heap stays usable; nothing new comes
        G_AI.worker_losses++;
        printf("ai sides: the worker has not beaten for %.0f s: keeping what is ready\n", WORKER_LOST_S);
    }
}

// ---- reading records -------------------------------------------------------------------------------------------------

// The frame and shot records of one monitor's refresh. A read can meet the worker writing that record (it then retries
// only microseconds); the monitor's last whole copy of the same row is used instead, so a write never blinks the sides.
static bool frame_record(int slot, int f, struct ai_frame_rec *out)
{
    if (ai_store_read_frame(&G_AI.store, (uint32_t) f, out)) {
        G_AI.cached_frame[slot] = f;
        G_AI.cached_fr[slot] = *out;
        return true;
    }
    if (G_AI.cached_frame[slot] == f) {
        *out = G_AI.cached_fr[slot];
        G_AI.record_reuses++;
        return true;
    }
    return false;
}

static bool shot_record(int slot, int32_t row, struct ai_shot_rec *out)
{
    if (ai_store_read_shot(&G_AI.store, (uint32_t) row, out)) {
        G_AI.cached_shot[slot] = row;
        G_AI.cached_sr[slot] = *out;
        return true;
    }
    if (G_AI.cached_shot[slot] == row) {
        *out = G_AI.cached_sr[slot];
        G_AI.record_reuses++;
        return true;
    }
    return false;
}

// ---- showings -------------------------------------------------------------------------------------------------------

static struct ai_showing *showing_find(int epoch, int32_t shot)
{
    for (int k = 0; k < AI_SHOWINGS; k++)
        if (G_AI.show[k].used && G_AI.show[k].epoch == epoch && G_AI.show[k].shot == shot)
            return &G_AI.show[k];
    return NULL;
}

// A new showing of `shot` in `epoch`, made on its first drawn refresh: the best canvas already on the GPU when that refresh
// starts the shot (a cut, its first frames) or the epoch (a seek, the film start); else glow, for the whole shot. No canvas
// either when the worker took another crop as the picture than this shot's (its canvas could hold bars where we see sides).
// With a canvas, the shot's patch page too if it is on the GPU by then (T3: the structure patches over the canvas).
static struct ai_showing *showing_new(int epoch, int32_t shot, uint32_t canvas_id, bool at_start, bool crop_ok, double pts,
                                      double now)
{
    int idx = G_AI.show_next;
    struct ai_showing *sh = &G_AI.show[idx];
    G_AI.show_next = (G_AI.show_next + 1) % AI_SHOWINGS;
    ai_patches_release(idx);
    int gi = at_start && crop_ok ? ai_gpu_best(canvas_id, pts) : -1;
    int pi = gi >= 0 && AIC.patches ? ai_gpu_page(shot, canvas_id) : -1;
    *sh = (struct ai_showing) { .used = true, .epoch = epoch, .shot = shot, .gpu = gi, .page = pi, .acc = -1,
                               .canvas_id = canvas_id, .version = gi >= 0 ? G_AI.gpu[gi].version : 0,
                               .tier = pi >= 0 ? AI_TIER_STRUCTURE : gi >= 0 ? G_AI.gpu[gi].tier : 0, .applied = -1,
                               .start_pts = pts, .last_drawn = now };
    G_AI.pages_shown += pi >= 0;
    if (G_AI.showing_log)
        fprintf(G_AI.showing_log, "{\"pts\":%.6f,\"epoch\":%d,\"shot\":%d,\"canvas\":%u,\"tier\":%u,\"version\":%u,"
                                  "\"page\":%s,\"at_start\":%s,\"crop_ok\":%s,\"worker_alive\":%s}\n",
                pts, epoch, shot, canvas_id, sh->tier, sh->version, pi >= 0 ? "true" : "false", at_start ? "true" : "false",
                crop_ok ? "true" : "false", G_AI.worker_alive ? "true" : "false");
    return sh;
}

// ---- the fill's inputs ------------------------------------------------------------------------------------------------

static void set_geometry(struct ai_fill_in *fi, const struct pic_pass1_job *job)
{
    const struct room_geom *geo = job->geom;
    float mon[4] = { (float) geo->px_w, (float) geo->px_h, (float) geo->w_mm, (float) geo->h_mm };
    float cen[4] = { (float) geo->centre.x, (float) geo->centre.y, (float) geo->centre.z, (float) geo->left_mm };
    float ex[4] = { (float) geo->ex.x, (float) geo->ex.y, (float) geo->ex.z, geo->curve ? 1.0f : 0.0f };
    float ey[4] = { (float) geo->ey.x, (float) geo->ey.y, (float) geo->ey.z, (float) geo->curvature };
    float srf[4] = { (float) geo->top_mm, (float) geo->u_axis, (float) geo->v_eye, (float) geo->seat_distance };
    memcpy(fi->u_mon, mon, sizeof(mon));
    memcpy(fi->u_cen, cen, sizeof(cen));
    memcpy(fi->u_ex, ex, sizeof(ex));
    memcpy(fi->u_ey, ey, sizeof(ey));
    memcpy(fi->u_srf, srf, sizeof(srf));
    for (int i = 0; i < 4; i++) {
        fi->u_pic[i] = (float) job->view->rect[i];
        fi->u_crop[i] = (float) job->view->crop[i];
    }
    const struct pic_slide *sl = job->slide;
    fi->u_slide[0] = sl ? (float) sl->du0 : 0.0f;
    fi->u_slide[1] = sl ? (float) sl->dv0 : 0.0f;
    fi->u_slide[2] = sl ? (float) sl->ddu : 0.0f;
    fi->u_slide[3] = sl ? (float) sl->ddv : 0.0f;
    int n = A.src_w > A.src_h ? A.src_w : A.src_h;
    fi->seam[1] = AI_SEAM_FALLOFF;
    fi->seam[2] = (float) n;
    fi->seam[3] = 1.0f / (float) n;
}

static void mul3(const double a[9], const double b[9], double out[9])
{
    for (int r = 0; r < 3; r++)
        for (int c = 0; c < 3; c++)
            out[r * 3 + c] = a[r * 3] * b[c] + a[r * 3 + 1] * b[3 + c] + a[r * 3 + 2] * b[6 + c];
}

static void rows_of(const double m[9], float r0[4], float r1[4], float r2[4])
{
    float *rows[3] = { r0, r1, r2 };
    for (int r = 0; r < 3; r++) {
        for (int c = 0; c < 3; c++)
            rows[r][c] = (float) m[r * 3 + c];
        rows[r][3] = 0.0f;
    }
}

static void no_patches(struct ai_fill_in *fi)
{
    static const double id[9] = { 1, 0, 0, 0, 1, 0, 0, 0, 1 };
    rows_of(id, fi->a0, fi->a1, fi->a2);
    for (int i = 0; i < 4; i++) {
        fi->asize[i] = 1.0f;
        fi->aon[i] = 0.0f;
    }
    fi->acc_tex = fi->ovl_tex = NULL;                       // the shaders bind their stand-ins
}

// False when the frame's transform is unusable (the sides then show the glow for this refresh).
static bool set_canvas(struct ai_fill_in *fi, const struct ai_frame_rec *fr, const struct ai_gpu_canvas *g, double pts,
                       const struct pic_prepared *front, int gi, const struct ai_showing *sh)
{
    // source px -> world px (the frame's projective camera transform) -> texel coordinates (the canvas's origin and scale)
    double H[9] = { fr->h[0], fr->h[1], fr->h[2], fr->h[3], fr->h[4], fr->h[5], fr->h[6], fr->h[7], 1.0 };
    for (int i = 0; i < 8; i++)
        if (!isfinite(H[i]))
            return false;
    for (int i = 0; i < AI_STAND_INS; i++)
        if (!G_AI.stand_in[i])                              // the shaders would refuse a pass with a missing texture
            return false;
    double s = g->scale > 0.0 ? g->scale : 1.0;
    const double S[9] = { 1.0 / s, 0, -g->world_x0 / s, 0, 1.0 / s, -g->world_y0 / s, 0, 0, 1 };
    double M[9];
    mul3(S, H, M);
    rows_of(M, fi->m0, fi->m1, fi->m2);
    fi->size[0] = (float) g->w;
    fi->size[1] = (float) g->h;
    fi->size[2] = 1.0f / (float) g->tex_w;
    fi->size[3] = 1.0f / (float) g->tex_h;
    for (int i = 0; i < 4; i++) {
        fi->gain[i] = isfinite(fr->gain[i]) && fr->gain[i] >= 0.0f ? fr->gain[i] : 1.0f;
        fi->margin[i] = G_AI.margins_valid && G_AI.margins[i] > 1.0f ? G_AI.margins[i] : 1000.0f;
    }
    fi->tex = g->tex;
    // the shot's structure patches: the accumulate and overlay textures, through the page grid's map of the set's world
    bool patches = sh && sh->page >= 0 && sh->acc >= 0;
    if (patches) {
        const struct ai_gpu_canvas *pg = &G_AI.gpu[sh->page];
        const struct ai_acc *a = &G_AI.acc[sh->acc];
        double Ag[9];
        mul3(pg->page.grid_map, H, Ag);
        rows_of(Ag, fi->a0, fi->a1, fi->a2);
        fi->asize[0] = (float) pg->page.grid_w;
        fi->asize[1] = (float) pg->page.grid_h;
        fi->asize[2] = 1.0f / (float) a->tex_w;
        fi->asize[3] = 1.0f / (float) a->tex_h;
        fi->aon[0] = 1.0f;
        fi->aon[1] = a->ovl_dirty ? 1.0f : 0.0f;
        fi->aon[2] = fi->aon[3] = 0.0f;
        fi->acc_tex = a->acc;
        fi->ovl_tex = a->ovl;
    } else {
        no_patches(fi);
    }
    fi->seam_tex = AIC.seam && front ? ai_seam_profile(gi, g->serial, patches ? sh->acc_gen : 0u, pts, front->lin, fi) : NULL;
    fi->seam[0] = fi->seam_tex ? 1.0f : 0.0f;
    fi->on = g->tex != NULL;
    return true;
}

// The warm-up frame (before the film plays) draws once with the fill on over a transparent stand-in canvas, makes a seam
// profile and draws a stand-in patch, so their shader variants are compiled then, not at the first canvas or patch the film
// shows.
static void warm_up(int slot, const struct pic_pass1_job *job, const struct pic_prepared *front)
{
    struct ai_fill_in *fi = &G_AI.fill[slot];
    for (int i = 0; i < AI_STAND_INS; i++)
        if (!G_AI.stand_in[i])
            return;
    if (G_AI.warmed[slot])
        return;
    G_AI.warmed[slot] = true;
    set_geometry(fi, job);
    static const double id[9] = { 1, 0, 0, 0, 1, 0, 0, 0, 1 };
    rows_of(id, fi->m0, fi->m1, fi->m2);
    for (int i = 0; i < 4; i++) {
        fi->size[i] = fi->gain[i] = 1.0f;
        fi->margin[i] = 1000.0f;
    }
    no_patches(fi);
    fi->tex = G_AI.stand_in[AI_STAND_CANVAS];
    fi->seam_tex = AIC.seam && front ? ai_seam_profile(-1, 0, 0, -1.0, front->lin, fi) : NULL;
    fi->seam[0] = fi->seam_tex ? 1.0f : 0.0f;
    fi->on = true;
    if (AIC.patches)
        ai_patches_warm_up();
}

// ---- one refresh ----------------------------------------------------------------------------------------------------

void ai_sides_refresh(const struct pic_refresh *r, const struct pic_shot *shot, const struct pic_pass1_job *job,
                      const struct pic_prepared *front)
{
    if (!ai_sides_on() || !r->sf)
        return;
    const int slot = r->m->slot;
    struct ai_fill_in *fi = &G_AI.fill[slot];
    fi->on = false;
    if (!r->timed) { // the warm-up frame: compile what the film will need, and work out the room's side widths
        ai_compute_margins();
        if (shot->look.look.mode == FP_SURROUND_GLOW)
            warm_up(slot, job, front);
        return;
    }
    const bool master = slot == A.master;
    double now = now_s();
    if (master) {
        store_poll(now);
        if (G_AI.open) {
            ai_compute_margins(); // a new cue sheet version
            ai_pressure_update(now);
        }
    }
    struct ai_showing *sh = NULL;
    if (G_AI.open && shot->look.look.mode == FP_SURROUND_GLOW) {
        int f = ai_store_find_frame(&G_AI.store, r->pts, G_AI.hint[slot]);
        G_AI.hint[slot] = f;
        struct ai_frame_rec fr;
        if (f >= 0 && frame_record(slot, f, &fr) && (fr.flags & AI_FRAME_VALID) && fr.shot >= 0) {
            sh = showing_find(r->epoch, fr.shot);
            struct ai_shot_rec sr;
            if (!sh && shot_record(slot, fr.shot, &sr)) {
                bool at_start = r->first_of_epoch || (fr.flags & AI_FRAME_CUT) || (uint32_t) f - sr.first_frame <= 2;
                bool crop_ok = true;
                for (int i = 0; i < 4; i++)
                    crop_ok &= fabs((double) sr.crop[i] - job->view->crop[i]) <= CROP_TOLERANCE_PX;
                if (!crop_ok && !G_AI.crop_mismatches++)
                    printf("ai sides: the worker took [%.0f %.0f %.0f %.0f] as the picture of shot %d, this cue sheet "
                           "[%.0f %.0f %.0f %.0f]: glow on such shots (give the worker the same --cues)\n",
                           sr.crop[0], sr.crop[1], sr.crop[2], sr.crop[3], fr.shot, job->view->crop[0], job->view->crop[1],
                           job->view->crop[2], job->view->crop[3]);
                sh = showing_new(r->epoch, fr.shot, sr.canvas_id, at_start, crop_ok, r->pts, now);
            }
            if (sh) {
                sh->last_drawn = now;
                const struct ai_gpu_canvas *g = sh->gpu >= 0 ? &G_AI.gpu[sh->gpu] : NULL;
                if (g && g->complete && g->canvas_id == sh->canvas_id && g->version == sh->version) {
                    G_AI.gpu[sh->gpu].last_used = now;
                    if (sh->page >= 0) {
                        if (ai_patches_to_frame(sh, f))
                            G_AI.gpu[sh->page].last_used = now;
                        if (sh->page < 0)                   // the page or its textures went away: the canvas alone
                            sh->tier = g->tier;
                    }
                    set_geometry(fi, job);
                    if (!set_canvas(fi, &fr, g, r->pts, front, sh->gpu, sh))
                        fi->on = false;
                }
            }
        }
    }
    if (fi->on)
        G_AI.refreshes_ai[slot]++;
    else
        G_AI.refreshes_glow[slot]++;
    if (master && G_AI.open) {
        double dt = r->pts - G_AI.last_master_pts;
        if (dt > 0.0 && dt < 0.5) // film time on screen per tier (pauses and seeks are not counted)
            G_AI.tier_film_s[fi->on && sh ? (sh->tier < 5 ? sh->tier : 4) : 0] += dt;
        G_AI.last_master_pts = r->pts;
        ai_player_record(now, r, sh);
        ai_upload_step(G_AI.hint[slot], engine_playhead(&A.engine));
    }
}

// ---- end -------------------------------------------------------------------------------------------------------------

void ai_sides_destroy(void)
{
    if (!ai_sides_on())
        return;
    if (G_AI.open) {
        volatile struct ai_player_block *p = ai_store_player_begin(&G_AI.store);
        p->state = AI_PLAYER_ENDED;
        ai_store_player_end(&G_AI.store);
    }
    ai_gpu_destroy();
    ai_patches_destroy();
    ai_seam_destroy();
    if (G_AI.open)
        ai_store_close(&G_AI.store);
    G_AI.open = false;
    if (G_AI.showing_log) {
        fclose(G_AI.showing_log);
        G_AI.showing_log = NULL;
    }
}

void ai_sides_write_log(void)
{
    if (!ai_sides_on() || !A.cfg.out_dir)
        return;
    char path[1024];
    snprintf(path, sizeof(path), "%s/ai-sides.json", A.cfg.out_dir);
    FILE *f = fopen(path, "w");
    if (!f) {
        fprintf(stderr, "perf1: cannot write %s\n", path);
        return;
    }
    struct summary up, seam, patch;
    summarize(G_AI.upload_ms.v, G_AI.upload_ms.n, &up);
    summarize(G_AI.seam_ms.v, G_AI.seam_ms.n, &seam);
    summarize(G_AI.patch_ms.v, G_AI.patch_ms.n, &patch);
    double total = 0.0;
    for (int i = 0; i < 5; i++)
        total += G_AI.tier_film_s[i];
    fprintf(f, "{\"schema\":\"panorama.perf1.ai-sides/2\",\"store\":\"");   // /2: "patches" (phase 4)
    for (const char *c = AIC.store; *c; c++)
        if ((unsigned char) *c >= 0x20 && *c != '"')
            fputc(*c == '\\' ? '/' : *c, f);
    fprintf(f, "\",\"opens\":%llu,\"store_restarts\":%llu,\"worker_losses\":%llu,\"record_reuses\":%llu,"
               "\"crop_mismatches\":%llu,\"margins_px\":[%.1f,%.1f,%.1f,%.1f],"
               "\"uploads\":{\"done\":%llu,\"bytes\":%llu,\"torn\":%llu,\"aborted\":%llu,\"chunk_cpu_ms\":{\"n\":%zu,"
               "\"p50\":%.4f,\"p99\":%.4f,\"max\":%.4f}},\"seam\":{\"passes\":%llu,\"gpu_ms\":{\"n\":%zu,\"p50\":%.4f,"
               "\"p99\":%.4f,\"max\":%.4f}},\"patches\":{\"pages_shown\":%llu,\"applied\":%llu,\"failed\":%llu,"
               "\"behind\":%llu,\"frame_cpu_ms\":{\"n\":%zu,\"p50\":%.4f,\"p99\":%.4f,\"max\":%.4f}},"
               "\"film_s_per_tier\":{\"glow\":%.3f,\"mosaic\":%.3f,\"plate\":%.3f,"
               "\"structure\":%.3f,\"refined\":%.3f,\"total\":%.3f},\"monitors\":[",
            (unsigned long long) G_AI.opens, (unsigned long long) G_AI.uid_changes, (unsigned long long) G_AI.worker_losses,
            (unsigned long long) G_AI.record_reuses, (unsigned long long) G_AI.crop_mismatches, G_AI.margins[0],
            G_AI.margins[1], G_AI.margins[2], G_AI.margins[3], (unsigned long long) G_AI.uploads,
            (unsigned long long) G_AI.upload_bytes, (unsigned long long) G_AI.uploads_torn,
            (unsigned long long) G_AI.uploads_aborted, up.n, up.p50, up.p99, up.max, (unsigned long long) G_AI.seam_passes,
            seam.n, seam.p50, seam.p99, seam.max, (unsigned long long) G_AI.pages_shown,
            (unsigned long long) G_AI.patches_applied, (unsigned long long) G_AI.patch_failures,
            (unsigned long long) G_AI.monitor_behind, patch.n, patch.p50, patch.p99,
            patch.max, G_AI.tier_film_s[0], G_AI.tier_film_s[1], G_AI.tier_film_s[2],
            G_AI.tier_film_s[3], G_AI.tier_film_s[4], total);
    for (int i = 0; i < A.nmon; i++)
        fprintf(f, "%s{\"name\":\"%s\",\"refreshes_ai\":%u,\"refreshes_glow\":%u}", i ? "," : "", A.mon[i].name,
                G_AI.refreshes_ai[i], G_AI.refreshes_glow[i]);
    fprintf(f, "]}\n");
    fclose(f);
    printf("ai sides: %llu canvases and pages copied (%.1f MB, %llu torn), film time glow %.1f s, mosaic %.1f s, plate %.1f s, "
           "structure %.1f s (%llu patches); wrote %s\n",
           (unsigned long long) G_AI.uploads, G_AI.upload_bytes / 1048576.0, (unsigned long long) G_AI.uploads_torn,
           G_AI.tier_film_s[0], G_AI.tier_film_s[1], G_AI.tier_film_s[2], G_AI.tier_film_s[3],
           (unsigned long long) G_AI.patches_applied, path);
    samples_free(&G_AI.upload_ms);
    samples_free(&G_AI.seam_ms);
    samples_free(&G_AI.patch_ms);
}
