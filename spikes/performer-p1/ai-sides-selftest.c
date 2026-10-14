// ai-sides-selftest: the player end of the side store (src/perf1-ai-sides-store.c) against a store the Python worker
// end wrote. Driven by spikes/ai-surround/live-store-selftest.py; no GPU, no window.
//
//   ai-sides-selftest dump STORE              every header field, published frame, shot and canvas record as JSON, with
//                                             a checksum of each complete canvas's pixels (the schema round trip)
//   ai-sides-selftest watch STORE SECONDS     acts as a player for SECONDS: advances a playhead in the player block,
//                                             follows the frame table, copies every complete canvas it sees (as uploads
//                                             do) and checks the copy against the test pattern; prints a JSON summary
//                                             (copies kept, torn copies caught by the counter, corrupt copies = bugs)
//
// Test pattern of the fake worker (live-store-selftest.py): texel (x, y) of canvas (id, version) is
// R = (x + 7 id) & 255, G = (y + 13 version) & 255, B = (x ^ y ^ id) & 255, A = 255.
//
// Compile: pwsh -File compile.ps1 -Program ai-sides-test   (bin/ai-sides-selftest.exe)
#define _CRT_SECURE_NO_WARNINGS
#include "src/perf1-ai-sides-store.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

static double now_s(void)
{
    LARGE_INTEGER c, f;
    QueryPerformanceCounter(&c);
    QueryPerformanceFrequency(&f);
    return (double) c.QuadPart / (double) f.QuadPart;
}

static uint64_t fnv1a(const uint8_t *p, size_t n)
{
    uint64_t h = 1469598103934665603ULL;
    for (size_t i = 0; i < n; i++)
        h = (h ^ p[i]) * 1099511628211ULL;
    return h;
}

static void print_str(const char *key, const volatile char *v, size_t n, const char *tail)
{
    printf("\"%s\":\"", key);
    for (size_t i = 0; i < n && v[i]; i++) {
        char c = v[i];
        if (c == '"' || c == '\\')
            printf("\\%c", c);
        else if ((unsigned char) c < 0x20)
            printf("?");
        else
            printf("%c", c);
    }
    printf("\"%s", tail);
}

