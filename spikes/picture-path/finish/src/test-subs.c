// Pass 4 tests: subtitles drawn at the monitor's own pixels. Exactness against a direct alpha blend of libass's own
// bitmaps, a line wider than the monitor (clip or refuse), a box that is off-centre, and on/off timing at the exact frame.
#include <ass/ass.h>

#include "test-canvas.h"
#include "test-main.h"

#define FAMILY "DejaVu Sans"

static char *read_file(const char *path, size_t *size)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t) n + 1);
    if (buf && fread(buf, 1, (size_t) n, f) != (size_t) n) {
        free(buf);
        buf = NULL;
    }
    fclose(f);
    if (buf) {
        buf[n] = 0;
        *size = (size_t) n;
    }
    return buf;
}

// An ASS script for a monitor of w x h pixels: PlayRes equals the monitor, so a font size is a pixel size.
static char *make_script(int w, int h, int fontsize, int margin_v, const char *wrap, const char *events)
{
    size_t cap = strlen(events) + 2048;
    char *s = malloc(cap);
    snprintf(s, cap,
             "[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: %s\nScaledBorderAndShadow: yes\n\n"
             "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
             "MarginR, MarginV, Encoding\n"
             "Style: Default," FAMILY ",%d,&H00FFFFFF,&H000000FF,&H00101010,&H80000000,0,0,0,0,100,100,0,0,1,2,1,2,20,20,%d,1\n\n"
             "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n%s",
             w, h, wrap, fontsize, margin_v, events);
    return s;
}

// The reference: the same script rendered by an independent libass instance, blended in float on the CPU.
struct ref_ctx {
    ASS_Library *lib;
    ASS_Renderer *rend;
    ASS_Track *track;
};

static bool ref_init(struct ref_ctx *r, const char *font, size_t font_size, const char *script, int w, int h)
{
    r->lib = ass_library_init();
    ass_add_font(r->lib, "dejavu", (char *) font, (int) font_size);
    r->rend = ass_renderer_init(r->lib);
    ass_set_frame_size(r->rend, w, h);
    ass_set_storage_size(r->rend, w, h);
    ass_set_pixel_aspect(r->rend, 1.0);
    ass_set_fonts(r->rend, NULL, "sans-serif", ASS_FONTPROVIDER_NONE, NULL, 1);
    char *copy = strdup(script);
    r->track = ass_read_memory(r->lib, copy, strlen(copy), NULL);
    free(copy);
    return r->track != NULL;
}

static void ref_done(struct ref_ctx *r)
{
    if (r->track)
        ass_free_track(r->track);
    if (r->rend)
        ass_renderer_done(r->rend);
    if (r->lib)
        ass_library_done(r->lib);
}

// Blends libass's images over `rgba` (w*h*4 bytes) the way a straight alpha blend does, rounding to nearest after each
// layer. `layers[]` counts the images over each pixel; `ambig[]` is set when some channel's unrounded result lies within
// 0.12 of a rounding boundary (where a GPU's own rounding may legitimately go the other way: the GPU's conversion of a
// float to 8 bits is only specified to within 0.6 of a code).
static void ref_blend(const ASS_Image *img, uint8_t *rgba, int w, int h, uint8_t *layers, uint8_t *ambig)
{
    for (; img; img = img->next) {
        double a_img = (255 - (int) (img->color & 0xFF)) / 255.0;
        int rgb[3] = { (int) ((img->color >> 24) & 0xFF), (int) ((img->color >> 16) & 0xFF), (int) ((img->color >> 8) & 0xFF) };
        for (int y = 0; y < img->h; y++) {
            for (int x = 0; x < img->w; x++) {
                int px = img->dst_x + x, py = img->dst_y + y;
                if (px < 0 || py < 0 || px >= w || py >= h)
                    continue;
                double c = img->bitmap[(size_t) y * img->stride + x] / 255.0 * a_img;
                uint8_t *d = rgba + 4 * ((size_t) py * w + px);
                size_t pi = (size_t) py * w + px;
                layers[pi]++;
                for (int k = 0; k < 3; k++) {
                    double v = d[k] + (rgb[k] - d[k]) * c;
                    if (fabs(v - floor(v) - 0.5) <= 0.12)
                        ambig[pi] = 1;
                    d[k] = (uint8_t) floor(v + 0.5);
                }
            }
        }
    }
}

