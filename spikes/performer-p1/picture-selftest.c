// picture-selftest: unit checks for the picture path's CPU side: the room file, the pixel -> surface -> source map (fold
// and curve), the camera-slide positions per row, the rolling-blend weights, the surround's look and time rule and the
// colour block. No GPU, no window; the room file is the harness's Setup A default, read from disk.
//
// Compile: pwsh -File compile.ps1 -Program picture-test   (bin/picture-selftest.exe). Run it from spikes/performer-p1.
// Exit code 0 = every check passed.
#define _CRT_SECURE_NO_WARNINGS
#include "src/perf1-cue-sheet.h"
#include "src/perf1-finish-passes.h"
#include "src/perf1-picture-look.h"
#include "src/perf1-picture-map.h"
#include "src/perf1-picture-plan.h"
#include "src/perf1-room.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

static int g_checks, g_failed;

#define CHECK(cond, ...)                                                                     \
    do {                                                                                     \
        g_checks++;                                                                          \
        if (!(cond)) {                                                                       \
            g_failed++;                                                                      \
            printf("FAIL %s:%d: %s\n  ", __FILE__, __LINE__, #cond);                         \
            printf(__VA_ARGS__);                                                             \
            printf("\n");                                                                    \
        }                                                                                    \
    } while (0)

#define CHECK_NEAR(value, expected, tol) \
    CHECK(fabs((value) - (expected)) <= (tol), "%s = %.12g, expected %.12g (+-%g)", #value, (double) (value), (double) (expected), (double) (tol))

#define ROOM_FILE "../picture-path/room/setup-a-default.json"

static char *read_file(const char *path, size_t *len)
{
    FILE *f = fopen(path, "rb");
    if (!f)
        return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    char *buf = malloc((size_t) n + 1);
    *len = fread(buf, 1, (size_t) n, f);
    buf[*len] = 0;
    fclose(f);
    return buf;
}

// text with the first `from` replaced by `to` (a new allocation), NULL if `from` is not there
static char *replace(const char *text, const char *from, const char *to)
{
    const char *at = strstr(text, from);
    if (!at)
        return NULL;
    char *out = malloc(strlen(text) + strlen(to) + 1);
    size_t head = (size_t) (at - text);
    memcpy(out, text, head);
    strcpy(out + head, to);
    strcpy(out + head + strlen(to), at + strlen(from));
    return out;
}

// ---- the room ------------------------------------------------------------------------------------------------

static void expect_room_error(const char *text, const char *from, const char *to, const char *needle)
{
    char *bad = replace(text, from, to), err[300] = "";
    struct room r;
    CHECK(bad != NULL, "test setup: '%s' is in the room file", from);
    if (!bad)
        return;
    CHECK(!room_parse(&r, bad, strlen(bad), "test", err, sizeof(err)), "must reject '%s' -> '%s'", from, to);
    CHECK(strstr(err, needle) != NULL, "message should mention \"%s\": %s", needle, err);
    free(bad);
}

static void test_room(const char *text, size_t len)
{
    struct room r;
    char err[300] = "";
    CHECK(room_parse(&r, text, len, "setup-a", err, sizeof(err)), "Setup A default room: %s", err);
    CHECK(r.nmon == 2 && !strcmp(r.mon[0].id, "laptop") && r.mon[1].w == 1920 && r.mon[1].h == 1080, "two monitors");
    CHECK_NEAR(r.seat_distance, 700.0, 0);
    CHECK_NEAR(r.mon[1].left_mm, 344.0 + 20.0, 1e-12);                  // gaps are physical gaps on the fold
    CHECK_NEAR(r.u_axis, 354.0, 1e-12);                                 // the gaze axis crosses the middle of the gap
    CHECK(!r.curve && fabs(r.curvature - 1.0 / 700.0) < 1e-15, "fold by default, curvature defaults to the seat distance");
    CHECK_NEAR(room_top_mm(&r, 0, 0), 0.0, 1e-12);
    CHECK_NEAR(room_top_mm(&r, 1, 0), 0.0, 1e-12);                      // both panels' tops are at 107.5 mm above eye level
    CHECK_NEAR(room_eye_v(&r, 0), 107.5, 1e-12);
    CHECK(r.mon[0].color.acm && r.mon[0].color.gain[1] == 1.0 && r.mon[0].color.lift[2] == 0.0 && !r.mon[0].color.has_primaries,
          "no colour block: neutral, Windows owns the gamut");

    expect_room_error(text, "panorama.room/1", "panorama.room/2", "schema");
    expect_room_error(text, "\"gap_mm\": 20.0", "\"gap_mm\": -1", "gap_mm");
    expect_room_error(text, "\"distance_mm\": 700.0", "\"distance_mm\": 0", "distance_mm");
    expect_room_error(text, "\"gaze_yaw_deg\": 0", "\"gaze_yaw_deg\": 5", "gaze");
    expect_room_error(text, "\"id\": \"external\"", "\"idx\": \"external\"", "id");
    expect_room_error(text, "\"seams\": [", "\"seamz\": [", "seams");

    // a colour block
    char *col = replace(text, "\"id\": \"external\",",
                        "\"id\": \"external\", \"color\": { \"acm\": false, \"gain\": 0.9, \"white\": [1, 0.98, 0.95], \"black_lift\": [0.001, 0, 0],"
                        " \"primaries\": { \"r\": [0.68, 0.32], \"g\": [0.265, 0.69], \"b\": [0.15, 0.06], \"w\": [0.3127, 0.329] } },");
    CHECK(col != NULL, "test setup: the external's id is found");
    if (col) {
        CHECK(room_parse(&r, col, strlen(col), "col", err, sizeof(err)), "colour block: %s", err);
        const struct room_color *c = &r.mon[1].color;
        CHECK(!c->acm && c->has_primaries && c->gain[0] == 0.9 && c->white[2] == 0.95 && c->lift[0] == 0.001, "colour block read");
        CHECK(r.mon[0].color.acm, "the other monitor stays neutral");
        // the colour the passes get: ACM off with primaries = the player maps the gamut (not the identity), tweaks multiply
        struct fp_color_gpu g;
        pic_color_make(c, 8, &g);
        CHECK_NEAR(g.gain[2], 0.9 * 0.95, 1e-6);
        CHECK(fabsf(g.m[0] - 1.0f) > 0.01f, "player-owned gamut: the BT.709 -> panel matrix is not the identity (%g)", g.m[0]);
        struct fp_color_gpu n;
        pic_color_make(&r.mon[0].color, 8, &n);
        CHECK(n.m[0] == 1.0f && n.m[4] == 1.0f && n.m[8] == 1.0f && n.m[1] == 0.0f && n.gain[0] == 1.0f && n.out_bits == 8,
              "ACM on, neutral block: identity, plain sRGB");
        free(col);
    }
    expect_room_error(text, "\"id\": \"external\",", "\"id\": \"external\", \"color\": { \"gain\": -2 },", "color");
    expect_room_error(text, "\"id\": \"external\",", "\"id\": \"external\", \"color\": { \"acm\": 1 },", "acm");
}

// ---- the map -------------------------------------------------------------------------------------------------

static void test_map(const char *text, size_t len)
{
    struct room r;
    char err[300];
    room_parse(&r, text, len, "setup-a", err, sizeof(err));
    struct room_geom fold[2], flat[2];
    for (int k = 0; k < 2; k++) {
        room_geom_fold(&fold[k], r.mon[k].w, r.mon[k].h, r.mon[k].w_mm, r.mon[k].h_mm, r.mon[k].left_mm, room_top_mm(&r, k, 0));
        r.curvature = 0.0; // a flat plane at the seat distance: the same surface as the coplanar fold
        room_geom_curve(&flat[k], &r, k, r.mon[k].w, r.mon[k].h, 0);
    }
    double u, v;
    room_map_uv(&fold[1], 0.0, 0.0, &u, &v);
    CHECK_NEAR(u, 364.0, 1e-12);
    CHECK_NEAR(v, 0.0, 1e-12);
    room_map_uv(&fold[1], 1920.0, 1080.0, &u, &v);
    CHECK_NEAR(u, 364.0 + 480.0, 1e-12);
    CHECK_NEAR(v, 270.0, 1e-12);

    // coplanar panels: the ray from the eye to the plane lands where the panel is, so curve (curvature 0) = fold
    double worst = 0.0;
    for (int k = 0; k < 2; k++)
        for (double fx = 0.0; fx <= 1.0; fx += 0.25)
            for (double fy = 0.0; fy <= 1.0; fy += 0.25) {
                double u1, v1, u2, v2;
                room_map_uv(&fold[k], fx * r.mon[k].w, fy * r.mon[k].h, &u1, &v1);
                room_map_uv(&flat[k], fx * r.mon[k].w, fy * r.mon[k].h, &u2, &v2);
                worst = fmax(worst, fmax(fabs(u1 - u2), fabs(v1 - v2)));
            }
    CHECK(worst < 1e-9, "flat curve equals the coplanar fold: worst difference %.3g mm", worst);

    // a curved surface: arc length. The point straight ahead (eye height) is at the axis; a point at 30 degrees to the
    // right on a cylinder of radius 700 centred on the eye is 700 * pi / 6 along the arc.
    struct room_geom cyl = flat[1];
    cyl.curvature = 1.0 / 700.0;
    cyl.centre = (struct room_vec3) { 700.0 * tan(M_PI / 6.0), 0.0, 700.0 };
    cyl.ex = (struct room_vec3) { 1, 0, 0 };
    cyl.ey = (struct room_vec3) { 0, 1, 0 };
    cyl.v_eye = 0.0;
    room_map_uv(&cyl, 0.5 * cyl.px_w, 0.5 * cyl.px_h, &u, &v);
    CHECK_NEAR(u, cyl.u_axis + 700.0 * M_PI / 6.0, 1e-6);
    CHECK_NEAR(v, 0.0, 1e-9);

    // the picture: the whole 1920x1080 source over the monitors' shared height (215 mm), centred on the gap
    struct pic_view view = { .crop = { 0, 0, 1920, 1080 }, .rect = { 354.0 - 0.5 * 215.0 * 16 / 9, 0, 354.0 + 0.5 * 215.0 * 16 / 9, 215 } };
    double sx, sy;
    pic_map_pixel(&fold[0], &view, NULL, 0.0, 0.0, &sx, &sy);
    CHECK_NEAR(sx, (0.0 - view.rect[0]) / (view.rect[2] - view.rect[0]) * 1920.0, 1e-9);
    CHECK_NEAR(sy, 0.0, 1e-9);
    double j[4];
    pic_jacobian(&fold[0], &view, NULL, 1280.0, 800.0, j);
    CHECK_NEAR(j[0], 344.0 / 2560.0 * 1920.0 / (215.0 * 16 / 9), 1e-9);   // source pixels per laptop pixel, across
    CHECK_NEAR(j[3], 215.0 / 1600.0 * 1080.0 / 215.0, 1e-9);              // and down
    CHECK(fabs(j[1]) < 1e-12 && fabs(j[2]) < 1e-12, "no shear");
    CHECK(pic_monitor_upscales(&fold[0], &view), "1080 source rows over the laptop's 1600: magnified 1.48 times, sigmoid domain");
    CHECK(!pic_monitor_upscales(&fold[1], &view), "on the external 1080 source rows land on 860 pixel rows: shrunk, linear light");
    // a small crop magnified on the laptop
    struct pic_view small = { .crop = { 800, 400, 1120, 580 }, .rect = { 0, 0, 344, 215 } };
    CHECK(pic_monitor_upscales(&fold[0], &small), "320x180 source pixels over the laptop panel: magnified");
    // exactly one source pixel per monitor pixel is a plain copy, not a magnification
    struct pic_view one = { .crop = { 0, 0, 1920, 1080 }, .rect = { 364, 0, 364 + 480, 270 } };
    CHECK(!pic_monitor_upscales(&fold[1], &one), "1:1 is not an upscale");

    // the slide displaces the picture by (du0 + ddu * row, ...): a picture 10 mm to the right shows source 10 mm to the left
    struct pic_slide slide = { .du0 = 10.0, .ddu = 0.002 };
    double sx0, sy0, sx1, sy1;
    pic_map_pixel(&fold[0], &view, NULL, 100.0, 400.0, &sx0, &sy0);
    pic_map_pixel(&fold[0], &view, &slide, 100.0, 400.0, &sx1, &sy1);
    double per_mm = 1920.0 / (215.0 * 16 / 9);
    CHECK_NEAR(sx0 - sx1, (10.0 + 0.002 * 400.0) * per_mm, 1e-9);
    CHECK_NEAR(sy0, sy1, 1e-12);
    // ... and the Jacobian sees the row gradient as a shear
    pic_jacobian(&fold[0], &view, &slide, 1280.0, 800.0, j);
    CHECK_NEAR(j[1], -0.002 * per_mm, 1e-9);
}

// ---- row times: the slide and the blend -------------------------------------------------------------------------

static void test_slide(void)
{
    struct cue_slide_pt pts[3] = { { 1.0, 0.0, 0.0 }, { 2.0, 30.0, -6.0 }, { 4.0, 90.0, -6.0 } };
    struct cue_shot shot = { .slide_n = 3, .slide = pts, .subtitle_monitor = -1 };
    // film time = 1.0 + (t - 100) * 24 / 96: a 96 Hz master, one refresh = 1/96 s of host time = 1/96 * (step / period) of film
    struct pic_clock clk = { .l0 = 100.0, .master_period = 1.0 / 96.0, .origin = 1.0, .step_s = 1.0 / 96.0, .shift = 0 };
    CHECK_NEAR(pic_film_time(&clk, 100.0), 1.0, 1e-12);
    CHECK_NEAR(pic_film_time(&clk, 100.5), 1.5, 1e-9);
    clk.shift = 1;
    CHECK_NEAR(pic_film_time(&clk, 100.0), 1.0 - 1.0 / 96.0, 1e-12);
    clk.shift = 0;

    // a laptop refresh whose top row lights at film time 1.5 (host 100.5); 240 Hz, 1600 rows, scan 3.7 ms
    const double period = 1.0 / 240.0, scan = 0.0037;
    struct pic_slide s;
    CHECK(pic_row_slide(&shot, &clk, 100.5, scan, period, 1600, &s), "the shot has a slide");
    // rows are drawn for the middle of their light: top row at 1.5 + period / 2, bottom at + scan
    double t_top = 1.5 + 0.5 * period, t_bot = 1.5 + scan + 0.5 * period;
    CHECK_NEAR(s.du0, 30.0 * (t_top - 1.0), 1e-9);
    CHECK_NEAR(s.dv0, -6.0 * (t_top - 1.0), 1e-9);
    CHECK_NEAR(s.du0 + s.ddu * 1600.0, 30.0 * (t_bot - 1.0), 1e-9);
    // the slide speed in mm/s is the path's: 30 mm per second, so over one scan the picture moves 30 * scan / 1.0
    CHECK_NEAR(s.ddu * 1600.0, 30.0 * scan, 1e-9);
    // before the first point and after the last the picture waits
    CHECK(pic_row_slide(&shot, &clk, 99.0, scan, period, 1600, &s) && s.du0 == 0.0 && s.ddu == 0.0, "held before the path");
    CHECK(pic_row_slide(&shot, &clk, 110.0, scan, period, 1600, &s) && s.du0 == 90.0 && s.dv0 == -6.0 && s.ddu == 0.0, "held after the path");
    struct cue_shot plain = { .subtitle_monitor = -1 };
    CHECK(!pic_row_slide(&plain, &clk, 100.5, scan, period, 1600, &s) && s.du0 == 0.0, "no slide, no displacement");
}

// For a refresh sequence of a follower and a master switch at e(y), the light-weighted moment the new frame takes over in
// one row is e whatever the phase: the area under the old frame's share over the refreshes equals e - t_start.
static void test_blend_weights(void)
{
    unsigned seed = 12345;
    double worst = 0.0;
    for (int trial = 0; trial < 2000; trial++) {
        seed = seed * 1664525u + 1013904223u;
        double period = (0.0041 + 0.0001 * (seed >> 24) / 255.0), scan = period * 0.9, a0 = 0.001 * (double) (seed >> 8 & 1023) / 1024.0;
        int rows = 1600;
        double e0 = 0.02 + 0.004 * (double) (seed >> 5 & 1023) / 1024.0, e1 = e0 + 0.0097 * (1.0 + 0.1 * ((int) (seed >> 3 & 7) - 4) / 4.0);
        double wa, wb;
        // refresh n has its top row at a0 + n * period; the switch is in the refreshes around e
        double row = (double) (seed >> 12 & 1023) / 1024.0 * rows;
        double e_row = e0 + (e1 - e0) * row / rows;
        double sum_old = 0.0; // seconds of old-frame light accumulated from the start of the first refresh below
        int first = (int) floor((e_row - 3 * period - a0) / period), last = first + 8;
        for (int n = first; n <= last; n++) {
            pic_blend_line(a0 + n * period, scan, period, rows, e0, e1, &wa, &wb);
            double w = fmin(1.0, fmax(0.0, wa + wb * row));
            sum_old += (1.0 - w) * period;
        }
        // row y of refresh n starts at a_n(y) = a0 + n * period + y / rows * scan
        double t_start = a0 + first * period + (row / rows) * scan;
        double switch_moment = t_start + sum_old;
        worst = fmax(worst, fabs(switch_moment - e_row));
    }
    CHECK(worst < 1e-12, "light-weighted switching moment equals the master's row moment: worst error %.3g s", worst);
}

static void test_blend_choice(void)
{
    // the refresh is entirely after the master's switch to c (weights >= 1 everywhere): no blend with c-1; the next switch is
    // far away (weights <= 0): no blend with c+1
    struct pic_blend b = pic_blend_choose(1600, true, 1.8, 0.0, true, -9.0, 0.0);
    CHECK(b.partner == 0, "settled refresh: no blend");
    // the switch to c straddles the refresh: rows near the top 40 % new, the bottom 70 %
    b = pic_blend_choose(1600, true, 0.4, 0.3 / 1600.0, true, -9.0, 0.0);
    CHECK(b.partner == -1 && fabs(b.wa - 0.4) < 1e-12, "straddling the switch into c: blend c-1 into c");
    // the switch to c+1 begins inside this refresh
    b = pic_blend_choose(1600, true, 3.0, 0.0, true, -0.2, 0.6 / 1600.0);
    CHECK(b.partner == +1 && fabs(b.wb - 0.6 / 1600.0) < 1e-15, "the switch to c+1 begins in this refresh");
    // frame 0 has no predecessor
    b = pic_blend_choose(1600, false, 0.0, 0.0, true, -0.2, 0.6 / 1600.0);
    CHECK(b.partner == +1, "no previous frame: only the next can mix in");
    b = pic_blend_choose(1600, false, 0.0, 0.0, false, 0.0, 0.0);
    CHECK(b.partner == 0, "no neighbours known: no blend");
    // a refresh lit wholly before the master's switch (weights <= 0): it shows c-1 in full, which is a blend with weight 0
    b = pic_blend_choose(1600, true, -0.5, 0.0, true, -9.0, 0.0);
    CHECK(b.partner == -1 && b.wa == -0.5 && b.wb == 0.0, "wholly before the switch: old frame in full");
}

// ---- the surround and the look ------------------------------------------------------------------------------------

static void test_look(void)
{
    struct pic_look l;
    pic_look_default(&l, true);
    CHECK(l.look.mode == FP_SURROUND_GLOW && l.look.premultiplied_alpha && l.time.cut_rule == FP_CUT_SNAP, "defaults");
    pic_look_default(&l, false);
    CHECK(l.look.mode == FP_SURROUND_BLACK, "black default");

    const char *good = "{\"mode\":\"glow\",\"gain\":0.5,\"tau_s\":2,\"cut\":\"hold\",\"hold_s\":0.25}";
    char err[200] = "";
    struct json_value *j = json_parse(good, strlen(good), err, sizeof(err));
    CHECK(j && pic_look_from_cue(&l, j, err, sizeof(err)), "good surround object: %s", err);
    CHECK(l.look.mode == FP_SURROUND_GLOW && l.look.gain == 0.5f && l.time.tau_steady_s == 2.0f && l.time.cut_rule == FP_CUT_HOLD &&
              l.time.hold_s == 0.25f && l.look.falloff_px == 250.0f,
          "values overlaid, the rest kept");
    json_free(j);
    const char *bad[] = { "{\"mode\":\"red\"}", "{\"gain\":-1}", "{\"cut\":\"jump\"}", "[1]", "{\"tau_s\":\"slow\"}" };
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        j = json_parse(bad[i], strlen(bad[i]), err, sizeof(err));
        err[0] = 0;
        CHECK(j && !pic_look_from_cue(&l, j, err, sizeof(err)) && err[0], "must reject %s", bad[i]);
        json_free(j);
    }

    // the temporal rule the surround follows (ported unchanged): snap jumps at a cut, hold freezes, a pause (dt 0) stands still
    struct fp_surround_clock c;
    struct fp_surround_time t = { .tau_steady_s = 0.5f, .cut_rule = FP_CUT_SNAP };
    fp_surround_clock_reset(&c);
    struct fp_surround_frame f = { .dt_s = 0.0 };
    struct fp_surround_step s = fp_surround_clock_step(&c, &t, &f);
    CHECK(s.alpha == 1.0f, "the first refresh snaps to the picture");
    f.dt_s = 1.0 / 24.0;
    s = fp_surround_clock_step(&c, &t, &f);
    CHECK_NEAR(s.alpha, 1.0 - exp(-(1.0 / 24.0) / 0.5), 1e-6);
    f.cut = true;
    s = fp_surround_clock_step(&c, &t, &f);
    CHECK(s.alpha == 1.0f, "snap at a cut");
    t.cut_rule = FP_CUT_HOLD;
    t.hold_s = 0.3f;
    s = fp_surround_clock_step(&c, &t, &f);
    CHECK(s.alpha == 0.0f, "hold: frozen on the cut's refresh");
    f.cut = false;
    f.dt_s = 0.0;
    s = fp_surround_clock_step(&c, &t, &f);
    CHECK(s.alpha == 0.0f, "no film time passed: nothing moves");
}

int main(void)
{
    size_t len = 0;
    char *text = read_file(ROOM_FILE, &len);
    if (!text) {
        printf("picture-selftest: cannot read %s (run it from spikes/performer-p1)\n", ROOM_FILE);
        return 2;
    }
    test_room(text, len);
    test_map(text, len);
    test_slide();
    test_blend_weights();
    test_blend_choice();
    test_look();
    free(text);
    printf("picture-selftest: %d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