static int cmd_dump(const char *path)
{
    struct ai_store s;
    char err[200];
    if (!ai_store_open(&s, path, err, sizeof(err))) {
        fprintf(stderr, "ai-sides-selftest: %s: %s\n", path, err);
        return 2;
    }
    const struct ai_layout_block *l = s.layout;
    printf("{\"layout\":{\"schema_major\":%u,\"schema_minor\":%u,\"file_bytes\":%llu,\"max_frames\":%u,\"max_shots\":%u,"
           "\"heap_slots\":%u,\"heap_slot_bytes\":%llu,\"off_frames\":%llu,\"off_shots\":%llu,\"off_canvases\":%llu,"
           "\"off_heap\":%llu,\"store_uid\":%llu},\n",
           l->schema_major, l->schema_minor, (unsigned long long) l->file_bytes, l->max_frames, l->max_shots, l->heap_slots,
           (unsigned long long) l->heap_slot_bytes, (unsigned long long) l->off_frames, (unsigned long long) l->off_shots,
           (unsigned long long) l->off_canvases, (unsigned long long) l->off_heap, (unsigned long long) l->store_uid);
    volatile struct ai_worker_block *w = s.worker;
    printf("\"worker\":{\"pid\":%u,\"state\":%u,\"beat\":%llu,\"qpc\":%lld,\"qpc_freq\":%lld,\"film_bytes\":%llu,\"src_w\":%u,"
           "\"src_h\":%u,\"frames_published\":%u,\"shots_published\":%u,\"analysed_frames\":%u,\"painter_state\":%u,"
           "\"plates_done\":%u,\"plates_skipped\":%u,\"cache_hits\":%u,\"throttle_events\":%u,\"painter_s_per_plate\":%.9g,"
           "\"epoch_seen\":%d,",
           w->pid, w->state, (unsigned long long) w->beat, (long long) w->qpc, (long long) w->qpc_freq,
           (unsigned long long) w->film_bytes, w->src_w, w->src_h, w->frames_published, w->shots_published, w->analysed_frames,
           w->painter_state, w->plates_done, w->plates_skipped, w->cache_hits, w->throttle_events, (double) w->painter_s_per_plate,
           w->epoch_seen);
    print_str("film_name", w->film_name, sizeof(w->film_name), ",");
    print_str("status", w->status, sizeof(w->status), "},\n");
    volatile struct ai_player_block *p = s.player;
    printf("\"player\":{\"seq\":%u,\"pid\":%u,\"state\":%u,\"epoch\":%d,\"playhead_s\":%.17g,\"speed\":%.17g,\"slack_ms\":%.9g,"
           "\"late_recent\":%u,\"late_total\":%llu,\"need_margin_px\":[%.9g,%.9g,%.9g,%.9g],\"need_valid\":%u,\"src_w\":%u,"
           "\"src_h\":%u,\"uploading_slot\":%d,\"uploading_gen\":%u,\"showing_shot\":%d,\"showing_tier\":%u,\"showing_canvas\":%d,"
           "\"resident_n\":%u,\"uploads_done\":%u,\"uploads_torn\":%u},\n",
           p->seq, p->pid, p->state, p->epoch, p->playhead_s, p->speed, (double) p->slack_ms, p->late_recent,
           (unsigned long long) p->late_total, (double) p->need_margin_px[0], (double) p->need_margin_px[1],
           (double) p->need_margin_px[2], (double) p->need_margin_px[3], p->need_valid, p->src_w, p->src_h, p->uploading_slot,
           p->uploading_gen, p->showing_shot, p->showing_tier, p->showing_canvas, p->resident_n, p->uploads_done, p->uploads_torn);
    uint32_t nf = ai_store_frames_published(&s);
    printf("\"frames\":[");
    for (uint32_t i = 0; i < nf; i++) {
        struct ai_frame_rec f;
        if (!ai_store_read_frame(&s, i, &f)) {
            printf("%snull", i ? "," : "");
            continue;
        }
        printf("%s\n{\"i\":%u,\"pts\":%.17g,\"seq\":%u,\"shot\":%d,\"h\":[%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g,%.9g],"
               "\"gain\":[%.9g,%.9g,%.9g,%.9g],\"state\":[%u,%u,%u,%u],\"flags\":%u,\"find\":%d}",
               i ? "," : "", i, f.pts, f.seq, f.shot, (double) f.h[0], (double) f.h[1], (double) f.h[2], (double) f.h[3],
               (double) f.h[4], (double) f.h[5], (double) f.h[6], (double) f.h[7], (double) f.gain[0], (double) f.gain[1],
               (double) f.gain[2], (double) f.gain[3], f.state[0], f.state[1], f.state[2], f.state[3], f.flags,
               ai_store_find_frame(&s, f.pts, -1));
    }
    printf("],\n\"shots\":[");
    uint32_t ns = ai_store_shots_published(&s);
    for (uint32_t i = 0; i < ns; i++) {
        struct ai_shot_rec r;
        if (!ai_store_read_shot(&s, i, &r)) {
            printf("%snull", i ? "," : "");
            continue;
        }
        printf("%s\n{\"i\":%u,\"seq\":%u,\"flags\":%u,\"first_frame\":%u,\"n_frames\":%u,\"canvas_id\":%u,\"key_frame\":%u,"
               "\"crop\":[%.9g,%.9g,%.9g,%.9g],\"start_s\":%.17g,\"end_s\":%.17g,\"best_tier\":%u}",
               i ? "," : "", i, r.seq, r.flags, r.first_frame, r.n_frames, r.canvas_id, r.key_frame, (double) r.crop[0],
               (double) r.crop[1], (double) r.crop[2], (double) r.crop[3], r.start_s, r.end_s, r.best_tier);
    }
    printf("],\n\"canvases\":[");
    for (uint32_t i = 0; i < s.heap_slots; i++) {
        struct ai_canvas_rec c;
        if (!ai_store_read_canvas(&s, i, &c)) {
            printf("%snull", i ? "," : "");
            continue;
        }
        uint64_t sum = 0;
        uint64_t off = c.kind == AI_KIND_PATCH_PAGE ? c.page_header_bytes : 0;
        if (c.state == AI_CANVAS_COMPLETE && off + c.bytes <= s.heap_slot_bytes)
            sum = fnv1a(ai_store_slot_pixels(&s, i) + off, (size_t) c.bytes);
        printf("%s\n{\"slot\":%u,\"gen\":%u,\"state\":%u,\"canvas_id\":%u,\"version\":%u,\"tier\":%u,\"format\":%u,\"tex_w\":%u,"
               "\"tex_h\":%u,\"row_bytes\":%u,\"shot_hint\":%d,\"world_x0\":%.17g,\"world_y0\":%.17g,\"scale\":%.17g,"
               "\"valid_from_s\":%.17g,\"bytes\":%llu,\"recipe\":%llu,\"made_qpc\":%lld,\"kind\":%u,\"shot\":%d,"
               "\"page_header_bytes\":%u,\"n_patches\":%u,\"pixels_fnv1a\":\"%016llx\"",
               i ? "," : "", i, c.gen, c.state, c.canvas_id, c.version, c.tier, c.format, c.tex_w, c.tex_h, c.row_bytes, c.shot_hint,
               c.world_x0, c.world_y0, c.scale, c.valid_from_s, (unsigned long long) c.bytes, (unsigned long long) c.recipe,
               (long long) c.made_qpc, c.kind, c.shot, c.page_header_bytes, c.n_patches, (unsigned long long) sum);
        struct ai_patch_page pg;
        struct ai_patch_rec *recs = NULL;
        if (c.kind == AI_KIND_PATCH_PAGE && ai_store_read_page(&s, i, &c, &pg, &recs)) {
            printf(",\"page\":{\"shot\":%d,\"grid_w\":%u,\"grid_h\":%u,\"first_frame\":%u,\"n_frames\":%u,\"grid_map\":[",
                   pg.shot, pg.grid_w, pg.grid_h, pg.first_frame, pg.n_frames);
            for (int k = 0; k < 9; k++)
                printf("%s%.17g", k ? "," : "", pg.grid_map[k]);
            printf("],\"patches\":[");
            for (uint32_t k = 0; k < pg.n_patches; k++)
                printf("%s[%u,%u,%u,%u,%u,%u,%u,%u,%u,%u]", k ? "," : "", recs[k].frame, recs[k].until, recs[k].kind,
                       recs[k].side, recs[k].dst_x, recs[k].dst_y, recs[k].w, recs[k].h, recs[k].src_x, recs[k].src_y);
            printf("]}");
            free(recs);
        } else if (c.kind == AI_KIND_PATCH_PAGE) {
            printf(",\"page\":null");
        }
        printf("}");
    }
    printf("]}\n");
    ai_store_close(&s);
    return 0;
}

