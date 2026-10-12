// The performer's decision core: film clock, frame picking, the monitors' switch refreshes, the per-shot latch and
// the break rule. No window, no GPU, no OS timer: every time comes in as an argument and every grid through a host
// callback, so the same code runs in perf1 and in the deterministic simulation (latch-sim.c).
//
// Film clock. The master monitor's refresh index j is the film clock. An epoch starts on master refresh J0 with the
// frame whose timestamp is `origin`; film time at refresh j is origin + (j - J0 - S) * step, where S (-1, 0 or +1)
// is the re-aim shift. Frame n is *due* on the first refresh whose film time plus half a master refresh reaches its
// timestamp: the last frame due, picked from the frames' own timestamps (so variable frame rates work).
//
// Schedule. Frame starts are decided in order, a little ahead of the refresh they matter for. For each frame the
// master's start refresh j is fixed first (possibly re-aimed at a cut), then each follower's start refresh s: the one
// whose picture lights the aim row nearest to the moment the master lights it (row-timing.h), kept per alignment
// class until another refresh is better by more than eta and the frame is a cut. A monitor at its refresh k shows the
// last frame whose start on that monitor is <= k.
//
// Breaks. Seek, pause and resume stop all presenting. A seek (resume = seek to the held frame) asks the frame supply
// (the decoder) to park, resets the queues and the frame index, waits until the first frames of the new generation
// are queued, then restarts the clock on one master refresh K far enough ahead for every monitor's render lead, in the
// alignment class that suits the followers best; each follower starts on its own refresh for K.
//
// Threads. engine_service / engine_tick run on the render thread. engine_post, engine_note_*, engine_status and the
// frame index may be used from other threads; the supply writes `sync`.
#pragma once

#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>

#include "perf1-cue-sheet.h"
#include "perf1-shot-latch.h"
#include "row-timing.h"

#define ENGINE_MAX_MON 4
#define ENGINE_MAX_Q 64
#define ENGINE_FS_RING 1024

// ---- timestamps of the frames the supply has queued -------------------------------------------------------------

struct frame_index {
    CRITICAL_SECTION cs;
    double *pts;
    size_t n, cap;
    int gen;
    uint32_t rejected_stale, rejected_order;
};

void findex_init(struct frame_index *f);
void findex_destroy(struct frame_index *f);
// Empties the index and makes `gen` the generation it accepts.
void findex_reset(struct frame_index *f, int gen);
// Appends a frame of generation gen (queued in every monitor's queue). Frames of another generation and frames
// whose timestamp does not exceed the last one are refused (false).
bool findex_append(struct frame_index *f, int gen, double pts);
size_t findex_count(struct frame_index *f);
bool findex_get(struct frame_index *f, size_t ord, double *pts);
// Copy of all timestamps; the caller frees it.
double *findex_copy(struct frame_index *f, size_t *n);

// ---- the handshake with the frame supply (the decoder, or the simulation) ---------------------------------------

struct supply_sync {
    atomic_int want_gen;            // engine -> supply: seek to seek_target for this generation
    _Atomic double seek_target;
    atomic_int parked_gen;          // supply -> engine: not pushing frames of an older generation any more
    atomic_int reset_gen;           // engine -> supply: queues and index are reset for this generation
    atomic_int ready_gen;           // supply -> engine: the first frames of this generation are queued
    _Atomic double ready_pts;       // timestamp of the first of them (NaN: the seek produced no frame)
    _Atomic double ready_time;      // when they were queued (host clock)
};

// ---- host interface ---------------------------------------------------------------------------------------------

// What the engine tells the sound (optional): the breaks and the start of each epoch, in the order they happen.
enum engine_note_kind {
    NOTE_SEEK,      // a seek (or the seek a resume is) begins: everything heard so far stops; t = film time sought
    NOTE_PAUSE,     // the film holds (a pause, or a seek that found nothing)
    NOTE_HOLD,      // while a seek is in flight: `hold` changed (true: the frame it lands on is shown, then the film holds)
    NOTE_EPOCH,     // the clock restarts: master refresh j0 shows film time t (the first frame), at step_s per master refresh
};

struct engine_note {
    enum engine_note_kind kind;
    int gen, epoch;
    double now;                     // host clock when the engine decided it
    double t;
    int64_t j0;
    double step_s;
    bool hold;
};

struct engine_host {
    void *user;
    // Monitor m's refresh grid now: refresh k wakes at base + k * period (host clock, seconds).
    row_vgrid (*grid)(void *user, int m);
    // Reset every monitor's frame queue (render thread, supply parked).
    void (*reset_queues)(void *user);
    // Optional, may be NULL (the simulation): a break or an epoch start, called on the render thread. Must not block.
    void (*note)(void *user, const struct engine_note *n);
};

