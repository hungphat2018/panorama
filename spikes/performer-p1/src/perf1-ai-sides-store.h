// The side store, player end: the memory-mapped file the AI surround's live worker writes (spikes/ai-surround/live-worker.py)
// and the player reads, laid out as spikes/ai-surround/live/side-store.layout.json (schema panorama.side-store/2). The
// structs below mirror that file field by field; the static asserts pin every offset, and the store self-test
// (ai-sides-selftest.c, run by spikes/ai-surround/live-store-selftest.py) proves the Python writer and this reader agree.
//
// Who writes what: the worker writes everything but the player block; the player writes only the player block. Records
// that change after they are first published carry a sequence counter (frame.seq, shot.seq, canvas.gen, player.seq):
// odd while the writer changes them. ai_store_read_* copy a record and keep the copy only if the counter was even and
// the same before and after; the canvas pixels are checked the same way with ai_store_canvas_still after copying.
//
// Pure CPU code with no graphics API, so the self-test runs it headless.
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define AI_STORE_MAGIC "PNRSIDE2"
#define AI_STORE_SCHEMA_MAJOR 2u
#define AI_STORE_HEADER_BYTES 4096u
#define AI_PAGE_MAGIC "PNRPAGE1"

// enums of the layout file
enum ai_worker_state { AI_WORKER_STARTING = 0, AI_WORKER_RUNNING = 1, AI_WORKER_STOPPED = 2 };
enum ai_player_state { AI_PLAYER_NONE = 0, AI_PLAYER_PLAYING = 1, AI_PLAYER_PAUSED = 2, AI_PLAYER_SEEKING = 3, AI_PLAYER_ENDED = 4 };
enum ai_canvas_state { AI_CANVAS_FREE = 0, AI_CANVAS_COMPLETE = 1, AI_CANVAS_EVICTED = 2 };
enum ai_tier { AI_TIER_GLOW = 0, AI_TIER_MOSAIC = 1, AI_TIER_PLATE = 2, AI_TIER_STRUCTURE = 3, AI_TIER_REFINED = 4 };
enum ai_pixel_format { AI_FMT_RGBA8_FILM = 1, AI_FMT_RGBA16F_LINEAR = 2 };
enum ai_frame_flags { AI_FRAME_VALID = 1, AI_FRAME_CUT = 2, AI_FRAME_NEW_DRAWING = 4 };
enum ai_shot_flags { AI_SHOT_KNOWN = 1, AI_SHOT_ANALYSED = 2, AI_SHOT_MOSAIC_EMPTY = 4, AI_SHOT_SHORT = 8,
                     AI_SHOT_FROM_CONTENT_MODEL = 16 };
enum ai_side { AI_SIDE_LEFT = 0, AI_SIDE_TOP = 1, AI_SIDE_RIGHT = 2, AI_SIDE_BOTTOM = 3 };
enum ai_canvas_kind { AI_KIND_CANVAS = 0, AI_KIND_PATCH_PAGE = 1 };
enum ai_patch_kind { AI_PATCH_PERSISTENT = 1, AI_PATCH_OVERLAY = 2 };

struct ai_layout_block {             // offset 0
    char magic[8];
    uint32_t schema_major, schema_minor;
    uint64_t file_bytes;
    uint32_t header_bytes, frame_rec_bytes, shot_rec_bytes, canvas_rec_bytes;
    uint32_t max_frames, max_shots, heap_slots, reserved0;
    uint64_t heap_slot_bytes;
    uint64_t off_frames, off_shots, off_canvases, off_heap;
    uint64_t store_uid;
    uint8_t pad[152];
};

struct ai_worker_block {             // offset 256
    uint32_t pid, state;
    uint64_t beat;
    int64_t qpc, qpc_freq;
    uint64_t film_bytes;
    uint32_t src_w, src_h;
    uint32_t frames_published, shots_published, analysed_frames, painter_state;
    uint32_t plates_done, plates_skipped, cache_hits, throttle_events;
    float painter_s_per_plate;
    int32_t epoch_seen;
    uint64_t reserved;
    char film_name[128];
    char status[32];
};

struct ai_player_block {             // offset 512
    uint32_t seq, pid;
    uint64_t beat;
    int64_t qpc;
    uint32_t state;
    int32_t epoch;
    double playhead_s, speed;
    float slack_ms;
    uint32_t late_recent;
    uint64_t late_total;
    float need_margin_px[4];
    uint32_t need_valid, src_w, src_h;
    int32_t uploading_slot;
    uint32_t uploading_gen;
    int32_t showing_shot;
    uint32_t showing_tier;
    int32_t showing_canvas;
    uint32_t resident_n;
    int32_t resident_canvas[16];
    uint32_t uploads_done, uploads_torn, reserved;
    uint8_t pad[320];
};

