// Pass 4: subtitles. libass renders the line at the monitor's own pixels (one libass pixel = one monitor pixel); the
// bitmaps are packed into one R8 atlas and drawn over the finished 8-bit frame by libplacebo's overlay path
// (pl_render_image with a NULL image draws overlays only). Nothing here resamples: every part maps 1:1 to monitor pixels.
//
// Blending happens last, after pass 3's dither, directly on the encoded 8-bit values, so the text is crisp and
// has no dither noise. libass colours are used as raw target code values (white text is code 255 whatever the transfer).
//
// A line can never reach the next monitor: libass renders into a frame the size of the allowed box (the whole monitor
// by default), wraps the line to that width and cuts its bitmaps at the frame, and the overlay is drawn into this
// monitor's own render target. What libass cuts is not reported by libass, so a line that reaches the box's edge is
// flagged as overflow (it is either cut or fits to the pixel), and the caller's policy decides: draw what fits, or draw nothing.
#include <ass/ass.h> // also: ass_new_track, ass_process_chunk (container subtitle streams)

#include "perf1-finish-internal.h"

#define ATLAS_W 2048
#define ATLAS_MAX_H 4096
#define PAD 1

struct fp_subs {
    const struct fp_env *env;
    int mon_w, mon_h;
    int box[4];                      // allowed area x0, y0, x1, y1 (monitor pixels)
    enum fp_subs_overflow overflow_mode;

    ASS_Library *lib;
    ASS_Renderer *rend;
    ASS_Track *track;
    bool fonts_dirty;
    bool fonts_ready;

    pl_renderer rr;                  // draws the overlays
    pl_tex atlas;
    int atlas_h;
    unsigned char *atlas_cpu;
    struct pl_overlay_part *parts;
    int n_parts, cap_parts;
    struct fp_subs_result last;      // the geometry of the subtitle picture libass last returned
    bool geom_valid;                 // `last` describes the current picture
    bool parts_valid;                // the atlas and `parts` hold the current picture
    uint64_t gpu_ns;                 // libplacebo's GPU timers of the overlay passes since the last fp_subs_take_gpu_ns
};

static void overlay_info(void *priv, const struct pl_render_info *info)
{
    struct fp_subs *s = priv;
    s->gpu_ns += info->pass->last;
}

uint64_t fp_subs_take_gpu_ns(struct fp_subs *s)
{
    uint64_t ns = s->gpu_ns;
    s->gpu_ns = 0;
    return ns;
}

static void ass_log(int level, const char *fmt, va_list args, void *data)
{
    (void) data;
    if (level > 3)
        return;
    fputs("libass: ", stderr);
    vfprintf(stderr, fmt, args);
    fputc('\n', stderr);
}

static void apply_frame_size(struct fp_subs *s)
{
    int bw = s->box[2] - s->box[0], bh = s->box[3] - s->box[1];
    ass_set_frame_size(s->rend, bw, bh);
    ass_set_storage_size(s->rend, bw, bh);
    ass_set_pixel_aspect(s->rend, 1.0);
    s->geom_valid = s->parts_valid = false;
}

struct fp_subs *fp_subs_create(const struct fp_env *env, int mon_w, int mon_h)
{
    struct fp_subs *s = calloc(1, sizeof(*s));
    if (!s)
        return NULL;
    s->env = env;
    s->mon_w = mon_w;
    s->mon_h = mon_h;
    s->box[2] = mon_w;
    s->box[3] = mon_h;
    s->lib = ass_library_init();
    if (s->lib) {
        ass_set_message_cb(s->lib, ass_log, NULL);
        s->rend = ass_renderer_init(s->lib);
    }
    s->rr = pl_renderer_create(env->log, env->gpu);
    if (!s->lib || !s->rend || !s->rr) {
        FP_ERR(env, "subtitles: libass or the overlay renderer could not start");
        fp_subs_destroy(&s);
        return NULL;
    }
    apply_frame_size(s);
    return s;
}

