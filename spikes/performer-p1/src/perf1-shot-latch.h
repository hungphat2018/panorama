// The per-shot latch, shared by every monitor.
//
// The cue for a frame is looked up by the frame's own timestamp in the version its shot started with. The first
// monitor that prepares a frame of a shot nobody has shown yet captures the store's newest version; every other
// refresh of every monitor that shows a frame of that shot finds the entry and gets the same version, however many
// versions the planner has published meanwhile. A new version therefore reaches only shots that have not started.
//
// A monitor can be a refresh or two behind another (it switches to a new shot on its own nearest refresh), so the
// latch keeps the last few shots: a lagging monitor still drawing the previous shot's last frame must not see the
// newer version. A break (seek, pause, resume) resets the latch, and the next shot captures afresh: that is where
// presentation may change.
//
// Entry spans never overlap. A new entry starts where the previous one ended, even when the new version's shot
// begins earlier (a planner that moved a boundary), so a frame that already belongs to a latched shot stays there.
#pragma once

#include "perf1-cue-sheet.h"

#define LATCH_RING 16

struct latch_entry {
    const struct cue_sheet *sheet;   // the version this shot started with
    int shot;                        // its index in that version
    double start, end;               // frame timestamps covered: [start, end)
    uint32_t id;                     // capture number within the run, never reused
};

struct shot_latch {
    struct latch_entry ring[LATCH_RING];
    int n;                           // valid entries
    int head;                        // index of the newest
    uint32_t next_id;
    uint32_t captures, stale_lookups;
    struct latch_entry scratch;      // answer for a stale lookup (not retained)
};

struct latch_result {
    const struct latch_entry *entry;
    bool captured;                   // this call captured the entry
    bool stale;                      // pts is older than everything retained: entry is a one-off, not latched
};

void shot_latch_init(struct shot_latch *l);
// Forgets every latched shot (ids keep counting).
void shot_latch_reset(struct shot_latch *l);
// The latched shot for a frame with timestamp pts, capturing the store's current version if the shot is new.
struct latch_result shot_latch_resolve(struct shot_latch *l, struct cue_store *st, double pts);
