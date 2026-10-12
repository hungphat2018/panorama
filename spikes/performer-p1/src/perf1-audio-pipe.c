// The sound's converter, see perf1-audio-pipe.h.
#include "perf1-audio-pipe-priv.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <libavutil/channel_layout.h>
#include <libavutil/opt.h>
#include <libavutil/samplefmt.h>
#include <libswresample/swresample.h>


// ---- create / destroy ----------------------------------------------------------------------------------------

struct audio_pipe *audio_pipe_create(struct audio_src *src, int dev_rate, int dev_channels, uint64_t dev_chmask)
{
    struct audio_pipe *p = calloc(1, sizeof(*p));
    if (!p)
        return NULL;
    p->src = src;
    p->dev_rate = dev_rate;
    p->dev_ch = dev_channels;
    if (dev_chmask && av_channel_layout_from_mask(&p->dev_layout, dev_chmask) == 0 && p->dev_layout.nb_channels == dev_channels)
        ;
    else
        av_channel_layout_default(&p->dev_layout, dev_channels);
    p->speed = 1.0;
    p->virt_rate = dev_rate;
    return p;
}



void audio_pipe_destroy(struct audio_pipe **pp)
{
    struct audio_pipe *p = *pp;
    if (!p)
        return;
    pipe_release_chunk(p);
    pipe_drop_swr(p);
    av_channel_layout_uninit(&p->dev_layout);
    free(p);
    *pp = NULL;
}

// ---- control -----------------------------------------------------------------------------------------------

void audio_pipe_begin(struct audio_pipe *p, int gen, double origin, double speed)
{
    pipe_release_chunk(p);
    pipe_drop_swr(p);
    p->gen = gen;
    p->virt_rate = (int) lround((double) p->dev_rate / (speed > 0.5 && speed < 2.0 ? speed : 1.0));
    p->speed = (double) p->dev_rate / (double) p->virt_rate;
    p->src_eof = p->tail_done = false;
    p->t_pos = origin;
    p->exact = true;
    p->sil = 0;
    p->comp_carry = 0.0;
}


bool audio_pipe_ready(struct audio_pipe *p)
{
    return pipe_fetch(p) || p->src_eof;
}


// Counted from where the output is now (the frame after the last one handed out), not from what the resampler was fed.
void audio_pipe_skip(struct audio_pipe *p, double film_s)
{
    if (film_s <= 0.0)
        return;
    p->t_pos = pipe_t_out(p) + film_s;
    p->exact = true;
    pipe_drop_swr(p);                // the filter history is of sound that is no longer next; pipe_position_chunk cuts the current frame
}

void audio_pipe_silence(struct audio_pipe *p, int64_t frames)
{
    if (frames > 0)
        p->sil += frames;
}

void audio_pipe_set_comp(struct audio_pipe *p, double corr)
{
    p->comp = corr;
}

double audio_pipe_speed(const struct audio_pipe *p)
{
    return p->speed;
}

double audio_pipe_film_per_frame(const struct audio_pipe *p)
{
    return 1.0 / (double) p->virt_rate;
}

// ---- pulling ---------------------------------------------------------------------------------------------------




static void add_piece(struct pipe_result *res, int n, bool data, double t0, double t1)
{
    if (n <= 0)
        return;
    struct pipe_piece *last = res->n_pieces ? &res->piece[res->n_pieces - 1] : NULL;
    if (last && last->data == data) {
        last->n += n;
        last->t1 = t1;
        return;
    }
    if (res->n_pieces == PIPE_MAX_PIECES) { // cannot happen in practice: fold into the last piece
        res->piece[res->n_pieces - 1].n += n;
        res->piece[res->n_pieces - 1].t1 = t1;
        return;
    }
    res->piece[res->n_pieces++] = (struct pipe_piece) { n, data, t0, t1 };
}

