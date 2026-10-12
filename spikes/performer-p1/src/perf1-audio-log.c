// audio.csv and the summary line. One file per run, rows in the order they happened:
//   kind,t,epoch,film_t,err_ms,comp_ppm,resyncs,underruns,latency_ms,resid_ms,n,note
// t is the host clock in seconds (the engine's clock); epoch is the engine's current epoch; film_t the film time the row is about;
// err_ms is audio minus film (+ = the sound is ahead); comp_ppm the speed correction in force; resyncs and underruns are
// running totals; latency_ms is the device's stream latency plus the queue kept ahead; resid_ms the spread of the device
// clock's readings around their fitted line. Kinds:
//   info      what was opened (note)
//   seek      a seek began (film_t = target, n = generation); pause, hold: the same for those breaks
//   start     the first sample of an epoch is placed (t = when it is due to sound, film_t = its film time)
//   epoch     the same placement as best known 0.5 s later: err_ms = how far off it really was, first_sound_ms in the note
//   err       one error measurement per device period while sound plays (t = host time)
//   sec       once per second: mean error, n measurements, max |error| in the note
//   resync    the sound was moved (err_ms = the error that triggered it, skip_ms or silence_ms in the note)
//   underrun  the device or the decoder ran dry (n = frames)
//   end       the file has no more sound
#include "perf1-audio-priv.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define LOG_MAX_BYTES (96u << 20)

static void put(struct audio *a, const char *text, size_t n)
{
    if (a->log_len + n + 1 > a->log_cap) {
        size_t cap = a->log_cap ? a->log_cap * 2 : (1u << 20);
        while (cap < a->log_len + n + 1)
            cap *= 2;
        if (cap > LOG_MAX_BYTES) {
            a->log_truncated = true;
            return;
        }
        char *p = realloc(a->log, cap);
        if (!p) {
            a->log_truncated = true;
            return;
        }
        a->log = p;
        a->log_cap = cap;
    }
    memcpy(a->log + a->log_len, text, n);
    a->log_len += n;
    a->log[a->log_len] = '\0';
}

void alog_row(struct audio *a, const char *kind, double t, double film_s, double err_ms, double comp_ppm, int n, const char *note, ...)
{
    char text[512];
    char tail[256] = "";
    if (note) {
        va_list ap;
        va_start(ap, note);
        vsnprintf(tail, sizeof(tail), note, ap);
        va_end(ap);
    }
    double latency_ms = a->sink_latency_s * 1e3 + (a->rate ? (double) a->ahead / (double) a->rate * 1e3 : 0.0);
    int len = snprintf(text, sizeof(text), "%s,%.6f,%d,%.6f,%.4f,%.1f,%u,%u,%.2f,%.4f,%d,%s\n", kind, t, a->ep.no, film_s, err_ms,
                       comp_ppm, a->resyncs, a->underruns, latency_ms, a->dc.resid_s * 1e3, n, tail);
    if (len > 0)
        put(a, text, (size_t) (len < (int) sizeof(text) ? len : (int) sizeof(text) - 1));
}

void alog_info(struct audio *a)
{
    alog_row(a, "info", a->t_open, 0.0, 0.0, 0.0, a->rate, "sink=%s;name=%s;channels=%d;period=%u;capacity=%u;stream_latency_ms=%.2f;"
             "offset_ms=%.2f", a->sink_kind, a->sink_name, a->channels, a->period, a->capacity, a->sink_latency_s * 1e3, a->offset_s * 1e3);
}

bool alog_write(struct audio *a, const char *dir)
{
    char path[512];
    snprintf(path, sizeof(path), "%s\\audio.csv", dir);
    FILE *f = fopen(path, "wb");
    if (!f) {
        fprintf(stderr, "perf1: audio: cannot write %s\n", path);
        return false;
    }
    fputs("kind,t,epoch,film_t,err_ms,comp_ppm,resyncs,underruns,latency_ms,resid_ms,n,note\n", f);
    if (a->log_len)
        fwrite(a->log, 1, a->log_len, f);
    if (a->log_truncated)
        fputs("truncated,0,0,0,0,0,0,0,0,0,0,the log reached its size limit\n", f);
    if (a->dropped_notes)
        fprintf(f, "dropped,0,0,0,0,0,0,0,0,0,%u,engine notes dropped\n", a->dropped_notes);
    fclose(f);
    return true;
}

void alog_summary(struct audio *a, const char *dir)
{
    struct summary s;
    summarize(a->errs_ms.v, a->errs_ms.n, &s);
    if (a->errs_ms.n)
        printf("audio: %s, %u epochs played, |error| p50 %.3f ms p99 %.3f ms max %.3f ms over %zu measurements, %u resyncs, "
               "%u underruns, largest correction %.0f ppm; %s\\audio.csv\n", a->sink_kind, a->epochs_played, s.p50, s.p99, s.max,
               s.n, a->resyncs, a->underruns, a->comp_max * 1e6, dir);
    else
        printf("audio: %s, %u epochs played, no sound was measured (%u underruns); %s\\audio.csv\n", a->sink_kind, a->epochs_played,
               a->underruns, dir);
}