void fp_subs_destroy(struct fp_subs **sp)
{
    struct fp_subs *s = *sp;
    if (!s)
        return;
    if (s->track)
        ass_free_track(s->track);
    if (s->rend)
        ass_renderer_done(s->rend);
    if (s->lib)
        ass_library_done(s->lib);
    pl_renderer_destroy(&s->rr);
    pl_tex_destroy(s->env->gpu, &s->atlas);
    free(s->atlas_cpu);
    free(s->parts);
    free(s);
    *sp = NULL;
}

bool fp_subs_add_font(struct fp_subs *s, const char *name, const void *data, size_t size)
{
    ass_add_font(s->lib, name, (const char *) data, (int) size);
    s->fonts_dirty = true;
    return true;
}

static void setup_fonts(struct fp_subs *s)
{
    // The fonts handed to fp_subs_add_font (a film's attachments) and the system's fonts (DirectWrite on Windows): a
    // film's subtitles name ordinary families.
    ass_set_fonts(s->rend, NULL, "Arial", ASS_FONTPROVIDER_AUTODETECT, NULL, 1);
    s->fonts_dirty = false;
    s->fonts_ready = true;
}

bool fp_subs_load_ass(struct fp_subs *s, const char *data, size_t size)
{
    char *copy = malloc(size + 1); // libass may edit the buffer it parses
    if (!copy)
        return false;
    memcpy(copy, data, size);
    copy[size] = 0;
    if (s->fonts_dirty || !s->fonts_ready)
        setup_fonts(s);
    ASS_Track *t = ass_read_memory(s->lib, copy, size, NULL);
    free(copy);
    if (!t) {
        FP_ERR(s->env, "subtitles: libass could not read the script");
        return false;
    }
    if (s->track)
        ass_free_track(s->track);
    s->track = t;
    s->geom_valid = s->parts_valid = false;
    return true;
}

// A track built from a container's subtitle stream: the stream's header first, then one chunk per packet.
bool fp_subs_track_start(struct fp_subs *s, const char *header, size_t size)
{
    if (s->fonts_dirty || !s->fonts_ready)
        setup_fonts(s);
    ASS_Track *t = ass_new_track(s->lib);
    if (!t)
        return false;
    if (header && size)
        ass_process_codec_private(t, (char *) header, (int) size);
    if (s->track)
        ass_free_track(s->track);
    s->track = t;
    s->geom_valid = s->parts_valid = false;
    return true;
}

void fp_subs_track_chunk(struct fp_subs *s, const char *data, size_t size, long long start_ms, long long duration_ms)
{
    if (s->track)
        ass_process_chunk(s->track, (char *) data, (int) size, start_ms, duration_ms);
    s->geom_valid = s->parts_valid = false;
}

void fp_subs_set_box(struct fp_subs *s, int x0, int y0, int x1, int y1)
{
    s->box[0] = x0 < 0 ? 0 : x0;
    s->box[1] = y0 < 0 ? 0 : y0;
    s->box[2] = x1 > s->mon_w ? s->mon_w : x1;
    s->box[3] = y1 > s->mon_h ? s->mon_h : y1;
    apply_frame_size(s);
}

void fp_subs_set_overflow(struct fp_subs *s, enum fp_subs_overflow mode)
{
    s->overflow_mode = mode;
}

// ---- atlas ------------------------------------------------------------------------------------------------------

static bool ensure_atlas(struct fp_subs *s, int need_h)
{
    if (s->atlas && s->atlas_h >= need_h)
        return true;
    int h = s->atlas_h ? s->atlas_h : 256;
    while (h < need_h)
        h *= 2;
    if (h > ATLAS_MAX_H)
        return false;
    pl_fmt fmt = pl_find_named_fmt(s->env->gpu, "r8");
    if (!fmt)
        return false;
    pl_tex_destroy(s->env->gpu, &s->atlas);
    s->atlas = pl_tex_create(s->env->gpu, pl_tex_params(.w = ATLAS_W, .h = h, .format = fmt, .sampleable = true,
                                                         .host_writable = true));
    free(s->atlas_cpu);
    s->atlas_cpu = malloc((size_t) ATLAS_W * h);
    s->atlas_h = s->atlas && s->atlas_cpu ? h : 0;
    return s->atlas_h != 0;
}