struct ai_frame_rec {                // one per frame of the film
    double pts;
    uint32_t seq;
    int32_t shot;
    float h[8];                      // source px -> world px, projective: d = h6 x + h7 y + 1, wx = (h0 x + h1 y + h2) / d,
                                     // wy = (h3 x + h4 y + h5) / d
    float gain[4];                   // per side (enum ai_side), linear light
    uint8_t state[4];
    uint32_t flags;
    uint32_t reserved[6];
};

struct ai_shot_rec {
    uint32_t seq, flags;
    uint32_t first_frame, n_frames;
    uint32_t canvas_id, key_frame;
    float crop[4];
    double start_s, end_s;
    uint32_t best_tier, reserved;
};

struct ai_canvas_rec {               // one per heap slot
    uint32_t gen, state;
    uint32_t canvas_id, version;
    uint32_t tier, format;
    uint32_t tex_w, tex_h;
    uint32_t row_bytes;
    int32_t shot_hint;
    double world_x0, world_y0, scale, valid_from_s;
    uint64_t bytes, recipe;
    int64_t made_qpc;
    uint32_t kind;                   // enum ai_canvas_kind
    int32_t shot;                    // a patch page's shot row (-1 for a canvas)
    uint32_t page_header_bytes;      // a patch page: bytes before its texels (header + patch table, rounded up to 4096)
    uint32_t n_patches;
    uint64_t reserved[2];
};

// A heap slot that holds a shot's patch page starts with this header, then n_patches patch records, then (from the
// canvas record's page_header_bytes) the packed RGBA8 texels.
struct ai_patch_page {
    char magic[8];
    int32_t shot;
    uint32_t n_patches, grid_w, grid_h;
    uint32_t first_frame, n_frames;
    double grid_map[9];              // set world px -> page grid texel coordinate (row-major 3x3, homogeneous)
    uint64_t reserved[19];
};

struct ai_patch_rec {
    uint32_t frame;                  // film frame (frame-table row) from which it applies
    uint32_t until;                  // overlay: last frame (inclusive)
    uint16_t kind;                   // enum ai_patch_kind
    uint8_t side, flags;
    uint16_t dst_x, dst_y, w, h;     // destination rect in the page grid
    uint16_t src_x, src_y;           // in the packed texels
    uint32_t reserved[2];
};

_Static_assert(sizeof(struct ai_layout_block) == 256, "layout block");
_Static_assert(offsetof(struct ai_layout_block, heap_slot_bytes) == 56, "layout.heap_slot_bytes");
_Static_assert(offsetof(struct ai_layout_block, off_heap) == 88, "layout.off_heap");
_Static_assert(offsetof(struct ai_layout_block, store_uid) == 96, "layout.store_uid");
_Static_assert(sizeof(struct ai_worker_block) == 256, "worker block");
_Static_assert(offsetof(struct ai_worker_block, frames_published) == 48, "worker.frames_published");
_Static_assert(offsetof(struct ai_worker_block, painter_s_per_plate) == 80, "worker.painter_s_per_plate");
_Static_assert(offsetof(struct ai_worker_block, film_name) == 96, "worker.film_name");
_Static_assert(offsetof(struct ai_worker_block, status) == 224, "worker.status");
_Static_assert(sizeof(struct ai_player_block) == 512, "player block");
_Static_assert(offsetof(struct ai_player_block, playhead_s) == 32, "player.playhead_s");
_Static_assert(offsetof(struct ai_player_block, need_margin_px) == 64, "player.need_margin_px");
_Static_assert(offsetof(struct ai_player_block, uploading_slot) == 92, "player.uploading_slot");
_Static_assert(offsetof(struct ai_player_block, resident_canvas) == 116, "player.resident_canvas");
_Static_assert(offsetof(struct ai_player_block, uploads_done) == 180, "player.uploads_done");
_Static_assert(sizeof(struct ai_frame_rec) == 96, "frame record");
_Static_assert(offsetof(struct ai_frame_rec, h) == 16, "frame.h");
_Static_assert(offsetof(struct ai_frame_rec, gain) == 48, "frame.gain");
_Static_assert(offsetof(struct ai_frame_rec, state) == 64, "frame.state");
_Static_assert(offsetof(struct ai_frame_rec, flags) == 68, "frame.flags");
_Static_assert(sizeof(struct ai_shot_rec) == 64, "shot record");
_Static_assert(offsetof(struct ai_shot_rec, crop) == 24, "shot.crop");
_Static_assert(offsetof(struct ai_shot_rec, start_s) == 40, "shot.start_s");
_Static_assert(offsetof(struct ai_shot_rec, best_tier) == 56, "shot.best_tier");
_Static_assert(sizeof(struct ai_canvas_rec) == 128, "canvas record");
_Static_assert(offsetof(struct ai_canvas_rec, shot_hint) == 36, "canvas.shot_hint");
_Static_assert(offsetof(struct ai_canvas_rec, world_x0) == 40, "canvas.world_x0");
_Static_assert(offsetof(struct ai_canvas_rec, bytes) == 72, "canvas.bytes");
_Static_assert(offsetof(struct ai_canvas_rec, made_qpc) == 88, "canvas.made_qpc");
_Static_assert(offsetof(struct ai_canvas_rec, kind) == 96, "canvas.kind");
_Static_assert(offsetof(struct ai_canvas_rec, shot) == 100, "canvas.shot");
_Static_assert(offsetof(struct ai_canvas_rec, page_header_bytes) == 104, "canvas.page_header_bytes");
_Static_assert(offsetof(struct ai_canvas_rec, n_patches) == 108, "canvas.n_patches");
_Static_assert(sizeof(struct ai_patch_page) == 256, "patch_page record");
_Static_assert(offsetof(struct ai_patch_page, shot) == 8, "patch_page.shot");
_Static_assert(offsetof(struct ai_patch_page, first_frame) == 24, "patch_page.first_frame");
_Static_assert(offsetof(struct ai_patch_page, grid_map) == 32, "patch_page.grid_map");
_Static_assert(sizeof(struct ai_patch_rec) == 32, "patch record");
_Static_assert(offsetof(struct ai_patch_rec, kind) == 8, "patch.kind");
_Static_assert(offsetof(struct ai_patch_rec, side) == 10, "patch.side");
_Static_assert(offsetof(struct ai_patch_rec, dst_x) == 12, "patch.dst_x");
_Static_assert(offsetof(struct ai_patch_rec, src_x) == 20, "patch.src_x");

