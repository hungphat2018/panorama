// cue-selftest: unit checks for the JSON parser, the cue sheet loader and store, the per-shot latch and the picture
// layout. No GPU, no window, no files (the log form is written to a temporary file the C runtime removes).
//
// Compile (compile.ps1 -Target selftest does this and runs it):
//   gcc -std=gnu11 -Wall -Wextra -o bin/cue-selftest.exe cue-selftest.c src/perf1-json.c src/perf1-cue-sheet.c
//       src/perf1-shot-latch.c src/perf1-layout.c -lm
// Exit code 0 = every check passed.
#define _CRT_SECURE_NO_WARNINGS
#include "src/perf1-cue-sheet.h"
#include "src/perf1-json.h"
#include "src/perf1-layout.h"
#include "src/perf1-shot-latch.h"

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
    CHECK(fabs((value) - (expected)) <= (tol), "%s = %.9g, expected %.9g (+-%g)", #value, (double) (value), (double) (expected), (double) (tol))

// ---- JSON ----------------------------------------------------------------------------------------------------

static struct json_value *parse(const char *s, char *err, size_t n)
{
    return json_parse(s, strlen(s), err, n);
}

static void test_json(void)
{
    char err[200];
    struct json_value *v = parse("{\"a\": [1, 2.5, -3e2, true, false, null], \"s\": \"x\\u00e9\\ud83d\\ude00\\n\\\"\", \"o\": {}}", err, sizeof(err));
    CHECK(v != NULL, "valid document: %s", err);
    if (v) {
        const struct json_value *a = json_member(v, "a");
        CHECK(a && a->type == JSON_ARRAY && a->n == 6, "array of six");
        if (a && a->n == 6) {
            CHECK_NEAR(a->items[1].num, 2.5, 0);
            CHECK_NEAR(a->items[2].num, -300.0, 0);
            CHECK(a->items[3].type == JSON_BOOL && a->items[3].num == 1, "true");
            CHECK(a->items[5].type == JSON_NULL, "null");
        }
        const struct json_value *s = json_member(v, "s");
        CHECK(s && s->type == JSON_STRING && !strcmp(s->str, "x\xC3\xA9\xF0\x9F\x98\x80\n\""), "string with escapes and a surrogate pair");
        CHECK(json_member(v, "o") && json_member(v, "o")->type == JSON_OBJECT && json_member(v, "o")->n == 0, "empty object");
        CHECK(json_member(v, "missing") == NULL, "missing member");
        struct json_value *c = json_clone(v);
        CHECK(c && c->n == v->n && !strcmp(json_member(c, "s")->str, s->str) && json_member(c, "a")->n == 6, "deep copy");
        json_free(c);
        json_free(v);
    }
    CHECK(parse("\xEF\xBB\xBF[1]", err, sizeof(err)) != NULL, "byte order mark accepted");

    static const char *const bad[] = {
        "[1,]", "{\"a\":1,}", "[01]", "{\"a\":1,\"a\":2}", "\"abc", "\"a\tb\"", "\"\\ud800\"", "[1] x", "NaN", "nul", "[1e999]",
        "{\"a\" 1}", "[", "{", "", "  ", "\"\\u0000\"", "-", "1.", ".5", "[1 2]", "{1:2}", "'a'", "/* c */ 1",
    };
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        struct json_value *r = parse(bad[i], err, sizeof(err));
        CHECK(r == NULL && err[0], "must reject: %s", bad[i]);
        json_free(r);
    }
    char deep[200];
    memset(deep, '[', 70);
    memset(deep + 70, ']', 70);
    deep[140] = '\0';
    struct json_value *r = parse(deep, err, sizeof(err));
    CHECK(r == NULL, "nesting of 70 is refused");
    json_free(r);
    r = parse("{\n  \"a\": ,\n}", err, sizeof(err));
    CHECK(r == NULL && strstr(err, "line 2, column"), "error names the line: %s", err);
    json_free(r);
}

// ---- cue sheets ------------------------------------------------------------------------------------------------