// Lays the bitmaps out in the atlas (shelf packing, one pixel apart) and fills s->parts. Returns false if they do not fit.
static bool pack_overlay(struct fp_subs *s, const ASS_Image *img)
{
    int n = 0;
    for (const ASS_Image *i = img; i; i = i->next)
        n++;
    if (n > s->cap_parts) {
        struct pl_overlay_part *p = realloc(s->parts, sizeof(*p) * (size_t) n);
        if (!p)
            return false;
        s->parts = p;
        s->cap_parts = n;
    }
    // Pass 1 finds the atlas height the layout needs, pass 2 copies the bitmaps and fills in the parts.
    int used_h = 0;
    for (int pass = 0; pass < 2; pass++) {
        int x = PAD, y = PAD, row_h = 0, k = 0;
        used_h = PAD;
        for (const ASS_Image *i = img; i; i = i->next) {
            if (i->w + 2 * PAD > ATLAS_W)
                return false;
            if (x + i->w + PAD > ATLAS_W) {
                x = PAD;
                y += row_h + PAD;
                row_h = 0;
            }
            if (y + i->h + PAD > used_h)
                used_h = y + i->h + PAD;
            if (pass == 1) {
                for (int r = 0; r < i->h; r++)
                    memcpy(s->atlas_cpu + (size_t) (y + r) * ATLAS_W + x, i->bitmap + (size_t) r * i->stride,
                           (size_t) i->w);
                float a = (float) (255 - (int) (i->color & 0xFF)) / 255.0f;
                s->parts[k++] = (struct pl_overlay_part) {
                    .src = { (float) x, (float) y, (float) (x + i->w), (float) (y + i->h) },
                    .dst = { (float) (s->box[0] + i->dst_x), (float) (s->box[1] + i->dst_y),
                             (float) (s->box[0] + i->dst_x + i->w), (float) (s->box[1] + i->dst_y + i->h) },
                    .color = { (float) ((i->color >> 24) & 0xFF) / 255.0f, (float) ((i->color >> 16) & 0xFF) / 255.0f,
                               (float) ((i->color >> 8) & 0xFF) / 255.0f, a },
                };
            }
            x += i->w + PAD;
            if (i->h > row_h)
                row_h = i->h;
        }
        if (pass == 0) {
            if (!ensure_atlas(s, used_h))
                return false;
            memset(s->atlas_cpu, 0, (size_t) ATLAS_W * (size_t) s->atlas_h);
        } else {
            s->n_parts = k;
        }
    }
    return pl_tex_upload(s->env->gpu, pl_tex_transfer_params(
        .tex = s->atlas, .rc = { 0, 0, 0, ATLAS_W, used_h, 1 }, .ptr = s->atlas_cpu, .row_pitch = ATLAS_W));
}

// Cuts every part to the allowed box (source and destination move together), so nothing is drawn outside it.
static void clip_parts_to_box(struct fp_subs *s)
{
    int k = 0;
    for (int i = 0; i < s->n_parts; i++) {
        struct pl_overlay_part p = s->parts[i];
        float cx0 = p.dst.x0 < s->box[0] ? (float) s->box[0] : p.dst.x0;
        float cy0 = p.dst.y0 < s->box[1] ? (float) s->box[1] : p.dst.y0;
        float cx1 = p.dst.x1 > s->box[2] ? (float) s->box[2] : p.dst.x1;
        float cy1 = p.dst.y1 > s->box[3] ? (float) s->box[3] : p.dst.y1;
        if (cx1 <= cx0 || cy1 <= cy0)
            continue;
        p.src.x0 += cx0 - p.dst.x0;
        p.src.y0 += cy0 - p.dst.y0;
        p.src.x1 += cx1 - p.dst.x1;
        p.src.y1 += cy1 - p.dst.y1;
        p.dst = (pl_rect2df) { cx0, cy0, cx1, cy1 };
        s->parts[k++] = p;
    }
    s->n_parts = k;
}

// ---- lookup and draw ---------------------------------------------------------------------------------------------

