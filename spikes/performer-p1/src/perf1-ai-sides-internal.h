// State shared by the AI sides' files (perf1-ai-sides.c: options, the store, the showings, the refresh;
// perf1-ai-sides-record.c: the room's side widths and the player record; perf1-ai-sides-upload.c: the canvases and patch
// pages on the GPU and their copies; perf1-ai-sides-patches.c: each showing's accumulate and overlay textures;
// perf1-finish-surround-ai.c: the shaders). Nothing else includes this. Every call comes from the render thread, so
// nothing here is locked.
#pragma once

#include "perf1-ai-sides.h"
#include "perf1-ai-sides-store.h"
#include "perf1-finish-internal.h"

#define AI_GPU_MAX 24
#define AI_SHOWINGS 4
#define AI_ACC_MAX 3              // accumulate + overlay texture pairs (the showing on screen, the next ones at quick cuts)
#define AI_SEAM_CACHE 2
#define AI_SEAM_FALLOFF 0.25f     // the seam pull fades over this share of the side's width (hidden_sides.live_seam)
#define AI_PIN_S 1.0              // a showing drawn this recently keeps its canvas on the GPU
#define AI_ACC_PIN_S 0.25         // ... and its accumulate and overlay textures (short: quick cuts need them back soon)
#define AI_TEX_BUCKET 256         // GPU canvas textures are allocated in steps of this many texels (reused across sizes)

// 1 x 1 transparent stand-ins for a texture a shader does not use this refresh, one per sampler: libplacebo refuses a
// pass that binds one texture to two of its descriptors.
enum ai_stand_in { AI_STAND_CANVAS, AI_STAND_ACC, AI_STAND_OVL, AI_STAND_SEAM, AI_STAND_INS };

struct ai_cfg {
    const char *store;      // --ai-sides
    double horizon_s;       // --ai-horizon-s: canvases of the shots starting this far ahead are copied to the GPU
    int gpu_canvases;       // --ai-gpu-canvases
    int upload_kb;          // --ai-upload-kb: bytes copied per master refresh
    bool seam;              // the seam pull (--ai-no-seam turns it off)
    bool patches;           // patch pages copied and shown (T3, retired: off unless --ai-patches)
};
extern struct ai_cfg AIC;

// A canvas version, or a shot's patch page, on the GPU (or being copied there).
struct ai_gpu_canvas {
    bool used, complete;
    uint32_t kind;            // enum ai_canvas_kind
    uint32_t canvas_id, version, tier;
    uint64_t recipe;
    pl_tex tex;
    int w, h;                 // the canvas's texels (a page: its packed texels)
    int tex_w, tex_h;         // the texture's (a bucket at least w x h; the canvas sits at its top left)
    double world_x0, world_y0, scale, valid_from_s;
    int shot_hint;
    uint32_t serial;          // a new number for every copy started (what the seam profile cache keys on)
    int heap_slot;            // the copy in progress: from this slot at this gen, rows done so far
    uint32_t heap_gen, rows_done, row_bytes, pixel_offset;
    double last_used;         // host time a showing last drew it, or when it was completed
    // a patch page: its shot, grid and patch table (copied from the slot when its copy started)
    int32_t shot;
    struct ai_patch_page page;
    struct ai_patch_rec *patches;
};

// What one showing (a shot from its first drawn frame, in one epoch) shows: chosen once, kept for the whole shot.
struct ai_showing {
    bool used;
    int epoch;
    int32_t shot;
    int gpu;                  // index in G_AI.gpu, -1 = glow
    int page;                 // index in G_AI.gpu of the shot's patch page (T3), -1 = none
    int acc;                  // index in G_AI.acc of its accumulate and overlay textures, -1 = none
    uint32_t canvas_id, version, tier;
    int32_t applied;          // the film frame its accumulate texture is up to (-1: empty)
    uint32_t acc_gen;         // grows with every change of its accumulate or overlay texture (the seam profile's key)
    double start_pts;
    double last_drawn;        // host time of its latest refresh
};

// An accumulate texture (persistent patches, premultiplied) and an overlay texture (the overlays of the frame on screen).
struct ai_acc {
    pl_tex acc, ovl;
    int tex_w, tex_h;         // their size (a bucket at least the page grid)
    int owner;                // index in G_AI.show, -1 = free
    bool ovl_dirty;           // the overlay texture holds overlays to clear
};

// The fill's inputs for one monitor's refresh (perf1-finish-surround-ai.c reads them when pass 2 asks).
struct ai_fill_in {
    bool on;
    float u_mon[4], u_cen[4], u_ex[4], u_ey[4], u_srf[4];     // the monitor's geometry, as pass 1 has it
    float u_pic[4], u_crop[4], u_slide[4];                  // the shot's picture and this refresh's camera slide
    float m0[4], m1[4], m2[4];  // source px -> canvas texel coordinates, projective: q = (m0, m1, m2) . (x, y, 1), t = q.xy / q.z
    float size[4];            // canvas w, h (texels), then 1 / texture w, 1 / texture h
    float a0[4], a1[4], a2[4];  // source px -> page grid texel coordinates (the accumulate and overlay textures), the same way
    float asize[4];           // page grid w, h, then 1 / texture w, 1 / texture h
    float aon[4];             // accumulate on, overlay on
    pl_tex acc_tex, ovl_tex;
    float gain[4];            // per side, left/top/right/bottom
    float margin[4];          // the side widths (source px) the seam pull fades over
    float seam[4];            // on, falloff share, profile length, 1 / length
    pl_tex tex, seam_tex;
};