static const char *SHEET_OK =
    "{\"schema\":\"panorama.cues/1\",\"version\":3,\"file_hash\":\"sha256:ab\",\"room_signature\":\"r1\","
    "\"preferences_hash\":\"p1\",\"extra\":1,\"shots\":["
    "{\"start\":0,\"crop\":[0,140,1920,940],\"placement\":{\"center_mm\":[354,107.5],\"height_mm\":215},"
    "\"subtitle_monitor\":1,\"surround\":{\"style\":\"glow\"},\"time\":\"as_filmed\"},"
    "{\"start\":10.5},"
    "{\"start\":20.25,\"crop\":[0.5,0,1920,1080]}]}";

static struct cue_sheet *load(const char *text, double w, double h, char *err, size_t n)
{
    return cue_sheet_parse(text, strlen(text), w, h, "test", err, n);
}

static void expect_rejected(const char *text, const char *needle)
{
    char err[300] = "";
    struct cue_sheet *s = load(text, 1920, 1080, err, sizeof(err));
    CHECK(s == NULL, "must reject (%s): %s", needle, text);
    if (s)
        cue_sheet_free(s);
    else
        CHECK(strstr(err, needle) != NULL, "message should mention \"%s\": %s", needle, err);
}

static void test_cue_sheet(void)
{
    char err[300];
    struct cue_sheet *s = load(SHEET_OK, 1920, 1080, err, sizeof(err));
    CHECK(s != NULL, "valid sheet: %s", err);
    if (s) {
        CHECK(s->version == 3 && s->n_shots == 3, "header and shot count");
        CHECK(s->file_hash && !strcmp(s->file_hash, "sha256:ab") && !strcmp(s->room_signature, "r1") &&
              !strcmp(s->preferences_hash, "p1"), "carried header fields");
        CHECK(s->ignored_keys == 1 && !strcmp(s->first_ignored_key, "extra"), "unknown key counted");
        CHECK(s->shots[0].has_crop && s->shots[0].crop[1] == 140 && s->shots[0].has_place && s->shots[0].height_mm == 215 &&
              s->shots[0].subtitle_monitor == 1 && s->shots[0].surround && s->shots[0].time_treatment, "shot 0 fields");
        CHECK(!s->shots[1].has_crop && !s->shots[1].has_place && s->shots[1].subtitle_monitor == -1, "shot 1 defaults");
        CHECK(s->shots[2].has_crop && s->shots[2].crop[0] == 0.5, "fractional crop accepted");

        // lookup: a shot starts at its start, the one before holds up to it
        CHECK(cue_sheet_find_shot(s, -1.0) == 0 && cue_sheet_find_shot(s, 0.0) == 0, "before and at the first start");
        CHECK(cue_sheet_find_shot(s, 10.4999999) == 0 && cue_sheet_find_shot(s, 10.5) == 1, "boundary at 10.5");
        CHECK(cue_sheet_find_shot(s, 20.25) == 2 && cue_sheet_find_shot(s, 1e9) == 2, "last shot");
        CHECK(cue_sheet_shot_end(s, 0) == 10.5 && cue_sheet_shot_end(s, 1) == 20.25 && isinf(cue_sheet_shot_end(s, 2)), "ends");

        // fingerprints, pinned to the values analyze-cues.py computes
        struct cue_shot plain = { .subtitle_monitor = -1 };
        CHECK(cue_shot_hash(&plain) == 1858958381u, "plain shot fingerprint %u", cue_shot_hash(&plain));
        CHECK(cue_shot_hash(&s->shots[0]) == 4221016235u, "shot 0 fingerprint %u", cue_shot_hash(&s->shots[0]));
        struct cue_shot half = { .has_crop = true, .crop = { 0.5, 0, 1920, 1080 }, .subtitle_monitor = -1 };
        CHECK(cue_shot_hash(&half) == 1664246429u, "fractional crop fingerprint %u", cue_shot_hash(&half));
        struct cue_shot zero_place = { .has_place = true, .center_u_mm = -0.0, .center_v_mm = 0.0, .height_mm = 215.0, .subtitle_monitor = -1 };
        CHECK(cue_shot_hash(&zero_place) == 3938428256u, "negative zero hashes like zero: %u", cue_shot_hash(&zero_place));
        CHECK(cue_shot_hash(&s->shots[1]) == cue_shot_hash(&plain), "shots with the same picture share a fingerprint");

        // the log form is JSON again and round-trips the numbers exactly
        FILE *f = tmpfile();
        if (f) {
            cue_sheet_write_json(f, s);
            long n = ftell(f);
            rewind(f);
            char *buf = calloc(1, (size_t) n + 1);
            size_t got = buf ? fread(buf, 1, (size_t) n, f) : 0;
            struct json_value *j = json_parse(buf, got, err, sizeof(err));
            CHECK(j != NULL, "log form parses: %s", err);
            if (j) {
                const struct json_value *shots = json_member(j, "shots");
                CHECK(shots && shots->n == 3 && json_member(&shots->items[0], "crop")->items[3].num == 940 &&
                      json_member(&shots->items[1], "crop")->type == JSON_NULL, "log form content");
                json_free(j);
            }
            free(buf);
            fclose(f);
        }
        cue_sheet_free(s);
    }

    expect_rejected("[1]", "top level");
    expect_rejected("{\"schema\":\"x\",\"version\":1,\"shots\":[{\"start\":0}]}", "schema");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":0,\"shots\":[{\"start\":0}]}", "version");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1.5,\"shots\":[{\"start\":0}]}", "version");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[]}", "shots");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1}", "shots");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0},{\"start\":5},{\"start\":4}]}", "shots[2].start");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0},{\"start\":0}]}", "shots[1].start");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0.01}]}", "first shot");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"crop\":[0,0,1921,1080]}]}", "outside");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"crop\":[-1,0,100,100]}]}", "outside");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"crop\":[10,0,10,100]}]}", "x1 > x0");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"crop\":[0,0,100]}]}", "crop");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"crop\":[0,0,\"a\",100]}]}", "crop[2]");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"placement\":{\"center_mm\":[1,2]}}]}", "height_mm");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"placement\":{\"center_mm\":[1,2],\"height_mm\":-5}}]}", "height_mm");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"placement\":{\"height_mm\":5}}]}", "center_mm");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"subtitle_monitor\":1.5}]}", "subtitle_monitor");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{}]}", "start");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[3]}", "object");
    expect_rejected("{not json", "JSON");

    // a crop exactly the size of the source is fine; the same crop is outside a smaller source
    const char *full = "{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":-0.02,\"crop\":[0,0,1920,1080]}]}";
    struct cue_sheet *a = load(full, 1920, 1080, err, sizeof(err));
    CHECK(a != NULL, "crop equal to the source: %s", err);
    cue_sheet_free(a);
    CHECK(load(full, 1280, 720, err, sizeof(err)) == NULL, "same crop in a 1280x720 source is rejected");

    // a missing file
    CHECK(cue_sheet_load_file("C:/no/such/cue-sheet.json", 1920, 1080, err, sizeof(err)) == NULL && strstr(err, "cannot open"), "missing file: %s", err);
}

