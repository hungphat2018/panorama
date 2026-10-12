// Checks of the sound's decoder, converter and arithmetic against a clip whose audio track is a ramp: sample k holds the value
// k / 2^24 (exact in float), the track starts 0.25 s after the first picture. So the value of any sample tells which film time
// it belongs to (0.25 + k / 48000), and the film time a block claims can be checked against what is in it.
//
//   audio-selftest.exe data\audio-test-ramp\ramp.mkv
//
// Compile: compile.ps1 -Program audio-test. FFmpeg's DLLs must be on PATH to run it (bin\run.cmd does that).
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "src/perf1-audio-decode.h"
#include "src/perf1-audio-pipe.h"
#include "src/perf1-audio-sync.h"

#define RATE 48000
#define TRACK_START 0.25

static int checks, failures;
static double tol_us = 25.0;   // how far a frame's claimed time may be from the time its content says (container rounding)

static void check(bool ok, const char *fmt, ...)
{
    checks++;
    if (ok)
        return;
    failures++;
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "FAIL: ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
}

// The film time a ramp value stands for.
static double film_of_value(double v)
{
    return TRACK_START + v * 16777216.0 / RATE;
}

static double sample_of(const AVFrame *f, int j)
{
    switch (f->format) {
    case AV_SAMPLE_FMT_S32:
    case AV_SAMPLE_FMT_S32P:
        return (double) ((const int32_t *) f->extended_data[0])[j] / 2147483648.0;
    case AV_SAMPLE_FMT_FLT:
    case AV_SAMPLE_FMT_FLTP:
        return (double) ((const float *) f->extended_data[0])[j];
    default:
        return NAN;
    }
}

// ---- decoder ------------------------------------------------------------------------------------------------------

static bool pop_wait(struct audio_src *s, int gen, struct audio_chunk *c)
{
    for (int i = 0; i < 1500; i++) {
        int r = audio_src_pop(s, gen, c);
        if (r == 1)
            return true;
        if (r < 0)
            return false;
        Sleep(2);
    }
    return false;
}

// After a seek to t: the queue must hold the sample at t, every sample must carry the film time its value says, and the
// frames must run on without a gap.
static void check_seek(struct audio_src *s, int gen, double t)
{
    audio_src_seek(s, gen, t);
    struct audio_chunk c;
    double prev_end = NAN;
    bool covered = false;
    int n = 0;
    while (pop_wait(s, gen, &c) && n < 400) {
        n++;
        const AVFrame *f = c.frame;
        double claim_first = c.t0, claim_last = c.t0 + (double) (f->nb_samples - 1) / RATE;
        double v_first = film_of_value(sample_of(f, 0)), v_last = film_of_value(sample_of(f, f->nb_samples - 1));
        check(fabs(v_first - claim_first) < tol_us * 1e-6 && fabs(v_last - claim_last) < tol_us * 1e-6,
              "seek %.3f: frame at %.6f claims %.6f..%.6f but holds %.6f..%.6f", t, c.t0, claim_first, claim_last, v_first, v_last);
        if (!isnan(prev_end))
            check(fabs(c.t0 - prev_end) < tol_us * 1e-6, "seek %.3f: gap before %.6f (previous ended %.6f)", t, c.t0, prev_end);
        prev_end = c.t0 + c.dur;
        if (c.t0 <= t && t < c.t0 + c.dur)
            covered = true;
        av_frame_free(&c.frame);
        if (covered && c.t0 > t + 0.2)
            break;
    }
    check(covered || t < TRACK_START, "seek %.3f: no frame of the queue holds that time", t);
}

