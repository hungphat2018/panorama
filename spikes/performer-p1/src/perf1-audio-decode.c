// The sound's decode thread and frame queue, see perf1-audio-decode.h.
#include "perf1-audio-decode-priv.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

// ---- the queue -------------------------------------------------------------------------------------------------

static void free_queue(struct audio_src *s)
{
    while (s->q_count) {
        av_frame_free(&s->q[s->q_head].frame);
        s->q_head = (s->q_head + 1) % QUEUE_FRAMES;
        s->q_count--;
    }
    s->q_seconds = 0.0;
}

void audio_src_seek(struct audio_src *s, int gen, double film_target)
{
    EnterCriticalSection(&s->cs);
    s->want_gen = gen;
    s->want_target = film_target;
    s->q_gen = gen;
    s->q_eof = false;
    free_queue(s);
    LeaveCriticalSection(&s->cs);
    WakeAllConditionVariable(&s->cv);
}

int audio_src_pop(struct audio_src *s, int gen, struct audio_chunk *out)
{
    int r = 0;
    EnterCriticalSection(&s->cs);
    if (gen == s->q_gen) {
        if (s->q_count) {
            struct qitem *it = &s->q[s->q_head];
            out->frame = it->frame;
            out->t0 = it->t0;
            out->dur = it->dur;
            s->q_seconds -= it->dur;
            s->q_head = (s->q_head + 1) % QUEUE_FRAMES;
            s->q_count--;
            r = 1;
        } else if (s->q_eof) {
            r = -1;
        }
    }
    LeaveCriticalSection(&s->cs);
    if (r == 1)
        WakeAllConditionVariable(&s->cv);
    return r;
}

double audio_src_queued_s(struct audio_src *s)
{
    EnterCriticalSection(&s->cs);
    double v = s->q_seconds;
    LeaveCriticalSection(&s->cs);
    return v;
}

// ---- the thread ------------------------------------------------------------------------------------------------

struct dstate {
    int gen;                    // generation being decoded
    double skip_before;         // film time: frames that end before it are dropped
    double next_t;              // film time the next frame is expected at when it carries no timestamp
    bool eof;                   // everything of this generation is queued
};

// True when the thread should stop what it is doing: a newer request or the end of the run.
static bool interrupted(struct audio_src *s, const struct dstate *st)
{
    if (atomic_load(&s->exit_thread))
        return true;
    EnterCriticalSection(&s->cs);
    bool newer = s->want_gen != st->gen;
    LeaveCriticalSection(&s->cs);
    return newer;
}

// Queues the frame (waits while the queue holds enough). Takes the frame. False when interrupted.
static bool enqueue(struct audio_src *s, struct dstate *st, AVFrame *frame, double t0, double dur)
{
    EnterCriticalSection(&s->cs);
    while (s->want_gen == st->gen && !atomic_load(&s->exit_thread) &&
           (s->q_count >= QUEUE_FRAMES || s->q_seconds >= QUEUE_MAX_S))
        SleepConditionVariableCS(&s->cv, &s->cs, 50);
    bool ok = s->want_gen == st->gen && !atomic_load(&s->exit_thread);
    if (ok) {
        struct qitem *it = &s->q[(s->q_head + s->q_count) % QUEUE_FRAMES];
        it->frame = frame;
        it->t0 = t0;
        it->dur = dur;
        s->q_count++;
        s->q_seconds += dur;
    }
    LeaveCriticalSection(&s->cs);
    if (!ok)
        av_frame_free(&frame);
    return ok;
}

static bool handle_frame(struct audio_src *s, struct dstate *st, AVFrame *frame)
{
    int64_t ts = frame->pts != AV_NOPTS_VALUE ? frame->pts : frame->best_effort_timestamp;
    double t0 = ts != AV_NOPTS_VALUE ? (double) ts * s->tb - s->info.video_first : st->next_t;
    double dur = (double) frame->nb_samples / (double) frame->sample_rate;
    st->next_t = t0 + dur;
    if (t0 + dur <= st->skip_before) {
        av_frame_free(&frame);
        return true;
    }
    return enqueue(s, st, frame, t0, dur);
}

