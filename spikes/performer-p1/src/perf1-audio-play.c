// One wake-up of the audio thread: read the device clock, measure the sound against the film clock, correct, place the first
// sample of an epoch, fill the device buffer. See perf1-audio.h for the model.
#include "perf1-audio-priv.h"
#include "perf1-app.h"

#include <math.h>
#include <string.h>

// ---- the film clock as the sound sees it ---------------------------------------------------------------------------

static bool master_grid(row_vgrid *g)
{
    vclock_line(&A.mon[A.master].clock, &g->base, &g->period);
    return g->period > 0.0;
}

// Host time at which master refresh j lights the aim row (the engine's reference for the film clock).
static double light_time(int64_t j)
{
    row_vgrid g;
    if (!master_grid(&g))
        return NAN;
    return rowmon_light_at(&A.mon[A.master].row, &g, j, A.aim_mm);
}

// Film time at host time t: the epoch's origin at J0's light, then step_s per master refresh. The re-aim shift is not
// included: the sound stays on the unshifted clock.
static double film_at(const struct audio *a, double t)
{
    row_vgrid g;
    if (!a->ep.valid || !master_grid(&g))
        return NAN;
    double l0 = rowmon_light_at(&A.mon[A.master].row, &g, a->ep.j0, A.aim_mm);
    return a->ep.origin + (t - l0) / g.period * a->ep.step_s;
}

void audio_epoch_begin(struct audio *a)
{
    row_vgrid g;
    double speed = master_grid(&g) ? a->ep.step_s / g.period : 1.0;
    audio_pipe_begin(a->pipe, a->gen, a->ep.origin, speed);
    seglog_clear(&a->segs);     // what is still queued from the epoch before belongs to another film time
    sync_ctl_reset(&a->ctl, true);
    a->last_ctl_s = 0.0;
    a->t_next_film = a->ep.origin;
    a->far_count = 0;
    a->place.pending = false;
}

// ---- measuring and correcting --------------------------------------------------------------------------------------

// The error (sound minus film) at host time t, in seconds; false when no sound of the file is sounding at t.
static bool measure(struct audio *a, double t, double *e, double *film)
{
    double x = devclock_frame_at(&a->dc, t), sounding;
    if (!seglog_film_at(&a->segs, x, &sounding))
        return false;
    double f = film_at(a, t - a->offset_s);
    if (isnan(f))
        return false;
    *e = sounding - f;
    *film = f;
    return true;
}

// Moves the sound onto the film clock: the next frame written sounds at devclock_time_of(written).
static void resync(struct audio *a, double host_s, double e)
{
    double f_w = film_at(a, devclock_time_of(&a->dc, (double) a->written) - a->offset_s);
    if (isnan(f_w))
        return;
    double delta = f_w - a->t_next_film;    // + : the sound must jump forward
    if (delta > 0.0)
        audio_pipe_skip(a->pipe, delta);
    else
        audio_pipe_silence(a->pipe, (int64_t) llround(-delta / audio_pipe_film_per_frame(a->pipe)));
    a->resyncs++;
    alog_row(a, "resync", host_s, f_w, e * 1e3, a->ctl.corr * 1e6, 0, "%s_ms=%.2f", delta > 0.0 ? "skip" : "silence", fabs(delta) * 1e3);
    sync_ctl_reset(&a->ctl, true);
    a->far_count = 0;
}

static void control(struct audio *a, double host_s)
{
    double e, film;
    if (a->mode != AM_PLAYING || !devclock_valid(&a->dc) || !measure(a, host_s, &e, &film))
        return;
    double period_s = (double) a->sink->period / (double) a->sink->rate;
    double dt = a->last_ctl_s > 0.0 ? host_s - a->last_ctl_s : period_s;
    if (dt <= 0.0 || dt > 0.25)
        dt = period_s;
    a->last_ctl_s = host_s;
    double corr = sync_ctl_update(&a->ctl, e, dt);
    audio_pipe_set_comp(a->pipe, corr);
    if (fabs(corr) > a->comp_max)
        a->comp_max = fabs(corr);
    alog_row(a, "err", host_s, film, e * 1e3, corr * 1e6, 0, NULL);
    samples_push(&a->errs_ms, fabs(e) * 1e3);
    a->sec_n++;
    a->sec_sum += e * 1e3;
    if (fabs(e) * 1e3 > a->sec_max)
        a->sec_max = fabs(e) * 1e3;
    if (fabs(e) > HARD_LIMIT_S) {
        if (++a->far_count >= HARD_LIMIT_SAMPLES)
            resync(a, host_s, e);
    } else {
        a->far_count = 0;
    }
}

// The first sample's placement as best known half a second later (or when the epoch ends first): the error it really had.
void audio_place_flush(struct audio *a, double host_s, bool force)
{
    if (!a->place.pending || (!force && host_s < a->place.t_check))
        return;
    a->place.pending = false;
    if (!devclock_valid(&a->dc))
        return;
    double t_first = devclock_time_of(&a->dc, (double) a->place.i_first);
    double f = film_at(a, t_first - a->offset_s);
    if (!isnan(f))
        alog_row(a, "epoch", a->place.t_target, a->place.film_first, (a->place.film_first - f) * 1e3, a->ctl.corr * 1e6, 0,
                 "first_sound_ms=%.2f", (t_first - a->place.t_target) * 1e3);
}