static void fill_background(uint8_t *rgba, int w, int h)
{
    unsigned rng = 99;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            rng = rng * 1664525u + 1013904223u;
            uint8_t *p = rgba + 4 * ((size_t) y * w + x);
            int base = 30 + (int) (150.0 * x / w);
            p[0] = (uint8_t) (base + ((rng >> 24) & 15));
            p[1] = (uint8_t) (60 + (int) (100.0 * y / h) + ((rng >> 16) & 15));
            p[2] = (uint8_t) (200 - (int) (140.0 * x / w) + ((rng >> 8) & 15));
            p[3] = 255;
        }
}

static bool upload8(const struct fp_env *env, pl_tex tex, const uint8_t *rgba)
{
    return pl_tex_upload(env->gpu, pl_tex_transfer_params(.tex = tex, .ptr = (void *) rgba));
}

// ---- S1: exact against a direct blend ------------------------------------------------------------------------------

static const char s1_events[] =
    "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\an2}Hello, this is a line of dialogue to blend.\n"
    "Dialogue: 1,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\an7\\pos(300,200)\\bord0\\shad0\\1c&H0000FF&\\p1}m 0 0 l 200 0 200 100 0 100{\\p0}\n";

static void s1_exact(struct tctx *t, const char *font, size_t font_size)
{
    printf("-- S1: the subtitle pixels against a direct alpha blend of libass's bitmaps\n");
    const struct fp_env *env = &t->dev.env;
    const int W = 1920, H = 1080;
    pl_tex target = tu_target8(env, W, H, false, "s1 target");
    uint8_t *bg = malloc((size_t) W * H * 4), *want = malloc((size_t) W * H * 4);
    uint8_t *layers = calloc((size_t) W * H, 1), *ambig = calloc((size_t) W * H, 1);
    fill_background(bg, W, H);
    memcpy(want, bg, (size_t) W * H * 4);

    // a dialogue line, and a solid rectangle drawn by libass (its coverage is 0 or 255 only: it must be bit-exact)
    char *script = make_script(W, H, 48, 60, "0", s1_events);
    struct fp_subs *s = fp_subs_create(env, W, H);
    fp_subs_add_font(s, "dejavu", font, font_size);
    tu_check(fp_subs_load_ass(s, script, strlen(script)), "script loaded");
    struct ref_ctx ref;
    tu_check(ref_init(&ref, font, font_size, script, W, H), "reference libass instance started");
    int change = 0;
    ASS_Image *img = ass_render_frame(ref.rend, ref.track, 2000, &change);
    ref_blend(img, want, W, H, layers, ambig);

    tu_check(upload8(env, target, bg), "background uploaded");
    pl_dispatch_reset_frame(env->dp);
    struct fp_subs_result res = { 0 };
    bool drew = fp_subs_draw(s, target, 2.0, &res);
    tu_check(drew && res.drawn, "fp_subs_draw drew %d libass bitmaps", res.n_images);
    uint8_t *got = tu_read_u8(env, target, W, H);
    if (got) {
        long under = 0, same = 0, d1 = 0, d2 = 0, outside_changed = 0, single_unamb = 0, single_unamb_bad = 0;
        long multi = 0, multi_bad = 0;
        int maxd = 0;
        for (long i = 0; i < (long) W * H; i++) {
            if (!layers[i]) {
                for (int k = 0; k < 3; k++)
                    outside_changed += got[4 * i + k] != bg[4 * i + k];
                continue;
            }
            under++;
            for (int k = 0; k < 3; k++) {
                int d = abs((int) got[4 * i + k] - (int) want[4 * i + k]);
                maxd = d > maxd ? d : maxd;
                same += d == 0;
                d1 += d == 1;
                d2 += d >= 2;
                if (layers[i] == 1 && !ambig[i]) {
                    single_unamb++;
                    single_unamb_bad += d != 0;
                    if (d != 0 && single_unamb_bad <= 4)
                        printf("    mismatch at (%ld,%ld) ch %d: background %d, GPU %d, reference %d\n", i % W, i / W, k,
                               bg[4 * i + k], got[4 * i + k], want[4 * i + k]);
                } else if (layers[i] > 1) {
                    multi++;
                    multi_bad += d > layers[i];
                }
            }
        }
        printf("  pixels under a bitmap: %ld; of their %ld values, %ld equal the float blend, %ld differ by 1, %ld by 2 or more\n",
               under, under * 3, same, d1, d2);
        tu_check(outside_changed == 0, "no pixel outside the bitmaps changed (%ld changed)", outside_changed);
        tu_check(single_unamb_bad * 10000 <= single_unamb,
                 "where one bitmap covers a pixel and the rounding is not borderline (%ld values) the result is bit-exact but for %ld values (%.4f %%, the GPU's blend arithmetic), each one code off",
                 single_unamb, single_unamb_bad, 100.0 * (double) single_unamb_bad / (double) (single_unamb ? single_unamb : 1));
        tu_check(multi_bad == 0 && maxd <= 3,
                 "elsewhere the GPU rounds each layer itself: at most one code per layer off (%ld values under 2 or 3 layers, max difference %d)",
                 multi, maxd);

        // the solid red rectangle: find libass's own bitmap for it (colour 0xFF000000) and compare against its coverage
        const ASS_Image *rect = NULL;
        for (const ASS_Image *i = img; i; i = i->next)
            if ((i->color & 0xFFFFFF00u) == 0xFF000000u)
                rect = i;
        long wrong = 0, red_n = 0, binary_bad = 0;
        if (rect) {
            for (int y = 0; y < rect->h; y++)
                for (int x = 0; x < rect->w; x++) {
                    uint8_t cov = rect->bitmap[(size_t) y * rect->stride + x];
                    binary_bad += cov != 0 && cov != 255;
                    const uint8_t *p = got + 4 * ((size_t) (rect->dst_y + y) * W + rect->dst_x + x);
                    const uint8_t *b = bg + 4 * ((size_t) (rect->dst_y + y) * W + rect->dst_x + x);
                    if (cov == 255) {
                        red_n++;
                        wrong += !(p[0] == 255 && p[1] == 0 && p[2] == 0);
                    } else if (cov == 0) {
                        wrong += memcmp(p, b, 3) != 0;
                    }
                }
        }
        printf("  libass's rectangle bitmap is %dx%d with %ld pixels of fully covered red and %ld of partial coverage (its own antialiasing)\n",
               rect ? rect->w : 0, rect ? rect->h : 0, red_n, binary_bad);
        tu_check(rect && wrong == 0,
                 "where libass's rectangle has coverage 255 the target is exactly 255,0,0 (%ld pixels) and where it has 0 the target is untouched (%ld wrong)",
                 red_n, wrong);
        char path[512];
        snprintf(path, sizeof(path), "%s/subs-exact.png", t->out);
        tu_png_write_rgba8(path, got, W, H);
        free(got);
    }
    ref_done(&ref);
    fp_subs_destroy(&s);
    free(script);
    free(bg);
    free(want);
    free(layers);
    free(ambig);
    pl_tex_destroy(env->gpu, &target);
}