// The fake worker's pattern at texel (x, y).
static void expected_texel(uint32_t id, uint32_t version, uint32_t x, uint32_t y, uint8_t out[4])
{
    out[0] = (uint8_t) ((x + 7u * id) & 255u);
    out[1] = (uint8_t) ((y + 13u * version) & 255u);
    out[2] = (uint8_t) ((x ^ y ^ id) & 255u);
    out[3] = 255;
}

static int cmd_watch(const char *path, double seconds)
{
    struct ai_store s;
    char err[200];
    double t_end = now_s() + 30.0;
    while (!ai_store_open(&s, path, err, sizeof(err))) { // the worker may still be creating the file
        if (now_s() > t_end) {
            fprintf(stderr, "ai-sides-selftest: %s: %s\n", path, err);
            return 2;
        }
        Sleep(20);
    }
    uint8_t *copy = malloc((size_t) s.heap_slot_bytes);
    uint32_t *last_gen = calloc(s.heap_slots, sizeof(uint32_t));
    uint32_t *max_version = calloc(65536, sizeof(uint32_t)); // per canvas id (the test keeps ids below 65536)
    if (!copy || !last_gen || !max_version)
        return 3;
    unsigned long long kept = 0, torn = 0, corrupt = 0, busy = 0, older_versions = 0, frames_checked = 0, frame_bad = 0;
    unsigned long long loops = 0, worker_beats = 0, evicted_seen = 0;
    uint64_t last_beat = ai_store_worker_beat(&s);
    int hint = -1, epoch = 1;
    double t0 = now_s(), playhead = 0.0;
    while (now_s() - t0 < seconds) {
        loops++;
        playhead = now_s() - t0; // 1x film speed
        volatile struct ai_player_block *p = ai_store_player_begin(&s);
        p->pid = GetCurrentProcessId();
        p->beat++;
        p->state = AI_PLAYER_PLAYING;
        p->epoch = epoch;
        p->playhead_s = playhead;
        p->speed = 1.0;
        p->slack_ms = 2.0f;
        ai_store_player_end(&s);
        uint64_t b = ai_store_worker_beat(&s);
        worker_beats += b != last_beat;
        last_beat = b;
        // the frame table: the frame on screen and its plan
        hint = ai_store_find_frame(&s, playhead, hint);
        if (hint >= 0) {
            struct ai_frame_rec f;
            if (ai_store_read_frame(&s, (uint32_t) hint, &f)) {
                frames_checked++;
                // the fake worker writes h = (1, 0, -i, 0, 1, -2 i, 0, 0) and gain 1 + i / 1000 for frame i of an analysed shot
                if ((f.flags & AI_FRAME_VALID) && (f.h[2] != -(float) hint || f.h[5] != -2.0f * (float) hint ||
                                                  f.h[6] != 0.0f || f.h[7] != 0.0f || f.gain[0] != 1.0f + (float) hint / 1000.0f))
                    frame_bad++;
            }
        }
        // every complete canvas: copy it like an upload, then check the counter and the pattern
        for (uint32_t i = 0; i < s.heap_slots; i++) {
            struct ai_canvas_rec c;
            if (!ai_store_read_canvas(&s, i, &c)) {
                busy++; // the worker holds the slot's counter odd: it is being written
                continue;
            }
            if (c.state == AI_CANVAS_EVICTED && c.gen != last_gen[i])
                evicted_seen++;
            if (c.state != AI_CANVAS_COMPLETE || c.gen == last_gen[i] || c.bytes > s.heap_slot_bytes || c.canvas_id >= 65536)
                continue;
            {
                // copy in two halves, as the player's chunked uploads do, with a 0.2 ms pause between them so the worker
                // gets to rewrite slots mid-copy (the race the counter must catch)
                const uint8_t *src = ai_store_slot_pixels(&s, i);
                uint32_t half = c.tex_h / 2;
                memcpy(copy, src, (size_t) half * c.row_bytes);
                for (double t_pause = now_s() + 0.0002; now_s() < t_pause;)
                    YieldProcessor();
                memcpy(copy + (size_t) half * c.row_bytes, src + (size_t) half * c.row_bytes, (size_t) (c.tex_h - half) * c.row_bytes);
            }
            if (!ai_store_canvas_still(&s, i, c.gen)) {
                torn++;
                continue;
            }
            last_gen[i] = c.gen;
            kept++;
            if (c.version < max_version[c.canvas_id])
                older_versions++; // another slot already held a newer version of this canvas (both may be in the heap)
            if (c.version > max_version[c.canvas_id])
                max_version[c.canvas_id] = c.version;
            bool ok = true;
            for (uint32_t y = 0; y < c.tex_h && ok; y += 7) {
                for (uint32_t x = 0; x < c.tex_w; x += 5) {
                    uint8_t e[4];
                    expected_texel(c.canvas_id, c.version, x, y, e);
                    if (memcmp(copy + (size_t) y * c.row_bytes + (size_t) x * 4, e, 4)) {
                        ok = false;
                        break;
                    }
                }
            }
            corrupt += !ok;
        }
        Sleep(2);
    }
    volatile struct ai_player_block *p = ai_store_player_begin(&s);
    p->state = AI_PLAYER_ENDED;
    ai_store_player_end(&s);
    printf("{\"loops\":%llu,\"seconds\":%.3f,\"playhead_s\":%.3f,\"worker_beats_seen\":%llu,\"canvas_copies_kept\":%llu,"
           "\"torn_copies_caught\":%llu,\"corrupt_copies\":%llu,\"records_busy\":%llu,\"older_versions_seen\":%llu,"
           "\"evictions_seen\":%llu,\"frames_checked\":%llu,\"frame_plans_wrong\":%llu}\n",
           loops, now_s() - t0, playhead, worker_beats, kept, torn, corrupt, busy, older_versions, evicted_seen,
           frames_checked, frame_bad);
    free(copy);
    free(last_gen);
    free(max_version);
    ai_store_close(&s);
    return corrupt || frame_bad ? 1 : 0;
}

int main(int argc, char **argv)
{
    if (argc == 3 && !strcmp(argv[1], "dump"))
        return cmd_dump(argv[2]);
    if (argc == 4 && !strcmp(argv[1], "watch"))
        return cmd_watch(argv[2], atof(argv[3]));
    fprintf(stderr, "usage: ai-sides-selftest dump STORE | watch STORE SECONDS\n");
    return 2;
}
