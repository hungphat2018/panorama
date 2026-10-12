// seam-x2-session.h: the film-step test ("X2"): does the scan-speed difference across the bezel show, and does
// the rolling blend fix it without a visible cost?
//
// A tall white bar moves left to right in 24 fps steps. Each trial plays two clips and asks a forced choice:
//   a  real bezel: today's whole-picture switch aimed at one row vs the rolling blend.
//      "In which do the bar's top and bottom come out of the bezel at different moments?" (right answer: aimed)
//   b  the laptop alone, the bar crossing a fake bezel (a black strip inside the screen): the plain switch vs the
//      rolling blend, with the blend weights the external's wave would give.
//      "Which shows a faint double edge?" (right answer: the blend)
// An anchor comes first (and with key A): the same bar crossing the fake bezel with a plain switch, where both
// sides are one panel, so top and bottom switch together. Answers go to a JSONL file with the hidden truth;
// seam-score-x2-trials.py gives the verdicts.
#pragma once
#include "seam-app.h"

#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace seam {

struct X2Options {
    std::vector<std::pair<char, int>> conditions;  // ('a' | 'b', speed in mm/s)
    int trials_per_condition = 10;
    bool quick = false;                            // short clips and waits, for smoke tests
    std::string auto_answer;                       // "", "right", "wrong" or "random": answers without a person
    uint32_t seed = 0;
    std::wstring out_dir = L".";
};

// False (with a message) if the setup can't run the test: it needs two monitors, the slower one on the right.
bool x2_can_run(std::string* why);

std::unique_ptr<Session> make_x2_session(const X2Options& options);

}  // namespace seam