// ---- S2: lines wider than the monitor ---------------------------------------------------------------------------------

// Counts the pixels (inside or outside the rectangle) where two RGBA images differ.
static long count_changed(const uint8_t *a, const uint8_t *b, int w, int h, int x0, int y0, int x1, int y1, bool inside)
{
    long n = 0;
    for (int y = 0; y < h; y++)
        for (int x = 0; x < w; x++) {
            bool in = x >= x0 && x < x1 && y >= y0 && y < y1;
            if (in != inside)
                continue;
            n += memcmp(a + 4 * ((size_t) y * w + x), b + 4 * ((size_t) y * w + x), 3) != 0;
        }
    return n;
}

static const char *const s2_texts[] = {
    // 0: ordinary long dialogue with spaces: libass wraps it inside the frame
    "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,This is a long line of dialogue that has many words and would be far wider than one screen if it were kept on a single line, so the renderer has to wrap it.\n",
    // 1: one unbreakable word, wider than the monitor
    "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,Supercalifragilisticexpialidocious_Supercalifragilisticexpialidocious_Supercalifragilisticexpialidocious\n",
    // 2: no-wrap style, a long line with spaces
    "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,{\\q2}This line may not wrap, and it is far wider than the screen it is drawn on, by a lot of pixels.\n",
};
static const char *const s2_names[] = { "long line with spaces (wraps)", "one unbreakable word", "no-wrap line (\\q2)" };

