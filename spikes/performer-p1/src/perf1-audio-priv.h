// Private state of the sound, shared by perf1-audio.c, perf1-audio-play.c and perf1-audio-log.c.
#pragma once

#include "perf1-audio.h"
#include "perf1-audio-decode.h"
#include "perf1-audio-pipe.h"
#include "perf1-audio-sink.h"
#include "perf1-audio-sync.h"

#define AUDIO_NOTES 256
#define HARD_LIMIT_S 0.040      // beyond this the sound is moved, not nudged
#define HARD_LIMIT_SAMPLES 3    // ... when this many device periods in a row are beyond it

// The device stream runs from the start to the end of the run, on silence whenever there is no sound to play: a break does
// not stop and restart it (a restarted shared-mode stream reports no position for the first 60 ms or so, the sound of
// the new epoch would start late). What was queued before the break, at most `ahead` frames, is still heard.
enum amode {
    AM_IDLE,                    // silence; no epoch is waiting (paused, seeking, or a seek that lands paused)
    AM_ARMED,                   // an epoch is known; silence until its first sample's device frame
    AM_PLAYING,
    AM_ENDED,                   // the file has no more sound
};

struct audio {
    // configuration
    bool null_sink;
    double volume, offset_s, drift_ppm, null_latency_ms;
    const char *file;
    int track;

    // parts (created by the thread)
    struct audio_src *src;
    struct audio_sink *sink;
    struct audio_pipe *pipe;

    // what the sink is (copied: the sink belongs to the thread and is gone before the log is written)
    char sink_kind[16], sink_name[160];
    int rate, channels;
    uint32_t period, capacity;
    double sink_latency_s;

    // thread and its mailbox
    HANDLE thread, cmd_event, ready_event;
    atomic_bool exit_thread;
    bool opened;
    char err[240];
    CRITICAL_SECTION cs;
    struct engine_note notes[AUDIO_NOTES];
    int n_notes;
    unsigned dropped_notes;

    // state owned by the thread
    enum amode mode;
    bool running;               // the sink is started
    bool hold;                  // a seek in flight lands paused: its epoch is not played
    int gen;
    struct { bool valid; int no; int64_t j0; double origin, step_s; } ep;
    int64_t written;            // frames handed to the sink since halt
    uint32_t ahead;             // frames to keep queued while sound plays
    uint32_t ahead_idle;        // ... while silent: shallow, so a restart announced late can still be placed in time
    struct dev_clock dc;
    struct seg_log segs;
    struct sync_ctl ctl;
    double last_ctl_s;
    double t_next_film;         // film time just after the last sound frame written
    int far_count;
    int pos_failures;           // consecutive failed clock readings
    struct {                    // the first sample of the epoch being played
        bool pending;           // the refined error of the placement is not logged yet
        int64_t i_first;
        double film_first, t_target, t_check;
    } place;

    // statistics and log
    unsigned resyncs, underruns, epochs_played;
    double comp_max;
    double t_open, sec_t0, sec_sum, sec_max;
    int sec_n;
    struct samples errs_ms;
    char *log;
    size_t log_len, log_cap;
    bool log_truncated;
};

// perf1-audio.c
void audio_run(struct audio *a);            // prefill with silence and start the stream
void audio_halt(struct audio *a);           // stop the stream and throw away what is queued (shut-down)
// perf1-audio-play.c
void audio_service(struct audio *a);        // one wake-up
void audio_epoch_begin(struct audio *a);    // the engine announced an epoch: prepare the converter
void audio_place_flush(struct audio *a, double host_s, bool force);   // log the first sample's placement as best known now
// perf1-audio-log.c
void alog_row(struct audio *a, const char *kind, double t, double film_s, double err_ms, double comp_ppm, int n, const char *note, ...);
void alog_info(struct audio *a);
bool alog_write(struct audio *a, const char *dir);
void alog_summary(struct audio *a, const char *dir);