static void add_silence(struct pipe_result *res, float *out, int dev_ch, int got, int n)
{
    memset(out + (size_t) got * (size_t) dev_ch, 0, (size_t) n * (size_t) dev_ch * sizeof(float));
    add_piece(res, n, false, NAN, NAN);
}


void audio_pipe_pull(struct audio_pipe *p, float *out, int n, struct pipe_result *res)
{
    memset(res, 0, sizeof(*res));
    int got = 0;
    bool comp_applied = false;
    while (got < n) {
        if (p->sil > 0) {
            int k = (int) (p->sil < n - got ? p->sil : n - got);
            add_silence(res, out, p->dev_ch, got, k);
            got += k;
            p->sil -= k;
            continue;
        }
        if (!pipe_fetch(p)) {
            if (p->src_eof) { // the file is exhausted: the resampler's tail, then silence for good
                int k = 0;
                if (p->swr && !p->tail_done) {
                    uint8_t *op[1] = { (uint8_t *) (out + (size_t) got * (size_t) p->dev_ch) };
                    double t0 = pipe_t_out(p);
                    k = swr_convert(p->swr, op, n - got, NULL, 0);
                    if (k > 0) {
                        p->t_outm = t0 + (double) k * (1.0 + p->comp_eff) / (double) p->virt_rate;
                        add_piece(res, k, true, t0, p->t_outm);
                    }
                }
                if (k > 0) {
                    got += k;
                    continue;
                }
                p->tail_done = true;
                res->ended = true;
            } else {
                res->underrun_frames = n - got;
            }
            add_silence(res, out, p->dev_ch, got, n - got);
            return;
        }
        pipe_position_chunk(p);
        if (!p->have_cur || p->sil > 0)
            continue;
        const AVFrame *f = p->cur.frame;
        if (!pipe_ensure_swr(p, f)) { // cannot convert this format: skip the chunk, play silence for its length
            p->sil += (int64_t) llround((double) (f->nb_samples - p->cur_off) / (double) f->sample_rate / audio_pipe_film_per_frame(p));
            p->t_pos += (double) (f->nb_samples - p->cur_off) / (double) f->sample_rate;
            pipe_release_chunk(p);
            continue;
        }
        if (!comp_applied) {
            pipe_apply_comp(p, n);
            comp_applied = true;
        }
        int avail = f->nb_samples - p->cur_off;
        int want_in = (int) ceil((double) (n - got) * (double) f->sample_rate / (double) p->virt_rate * 1.01) + 8;
        int in_n = avail < want_in ? avail : want_in;
        const uint8_t *ip[MAX_CHANNELS];
        int planes = av_sample_fmt_is_planar((enum AVSampleFormat) f->format) ? f->ch_layout.nb_channels : 1;
        int bps = av_get_bytes_per_sample((enum AVSampleFormat) f->format);
        int step = planes == 1 ? bps * f->ch_layout.nb_channels : bps;
        for (int c = 0; c < planes && c < MAX_CHANNELS; c++)
            ip[c] = f->extended_data[c] + (size_t) p->cur_off * (size_t) step;
        uint8_t *op[1] = { (uint8_t *) (out + (size_t) got * (size_t) p->dev_ch) };
        if (p->anchor) {
            p->t_outm = p->t_pos;
            p->anchor = false;
        }
        double t0 = p->t_outm;
        int k = swr_convert(p->swr, op, n - got, ip, in_n);
        if (k < 0) {
            fprintf(stderr, "perf1: audio: swr_convert: %s\n", av_err2str(k));
            pipe_release_chunk(p);
            continue;
        }
        p->cur_off += in_n;
        p->t_pos += (double) in_n / (double) f->sample_rate;
        p->t_outm = t0 + (double) k * (1.0 + p->comp_eff) / (double) p->virt_rate;
        add_piece(res, k, true, t0, p->t_outm);
        got += k;
        if (p->cur_off >= f->nb_samples)
            pipe_release_chunk(p);
    }
}
