// From decoded frames to device frames, with film time attached: the sound's converter. It takes the frames the audio
// source queued, converts them to the device's float format and rate with libswresample, plays them at the film's speed
// (the film clock may run a fraction of a percent fast: the rate lock) and answers, for every block it hands out, which film
// time its first and last frame carry. It never looks at a clock; it does what it is told:
//   begin     start a generation at a film time (the first frame returned carries exactly that time)
//   skip      drop sound from the head (the audio is behind the film)
//   silence   owe the device some silence before the sound goes on (the audio is ahead, or nothing to play yet)
//   set_comp  play the sound a little faster or slower (soft correction by resampling, no frame is dropped)
// A gap in the file's timestamps becomes silence, an overlap is cut; smaller steps (a few milliseconds of timestamp
// rounding) are ignored, the samples are counted.
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "perf1-audio-decode.h"

#define PIPE_MAX_PIECES 12

struct pipe_piece {                 // a run of frames in one pull
    int n;
    bool data;                      // sound from the file; otherwise silence (t0, t1 are then NaN)
    double t0, t1;                  // film time of the first frame and just after the last
};

struct pipe_result {
    struct pipe_piece piece[PIPE_MAX_PIECES];
    int n_pieces;
    int underrun_frames;            // frames of silence that stand for sound the decoder had not delivered yet
    bool ended;                     // the file has no more sound; the rest of the block is silence
};

struct audio_pipe;

struct audio_pipe *audio_pipe_create(struct audio_src *src, int dev_rate, int dev_channels, uint64_t dev_chmask);
void audio_pipe_destroy(struct audio_pipe **p);

// The next frame returned carries film time `origin`; sound is played at `speed` film seconds per second.
void audio_pipe_begin(struct audio_pipe *p, int gen, double origin, double speed);
// True once the generation's first frame (or its end) can be had, so begin's positioning is possible.
bool audio_pipe_ready(struct audio_pipe *p);
void audio_pipe_skip(struct audio_pipe *p, double film_s);
void audio_pipe_silence(struct audio_pipe *p, int64_t frames);
// Fraction of speed to add (+: the sound is consumed faster, so the audio moves forward against the film). Held until changed.
void audio_pipe_set_comp(struct audio_pipe *p, double corr);
// The speed actually in force (the resampler's output rate is an integer).
double audio_pipe_speed(const struct audio_pipe *p);
// Film seconds one device frame stands for at the speed in force.
double audio_pipe_film_per_frame(const struct audio_pipe *p);

// Fills out[0 .. n*channels) completely.
void audio_pipe_pull(struct audio_pipe *p, float *out, int n, struct pipe_result *res);
