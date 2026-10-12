// Cue sheet v0 (panorama.cues/1): what the performer applies, shot by shot. The planner writes it, the performer
// only reads it. A loaded sheet is an immutable "version"; the store keeps every published version alive for the
// whole run and holds one atomic pointer to the newest, so a render thread reads the current version without a
// lock and a shot that latched an older version can keep using it.
//
// File format (JSON, readable and writable by hand):
//   {
//     "schema": "panorama.cues/1",
//     "version": 3,                      integer >= 1, must grow with every sheet loaded into one run
//     "file_hash": "...",                carried, not checked yet
//     "room_signature": "...",           carried, not checked yet
//     "preferences_hash": "...",         carried, not checked yet
//     "shots": [
//       { "start": 0.0,                  seconds on the rebased timeline (first video frame = 0), halfway between
//                                        the last frame of the previous shot and the first of this one
//         "crop": [x0, y0, x1, y1],      source pixels of the visible frame; optional (whole frame)
//         "placement": { "center_mm": [u, v], "height_mm": h },
//                                        where the cropped picture lands on the surface: u is millimetres along the
//                                        screens from the left edge of the leftmost one, v millimetres down from the
//                                        top of the master screen; optional (centred on the gap, as tall as the
//                                        height all screens share)
//         "slide": [[t, du, dv], ...],   optional camera slide: where the picture sits at film time t (seconds on the
//                                        rebased timeline), as an offset in millimetres from its placement (du to the
//                                        right, dv down). At least two points, t strictly increasing; linear in between,
//                                        held before the first and after the last. Each row of a monitor is drawn at
//                                        the position of its own light-up time (the middle of the time its light stays
//                                        up). For a position measured on frame n, t is the middle of that frame's hold:
//                                        its timestamp plus half its duration. Not part of the shot fingerprint
//         "subtitle_monitor": 1,         the monitor (its number in the run, 0..15) that shows the dialogue; optional
//         "surround": {...},             what fills the leftover area: see perf1-picture-look.h; optional
//         "time": "as_filmed" }          carried, not applied yet
//     ]
//   }
// The first shot starts at or before 0 and starts must increase strictly. Unknown keys are ignored (and counted, so
// a typo does not go unnoticed).
#pragma once

#include <stdatomic.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#include "perf1-json.h"

#define CUE_SCHEMA "panorama.cues/1"
#define CUE_MAX_SHOTS 200000
#define CUE_MAX_VERSIONS 4096
#define CUE_MAX_FILE_BYTES (16u * 1024u * 1024u)
#define CUE_MAX_SLIDE_POINTS 200000

struct cue_slide_pt {
    double t, du, dv;                     // film time (s), offset of the picture from its placement (mm, right / down)
};

struct cue_shot {
    double start;
    bool has_crop;
    double crop[4];                       // x0, y0, x1, y1
    bool has_place;
    double center_u_mm, center_v_mm, height_mm;
    int subtitle_monitor;                 // -1 = none
    size_t slide_n;                       // 0 = no camera slide
    struct cue_slide_pt *slide;           // slide_n points, owned by the sheet
    struct json_value *surround;          // NULL when absent
    struct json_value *time_treatment;    // NULL when absent
};

struct cue_sheet {
    int version;                          // 0 = the built-in default sheet
    char *file_hash, *room_signature, *preferences_hash;
    char *source;                         // where it came from, for messages and the log
    size_t n_shots;
    struct cue_shot *shots;
    unsigned ignored_keys;                // keys the loader did not know
    char first_ignored_key[48];           // the first of them, for the warning
};

// Parses and validates a sheet. src_w x src_h is the visible size of the video frame the crops must fit in.
// Returns NULL with a message in err.
struct cue_sheet *cue_sheet_parse(const char *text, size_t len, double src_w, double src_h, const char *source,
                                  char *err, size_t err_size);
struct cue_sheet *cue_sheet_load_file(const char *path, double src_w, double src_h, char *err, size_t err_size);

// Version 0: one shot from the start of the film, whole frame, default placement.
struct cue_sheet *cue_sheet_default(void);
void cue_sheet_free(struct cue_sheet *s);

// Index of the last shot whose start is at or before pts (0 when pts is before the first start).
int cue_sheet_find_shot(const struct cue_sheet *s, double pts);
// Start of the next shot, or +infinity for the last one.
double cue_sheet_shot_end(const struct cue_sheet *s, int shot);

// The picture's offset from its placement at film time t (linear between the points, held outside them). False when
// the shot has no slide (du, dv are then 0).
bool cue_slide_eval(const struct cue_shot *shot, double t, double *du, double *dv);

// Fingerprint of the values a renderer applies for a shot (crop and placement; FNV-1a over a fixed byte layout
// that analyze-cues.py reproduces). Two shots with the same applied picture have the same fingerprint.
uint32_t cue_shot_hash(const struct cue_shot *shot);

// {"id":..,"source":"..","shots":[{"start":..,"crop":[..]|null,"placement":{..}|null},..]} with exact doubles.
void cue_sheet_write_json(FILE *f, const struct cue_sheet *s);

// ---- the store -------------------------------------------------------------------------------------------

struct cue_store {
    _Atomic(const struct cue_sheet *) current;
    atomic_flag lock;                     // guards all[] / n (publishing is rare)
    const struct cue_sheet *all[CUE_MAX_VERSIONS];
    size_t n;
};

// Starts with the default sheet (version 0) published.
bool cue_store_init(struct cue_store *st);
// Frees every version.
void cue_store_destroy(struct cue_store *st);
// Publishes `sheet` as the current version. Its version must exceed the current one. On success the store owns the
// sheet; on failure the caller still does.
bool cue_store_publish(struct cue_store *st, struct cue_sheet *sheet, char *err, size_t err_size);
// The newest version (acquire load: safe from any thread).
const struct cue_sheet *cue_store_current(struct cue_store *st);
// A published version by number, or NULL.
const struct cue_sheet *cue_store_find(struct cue_store *st, int version);
size_t cue_store_count(struct cue_store *st);
