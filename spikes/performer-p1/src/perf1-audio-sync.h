// The parts of audio sync that are plain arithmetic (no device, no thread): the device clock seen from the host clock, the
// log of which film time was written at which device frame, and the correction loop.
//
//   device clock   frames played vs host time, fitted to the (position, host time) pairs the device reports: position
//                  = rate * time + c, with c the mean of recent pairs (the rate is the nominal one; a device that
//                  runs a hundred ppm fast against the host clock shows as a slow change of c, followed within a window)
//   write log      for each run of frames handed to the device: its first device frame, length and the film time of
//                  its first and last frame; asks "which film time is sounding at device frame x"
//   control        audio-minus-film error in, speed correction out (a PI loop; the plant is an integrator)
#pragma once

#include <stdbool.h>
#include <stdint.h>

// ---- device clock -------------------------------------------------------------------------------------------------

#define DC_WINDOW 128

struct dev_clock {
    double rate;
    double off[DC_WINDOW];      // position - rate * host time, per observation
    int n, head;
    double sum, c;
    double first_s, last_s;     // host times of the oldest and newest observation since reset
    double resid_s;             // rms spread of the window's observations, in seconds
};

void devclock_reset(struct dev_clock *c, int rate);
// An observation: the device had played `frames` frames at host time host_s. A zero position (the stream has not
// begun to play) is not an observation.
void devclock_add(struct dev_clock *c, uint64_t frames, double host_s);
bool devclock_valid(const struct dev_clock *c);
double devclock_frame_at(const struct dev_clock *c, double host_s);     // device frame sounding at host time
double devclock_time_of(const struct dev_clock *c, double frame);       // host time at which a device frame sounds

// ---- write log ----------------------------------------------------------------------------------------------------

#define SEG_MAX 256

struct seg { int64_t i0; int n; bool data; double t0, t1; };

struct seg_log {
    struct seg s[SEG_MAX];
    int head, count;            // ring: newest at (head + count - 1)
};

void seglog_clear(struct seg_log *l);
void seglog_add(struct seg_log *l, int64_t i0, int n, bool data, double t0, double t1);
// Film time sounding at device frame x, if x lies in a run of sound from the file.
bool seglog_film_at(const struct seg_log *l, double x, double *film_s);

// ---- control ------------------------------------------------------------------------------------------------------

struct sync_ctl {
    double e_f;                 // the error, low-passed
    double integ;               // integral of the error
    double corr;                // speed correction in force (fraction, + = sound consumed faster)
    bool primed;
};

#define SYNC_KP 2.0             // 1/s: with SYNC_KI the loop settles in a few seconds without overshoot
#define SYNC_KI 1.0             // 1/s^2
#define SYNC_TAU_S 0.08         // low-pass on the error
#define SYNC_MAX_CORR 0.005     // never more than 0.5 % (8.7 cents of pitch)

void sync_ctl_reset(struct sync_ctl *c, bool keep_clock_error);
// One step. e_s: audio minus film (s), + = the sound is ahead. Returns the correction to apply.
double sync_ctl_update(struct sync_ctl *c, double e_s, double dt);