struct ai_state {
    struct ai_store store;
    bool open;
    double next_open_try;
    char open_err[160];
    uint64_t store_uid;
    uint64_t worker_beat;
    double worker_beat_t;     // host time the worker's beat last changed
    bool worker_alive;
    struct ai_gpu_canvas gpu[AI_GPU_MAX];
    int uploading;            // index in gpu, -1 = none
    double next_pick;         // host time of the next search for a canvas to copy
    uint32_t serial;          // the last ai_gpu_canvas.serial handed out
    pl_tex stand_in[AI_STAND_INS];
    struct ai_showing show[AI_SHOWINGS];
    int show_next;
    struct ai_acc acc[AI_ACC_MAX];
    uint64_t patches_applied, pages_shown, monitor_behind, patch_failures;
    struct samples patch_ms;   // CPU time of one film frame's patch work
    struct ai_fill_in fill[MAX_MONITORS];
    int hint[MAX_MONITORS];   // frame-table row of each monitor's last refresh
    // the last records each monitor read whole: reused when a read meets a write in progress
    int cached_frame[MAX_MONITORS];
    struct ai_frame_rec cached_fr[MAX_MONITORS];
    int32_t cached_shot[MAX_MONITORS];
    struct ai_shot_rec cached_sr[MAX_MONITORS];
    bool warmed[MAX_MONITORS];  // the fill's shader variant compiled during the warm-up frame
    // the room's side widths, from the cue sheet in force
    const void *margin_sheet;
    float margins[4];
    bool margins_valid;
    // seam profiles, per film frame
    pl_dispatch dp;
    pl_tex seam_raw, seam_tex[AI_SEAM_CACHE];
    double seam_pts[AI_SEAM_CACHE];
    int seam_gpu[AI_SEAM_CACHE];
    uint32_t seam_gen[AI_SEAM_CACHE];
    uint32_t seam_acc[AI_SEAM_CACHE];   // the showing's acc_gen the profile was made with
    int seam_next;
    pl_timer seam_timer;
    // pressure, for the player record
    double lead_min_ms, lead_window_t;
    size_t stats_seen[MAX_MONITORS];
    double miss_t[64];        // host times of the latest missed refreshes (ring)
    int miss_n;
    uint64_t late_total;
    // log
    uint64_t uploads, upload_bytes, uploads_torn, uploads_aborted, opens, uid_changes, worker_losses, seam_passes;
    uint64_t record_reuses, crop_mismatches;
    double tier_film_s[5], last_master_pts;
    struct samples upload_ms, seam_ms;
    uint32_t refreshes_ai[MAX_MONITORS], refreshes_glow[MAX_MONITORS];
    FILE *showing_log;
};
extern struct ai_state G_AI;

// perf1-ai-sides-record.c
void ai_compute_margins(void);                             // the side widths for the cue sheet in force (when it changed)
void ai_pressure_update(double now);                       // present slack and missed refreshes from the monitors' logs
void ai_player_record(double now, const struct pic_refresh *r, const struct ai_showing *sh);

// perf1-ai-sides-upload.c
void ai_gpu_drop_all(void);                                // the store changed under us: forget every canvas
int ai_gpu_best(uint32_t canvas_id, double pts);            // complete canvas of that id to show from pts, best tier first
int ai_gpu_page(int32_t shot, uint32_t canvas_id);          // complete patch page of that shot and set, newest first
void ai_upload_step(int frame, double playhead);           // continue the copy in progress, or start the next one
void ai_gpu_destroy(void);

// perf1-ai-sides-patches.c
// The showing's accumulate (and overlay) textures brought forward to film frame f: the persistent patches after what is
// applied, up to f, copied in, the overlays of f drawn. A frame at or before what is applied changes nothing (a monitor a
// frame behind the other sees the newer state; a seek starts a new showing). False: no textures (the showing then shows
// its canvas without patches, for the rest of the shot).
bool ai_patches_to_frame(struct ai_showing *sh, int f);
void ai_patches_release(int show_index);                   // a showing's textures go back to the pool
void ai_patches_warm_up(void);                             // makes a pair and draws a stand-in patch (shader compiled now)
void ai_patches_destroy(void);

// perf1-finish-surround-ai.c
bool ai_fill_append(void *priv, pl_shader sh);             // the fill hook (priv: struct ai_fill_in *)
// This frame's seam profile for a canvas (and the showing's accumulate and overlay textures, when fi->aon says so),
// cached per frame, copy serial and acc_gen; key: the GPU canvas index, -1 for the warm-up's stand-in. NULL on failure.
pl_tex ai_seam_profile(int key, uint32_t serial, uint32_t acc_gen, double pts, pl_tex front_lin, const struct ai_fill_in *fi);
void ai_seam_destroy(void);
