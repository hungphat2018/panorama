// Private state of the sound's converter, shared by perf1-audio-pipe.c and perf1-audio-pipe-feed.c.
#pragma once

#include <stdlib.h>

#include <libavutil/channel_layout.h>
#include <libswresample/swresample.h>

#include "perf1-audio-pipe.h"

#define GAP_TOL_S 0.020         // timestamp steps up to this are rounding; the samples are counted
#define MAX_CHANNELS 64

struct audio_pipe {
    struct audio_src *src;
    int dev_rate, dev_ch;
    AVChannelLayout dev_layout;
    int gen;
    double speed;               // film seconds per second of playback, as the resampler's integer output rate allows
    int virt_rate;              // resampler output rate: dev_rate / speed
    struct SwrContext *swr;
    int swr_fmt, swr_rate;
    AVChannelLayout swr_layout;
    struct audio_chunk cur;     // the frame being consumed, from cur_off
    bool have_cur;
    int cur_off;
    bool src_eof, tail_done;
    double t_pos;               // film time of the next input sample to feed the resampler
    double t_outm;              // film time of the next output frame: counted from the first one, see t_out()
    bool anchor;                // the resampler is fresh: its first output frame carries t_pos
    double comp_eff;            // sample delta / distance of the compensation in force (+: the source is consumed faster)
    bool exact;                 // the next frame is cut to t_pos to the sample
    int64_t sil;                // frames of silence still owed
    double comp, comp_carry;
    bool comp_set;              // the resampler has a compensation in force that must be undone
};

void pipe_release_chunk(struct audio_pipe *p);
void pipe_drop_swr(struct audio_pipe *p);                  // forget the resampler; the next one is anchored at t_pos
bool pipe_fetch(struct audio_pipe *p);                     // the next queued frame into `cur`, if there is one
double pipe_t_out(const struct audio_pipe *p);             // film time of the next output frame
void pipe_position_chunk(struct audio_pipe *p);            // put the current frame's first sample at t_pos
bool pipe_ensure_swr(struct audio_pipe *p, const AVFrame *f);
void pipe_apply_comp(struct audio_pipe *p, int frames);    // soft correction for the next `frames` output frames
