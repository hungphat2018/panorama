// The converter's input side: the frame being consumed, positioning it on the film clock, the resampler and its soft
// correction. See perf1-audio-pipe.h.
#include "perf1-audio-pipe-priv.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libavutil/channel_layout.h>
#include <libavutil/opt.h>
#include <libavutil/samplefmt.h>
#include <libswresample/swresample.h>

void pipe_release_chunk(struct audio_pipe *p)
{
    if (p->have_cur)
        av_frame_free(&p->cur.frame);
    p->have_cur = false;
    p->cur_off = 0;
}

void pipe_drop_swr(struct audio_pipe *p)
{
    swr_free(&p->swr);
    av_channel_layout_uninit(&p->swr_layout);
    p->comp_set = false;
    p->comp_eff = 0.0;
    p->anchor = true;
}

// The next chunk of the generation into `cur`, if one is queued.
bool pipe_fetch(struct audio_pipe *p)
{
    if (p->have_cur)
        return true;
    int r = audio_src_pop(p->src, p->gen, &p->cur);
    if (r == 1) {
        p->have_cur = true;
        p->cur_off = 0;
        return true;
    }
    if (r < 0)
        p->src_eof = true;
    return false;
}

// Film time of the next output frame. A fresh resampler's first output frame carries the time of the first input sample (it
// pads its history so the filter is centred); from there time advances by the frames produced, plus what the soft correction
// consumed on top. (swr_get_delay was tried and is not exact enough: it wanders by a few samples.)
double pipe_t_out(const struct audio_pipe *p)
{
    return p->anchor ? p->t_pos : p->t_outm;
}

static double chunk_time(const struct audio_pipe *p)
{
    return p->cur.t0 + (double) p->cur_off / (double) p->cur.frame->sample_rate;
}

// Puts the current chunk's first sample at t_pos: silence for a gap, a cut for an overlap. Exact after a begin or a
// skip; otherwise only steps beyond the timestamp rounding count. May release the chunk (the caller fetches again).
void pipe_position_chunk(struct audio_pipe *p)
{
    const AVFrame *f = p->cur.frame;
    double ct = chunk_time(p);
    double gap = ct - p->t_pos;
    double tol = p->exact ? 0.5 / (double) f->sample_rate : GAP_TOL_S;
    bool exact = p->exact;
    p->exact = false;
    if (fabs(gap) <= tol) {
        if (!exact)
            return;
        p->t_pos = ct;          // within half a sample of the request: the clock follows the sample
        return;
    }
    pipe_drop_swr(p);                // a step: the resampler's held-back tail does not belong after it
    if (gap > 0.0) {
        p->sil += (int64_t) llround(gap / audio_pipe_film_per_frame(p));
        p->t_pos = ct;
        return;
    }
    int avail = f->nb_samples - p->cur_off;
    int drop = (int) llround(-gap * (double) f->sample_rate);
    if (drop >= avail) {
        if (!exact)
            p->t_pos = ct + (double) avail / (double) f->sample_rate;
        pipe_release_chunk(p);       // all of it is before the position
        if (exact)
            p->exact = true;    // the next chunk gets cut as well
        return;
    }
    p->cur_off += drop;
    p->t_pos = chunk_time(p);
}

bool pipe_ensure_swr(struct audio_pipe *p, const AVFrame *f)
{
    AVChannelLayout in_l = { 0 };
    if (f->ch_layout.order == AV_CHANNEL_ORDER_UNSPEC)
        av_channel_layout_default(&in_l, f->ch_layout.nb_channels);
    else
        av_channel_layout_copy(&in_l, &f->ch_layout);
    if (p->swr && (p->swr_fmt != f->format || p->swr_rate != f->sample_rate || av_channel_layout_compare(&p->swr_layout, &in_l)))
        pipe_drop_swr(p);
    if (p->swr) {
        av_channel_layout_uninit(&in_l);
        return true;
    }
    int r = swr_alloc_set_opts2(&p->swr, &p->dev_layout, AV_SAMPLE_FMT_FLT, p->virt_rate, &in_l, (enum AVSampleFormat) f->format,
                                f->sample_rate, 0, NULL);
    if (r >= 0) {
        av_opt_set_int(p->swr, "swr_flags", SWR_FLAG_RESAMPLE, 0); // the resampler is always there: compensation needs it
        r = swr_init(p->swr);
    }
    if (r < 0) {
        fprintf(stderr, "perf1: audio: cannot convert %s %d Hz to the device: %s\n",
                av_get_sample_fmt_name((enum AVSampleFormat) f->format), f->sample_rate, av_err2str(r));
        swr_free(&p->swr);
        av_channel_layout_uninit(&in_l);
        return false;
    }
    av_channel_layout_copy(&p->swr_layout, &in_l);
    av_channel_layout_uninit(&in_l);
    p->swr_fmt = f->format;
    p->swr_rate = f->sample_rate;
    return true;
}

// Applies the soft correction for the next `frames` output frames. A positive correction consumes the source faster, so
// the resampler is asked to produce fewer frames over the distance.
void pipe_apply_comp(struct audio_pipe *p, int frames)
{
    if (!p->swr || (p->comp == 0.0 && p->comp_carry == 0.0 && !p->comp_set))
        return;
    double want = p->comp * (double) frames + p->comp_carry;
    int delta = (int) lround(want);
    p->comp_carry = want - (double) delta;
    if (swr_set_compensation(p->swr, -delta, frames) < 0) {
        p->comp_eff = 0.0;
        return;
    }
    p->comp_eff = (double) delta / (double) frames;
    p->comp_set = delta != 0;
}