static void test_decoder(const char *file)
{
    char err[200];
    struct audio_src *s = audio_src_open(file, -1, err, sizeof(err));
    if (!s) {
        check(false, "cannot open %s: %s", file, err);
        return;
    }
    const struct audio_src_info *in = audio_src_info(s);
    check(in->rate == RATE && in->channels == 1, "the ramp track is %d Hz %d ch", in->rate, in->channels);
    printf("film time 0 is the first picture, at %.6f s of the file's timeline\n", in->video_first);
    audio_src_start(s);
    const double targets[] = { 0.0, 0.1, 0.2499, 0.2501, 3.3, 7.77, 12.5, 19.9, 0.0, 5.0, 5.0 };
    for (size_t i = 0; i < sizeof(targets) / sizeof(targets[0]); i++)
        check_seek(s, (int) i + 1, targets[i]);
    // a seek past the end: nothing to play, and the end is reported
    audio_src_seek(s, 100, 500.0);
    struct audio_chunk c;
    int r = 0;
    for (int i = 0; i < 1500 && r == 0; i++) {
        r = audio_src_pop(s, 100, &c);
        if (r == 0)
            Sleep(2);
    }
    check(r == -1, "a seek past the end reports the end (got %d)", r);
    if (r == 1)
        av_frame_free(&c.frame);
    check(audio_src_pop(s, 99, &c) == 0, "an older generation never returns a frame");
    audio_src_close(&s);
    check(s == NULL, "close clears the pointer");
    char err2[200];
    check(audio_src_open(file, 7, err2, sizeof(err2)) == NULL && err2[0], "a track that does not exist is refused with a reason");
}

// ---- converter ----------------------------------------------------------------------------------------------------

struct pull_stats {
    double worst_us;            // worst difference between a block's claimed film time and what its samples say
    long data_frames, silence_frames;
    double t_first, t_last;     // claimed film time of the first data frame and just after the last
    bool have_first;
    int underruns;
    bool ended;
};

// Pulls `blocks` blocks of 480 frames; checks every 61st data frame's value against the film time the pieces claim. (A resampler's
// gain is not exactly 1: a few parts in 1e5, which a ramp turns into a time error that grows with the time, so the absolute
// checks start early in the track.)
static void pull_blocks(struct audio_pipe *p, int blocks, struct pull_stats *st)
{
    float out[480];
    struct pipe_result res;
    for (int b = 0; b < blocks; b++) {
        audio_pipe_pull(p, out, 480, &res);
        int at = 0;
        for (int i = 0; i < res.n_pieces; i++) {
            const struct pipe_piece *pc = &res.piece[i];
            if (pc->data) {
                if (!st->have_first) {
                    st->t_first = pc->t0;
                    st->have_first = true;
                }
                st->t_last = pc->t1;
                for (int j = 0; j < pc->n; j += 61) {
                    double claim = pc->t0 + (double) j / pc->n * (pc->t1 - pc->t0);
                    double said = film_of_value(out[at + j]);
                    double d = fabs(claim - said) * 1e6;
                    if (d > st->worst_us)
                        st->worst_us = d;
                }
                st->data_frames += pc->n;
            } else {
                for (int j = 0; j < pc->n; j++)
                    if (out[at + j] != 0.0f)
                        check(false, "silence piece holds a non-zero sample");
                st->silence_frames += pc->n;
            }
            at += pc->n;
        }
        check(at == 480, "the pieces of a block add up to 480 frames (%d)", at);
        st->underruns += res.underrun_frames > 0;
        st->ended |= res.ended;
    }
}

static struct audio_pipe *start_pipe(struct audio_src *s, int gen, double origin, double speed)
{
    struct audio_pipe *p = audio_pipe_create(s, RATE, 1, 0);
    audio_src_seek(s, gen, origin);
    audio_pipe_begin(p, gen, origin, speed);
    for (int i = 0; i < 1500 && !audio_pipe_ready(p); i++)
        Sleep(2);
    check(audio_pipe_ready(p), "the converter gets a first frame");
    return p;
}