static void s2_wide(struct tctx *t, const char *font, size_t font_size)
{
    printf("-- S2: lines wider than the monitor, and a box that is off-centre\n");
    const struct fp_env *env = &t->dev.env;
    const int W = 1280, H = 720;
    pl_tex target = tu_target8(env, W, H, false, "s2 target");
    uint8_t *bg = malloc((size_t) W * H * 4);
    fill_background(bg, W, H);

    for (int i = 0; i < 3; i++) {
        char *script = make_script(W, H, 40, 40, "0", s2_texts[i]);
        for (int mode = 0; mode < 2; mode++) {
            struct fp_subs *s = fp_subs_create(env, W, H);
            fp_subs_add_font(s, "dejavu", font, font_size);
            fp_subs_load_ass(s, script, strlen(script));
            fp_subs_set_overflow(s, mode ? FP_SUBS_REFUSE : FP_SUBS_CLIP);
            upload8(env, target, bg);
            pl_dispatch_reset_frame(env->dp);
            struct fp_subs_result r = { 0 };
            bool ok = fp_subs_draw(s, target, 2.0, &r);
            uint8_t *got = tu_read_u8(env, target, W, H);
            long changed = got ? count_changed(got, bg, W, H, 0, 0, W, H, true) : -1;
            printf("  %-30s %-6s: %d bitmaps, x %d..%d of %d, edge reached left %d right %d, %ld pixels changed\n",
                   s2_names[i], mode ? "refuse" : "clip", r.n_images, r.bbox[0], r.bbox[2], W, r.edge_hit[0], r.edge_hit[2], changed);
            if (i == 0) {
                tu_check(ok && !r.overflow && r.drawn, "%s: libass wraps it, it fits (%d bitmaps), nothing is refused or clipped", s2_names[i], r.n_images);
            } else if (mode == 0) {
                tu_check(ok && r.overflow && r.drawn && changed > 0 && r.bbox[0] <= 0 && r.bbox[2] >= W,
                         "%s, clip: overflow reported (the line reaches both edges), the part that fits is drawn", s2_names[i]);
            } else {
                tu_check(ok && r.overflow && !r.drawn && changed == 0, "%s, refuse: overflow reported, nothing drawn", s2_names[i]);
            }
            if (i == 1 && mode == 0 && got) {
                char path[512];
                snprintf(path, sizeof(path), "%s/subs-overflow-clip.png", t->out);
                tu_png_write_rgba8(path, got, W, H);
            }
            free(got);
            fp_subs_destroy(&s);
        }
        free(script);
    }

    // An off-centre box (x 700..1180, y 380..700): the line wraps inside it and nothing is drawn outside it, even for
    // the unbreakable word that is wider than the box.
    for (int i = 0; i < 2; i++) {
        char *script = make_script(W, H, 36, 30, "0", i ? s2_texts[1] : s2_texts[0]);
        struct fp_subs *s = fp_subs_create(env, W, H);
        fp_subs_add_font(s, "dejavu", font, font_size);
        fp_subs_load_ass(s, script, strlen(script));
        fp_subs_set_box(s, 700, 380, 1180, 700);
        upload8(env, target, bg);
        pl_dispatch_reset_frame(env->dp);
        struct fp_subs_result r = { 0 };
        bool ok = fp_subs_draw(s, target, 2.0, &r);
        uint8_t *got = tu_read_u8(env, target, W, H);
        long out = got ? count_changed(got, bg, W, H, 700, 380, 1180, 700, false) : -1;
        long in = got ? count_changed(got, bg, W, H, 700, 380, 1180, 700, true) : -1;
        tu_check(ok && out == 0 && in > 0 && (i == 0 ? !r.overflow : r.overflow),
                 "box 700..1180 x 380..700, %s: %ld pixels changed inside, %ld outside; overflow %s",
                 i ? "unbreakable word" : "long line", in, out, r.overflow ? "reported" : "none");
        if (i == 0 && got) {
            char path[512];
            snprintf(path, sizeof(path), "%s/subs-box.png", t->out);
            tu_png_write_rgba8(path, got, W, H);
        }
        free(got);
        fp_subs_destroy(&s);
        free(script);
    }
    free(bg);
    pl_tex_destroy(env->gpu, &target);
}

// ---- S3: on and off at the exact frame --------------------------------------------------------------------------------

struct ev {
    int start_cs, end_cs; // ASS times in centiseconds
};