// Seeks the demuxer to PREROLL_S before the target and flushes the decoder.
static void start_generation(struct audio_src *s, struct dstate *st, int gen, double target)
{
    st->gen = gen;
    st->eof = false;
    st->skip_before = target - PREROLL_S;
    st->next_t = st->skip_before > 0.0 ? st->skip_before : 0.0;
    double from = st->skip_before > 0.0 ? st->skip_before : 0.0;
    int64_t ts = (int64_t) llround((from + s->info.video_first) / s->tb);
    if (av_seek_frame(s->fmt, s->info.stream, ts, AVSEEK_FLAG_BACKWARD) < 0)
        av_seek_frame(s->fmt, s->info.stream, s->st->start_time != AV_NOPTS_VALUE ? s->st->start_time : 0, AVSEEK_FLAG_BACKWARD);
    avcodec_flush_buffers(s->codec);
}

static void finish_generation(struct audio_src *s, struct dstate *st)
{
    EnterCriticalSection(&s->cs);
    if (s->want_gen == st->gen)
        s->q_eof = true;
    LeaveCriticalSection(&s->cs);
    st->eof = true;
}

// Receives every frame the decoder has ready. False when interrupted.
static bool drain_decoder(struct audio_src *s, struct dstate *st, AVFrame **frame)
{
    for (;;) {
        int r = avcodec_receive_frame(s->codec, *frame);
        if (r != 0)
            return true;
        AVFrame *f = *frame;
        *frame = av_frame_alloc();
        if (!*frame || !handle_frame(s, st, f))
            return false;
    }
}

static DWORD WINAPI decode_thread(LPVOID arg)
{
    struct audio_src *s = arg;
    AVPacket *pkt = av_packet_alloc();
    AVFrame *frame = av_frame_alloc();
    struct dstate st = { .gen = -1, .eof = true };
    while (pkt && frame && !atomic_load(&s->exit_thread)) {
        EnterCriticalSection(&s->cs);
        int want = s->want_gen;
        double target = s->want_target;
        LeaveCriticalSection(&s->cs);
        if (want != st.gen) {
            start_generation(s, &st, want, target);
            continue;
        }
        if (st.eof) { // parked until a seek or the end of the run
            EnterCriticalSection(&s->cs);
            if (s->want_gen == st.gen && !atomic_load(&s->exit_thread))
                SleepConditionVariableCS(&s->cv, &s->cs, 20);
            LeaveCriticalSection(&s->cs);
            continue;
        }
        int r = av_read_frame(s->fmt, pkt);
        if (r == 0) {
            if (pkt->stream_index == s->info.stream) {
                r = avcodec_send_packet(s->codec, pkt);
                av_packet_unref(pkt);
                if (r < 0 && r != AVERROR(EAGAIN))
                    fprintf(stderr, "perf1: audio: avcodec_send_packet: %s\n", av_err2str(r));
                drain_decoder(s, &st, &frame); // interrupted or not, the top of the loop deals with a newer request
            } else {
                av_packet_unref(pkt);
            }
            continue;
        }
        if (r != AVERROR_EOF)
            fprintf(stderr, "perf1: audio: av_read_frame: %s\n", av_err2str(r));
        avcodec_send_packet(s->codec, NULL); // drain
        if (drain_decoder(s, &st, &frame) && !interrupted(s, &st))
            finish_generation(s, &st);
    }
    av_packet_free(&pkt);
    av_frame_free(&frame);
    return 0;
}

bool audio_src_start(struct audio_src *s)
{
    s->thread = CreateThread(NULL, 0, decode_thread, s, 0, NULL);
    return s->thread != NULL;
}

void audio_src_close(struct audio_src **sp)
{
    struct audio_src *s = *sp;
    if (!s)
        return;
    if (s->thread) {
        atomic_store(&s->exit_thread, true);
        WakeAllConditionVariable(&s->cv);
        WaitForSingleObject(s->thread, INFINITE);
        CloseHandle(s->thread);
    }
    free_queue(s);
    avcodec_free_context(&s->codec);
    avformat_close_input(&s->fmt);
    DeleteCriticalSection(&s->cs);
    free(s);
    *sp = NULL;
}