// A shot's camera slide: parsing, validation, interpolation; old sheets without one stay valid and keep their fingerprints.
static void test_slide(void)
{
    char err[300];
    const char *text = "{\"schema\":\"panorama.cues/1\",\"version\":2,\"shots\":["
        "{\"start\":0,\"slide\":[[1,0,0],[2,30,-6],[4,90,-6]]},{\"start\":5}]}";
    struct cue_sheet *s = load(text, 1920, 1080, err, sizeof(err));
    CHECK(s != NULL, "sheet with a slide: %s", err);
    if (s) {
        const struct cue_shot *sh = &s->shots[0];
        CHECK(s->ignored_keys == 0 && sh->slide_n == 3 && s->shots[1].slide_n == 0, "slide read, not counted as unknown");
        double du, dv;
        CHECK(cue_slide_eval(sh, 1.5, &du, &dv), "evaluates inside the path");
        CHECK_NEAR(du, 15.0, 1e-12);
        CHECK_NEAR(dv, -3.0, 1e-12);
        cue_slide_eval(sh, 3.0, &du, &dv);
        CHECK_NEAR(du, 60.0, 1e-12);
        CHECK_NEAR(dv, -6.0, 1e-12);
        cue_slide_eval(sh, 2.0, &du, &dv);
        CHECK(du == 30.0 && dv == -6.0, "exactly on a point");
        cue_slide_eval(sh, -5.0, &du, &dv);
        CHECK(du == 0.0 && dv == 0.0, "held before the first point");
        cue_slide_eval(sh, 4.0, &du, &dv);
        CHECK(du == 90.0 && dv == -6.0, "at the last point");
        cue_slide_eval(sh, 1e9, &du, &dv);
        CHECK(du == 90.0 && dv == -6.0, "held after the last point");
        cue_slide_eval(sh, NAN, &du, &dv);
        CHECK(du == 0.0 && dv == 0.0, "not a number: the picture waits at its first position");
        CHECK(!cue_slide_eval(&s->shots[1], 1.5, &du, &dv) && du == 0.0 && dv == 0.0, "a shot without a slide");
        // the slide is not part of the fingerprint: the judge's reading of a sheet does not change
        struct cue_shot plain = { .subtitle_monitor = -1 };
        CHECK(cue_shot_hash(sh) == cue_shot_hash(&plain), "fingerprint ignores the slide");
        cue_sheet_free(s);
    }
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"slide\":[[1,0,0]]}]}", "slide");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"slide\":[]}]}", "slide");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"slide\":[[1,0,0],[1,5,0]]}]}", "slide[1]");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"slide\":[[2,0,0],[1,5,0]]}]}", "does not exceed");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"slide\":[[1,0],[2,5,0]]}]}", "slide[0]");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"slide\":[[1,0,0],[2,\"x\",0]]}]}", "slide[1]");
    expect_rejected("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0,\"slide\":3}]}", "slide");
}

