// Pass 2, the temporal rule on the CPU: how far the surround's state moves toward the new frame on each refresh.
// No GPU here, so tests and the planner can run exactly the rule the performer runs.
//
// The state follows the picture's edge colours like a first-order low-pass filter (an exponential follower) with time
// constant tau: a step in the picture moves the state a fraction 1 - exp(-dt / tau) of the way each refresh. Because
// that fraction is computed from the refresh's own film-time step, the result does not depend on the refresh rate
// (240 Hz and 60 Hz screens show the same curve) and a pause (dt = 0) freezes it.
//
// At a hard cut the cue picks one of three rules (finish-passes.h): snap, blend, hold.
#include <math.h>

#include "perf1-finish-passes.h"

#define NO_LIMIT 1.0e9f

void fp_surround_clock_reset(struct fp_surround_clock *c)
{
    c->since_cut_s = 1.0e9;
    c->primed = false;
}

static float follow_alpha(double dt, double tau)
{
    if (dt <= 0.0)
        return 0.0f;
    if (tau <= 0.0)
        return 1.0f;
    return (float) (1.0 - exp(-dt / tau));
}

struct fp_surround_step fp_surround_clock_step(struct fp_surround_clock *c, const struct fp_surround_time *t,
                                               const struct fp_surround_frame *f)
{
    double dt = f->dt_s > 0.0 ? f->dt_s : 0.0;
    if (f->reset || !c->primed) {
        // First picture after a start, a seek or a pause: there is no past to follow, so the state is the picture.
        c->primed = true;
        c->since_cut_s = 1.0e9;
        return (struct fp_surround_step) { 1.0f, NO_LIMIT };
    }

    // Time since the last cut at the START of the interval this refresh covers (negative on the cut refresh itself).
    double t0;
    if (f->cut) {
        t0 = -dt;
        c->since_cut_s = 0.0;
    } else {
        t0 = c->since_cut_s;
        c->since_cut_s += dt;
    }

    switch (t->cut_rule) {
    case FP_CUT_SNAP:
        if (f->cut)
            return (struct fp_surround_step) { 1.0f, NO_LIMIT };
        break;
    case FP_CUT_BLEND:
        if (t0 < t->blend_window_s)
            return (struct fp_surround_step) { follow_alpha(dt, t->tau_blend_s), NO_LIMIT };
        break;
    case FP_CUT_HOLD:
        if (t0 < t->hold_s)
            return (struct fp_surround_step) { 0.0f, 0.0f };
        break;
    }
    return (struct fp_surround_step) {
        follow_alpha(dt, t->tau_steady_s),
        t->max_rate > 0.0f ? (float) (t->max_rate * dt) : NO_LIMIT,
    };
}