// Asks libass what is on at `pts_s`. Keeps s->last (geometry, flags) current and, when `with_parts`, the atlas and parts.
static bool lookup(struct fp_subs *s, double pts_s, bool with_parts, bool *changed_out)
{
    int change = 0;
    ASS_Image *img = ass_render_frame(s->rend, s->track, fp_subs_ass_time_ms(pts_s), &change);
    *changed_out = change != 0;
    if (change)
        s->geom_valid = s->parts_valid = false;

    if (!s->geom_valid) {
        s->last = (struct fp_subs_result) { 0 };
        int bb[4] = { 1 << 30, 1 << 30, -(1 << 30), -(1 << 30) };
        for (const ASS_Image *i = img; i; i = i->next) {
            int x0 = s->box[0] + i->dst_x, y0 = s->box[1] + i->dst_y;
            bb[0] = x0 < bb[0] ? x0 : bb[0];
            bb[1] = y0 < bb[1] ? y0 : bb[1];
            bb[2] = x0 + i->w > bb[2] ? x0 + i->w : bb[2];
            bb[3] = y0 + i->h > bb[3] ? y0 + i->h : bb[3];
            s->last.n_images++;
        }
        if (img) {
            memcpy(s->last.bbox, bb, sizeof(bb));
            s->last.edge_hit[0] = bb[0] <= s->box[0];
            s->last.edge_hit[1] = bb[1] <= s->box[1];
            s->last.edge_hit[2] = bb[2] >= s->box[2];
            s->last.edge_hit[3] = bb[3] >= s->box[3];
            s->last.overflow = s->last.edge_hit[0] || s->last.edge_hit[1] || s->last.edge_hit[2] || s->last.edge_hit[3];
        }
        s->geom_valid = true;
    }
    if (with_parts && !s->parts_valid) {
        s->n_parts = 0;
        if (img) {
            if (!pack_overlay(s, img)) {
                FP_ERR(s->env, "subtitles: the bitmaps do not fit the atlas");
                return false;
            }
            clip_parts_to_box(s);
        }
        s->parts_valid = true;
    }
    return true;
}

bool fp_subs_measure(struct fp_subs *s, double pts_s, struct fp_subs_result *res)
{
    struct fp_subs_result zero = { 0 };
    if (!s->track) {
        *res = zero;
        return true;
    }
    bool changed;
    if (!lookup(s, pts_s, false, &changed))
        return false;
    *res = s->last;
    res->changed = changed;
    return true;
}

bool fp_subs_draw(struct fp_subs *s, pl_tex target, double pts_s, struct fp_subs_result *res)
{
    struct fp_subs_result r = { 0 };
    if (!s->track) {
        if (res)
            *res = r;
        return true;
    }
    bool changed;
    if (!lookup(s, pts_s, true, &changed))
        return false;
    r = s->last;
    r.changed = changed;
    if (s->n_parts == 0 || (r.overflow && s->overflow_mode == FP_SUBS_REFUSE)) {
        if (res)
            *res = r;
        return true;
    }

    struct pl_overlay ol = {
        .tex = s->atlas, .mode = PL_OVERLAY_MONOCHROME, .coords = PL_OVERLAY_COORDS_DST_FRAME,
        .repr = pl_color_repr_rgb, .color = pl_color_space_srgb,
        .parts = s->parts, .num_parts = s->n_parts,
    };
    struct pl_frame frame = {
        .num_planes = 1,
        .planes = {{ .texture = target, .components = 4,
                     .component_mapping = { PL_CHANNEL_R, PL_CHANNEL_G, PL_CHANNEL_B, PL_CHANNEL_A } }},
        .repr = { .sys = PL_COLOR_SYSTEM_RGB, .levels = PL_COLOR_LEVELS_FULL, .alpha = PL_ALPHA_UNKNOWN,
                  .bits = { .sample_depth = 8, .color_depth = 8 } },
        .color = pl_color_space_srgb,
        .overlays = &ol, .num_overlays = 1,
    };
    struct pl_render_params params = pl_render_fast_params;
    params.border = PL_CLEAR_SKIP; // keep the finished picture: only the overlay is drawn
    params.info_callback = overlay_info;
    params.info_priv = s;
    bool ok = pl_render_image(s->rr, NULL, &frame, &params);
    r.drawn = ok;
    if (res)
        *res = r;
    return ok;
}