static void test_store(void)
{
    char err[200];
    struct cue_store st;
    CHECK(cue_store_init(&st), "store init");
    CHECK(cue_store_current(&st)->version == 0 && cue_store_count(&st) == 1, "starts with the default sheet");
    struct cue_sheet *v2 = load("{\"schema\":\"panorama.cues/1\",\"version\":2,\"shots\":[{\"start\":0}]}", 1920, 1080, err, sizeof(err));
    struct cue_sheet *v2b = load("{\"schema\":\"panorama.cues/1\",\"version\":2,\"shots\":[{\"start\":0}]}", 1920, 1080, err, sizeof(err));
    struct cue_sheet *v1 = load("{\"schema\":\"panorama.cues/1\",\"version\":1,\"shots\":[{\"start\":0}]}", 1920, 1080, err, sizeof(err));
    CHECK(v2 && v2b && v1, "three small sheets");
    CHECK(cue_store_publish(&st, v2, err, sizeof(err)) && cue_store_current(&st) == v2, "publish 2");
    CHECK(!cue_store_publish(&st, v2b, err, sizeof(err)) && strstr(err, "does not exceed"), "same version again is refused: %s", err);
    CHECK(!cue_store_publish(&st, v1, err, sizeof(err)), "older version is refused");
    CHECK(cue_store_current(&st) == v2 && cue_store_find(&st, 2) == v2 && cue_store_find(&st, 0) != NULL && cue_store_find(&st, 7) == NULL, "find");
    cue_sheet_free(v2b);
    cue_sheet_free(v1);
    // fill to the cap
    int next = 3;
    bool ok = true;
    while (cue_store_count(&st) < CUE_MAX_VERSIONS && ok) {
        struct cue_sheet *s = cue_sheet_default();
        s->version = next++;
        ok = cue_store_publish(&st, s, err, sizeof(err));
        if (!ok)
            cue_sheet_free(s);
    }
    CHECK(ok && cue_store_count(&st) == CUE_MAX_VERSIONS, "store filled to %d versions", CUE_MAX_VERSIONS);
    struct cue_sheet *over = cue_sheet_default();
    over->version = next;
    CHECK(!cue_store_publish(&st, over, err, sizeof(err)) && strstr(err, "at most"), "the cap is enforced: %s", err);
    cue_sheet_free(over);
    cue_store_destroy(&st);
}

