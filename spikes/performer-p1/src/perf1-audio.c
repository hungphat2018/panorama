// The sound: open, close, the thread and the engine's notes. See perf1-audio.h.
#include "perf1-audio-priv.h"

#include <avrt.h>
#include <math.h>
#include <objbase.h>

#define NULL_SINK_RATE 48000
#define NULL_SINK_CHANNELS 2

static struct audio *g_audio;      // the engine's note callback carries no pointer to it

// ---- the stream: halt and run --------------------------------------------------------------------------------------

void audio_halt(struct audio *a)
{
    if (a->sink->halt)
        a->sink->halt(a->sink);
    a->running = false;
    a->written = 0;
    devclock_reset(&a->dc, a->sink->rate);
    seglog_clear(&a->segs);
}

// Silence in the buffer first, so the stream starts with a known fill and the device position starts at frame 0.
void audio_run(struct audio *a)
{
    struct audio_sink *s = a->sink;
    uint32_t n = a->ahead_idle;
    float *buf = s->lock(s, n);
    if (buf) {
        memset(buf, 0, (size_t) n * (size_t) s->channels * sizeof(float));
        s->unlock(s, n);
        a->written = n;
    }
    a->running = s->start(s);
}

// ---- notes from the engine -----------------------------------------------------------------------------------------

void audio_engine_note(void *user, const struct engine_note *n)
{
    struct audio *a = g_audio;
    (void) user;
    if (!a)
        return;
    EnterCriticalSection(&a->cs);
    if (a->n_notes < AUDIO_NOTES)
        a->notes[a->n_notes++] = *n;
    else
        a->dropped_notes++;
    LeaveCriticalSection(&a->cs);
    SetEvent(a->cmd_event);
}

static void handle_note(struct audio *a, const struct engine_note *n)
{
    switch (n->kind) {
    case NOTE_SEEK:
        audio_place_flush(a, n->now, true);
        alog_row(a, "seek", n->now, n->t, 0.0, 0.0, n->gen, n->hold ? "hold" : "");
        a->gen = n->gen;
        a->hold = n->hold;
        a->ep.valid = false;
        audio_src_seek(a->src, n->gen, n->t);
        a->mode = AM_IDLE;              // silence from the next block on
        break;
    case NOTE_PAUSE:
        audio_place_flush(a, n->now, true);
        alog_row(a, "pause", n->now, n->t, 0.0, 0.0, 0, "");
        a->ep.valid = false;
        a->mode = AM_IDLE;
        break;
    case NOTE_HOLD:
        a->hold = n->hold;
        alog_row(a, "hold", n->now, 0.0, 0.0, 0.0, n->hold, "");
        if (!a->hold && a->ep.valid && a->mode == AM_IDLE)
            a->mode = AM_ARMED;
        break;
    case NOTE_EPOCH:
        a->gen = n->gen;
        a->ep.valid = true;
        a->ep.no = n->epoch;
        a->ep.j0 = n->j0;
        a->ep.origin = n->t;
        a->ep.step_s = n->step_s;
        audio_epoch_begin(a);
        a->mode = a->hold ? AM_IDLE : AM_ARMED;     // a seek that lands paused: the picture is shown, the sound stays silent
        break;
    }
}

static void drain_notes(struct audio *a)
{
    struct engine_note batch[AUDIO_NOTES];
    int n;
    EnterCriticalSection(&a->cs);
    n = a->n_notes;
    memcpy(batch, a->notes, (size_t) n * sizeof(batch[0]));
    a->n_notes = 0;
    LeaveCriticalSection(&a->cs);
    for (int i = 0; i < n; i++)
        handle_note(a, &batch[i]);
}

// ---- the thread ----------------------------------------------------------------------------------------------------

static bool open_parts(struct audio *a)
{
    if (a->null_sink) {
        a->sink = audio_sink_null_open(NULL_SINK_RATE, NULL_SINK_CHANNELS, a->drift_ppm, a->null_latency_ms);
        if (!a->sink)
            snprintf(a->err, sizeof(a->err), "cannot create the stand-in sink");
    } else {
        a->sink = audio_sink_wasapi_open(a->volume, a->err, sizeof(a->err));
    }
    if (!a->sink)
        return false;
    a->pipe = audio_pipe_create(a->src, a->sink->rate, a->sink->channels, a->sink->chmask);
    if (!a->pipe || !audio_src_start(a->src)) {
        snprintf(a->err, sizeof(a->err), "cannot start the audio decoder");
        return false;
    }
    a->ahead = a->sink->period * 3 < a->sink->capacity ? a->sink->period * 3 : a->sink->capacity;
    a->ahead_idle = a->sink->period * 3 / 2 < a->ahead ? a->sink->period * 3 / 2 : a->ahead;
    snprintf(a->sink_kind, sizeof(a->sink_kind), "%s", a->sink->kind);
    snprintf(a->sink_name, sizeof(a->sink_name), "%s", a->sink->name);
    for (char *c = a->sink_name; *c; c++)
        if (*c == ',' || *c == ';')
            *c = ' ';
    a->rate = a->sink->rate;
    a->channels = a->sink->channels;
    a->period = a->sink->period;
    a->capacity = a->sink->capacity;
    a->sink_latency_s = a->sink->stream_latency_s;
    devclock_reset(&a->dc, a->sink->rate);
    a->mode = AM_IDLE;
    audio_run(a);
    if (!a->running) {
        snprintf(a->err, sizeof(a->err), "the output stream did not start");
        return false;
    }
    return true;
}

