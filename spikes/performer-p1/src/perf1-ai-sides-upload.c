// The AI sides' canvases on the GPU (see perf1-ai-sides.h): which canvas versions are there, and the copy of the next one
// the coming shots need, --ai-upload-kb per master refresh, from the store's heap. The worker may reuse a heap slot at any
// time, so a copy is kept only if the slot's counter is unchanged after every chunk; otherwise it is dropped and found
// again later. libplacebo copies host memory before pl_tex_upload returns (no_import: never an imported host pointer).
// When a copy starts, the slot's pages are prefetched, so a chunk does not wait on a page the system had paged out.
//
// Which canvas next: walking the frame table forward from the shot after the master's, shot by shot, while shots start
// within --ai-horizon-s, twice: first the first shot whose best complete canvas in the heap is better than what the GPU
// holds for it; then (phase 4) the first shot with a canvas on the GPU whose patch page is in the heap and not on the GPU
// (so a page never delays a later shot's canvas). A page's header and patch table are copied to host memory when its copy
// starts (ai_store_read_page), its texels like a canvas's rows; a page the reader refuses is skipped until its slot holds
// something else.
// Room on the GPU (at most --ai-gpu-canvases): a free place, else a canvas no recent showing draws that was made for a shot
// behind the playhead, else one made for a shot farther ahead than the new one; a canvas for a nearer shot is never given
// up for a farther one, so the walk stops when the GPU is full of what comes first. Textures are allocated in steps of
// AI_TEX_BUCKET texels and reused when big enough, so the render thread rarely creates one.
#include "perf1-ai-sides-internal.h"

// Patch pages the reader refused (slot and gen): skipped until the slot holds something else, so one bad page never
// stalls the copies of the shots after it.
struct ai_rejected { uint32_t slot, gen; };
static struct ai_rejected g_rejected[8];
static int g_rejected_n;

static bool walk(int frame, double playhead, double now, int pages);

static void forget(struct ai_gpu_canvas *g)
{
    g->used = g->complete = false;                          // the texture stays for reuse
    g->canvas_id = UINT32_MAX;
    g->shot = -1;
    free(g->patches);
    g->patches = NULL;
}

void ai_gpu_drop_all(void)
{
    for (int i = 0; i < AI_GPU_MAX; i++)
        forget(&G_AI.gpu[i]);
    G_AI.uploading = -1;
    g_rejected_n = 0;
}

void ai_gpu_destroy(void)
{
    if (!A.gpu.gpu)
        return;
    for (int i = 0; i < AI_GPU_MAX; i++)
        pl_tex_destroy(A.gpu.gpu, &G_AI.gpu[i].tex);
    ai_gpu_drop_all();
}

int ai_gpu_best(uint32_t canvas_id, double pts)
{
    int best = -1;
    for (int i = 0; i < AIC.gpu_canvases; i++) {
        const struct ai_gpu_canvas *g = &G_AI.gpu[i];
        if (!g->complete || g->kind != AI_KIND_CANVAS || g->canvas_id != canvas_id || g->valid_from_s > pts + 1e-3)
            continue;
        if (best < 0 || g->tier > G_AI.gpu[best].tier || (g->tier == G_AI.gpu[best].tier && g->version > G_AI.gpu[best].version))
            best = i;
    }
    return best;
}

int ai_gpu_page(int32_t shot, uint32_t canvas_id)
{
    int best = -1;
    for (int i = 0; i < AIC.gpu_canvases; i++) {
        const struct ai_gpu_canvas *g = &G_AI.gpu[i];
        if (g->complete && g->kind == AI_KIND_PATCH_PAGE && g->shot == shot && g->canvas_id == canvas_id && g->patches &&
            (best < 0 || g->version > G_AI.gpu[best].version))
            best = i;
    }
    return best;
}

// What the GPU holds (complete or being copied) for a canvas id: the best tier, 0 = nothing.
static uint32_t gpu_tier(uint32_t canvas_id)
{
    uint32_t tier = 0;
    for (int i = 0; i < AIC.gpu_canvases; i++) {
        const struct ai_gpu_canvas *g = &G_AI.gpu[i];
        if (g->used && g->kind == AI_KIND_CANVAS && g->canvas_id == canvas_id && g->tier > tier)
            tier = g->tier;
    }
    return tier;
}