// ---- the latch ------------------------------------------------------------------------------------------------

static struct cue_sheet *sheet3(int version, double b1, double b2, double crop_x)
{
    char text[600];
    snprintf(text, sizeof(text),
             "{\"schema\":\"panorama.cues/1\",\"version\":%d,\"shots\":[{\"start\":0,\"crop\":[%g,0,1920,1080]},"
             "{\"start\":%g,\"crop\":[%g,10,1920,1080]},{\"start\":%g,\"crop\":[%g,20,1920,1080]}]}",
             version, crop_x, b1, crop_x + 1, b2, crop_x + 2);
    char err[200];
    struct cue_sheet *s = load(text, 1920, 1080, err, sizeof(err));
    if (!s)
        printf("sheet3: %s\n", err);
    return s;
}

static void test_latch(void)
{
    char err[200];
    struct cue_store st;
    cue_store_init(&st);
    struct shot_latch l;
    shot_latch_init(&l);
    cue_store_publish(&st, sheet3(1, 10.0, 20.0, 0), err, sizeof(err));

    struct latch_result r = shot_latch_resolve(&l, &st, 1.0);
    CHECK(r.captured && r.entry->sheet->version == 1 && r.entry->shot == 0 && r.entry->start == 0 && r.entry->end == 10.0, "first frame latches v1 shot 0");
    cue_store_publish(&st, sheet3(2, 10.0, 20.0, 100), err, sizeof(err));
    r = shot_latch_resolve(&l, &st, 5.0);
    CHECK(!r.captured && r.entry->sheet->version == 1, "a new version does not reach a shot on screen");
    r = shot_latch_resolve(&l, &st, 10.04);
    CHECK(r.captured && r.entry->sheet->version == 2 && r.entry->shot == 1, "the next shot takes the new version");
    uint32_t id1 = r.entry->id;
    r = shot_latch_resolve(&l, &st, 9.96); // a monitor still on the last frame of shot 0
    CHECK(!r.captured && r.entry->sheet->version == 1 && r.entry->shot == 0, "a lagging monitor keeps the old shot's version");
    r = shot_latch_resolve(&l, &st, 12.0);
    CHECK(!r.captured && r.entry->id == id1, "same entry for the whole shot");

    // a planner that moved a boundary earlier: the new entry starts where the previous one ended
    cue_store_publish(&st, sheet3(3, 9.0, 20.0, 200), err, sizeof(err));
    shot_latch_reset(&l);
    r = shot_latch_resolve(&l, &st, 9.5); // first latched after a break: shot 1 of v3 starts at 9.0
    CHECK(r.captured && r.entry->sheet->version == 3 && r.entry->shot == 1 && r.entry->start == 9.0, "after a reset the shot captures the newest version");
    cue_store_publish(&st, sheet3(4, 8.0, 15.0, 300), err, sizeof(err));
    r = shot_latch_resolve(&l, &st, 15.5); // v4 shot 2 starts at 15: previous entry (v3 shot 1) ended at 20 -> not covered? 15.5 < 20
    CHECK(!r.captured && r.entry->sheet->version == 3, "inside the latched span nothing changes");
    r = shot_latch_resolve(&l, &st, 20.5);
    CHECK(r.captured && r.entry->sheet->version == 4 && r.entry->shot == 2 && r.entry->start == 20.0, "span starts where the last ended (20), not at v4's 15");
    r = shot_latch_resolve(&l, &st, 19.99);
    CHECK(!r.captured && r.entry->sheet->version == 3, "no overlap: 19.99 still belongs to the older entry");

    // reset forgets everything
    shot_latch_reset(&l);
    cue_store_publish(&st, sheet3(5, 10.0, 20.0, 400), err, sizeof(err));
    r = shot_latch_resolve(&l, &st, 5.0);
    CHECK(r.captured && r.entry->sheet->version == 5, "after a break the shot is latched afresh");

    // stale lookup: older than everything the ring remembers
    shot_latch_reset(&l);
    char text[8000];
    int len = snprintf(text, sizeof(text), "{\"schema\":\"panorama.cues/1\",\"version\":6,\"shots\":[");
    for (int i = 0; i < 40; i++)
        len += snprintf(text + len, sizeof(text) - (size_t) len, "%s{\"start\":%d}", i ? "," : "", i * 10);
    snprintf(text + len, sizeof(text) - (size_t) len, "]}");
    cue_store_publish(&st, load(text, 1920, 1080, err, sizeof(err)), err, sizeof(err));
    for (int i = 0; i < 30; i++)
        shot_latch_resolve(&l, &st, i * 10 + 1.0);
    r = shot_latch_resolve(&l, &st, 1.0);
    CHECK(r.stale && !r.captured && l.stale_lookups == 1, "a lookup older than the ring is flagged stale");
    r = shot_latch_resolve(&l, &st, 29 * 10 + 5.0);
    CHECK(!r.stale && !r.captured, "recent shots are still found");
    cue_store_destroy(&st);
}