static DWORD WINAPI audio_thread(LPVOID arg)
{
    struct audio *a = arg;
    CoInitializeEx(NULL, COINIT_MULTITHREADED);
    DWORD task = 0;
    HANDLE mmcss = NULL;
    a->opened = open_parts(a);
    if (a->opened) {
        // Real output: the multimedia scheduler's audio classes. The stand-in (tests, possibly beside a live session)
        // only gets a modest boost.
        if (a->null_sink) {
            SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_ABOVE_NORMAL);
        } else {
            mmcss = AvSetMmThreadCharacteristicsW(L"Pro Audio", &task);
            if (!mmcss)
                mmcss = AvSetMmThreadCharacteristicsW(L"Audio", &task);
        }
        a->t_open = now_s();
        a->sec_t0 = a->t_open;
        alog_info(a);
    }
    SetEvent(a->ready_event);
    HANDLE h[2];
    if (a->opened) {
        h[0] = a->sink->wake;
        h[1] = a->cmd_event;
    }
    while (a->opened && !atomic_load(&a->exit_thread)) {
        DWORD r = WaitForMultipleObjects(2, h, FALSE, 50);
        if (r == WAIT_OBJECT_0)
            a->sink->on_wake(a->sink);
        drain_notes(a);
        audio_service(a);
    }
    if (a->opened) {
        audio_place_flush(a, now_s(), true);
        audio_halt(a);
    }
    audio_sink_destroy(&a->sink);   // its COM objects belong to this thread's apartment
    if (mmcss)
        AvRevertMmThreadCharacteristics(mmcss);
    CoUninitialize();
    return 0;
}

// ---- open / close --------------------------------------------------------------------------------------------------

struct audio *audio_open(const struct cfg *cfg)
{
    if (cfg->no_audio) {
        printf("audio: off (--no-audio)\n");
        return NULL;
    }
    char err[240] = "";
    struct audio_src *src = audio_src_open(cfg->file, cfg->audio_track, err, sizeof(err));
    if (!src) {
        printf("audio: no sound: %s\n", err);
        return NULL;
    }
    struct audio *a = calloc(1, sizeof(*a));
    if (!a) {
        audio_src_close(&src);
        return NULL;
    }
    a->src = src;
    a->null_sink = cfg->audio_null;
    a->volume = cfg->audio_volume;
    a->offset_s = cfg->audio_offset_ms * 1e-3;
    a->drift_ppm = cfg->audio_null_drift_ppm;
    a->null_latency_ms = cfg->audio_null_latency_ms;
    InitializeCriticalSection(&a->cs);
    a->cmd_event = CreateEventW(NULL, FALSE, FALSE, NULL);
    a->ready_event = CreateEventW(NULL, FALSE, FALSE, NULL);
    const struct audio_src_info *in = audio_src_info(src);
    printf("audio: track %d of %d, stream %d: %s, %d Hz, %d ch (%s)\n", in->track, in->n_tracks, in->stream, in->codec, in->rate,
           in->channels, in->layout);
    g_audio = a;
    a->thread = CreateThread(NULL, 0, audio_thread, a, 0, NULL);
    if (!a->thread || WaitForSingleObject(a->ready_event, 5000) != WAIT_OBJECT_0 || !a->opened) {
        printf("audio: no sound: %s\n", a->err[0] ? a->err : "the output did not open in time");
        audio_close(&a, NULL);
        return NULL;
    }
    printf("audio: %s \"%s\", %d Hz, %d ch, buffer %u frames, period %u, stream latency %.1f ms\n", a->sink->kind, a->sink->name,
           a->sink->rate, a->sink->channels, a->sink->capacity, a->sink->period, a->sink->stream_latency_s * 1e3);
    return a;
}

double audio_start_lead_s(struct audio *a, double wait_s)
{
    double until = now_s() + wait_s, lead;
    while ((lead = atomic_load(&a->lead_s)) <= 0.0 && now_s() < until)
        Sleep(5);
    return lead;
}

void audio_close(struct audio **ap, const char *dir)
{
    struct audio *a = *ap;
    if (!a)
        return;
    g_audio = NULL;
    atomic_store(&a->exit_thread, true);
    if (a->thread) {
        SetEvent(a->cmd_event);
        WaitForSingleObject(a->thread, 5000);
        CloseHandle(a->thread);
    }
    if (a->opened && dir) {
        alog_write(a, dir);
        alog_summary(a, dir);
    }
    audio_pipe_destroy(&a->pipe);
    audio_sink_destroy(&a->sink);
    audio_src_close(&a->src);
    CloseHandle(a->cmd_event);
    CloseHandle(a->ready_event);
    DeleteCriticalSection(&a->cs);
    samples_free(&a->errs_ms);
    free(a->log);
    free(a);
    *ap = NULL;
}

void audio_sink_destroy(struct audio_sink **s)
{
    if (*s) {
        (*s)->close(*s);
        free(*s);
        *s = NULL;
    }
}
