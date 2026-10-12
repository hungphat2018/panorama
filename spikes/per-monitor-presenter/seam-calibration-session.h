// seam-calibration-session.h: forced-choice calibration of the right screen's height, the gap and its lag.
//
// For each parameter: an anchor first (the pattern running straight across a fake bezel drawn inside the laptop
// screen), then rounds of two settings shown in turn, labelled 1 and 2, about 2.5 s each. The viewer says in which
// the pattern runs straighter across the real bezel; the software moves the setting to that pick and halves the
// step (start: height 0 +-4 mm, gap 20 +-8 mm, lag 0 +-4 ms; 7 rounds). Order: height (horizontal lines only,
// which a wrong gap can't bend), gap (diagonal stripes), then lag (the stripes sliding right and left: a lag
// error flips side with the direction, a gap error does not). Results go to the seam-settings JSON.
#pragma once
#include "seam-app.h"

#include <memory>
#include <string>

namespace seam {

struct CalibrationOptions {
    int rounds = 7;
    bool quick = false;            // short settings and waits, for smoke tests
    std::string auto_answer;       // "", "right", "wrong" or "random": answers without a person
    uint32_t seed = 0;
    std::wstring out_dir = L".";
    // The hidden truth that auto "right" and "wrong" answers aim at (a test of the search, not a measurement).
    double sim_height_mm = -1.3, sim_gap_mm = 23.7, sim_lag_ms = 2.6;
};

bool calibration_can_run(std::string* why);

std::unique_ptr<Session> make_calibration_session(const CalibrationOptions& options);

}  // namespace seam