// Deliberate defects for the negative controls of the headless test: each one breaks a rule on purpose so the checker
// can be seen to catch it. Honoured only when the file is compiled with ENGINE_FAULTS (the simulation, never perf1).
#define FAULT_NO_LATCH          0x01u  // every refresh captures the newest version afresh
#define FAULT_LOOKUP_BY_CLOCK   0x02u  // the cue is looked up by film time instead of the drawn frame's timestamp
#define FAULT_KEEP_LATCH        0x04u  // a break leaves the latched shots as they were
#define FAULT_PL_QUEUE_PICK     0x08u  // frames picked like the old queue_position shortcut
#define FAULT_INDEPENDENT_START 0x10u  // each follower restarts on its own earliest refresh, not the master's
#define FAULT_NO_HOLD           0x20u  // monitors keep presenting the old epoch while a seek is in flight
#define FAULT_NO_REAIM_GUARD    0x40u  // a re-aim may shorten the frame before the cut below one refresh

struct engine_cfg {
    int n_mon, master;
    const row_mon *row[ENGINE_MAX_MON];
    double aim_mm;                  // where the screens agree
    double frame_dur_s;             // nominal frame duration of the stream
    double step_s;                  // film seconds per master refresh
    bool own_mode;                  // followers switch on their first refresh at or after the master's refresh time
    bool hysteresis;                // keep a follower's refresh per alignment class (moves only at cuts)
    bool reaim;                     // re-aim the master's frame changes at cuts
    double eta_s;                   // hysteresis and re-aim threshold
    double svc_margin_s;            // a monitor prepares the first refresh at least this far ahead
    double decide_after_wake_s;     // a render thread decides just after the previous wake-up
    double restart_margin_s;        // render time reserved for the first frame of an epoch
    int horizon;                    // frame starts are decided this many master refreshes ahead
    int lead_refreshes[ENGINE_MAX_MON]; // render lead of each monitor in its own refreshes
    unsigned faults;                // ENGINE_FAULTS builds only: deliberate defects for the negative controls
};

void engine_cfg_defaults(struct engine_cfg *c);

// ---- results of one refresh -------------------------------------------------------------------------------------

enum svc_action { SVC_HOLD, SVC_PRESENT };

struct svc_out {
    enum svc_action action;
    int64_t k;                      // the refresh being prepared (this monitor's index)
    double target;                  // its wake-up time
    double retry_at;                // SVC_HOLD: a useful time for the next service
    long ord;                       // frame ordinal in the epoch
    double pts;                     // its timestamp
    int64_t j;                      // the master refresh whose picture this is
    int epoch;
    bool first_of_epoch;
    bool captured;                  // the shot was latched on this refresh
    bool stale;                     // latch lookup older than the ring remembers (a violation)
    const struct latch_entry *shot; // the latched shot: sheet, shot index
    const struct cue_shot *cue;     // its cue
    uint32_t cue_hash;
    long rec;                       // index of this refresh's record in the monitor's log
};

// ---- logs -------------------------------------------------------------------------------------------------------

struct refresh_rec {
    int64_t k, j;
    double svc, target, pts, sstart;
    int32_t ep, ord, vid, sidx, drawn, dec;   // dec: frames the engine had decided when it prepared the refresh
    uint32_t ach, lid;
    uint8_t flags;                  // 1 captured, 2 first refresh of the epoch on this monitor, 4 stale latch
};

struct start_rec {                  // a decided frame start
    int32_t ep, ord;
    int64_t j, s[ENGINE_MAX_MON];
    bool cut;
    int8_t shift;                   // re-aim shift S in force for this frame
};

enum ev_kind { EV_LOAD, EV_LOAD_FAIL, EV_SEEK_CMD, EV_PAUSE, EV_RESUME, EV_PARKED, EV_READY, EV_RESTART, EV_REAIM,
               EV_SEEK_FAILED, EV_STOP };

struct event_rec {                  // meaning of the fields depends on the kind, see engine_write_cuelog
    enum ev_kind kind;
    double t, d0, d1;
    int64_t i0, i1;
    int gen;
    char text[96];
};

struct epoch_rec {
    int ep, gen;
    double origin, step_s, t_plan, t_ready, cost_s;
    int64_t J0, start_k[ENGINE_MAX_MON];
    double *frame_pts;              // timestamps the supply queued during the epoch (filled when it ends)
    size_t n_frames;
};

