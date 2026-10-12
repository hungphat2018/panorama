// The stand-in sink for --audio-null, see perf1-audio-sink.h. Its "device" clock is the host clock times (1 + drift):
// frames played = rate * (1 + drift) * seconds since start. The buffer is four 10 ms periods, like a shared-mode stream.
#include "perf1-audio-sink.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

double now_s(void);   // QueryPerformanceCounter, seconds (perf1-stats.c)

struct null_dev {
    double drift;
    uint64_t lag_frames;        // what the device has taken from the buffer but not yet sounded (a driver's own latency)
    bool running;
    double t_start;             // host time of start()
    uint64_t next_tick;         // the next period boundary to wake at
    uint64_t written;           // frames handed to unlock()
    float *scratch;             // what lock() returns
    double period_s;
};

// Frames taken from the buffer by now.
static uint64_t consumed(const struct null_dev *d, const struct audio_sink *s, double now)
{
    if (!d->running)
        return 0;
    double f = (now - d->t_start) * (double) s->rate * (1.0 + d->drift);
    return f > 0.0 ? (uint64_t) f : 0;
}

// Frames sounded by now: the clock the position reports.
static uint64_t played(const struct null_dev *d, const struct audio_sink *s, double now)
{
    uint64_t c = consumed(d, s, now);
    return c > d->lag_frames ? c - d->lag_frames : 0;
}

static void arm(struct audio_sink *s)
{
    struct null_dev *d = s->impl;
    double due = d->t_start + (double) d->next_tick * d->period_s / (1.0 + d->drift) - now_s();
    if (due < 1e-4)
        due = 1e-4;
    LARGE_INTEGER t;
    t.QuadPart = -(LONGLONG) (due * 1e7);
    SetWaitableTimer(s->wake, &t, 0, NULL, NULL, FALSE);
}

static bool null_start(struct audio_sink *s)
{
    struct null_dev *d = s->impl;
    d->running = true;
    d->t_start = now_s();
    d->next_tick = 1;
    arm(s);
    return true;
}

static bool null_halt(struct audio_sink *s)
{
    struct null_dev *d = s->impl;
    d->running = false;
    d->written = 0;
    CancelWaitableTimer(s->wake);
    return true;
}

static uint32_t null_padding(struct audio_sink *s)
{
    struct null_dev *d = s->impl;
    uint64_t p = consumed(d, s, now_s());
    return d->written > p ? (uint32_t) (d->written - p) : 0;
}

static float *null_lock(struct audio_sink *s, uint32_t frames)
{
    struct null_dev *d = s->impl;
    if (frames > s->capacity - null_padding(s))
        return NULL;
    return d->scratch;
}

static bool null_unlock(struct audio_sink *s, uint32_t frames)
{
    struct null_dev *d = s->impl;
    d->written += frames;
    return true;
}

static bool null_position(struct audio_sink *s, uint64_t *frames, double *host_s)
{
    struct null_dev *d = s->impl;
    double now = now_s();
    *frames = played(d, s, now);
    *host_s = now;
    return true;
}

static void null_on_wake(struct audio_sink *s)
{
    struct null_dev *d = s->impl;
    double now = now_s();
    // the next boundary after now (a late thread skips the periods it missed)
    uint64_t due = (uint64_t) ((now - d->t_start) * (1.0 + d->drift) / d->period_s) + 1;
    d->next_tick = due > d->next_tick ? due : d->next_tick + 1;
    arm(s);
}

static void null_close(struct audio_sink *s)
{
    struct null_dev *d = s->impl;
    CancelWaitableTimer(s->wake);
    CloseHandle(s->wake);
    free(d->scratch);
    free(d);
}

struct audio_sink *audio_sink_null_open(int rate, int channels, double drift_ppm, double latency_ms)
{
    struct audio_sink *s = calloc(1, sizeof(*s));
    struct null_dev *d = calloc(1, sizeof(*d));
    if (!s || !d)
        goto fail;
    s->kind = "null";
    snprintf(s->name, sizeof(s->name), "no device (stand-in clock, drift %+.1f ppm, latency %.1f ms)", drift_ppm, latency_ms);
    s->rate = rate;
    s->channels = channels;
    s->period = (uint32_t) (rate / 100);
    s->capacity = 4 * s->period;
    d->drift = drift_ppm * 1e-6;
    d->lag_frames = (uint64_t) llround(latency_ms * 1e-3 * (double) rate);
    s->stream_latency_s = latency_ms * 1e-3;
    d->period_s = (double) s->period / (double) rate;
    d->scratch = malloc((size_t) s->capacity * (size_t) channels * sizeof(float));
    s->wake = CreateWaitableTimerExW(NULL, NULL, CREATE_WAITABLE_TIMER_HIGH_RESOLUTION, TIMER_ALL_ACCESS);
    if (!s->wake)
        s->wake = CreateWaitableTimerW(NULL, FALSE, NULL);
    if (!d->scratch || !s->wake)
        goto fail;
    s->start = null_start;
    s->halt = null_halt;
    s->padding = null_padding;
    s->lock = null_lock;
    s->unlock = null_unlock;
    s->position = null_position;
    s->on_wake = null_on_wake;
    s->close = null_close;
    s->impl = d;
    return s;
fail:
    if (d) {
        free(d->scratch);
        free(d);
    }
    if (s && s->wake)
        CloseHandle(s->wake);
    free(s);
    return NULL;
}