struct ai_store {
    void *file, *mapping;            // Windows handles
    uint8_t *base;
    uint64_t size;
    const struct ai_layout_block *layout;
    volatile struct ai_worker_block *worker;
    volatile struct ai_player_block *player;
    const struct ai_frame_rec *frames;
    const struct ai_shot_rec *shots;
    const struct ai_canvas_rec *canvases;
    const uint8_t *heap;
    uint32_t max_frames, max_shots, heap_slots;
    uint64_t heap_slot_bytes, store_uid;
};

// Maps an existing store file read-write (the player block is ours). False with a reason when the file is missing, still
// being created, or not a store of this schema; the caller retries later.
bool ai_store_open(struct ai_store *s, const char *path, char *err, size_t err_size);
void ai_store_close(struct ai_store *s);

// Rows the worker has published so far (acquire loads).
uint32_t ai_store_frames_published(const struct ai_store *s);
uint32_t ai_store_shots_published(const struct ai_store *s);
uint64_t ai_store_worker_beat(const struct ai_store *s);

// Index of the frame on screen at film time pts: the last published frame whose time is at most pts (+1 us), or -1.
// `hint` (a previous answer, or -1) makes a forward walk instead of a search.
int ai_store_find_frame(const struct ai_store *s, double pts, int hint);

// Consistent copies under the record's counter (false: torn too often, or out of range).
bool ai_store_read_frame(const struct ai_store *s, uint32_t i, struct ai_frame_rec *out);
bool ai_store_read_shot(const struct ai_store *s, uint32_t i, struct ai_shot_rec *out);
bool ai_store_read_canvas(const struct ai_store *s, uint32_t slot, struct ai_canvas_rec *out);
// The slot still holds what a copy with this gen saw (call after copying its pixels).
bool ai_store_canvas_still(const struct ai_store *s, uint32_t slot, uint32_t gen);
const uint8_t *ai_store_slot_pixels(const struct ai_store *s, uint32_t slot);
// A patch page's header and patch table (c: its canvas record, as the caller read it): copied, checked (magic, sizes,
// every rect inside the grid and the texels) and kept only if the slot's gen did not change across the copy. *recs is
// malloc'd (the caller frees it).
bool ai_store_read_page(const struct ai_store *s, uint32_t slot, const struct ai_canvas_rec *c, struct ai_patch_page *hdr,
                        struct ai_patch_rec **recs);

// The player block: begin makes seq odd, end makes it even again; write the fields in between.
volatile struct ai_player_block *ai_store_player_begin(struct ai_store *s);
void ai_store_player_end(struct ai_store *s);