static void test_pipe(const char *file)
{
    char err[200];
    struct audio_src *s = audio_src_open(file, -1, err, sizeof(err));
    if (!s) {
        check(false, "cannot open %s: %s", file, err);
        return;
    }
    audio_src_start(s);
    int gen = 1;
    struct pull_stats st;

    // plain, the first frame is exactly the requested film time
    memset(&st, 0, sizeof(st));
    struct audio_pipe *p = start_pipe(s, gen++, 0.5, 1.0);
    pull_blocks(p, 100, &st);
    check(st.worst_us < tol_us + 30.0, "speed 1: claimed film time vs content %.1f us", st.worst_us);
    check(fabs(st.t_first - 0.5) < tol_us * 1e-6, "speed 1: first frame is film time 0.5 (claimed %.6f)", st.t_first);
    check(fabs((st.t_last - st.t_first) - 1.0) < 2e-5, "speed 1: one second of output is one film second (%.6f)", st.t_last - st.t_first);
    check(st.silence_frames == 0 && st.underruns == 0, "speed 1: no silence, no underrun");
    audio_pipe_destroy(&p);

    // the rate lock: 0.1 % fast
    memset(&st, 0, sizeof(st));
    p = start_pipe(s, gen++, 0.6, 1.001);
    check(fabs(audio_pipe_speed(p) - 1.001) < 3e-5, "speed in force %.6f", audio_pipe_speed(p));
    pull_blocks(p, 100, &st);
    check(st.worst_us < tol_us + 30.0, "speed 1.001: claimed film time vs content %.1f us", st.worst_us);
    check(fabs((st.t_last - st.t_first) - 1.001) < 3e-5, "speed 1.001: one second of output is 1.001 film seconds (%.6f)", st.t_last - st.t_first);
    audio_pipe_destroy(&p);

    // soft correction: +0.2 % consumes the source faster, -0.2 % slower; the claimed times follow
    for (int sign = -1; sign <= 1; sign += 2) {
        memset(&st, 0, sizeof(st));
        p = start_pipe(s, gen++, 1.0, 1.0);
        audio_pipe_set_comp(p, 0.002 * sign);
        pull_blocks(p, 100, &st);
        double got = st.t_last - st.t_first;
        check(st.worst_us < tol_us + 40.0, "comp %+.3f: claimed film time vs content %.1f us", 0.002 * sign, st.worst_us);
        check(fabs(got - (1.0 + 0.002 * sign)) < 5e-5, "comp %+.3f: one second of output is %.6f film seconds", 0.002 * sign, got);
        audio_pipe_destroy(&p);
    }

    // before the track starts: silence up to its first sample (0.25 s = 12000 frames), then the ramp from 0
    memset(&st, 0, sizeof(st));
    p = start_pipe(s, gen++, 0.0, 1.0);
    pull_blocks(p, 40, &st);
    check(st.silence_frames == 12000, "the head of the film before the track: %ld silence frames (want 12000)", st.silence_frames);
    check(st.worst_us < tol_us + 30.0 && fabs(st.t_first - TRACK_START) < tol_us * 1e-6, "sound begins at film time %.6f", st.t_first);
    audio_pipe_destroy(&p);

    // silence owed and sound skipped
    memset(&st, 0, sizeof(st));
    p = start_pipe(s, gen++, 0.6, 1.0);
    audio_pipe_silence(p, 4800);
    pull_blocks(p, 20, &st);
    check(st.silence_frames == 4800 && fabs(st.t_first - 0.6) < tol_us * 1e-6, "owed silence: %ld frames, then film time %.6f", st.silence_frames, st.t_first);
    memset(&st, 0, sizeof(st));
    audio_pipe_skip(p, 0.5);
    pull_blocks(p, 10, &st);
    check(fabs(st.t_first - (0.6 + 0.1 + 0.5)) < tol_us * 1e-6 + 1.0 / RATE, "skip of 0.5 s: sound resumes at film time %.6f (want 1.2)", st.t_first);
    check(st.worst_us < tol_us + 40.0, "after a skip: claimed film time vs content %.1f us", st.worst_us);
    audio_pipe_destroy(&p);

    // the end of the file: the tail, then silence, flagged
    memset(&st, 0, sizeof(st));
    p = start_pipe(s, gen++, 19.5, 1.0);
    pull_blocks(p, 60, &st);
    check(st.ended && st.data_frames > 20000 && st.data_frames < 26000, "the end is flagged after %ld data frames", st.data_frames);
    audio_pipe_destroy(&p);

    // a decoder that has delivered nothing yet is an underrun, not a crash
    memset(&st, 0, sizeof(st));
    p = audio_pipe_create(s, RATE, 1, 0);
    audio_src_seek(s, gen, 10.0);
    audio_pipe_begin(p, gen++, 10.0, 1.0);
    float out[480];
    struct pipe_result res;
    audio_pipe_pull(p, out, 480, &res);
    check(res.underrun_frames > 0 || res.n_pieces > 0, "pull before the decoder is ready returns");
    audio_pipe_destroy(&p);
    audio_src_close(&s);
}