struct engine_status {
    int state, epoch, gen, version;
    double playhead;
    unsigned swaps, seeks, restarts;
};

// ---- the engine -------------------------------------------------------------------------------------------------

enum eng_state { ES_IDLE, ES_PLAYING, ES_PAUSED, ES_SEEK_PARK, ES_SEEK_FILL };

enum cmd_kind { CMD_SEEK, CMD_PAUSE, CMD_RESUME };
struct engine_cmd { enum cmd_kind kind; double arg, posted; };

struct frame_start {
    long ord;
    double pts;
    int64_t j;
    int64_t s[ENGINE_MAX_MON];
    bool cut;
};

struct follower_state {
    bool active;
    int p, q;                       // q master refreshes carry p of this monitor's refreshes
    struct { bool valid; int64_t j, k; } cls[ENGINE_MAX_Q];
};

struct engine {
    struct engine_cfg cfg;
    struct engine_host host;
    struct cue_store *store;
    struct shot_latch latch;
    struct frame_index findex;
    struct supply_sync sync;

    enum eng_state state;
    int gen, epoch;
    int n_followers;
    bool pause_after_first;
    double held_pts, playhead, break_time;

    // current epoch
    int64_t J0;
    double origin;
    int shift;                      // re-aim shift S
    int64_t start_k[ENGINE_MAX_MON];
    bool started[ENGINE_MAX_MON];
    long decided;
    struct frame_start fs[ENGINE_FS_RING];
    long cursor[ENGINE_MAX_MON];
    struct follower_state fol[ENGINE_MAX_MON];

    // mailbox, status, events (touched by other threads)
    CRITICAL_SECTION cs;
    struct engine_cmd mail[64];
    int n_mail;
    struct engine_status status;
    struct event_rec *events;
    size_t n_events, cap_events;

    // logs (render thread; read after the run)
    struct refresh_rec *rlog[ENGINE_MAX_MON];
    size_t n_rlog[ENGINE_MAX_MON], cap_rlog[ENGINE_MAX_MON];
    bool rlog_truncated;
    bool no_refresh_log;            // clock runs of hours: keep the decided starts, skip the per-refresh records
    struct start_rec *slog;
    size_t n_slog, cap_slog;
    struct epoch_rec *epochs;
    size_t n_epochs, cap_epochs;

    // statistics
    unsigned swaps, seeks, restarts, reaims, moves, forced_moves, class_resyncs, stale_lookups;
};

bool engine_init(struct engine *e, const struct engine_cfg *cfg, const struct engine_host *host, struct cue_store *store);
void engine_destroy(struct engine *e);

// Plans the first epoch: the first frame is `first_pts`, and the supply has queued frames (and will have them in every
// monitor's queue) by `t_ready`. Returns false if the film cannot start (no frames).
bool engine_start(struct engine *e, double now, double first_pts, double t_ready);

// Thread-safe. Seek (arg = film time in seconds), pause, resume. False if the mailbox is full.
bool engine_post(struct engine *e, enum cmd_kind kind, double arg, double now);
// Drains the mailbox and advances the break state machine. Call it often (each service does).
void engine_tick(struct engine *e, double now);

// Decides what monitor m shows on the refresh it prepares at host time `now`, and logs it.
void engine_service(struct engine *e, int m, double now, struct svc_out *out);
// The renderer reports which frame ordinal it actually drew for the record at `rec` (-1: nothing drawn).
void engine_note_drawn(struct engine *e, int m, long rec, int drawn_ord);

// Copies the frame timestamps of the epoch that is running into its record (done at a break and before the log is
// written).
void engine_save_epoch_frames(struct engine *e);

// Thread-safe event notes from the control thread.
void engine_note_load(struct engine *e, double now, int version, bool ok, const char *text);
void engine_note_stop(struct engine *e, double now);

// The film's pace becomes known after the monitors' grids have settled: set it before engine_start.
void engine_set_film(struct engine *e, double step_s, double frame_dur_s);
// Render thread: the master is showing the newest frame the supply has queued.
bool engine_on_last_frame(struct engine *e);

void engine_status(struct engine *e, struct engine_status *out);
double engine_playhead(struct engine *e);
// Alignment of the master's refresh index to a frame: the start refresh of the frame that is due at refresh j.
int64_t engine_nominal_start(const struct engine *e, double pts);

// Writes the cue log (versions, epochs with frame timestamps and decided starts, events, refresh records).
bool engine_write_cuelog(struct engine *e, const char *path, const char *const *mon_names);