static void s3_timing(struct tctx *t, const char *font, size_t font_size)
{
    printf("-- S3: on and off at the exact frame\n");
    const struct fp_env *env = &t->dev.env;
    const int W = 640, H = 360;
    pl_tex target = tu_target8(env, W, H, false, "s3 target");
    uint8_t *bg = malloc((size_t) W * H * 4);
    fill_background(bg, W, H);
    static const struct ev evs[] = { { 100, 250 }, { 250, 400 }, { 407, 412 }, { 1000, 1004 }, { 1500, 1800 }, { 1999, 2001 } };
    char events[1024] = "";
    for (size_t i = 0; i < TU_ARRAY_SIZE(evs); i++) {
        char line[160];
        snprintf(line, sizeof(line), "Dialogue: 0,0:%02d:%02d.%02d,0:%02d:%02d.%02d,Default,,0,0,0,,Line %zu\n",
                 evs[i].start_cs / 6000, (evs[i].start_cs / 100) % 60, evs[i].start_cs % 100, evs[i].end_cs / 6000,
                 (evs[i].end_cs / 100) % 60, evs[i].end_cs % 100, i + 1);
        strcat(events, line);
    }
    char *script = make_script(W, H, 28, 20, "0", events);

    static const struct { const char *name; long num, den; int frames; } clocks[] = {
        { "23.976 fps", 1001, 24000, 24 * 24 }, { "25 fps", 1, 25, 25 * 22 }, { "60 fps", 1, 60, 60 * 22 },
    };
    for (size_t c = 0; c < TU_ARRAY_SIZE(clocks); c++) {
        struct fp_subs *s = fp_subs_create(env, W, H);
        fp_subs_add_font(s, "dejavu", font, font_size);
        fp_subs_load_ass(s, script, strlen(script));
        int wrong = 0, shown = 0, changed_flag_bad = 0;
        bool prev_on = false;
        int first[TU_ARRAY_SIZE(evs)], last[TU_ARRAY_SIZE(evs)];
        for (size_t i = 0; i < TU_ARRAY_SIZE(evs); i++)
            first[i] = last[i] = -1;
        for (int k = 0; k < clocks[c].frames; k++) {
            // exact: the frame's time in whole milliseconds by integer arithmetic (floor(k * num * 1000 / den))
            long long ms = (long long) k * clocks[c].num * 1000LL / clocks[c].den;
            int expect_ev = -1;
            for (size_t i = 0; i < TU_ARRAY_SIZE(evs); i++)
                if (ms >= evs[i].start_cs * 10LL && ms < evs[i].end_cs * 10LL)
                    expect_ev = (int) i;
            double pts = (double) k * (double) clocks[c].num / (double) clocks[c].den;
            struct fp_subs_result r = { 0 };
            upload8(env, target, bg);
            pl_dispatch_reset_frame(env->dp);
            fp_subs_draw(s, target, pts, &r);
            bool on = r.n_images > 0;
            bool want_on = expect_ev >= 0;
            wrong += on != want_on;
            shown += on;
            if (want_on) {
                if (first[expect_ev] < 0)
                    first[expect_ev] = k;
                last[expect_ev] = k;
            }
            if (on != prev_on && !r.changed)
                changed_flag_bad++;
            prev_on = on;
        }
        printf("  %s: %d frames drawn, %d on/off differences from exact integer arithmetic; first/last frame per line:", clocks[c].name, shown, wrong);
        for (size_t i = 0; i < TU_ARRAY_SIZE(evs); i++)
            printf(" [%d..%d]", first[i], last[i]);
        printf("\n");
        tu_check(wrong == 0, "%s: the subtitle is on for exactly the frames whose time is inside the event (%d wrong)", clocks[c].name, wrong);
        tu_check(changed_flag_bad == 0, "%s: libass reports a change at every on/off edge", clocks[c].name);
        fp_subs_destroy(&s);
    }
    free(script);
    free(bg);
    pl_tex_destroy(env->gpu, &target);
}

// ---- entry --------------------------------------------------------------------------------------------------------

int test_subs(struct tctx *t)
{
    printf("== subtitles (pass 4)\n");
    size_t font_size = 0;
    char *font = t->font_path[0] ? read_file(t->font_path, &font_size) : NULL;
    if (!font) {
        fprintf(stderr, "finish-test: cannot read the font '%s' (use --font FILE with a TrueType font whose family is \"" FAMILY "\")\n", t->font_path);
        tu_check(false, "font file readable");
        return 1;
    }
    s1_exact(t, font, font_size);
    s2_wide(t, font, font_size);
    s3_timing(t, font, font_size);
    free(font);
    return 0;
}