static void log_second(struct audio *a, double host_s)
{
    if (host_s < a->sec_t0 + 1.0)
        return;
    if (a->sec_n) {
        double queued = (double) a->written - devclock_frame_at(&a->dc, host_s);
        alog_row(a, "sec", host_s, a->t_next_film, a->sec_sum / a->sec_n, a->ctl.corr * 1e6, a->sec_n, "max_abs_ms=%.3f;ahead_ms=%.1f",
                 a->sec_max, queued / (double) a->sink->rate * 1e3);
    }
    a->sec_t0 = host_s;
    a->sec_n = 0;
    a->sec_sum = a->sec_max = 0.0;
}

// ---- filling the device buffer -------------------------------------------------------------------------------------

// Notes a pull's pieces in the write log and in the counters. `at` is the device frame of the first frame written.
static void note_pieces(struct audio *a, const struct pipe_result *res, int64_t at, double host_s)
{
    for (int i = 0; i < res->n_pieces; i++) {
        const struct pipe_piece *p = &res->piece[i];
        if (p->data) {
            seglog_add(&a->segs, at, p->n, true, p->t0, p->t1);
            a->t_next_film = p->t1;
            if (!a->place.pending && a->place.i_first < 0) {
                a->place.pending = true;
                a->place.i_first = at;
                a->place.film_first = p->t0;
                a->place.t_check = host_s + 0.5;
            }
        }
        at += p->n;
    }
    if (res->underrun_frames > 0) {
        a->underruns++;
        alog_row(a, "underrun", host_s, a->t_next_film, 0.0, 0.0, res->underrun_frames, "decoder");
    }
    if (res->ended && a->mode == AM_PLAYING) {
        a->mode = AM_ENDED;
        alog_row(a, "end", host_s, a->t_next_film, 0.0, 0.0, 0, "the file has no more sound");
    }
}

// How many silence frames to write before the first sample of the epoch, if the epoch can start in this block; -1 to wait.
// Sets *late_s when the moment has passed already (the head of the sound is skipped instead).
static int start_silence(struct audio *a, int n, double *target, double *late_s)
{
    *late_s = 0.0;
    if (!audio_pipe_ready(a->pipe) || !devclock_valid(&a->dc))
        return -1;
    double l0 = light_time(a->ep.j0);
    if (isnan(l0))
        return -1;
    *target = l0 + a->offset_s;
    double need = devclock_frame_at(&a->dc, *target) - (double) a->written; // frames until the sound for `origin` is due
    if (need >= (double) n - 0.5)
        return -1;
    if (need < -0.5)
        *late_s = -need * audio_pipe_film_per_frame(a->pipe);
    return need > 0.0 ? (int) llround(need) : 0;
}

static void fill(struct audio *a, int n, double host_s)
{
    float *buf = a->sink->lock(a->sink, (uint32_t) n);
    if (!buf)
        return; // the device refused; the next wake-up tries again
    const int ch = a->sink->channels;
    int sil = n;
    struct pipe_result res = { 0 };
    if (a->mode == AM_ARMED) {
        double target, late_s;
        sil = start_silence(a, n, &target, &late_s);
        if (sil < 0) {
            sil = n;
        } else {
            if (late_s > 0.0)
                audio_pipe_skip(a->pipe, late_s);
            a->mode = AM_PLAYING;
            a->epochs_played++;
            a->place.i_first = -1;
            a->place.t_target = target;
            alog_row(a, "start", target, a->ep.origin, 0.0, 0.0, 0, "silence_ms=%.2f;skipped_ms=%.2f",
                     (double) sil / a->sink->rate * 1e3, late_s * 1e3);
        }
    } else if (a->mode == AM_PLAYING) {
        sil = 0;
    }
    if (sil > 0)
        memset(buf, 0, (size_t) sil * (size_t) ch * sizeof(float));
    if (sil < n) {
        audio_pipe_pull(a->pipe, buf + (size_t) sil * (size_t) ch, n - sil, &res);
        note_pieces(a, &res, a->written + sil, host_s);
    }
    if (a->sink->unlock(a->sink, (uint32_t) n))
        a->written += n;
}

void audio_service(struct audio *a)
{
    if (!a->running)
        return;
    struct audio_sink *s = a->sink;
    uint64_t pos;
    double host_s;
    if (!s->position(s, &pos, &host_s)) {
        if (++a->pos_failures == 50) // a removed or reconfigured device: there is no hot-plug handling, the film plays on silently
            alog_row(a, "device", now_s(), a->t_next_film, 0.0, 0.0, 0, "the sink stopped answering");
        return;
    }
    a->pos_failures = 0;
    devclock_add(&a->dc, pos, host_s);
    if ((int64_t) pos > a->written + 2) { // the device played past everything written: it starved
        if (a->mode == AM_PLAYING) {
            a->underruns++;
            alog_row(a, "underrun", host_s, a->t_next_film, 0.0, 0.0, (int) ((int64_t) pos - a->written), "device");
        }
        a->written = (int64_t) pos;
    }
    control(a, host_s);
    audio_place_flush(a, host_s, false);
    log_second(a, host_s);
    uint32_t pad = s->padding(s);
    uint32_t want = a->mode == AM_PLAYING ? a->ahead : a->ahead_idle;
    if (want < pad + s->period / 2)
        return;
    fill(a, (int) (want - pad), host_s);
}