// The GPU holds (or is copying) this version of a shot's patch page, or a newer one.
static bool gpu_has_page(int32_t shot, uint32_t canvas_id, uint32_t version)
{
    for (int i = 0; i < AIC.gpu_canvases; i++) {
        const struct ai_gpu_canvas *g = &G_AI.gpu[i];
        if (g->used && g->kind == AI_KIND_PATCH_PAGE && g->shot == shot && g->canvas_id == canvas_id && g->version >= version)
            return true;
    }
    return false;
}

static bool pinned(int i, double now)
{
    for (int k = 0; k < AI_SHOWINGS; k++)
        if (G_AI.show[k].used && (G_AI.show[k].gpu == i || G_AI.show[k].page == i) &&
            now - G_AI.show[k].last_drawn < AI_PIN_S)
            return true;
    return false;
}

// A place for a canvas made for the showing that starts at `valid_from`: free, else the unpinned canvas made for the shot
// farthest behind the playhead, else the one made for the shot farthest ahead, if that is farther than the new one.
static int gpu_place(double playhead, double valid_from, double now)
{
    int best = -1;
    double best_key = 0.0;
    for (int i = 0; i < AIC.gpu_canvases; i++) {
        const struct ai_gpu_canvas *g = &G_AI.gpu[i];
        if (!g->used)
            return i;
        if (i == G_AI.uploading || pinned(i, now))
            continue;
        bool behind = g->valid_from_s < playhead - 1.0;
        if (!behind && g->valid_from_s <= valid_from)
            continue; // made for a nearer shot (or this one): it stays
        // behind: the farther behind the better (keys below -1e8); ahead: the farther the better
        double key = behind ? -1e9 + g->valid_from_s : -g->valid_from_s;
        if (best < 0 || key < best_key) {
            best = i;
            best_key = key;
        }
    }
    return best;
}

static bool sane(const struct ai_canvas_rec *c)
{
    return c->state == AI_CANVAS_COMPLETE && c->format == AI_FMT_RGBA8_FILM && c->tex_w > 0 && c->tex_h > 0 &&
           c->tex_w <= 16384 && c->tex_h <= 16384 && c->row_bytes >= c->tex_w * 4 && c->bytes == (uint64_t) c->tex_h * c->row_bytes &&
           (uint64_t) (c->kind == AI_KIND_PATCH_PAGE ? c->page_header_bytes : 0) + c->bytes <= G_AI.store.heap_slot_bytes;
}

// The best complete version of a canvas id in the heap: its slot and record, or -1.
static int heap_best(uint32_t canvas_id, struct ai_canvas_rec *out)
{
    int best = -1;
    struct ai_canvas_rec c;
    for (uint32_t s = 0; s < G_AI.store.heap_slots; s++) {
        if (!ai_store_read_canvas(&G_AI.store, s, &c) || c.kind != AI_KIND_CANVAS || c.canvas_id != canvas_id || !sane(&c))
            continue;
        if (best < 0 || c.tier > out->tier || (c.tier == out->tier && c.version > out->version)) {
            best = (int) s;
            *out = c;
        }
    }
    return best;
}

static void reject(int slot, uint32_t gen)
{
    g_rejected[g_rejected_n % 8] = (struct ai_rejected) { (uint32_t) slot, gen };
    g_rejected_n++;
}

static bool rejected(uint32_t slot, uint32_t gen)
{
    for (int i = 0; i < (g_rejected_n < 8 ? g_rejected_n : 8); i++)
        if (g_rejected[i].slot == slot && g_rejected[i].gen == gen)
            return true;
    return false;
}