// ---- arithmetic ---------------------------------------------------------------------------------------------------

static void test_sync(void)
{
    // device clock: positions that run 300 ppm fast against the host clock, with 0.4 ms of reading jitter
    struct dev_clock dc;
    devclock_reset(&dc, RATE);
    check(!devclock_valid(&dc), "a fresh device clock is not valid");
    devclock_add(&dc, 0, 1000.0);
    check(!devclock_valid(&dc), "a zero position is not an observation");
    srand(7);
    double t0 = 5000.0, worst = 0.0;
    for (int i = 0; i < 600; i++) {
        double t = t0 + i * 0.01;
        double jitter = ((rand() % 2001) - 1000) * 0.4e-6;
        devclock_add(&dc, (uint64_t) llround(RATE * (1.0 + 300e-6) * (t - t0 + jitter)) + 100, t);
        if (i > 200) {
            double err = fabs(devclock_frame_at(&dc, t) - (RATE * (1.0 + 300e-6) * (t - t0) + 100)) / RATE;
            if (err > worst)
                worst = err;
        }
    }
    check(devclock_valid(&dc) && worst < 0.4e-3, "device clock follows a 300 ppm drift within %.3f ms", worst * 1e3);
    check(fabs(devclock_time_of(&dc, devclock_frame_at(&dc, 5003.0)) - 5003.0) < 1e-9, "frame_at and time_of are inverse");

    // write log
    struct seg_log sl;
    seglog_clear(&sl);
    seglog_add(&sl, 1000, 480, true, 10.0, 10.01);
    seglog_add(&sl, 1480, 480, false, NAN, NAN);
    seglog_add(&sl, 1960, 480, true, 10.01, 10.02);
    double f;
    check(seglog_film_at(&sl, 1240.0, &f) && fabs(f - 10.005) < 1e-9, "write log interpolates inside a run");
    check(!seglog_film_at(&sl, 1500.0, &f), "write log: silence has no film time");
    check(!seglog_film_at(&sl, 999.0, &f) && !seglog_film_at(&sl, 2440.0, &f), "write log: outside the runs has none");
    for (int i = 0; i < 400; i++)
        seglog_add(&sl, 5000 + i * 10, 10, true, i, i + 1);
    check(seglog_film_at(&sl, 5000 + 399 * 10 + 5, &f) && fabs(f - 399.5) < 1e-9, "write log keeps the newest runs when full");

    // correction loop on an integrator plant with a drift: starts 8 ms off, 300 ppm of clock mismatch
    struct sync_ctl c;
    sync_ctl_reset(&c, false);
    double e = 0.008, tail_max = 0.0, peak = 0.0;
    for (int i = 0; i < 3000; i++) {      // 30 s at 10 ms
        double corr = sync_ctl_update(&c, e, 0.01);
        e += (corr + 300e-6) * 0.01;
        if (fabs(e) > peak)
            peak = fabs(e);
        if (i > 1500 && fabs(e) > tail_max)
            tail_max = fabs(e);
    }
    check(tail_max < 1e-5, "loop settles: |error| after 15 s %.3f us", tail_max * 1e6);
    check(peak < 0.0081, "loop does not overshoot: peak %.3f ms", peak * 1e3);
    check(fabs(c.corr + 300e-6) < 5e-6, "loop learns the clock mismatch: %.1f ppm", c.corr * 1e6);
    sync_ctl_reset(&c, true);
    check(fabs(c.corr + 300e-6) < 5e-6, "a reset that keeps the mismatch keeps the correction");
}

int main(int argc, char **argv)
{
    if (argc < 2 || argc > 3) {
        fprintf(stderr, "usage: audio-selftest RAMP [TOLERANCE_US]   data\\audio-test-ramp\\ramp.nut: 25 (default); ramp.mkv: 700,\n"
                        "       because Matroska rounds timestamps to the millisecond; see audio-make-test-media.py\n");
        return 2;
    }
    if (argc == 3)
        tol_us = atof(argv[2]);
    test_sync();
    test_decoder(argv[1]);
    test_pipe(argv[1]);
    printf("audio-selftest: %d checks, %d failed\n", checks, failures);
    return failures ? 1 : 0;
}