// ---- layout ----------------------------------------------------------------------------------------------------

static void test_layout(void)
{
    // Setup A at the half-size windowed run of 2026-10-11 (spikes/performer-p1/results-2026-10-11/windowed-rowtiming-judas):
    // laptop 344x215 mm at u = 0 with a 1280x800 swapchain, external 476x268 mm at u = 364 with 960x540, tops level.
    struct lay_monitor mon[2] = {
        { .left_mm = 0, .top_mm = 0, .w_mm = 344, .h_mm = 215, .sc_w = 1280, .sc_h = 800 },
        { .left_mm = 364, .top_mm = 0, .w_mm = 476, .h_mm = 268, .sc_w = 960, .sc_h = 540 },
    };
    struct lay_defaults def;
    layout_defaults(mon, 2, 0.0, NAN, &def);
    CHECK_NEAR(def.center_u_mm, 354.0, 1e-9);
    CHECK_NEAR(def.height_mm, 215.0, 1e-9);
    CHECK_NEAR(def.top_v_mm, 0.0, 1e-9);

    struct cue_shot plain = { .subtitle_monitor = -1 };
    struct lay_picture p;
    layout_shot(&plain, 1920, 1080, 1.0, mon, 2, &def, &p);
    CHECK_NEAR(p.rect_mm[0], 162.89, 0.01);
    CHECK_NEAR(p.rect_mm[2], 545.11, 0.01);
    CHECK_NEAR(p.rect_mm[1], 0.0, 1e-9);
    CHECK_NEAR(p.rect_mm[3], 215.0, 1e-9);
    CHECK(p.share[0].has_picture && p.share[0].tx0 == 606 && p.share[0].ty0 == 0 && p.share[0].tx1 == 1280 && p.share[0].ty1 == 800,
          "laptop target %d %d %d %d (golden 606 0 1280 800)", p.share[0].tx0, p.share[0].ty0, p.share[0].tx1, p.share[0].ty1);
    CHECK_NEAR(p.share[0].u0, -7e-5, 2e-5);
    CHECK_NEAR(p.share[0].u1, 0.47384, 2e-5);
    CHECK_NEAR(p.share[0].v0, 0.0, 2e-5);
    CHECK_NEAR(p.share[0].v1, 1.0, 2e-5);
    CHECK(p.share[1].has_picture && p.share[1].tx0 == 0 && p.share[1].ty0 == 0 && p.share[1].tx1 == 365 && p.share[1].ty1 == 433,
          "external target %d %d %d %d (golden 0 0 365 433)", p.share[1].tx0, p.share[1].ty0, p.share[1].tx1, p.share[1].ty1);
    CHECK_NEAR(p.share[1].u0, 0.52616, 2e-5);
    CHECK_NEAR(p.share[1].u1, 0.99965, 2e-5);
    CHECK_NEAR(p.share[1].v1, 0.99952, 2e-5);

    // a letterboxed film: crop 1920x800 (2.4:1) at the same centre and height
    struct cue_shot box = { .has_crop = true, .crop = { 0, 140, 1920, 940 }, .has_place = true, .center_u_mm = 354,
                            .center_v_mm = 107.5, .height_mm = 215, .subtitle_monitor = -1 };
    layout_shot(&box, 1920, 1080, 1.0, mon, 2, &def, &p);
    CHECK_NEAR(p.aspect, 2.4, 1e-12);
    CHECK_NEAR(p.rect_mm[0], 354 - 0.5 * 215 * 2.4, 1e-9);
    CHECK_NEAR(p.rect_mm[2], 354 + 0.5 * 215 * 2.4, 1e-9);
    CHECK_NEAR(p.rect_mm[1], 0.0, 1e-9);
    CHECK(p.share[0].has_picture && p.share[1].has_picture, "both screens show part of it");
    CHECK_NEAR(p.share[0].u0, 0.0, 0.002);
    CHECK_NEAR(p.share[0].u1, (344.0 - p.rect_mm[0]) / 516.0, 0.002);
    CHECK_NEAR(p.share[1].u0, (364.0 - p.rect_mm[0]) / 516.0, 0.002);
    CHECK_NEAR(p.share[1].u1, 1.0, 0.002);
    CHECK(p.crop[1] == 140 && p.crop[3] == 940, "crop reported");

    // anamorphic: 1440x1080 with a pixel aspect ratio of 4/3 shows as 16:9
    layout_shot(&plain, 1440, 1080, 4.0 / 3.0, mon, 2, &def, &p);
    CHECK_NEAR(p.aspect, 16.0 / 9.0, 1e-9);

    // a picture parked beyond the right edge of the screens touches neither
    struct cue_shot away = { .has_place = true, .center_u_mm = 5000, .center_v_mm = 100, .height_mm = 100, .subtitle_monitor = -1 };
    layout_shot(&away, 1920, 1080, 1.0, mon, 2, &def, &p);
    CHECK(!p.share[0].has_picture && !p.share[1].has_picture, "picture off the screens");

    // one monitor: centred on it, as tall as it is
    struct lay_monitor one = { .left_mm = 0, .top_mm = 0, .w_mm = 344, .h_mm = 215, .sc_w = 2560, .sc_h = 1600 };
    layout_defaults(&one, 1, 0.0, NAN, &def);
    CHECK_NEAR(def.center_u_mm, 172.0, 1e-9);
    layout_shot(&plain, 1920, 1080, 1.0, &one, 1, &def, &p);
    // the 16:9 picture is 382 mm wide on a 344 mm screen: centred, so 19 mm of it falls off each side
    CHECK(p.share[0].has_picture && p.share[0].tx0 == 0 && p.share[0].tx1 == 2560, "centred on a single screen");
    CHECK_NEAR(p.share[0].u0, 19.11 / 382.22, 0.001);
    CHECK_NEAR(p.share[0].u1, 1.0 - 19.11 / 382.22, 0.001);

    // overrides
    layout_defaults(mon, 2, 100.0, 300.0, &def);
    CHECK_NEAR(def.height_mm, 100.0, 1e-9);
    CHECK_NEAR(def.center_u_mm, 300.0, 1e-9);
}

int main(void)
{
    test_json();
    test_cue_sheet();
    test_slide();
    test_store();
    test_latch();
    test_layout();
    printf("cue-selftest: %d checks, %d failed\n", g_checks, g_failed);
    return g_failed ? 1 : 0;
}