// The newest complete patch page of a shot (in its set) in the heap: its slot and record, or -1.
static int heap_page(int32_t shot, uint32_t canvas_id, struct ai_canvas_rec *out)
{
    int best = -1;
    struct ai_canvas_rec c;
    for (uint32_t s = 0; s < G_AI.store.heap_slots; s++) {
        if (!ai_store_read_canvas(&G_AI.store, s, &c) || c.kind != AI_KIND_PATCH_PAGE || c.shot != shot ||
            c.canvas_id != canvas_id || !sane(&c) || rejected(s, c.gen))
            continue;
        if (best < 0 || c.version > out->version) {
            best = (int) s;
            *out = c;
        }
    }
    return best;
}

static void copy_rows(void)
{
    struct ai_gpu_canvas *g = &G_AI.gpu[G_AI.uploading];
    uint32_t budget = (uint32_t) AIC.upload_kb * 1024u / (g->row_bytes ? g->row_bytes : 1u);
    uint32_t rows = budget ? budget : 1u;
    if (rows > (uint32_t) g->h - g->rows_done)
        rows = (uint32_t) g->h - g->rows_done;
    double t0 = now_ms();
    const uint8_t *src = ai_store_slot_pixels(&G_AI.store, (uint32_t) g->heap_slot) + g->pixel_offset +
                         (size_t) g->rows_done * g->row_bytes;
    bool ok = pl_tex_upload(A.gpu.gpu, pl_tex_transfer_params(
        .tex = g->tex, .rc = { .x0 = 0, .y0 = (int) g->rows_done, .z0 = 0, .x1 = g->w, .y1 = (int) (g->rows_done + rows), .z1 = 1 },
        .row_pitch = g->row_bytes, .ptr = (void *) src, .no_import = true));
    samples_push(&G_AI.upload_ms, now_ms() - t0);
    if (!ok || !ai_store_canvas_still(&G_AI.store, (uint32_t) g->heap_slot, g->heap_gen)) {
        if (ok)
            G_AI.uploads_torn++;   // the worker took the slot back mid-copy
        else
            G_AI.uploads_aborted++;
        forget(g);
        G_AI.uploading = -1;
        return;
    }
    g->rows_done += rows;
    G_AI.upload_bytes += (uint64_t) rows * g->row_bytes;
    if (g->rows_done >= (uint32_t) g->h) {
        g->complete = true;
        g->last_used = now_s();
        G_AI.uploads++;
        G_AI.uploading = -1;
    }
}

static int bucket(uint32_t n)
{
    return (int) ((n + AI_TEX_BUCKET - 1) / AI_TEX_BUCKET * AI_TEX_BUCKET);
}

static bool start_copy(int slot, const struct ai_canvas_rec *c, int gi)
{
    struct ai_gpu_canvas *g = &G_AI.gpu[gi];
    struct ai_patch_page page = {0};
    struct ai_patch_rec *patches = NULL;
    if (c->kind == AI_KIND_PATCH_PAGE && !ai_store_read_page(&G_AI.store, (uint32_t) slot, c, &page, &patches)) {
        G_AI.uploads_torn++;                                // changed under us, or not a sound page: the place keeps
        return false;                                       // what it holds
    }
    forget(g);
    g->page = page;
    g->patches = patches;
    if (!g->tex || g->tex_w < (int) c->tex_w || g->tex_h < (int) c->tex_h) {
        pl_tex_destroy(A.gpu.gpu, &g->tex);
        pl_fmt fmt = pl_find_named_fmt(A.gpu.gpu, "rgba8");
        int tw = bucket(c->tex_w) > g->tex_w ? bucket(c->tex_w) : g->tex_w;   // grow, never shrink the other axis
        int th = bucket(c->tex_h) > g->tex_h ? bucket(c->tex_h) : g->tex_h;
        g->tex = fmt ? pl_tex_create(A.gpu.gpu, pl_tex_params(.w = tw, .h = th, .format = fmt, .sampleable = true,
                                                              .host_writable = true, .debug_tag = PL_DEBUG_TAG))
                     : NULL;
        if (!g->tex) {
            forget(g);
            g->tex_w = g->tex_h = 0;
            G_AI.uploads_aborted++;
            return false;
        }
        g->tex_w = tw;
        g->tex_h = th;
    }
    g->w = (int) c->tex_w;
    g->h = (int) c->tex_h;
    g->used = true;
    g->complete = false;
    g->kind = c->kind;
    g->shot = c->kind == AI_KIND_PATCH_PAGE ? c->shot : -1;
    g->pixel_offset = c->kind == AI_KIND_PATCH_PAGE ? c->page_header_bytes : 0;
    g->canvas_id = c->canvas_id;
    g->version = c->version;
    g->tier = c->tier;
    g->recipe = c->recipe;
    g->world_x0 = c->world_x0;
    g->world_y0 = c->world_y0;
    g->scale = c->scale;
    g->valid_from_s = c->valid_from_s;
    g->shot_hint = c->shot_hint;
    g->serial = ++G_AI.serial;
    g->heap_slot = slot;
    g->heap_gen = c->gen;
    g->rows_done = 0;
    g->row_bytes = c->row_bytes;
    G_AI.uploading = gi;
    WIN32_MEMORY_RANGE_ENTRY range = { (PVOID) (ai_store_slot_pixels(&G_AI.store, (uint32_t) slot) + g->pixel_offset),
                                       (SIZE_T) c->bytes };
    PrefetchVirtualMemory(GetCurrentProcess(), 1, &range, 0); // a hint: the copies below must not wait on the disk
    return true;
}

void ai_upload_step(int frame, double playhead)
{
    if (G_AI.uploading >= 0) {
        copy_rows();
        return;
    }
    double now = now_s();
    if (now < G_AI.next_pick || frame < 0)
        return;
    G_AI.next_pick = now + 0.05;
    // canvases for every shot within the horizon first, then their patch pages: a page never delays a later canvas
    for (int pages = 0; pages < (AIC.patches ? 2 : 1); pages++)
        if (walk(frame, playhead, now, pages))
            return;
}

// One pass over the shots starting within the horizon, from the one after the master's (the showing on screen keeps what
// it chose at its start): the first canvas (pages: patch page) to copy. True when a copy started or the GPU is full of
// what comes first.
static bool walk(int frame, double playhead, double now, int pages)
{
    const struct ai_store *st = &G_AI.store;
    uint32_t n = ai_store_frames_published(st);
    uint32_t f = (uint32_t) frame;
    struct ai_frame_rec fr;
    struct ai_shot_rec sr;
    if (f < n && ai_store_read_frame(st, f, &fr) && (fr.flags & AI_FRAME_VALID) && fr.shot >= 0 &&
        ai_store_read_shot(st, (uint32_t) fr.shot, &sr) && sr.first_frame + sr.n_frames > f)
        f = sr.first_frame + sr.n_frames;
    while (f < n && st->frames[f].pts <= playhead + AIC.horizon_s) {
        if (!ai_store_read_frame(st, f, &fr) || !(fr.flags & AI_FRAME_VALID) || fr.shot < 0 ||
            !ai_store_read_shot(st, (uint32_t) fr.shot, &sr) || sr.n_frames == 0) {
            f++; // not analysed yet (or being written): look at the next frame
            continue;
        }
        struct ai_canvas_rec c = {0};
        int slot = -1;
        if (!pages) {
            slot = heap_best(sr.canvas_id, &c);
            if (slot >= 0 && c.tier <= gpu_tier(c.canvas_id))
                slot = -1;
        } else if (gpu_tier(sr.canvas_id)) {               // patches need a canvas under them
            slot = heap_page(fr.shot, sr.canvas_id, &c);
            if (slot >= 0 && gpu_has_page(fr.shot, sr.canvas_id, c.version))
                slot = -1;
        }
        if (slot >= 0) {
            int gi = gpu_place(playhead, c.valid_from_s, now);
            if (gi >= 0 && start_copy(slot, &c, gi))
                copy_rows();
            else if (gi >= 0 && pages)
                reject(slot, c.gen);                        // a page the reader refuses: skip it from now on
            return true;
        }
        uint32_t next = sr.first_frame + sr.n_frames;
        f = next > f ? next : f + 1;
    }
    return false;
}
