// seam-free-session.h: the original key-driven tuner (the user adjusts, the screens show one pattern laid out in
// millimetres along the row of monitors, continuing behind the bezel), now on the scan-line row clocks.
//
//   1 2 3 4       speed 0 / 150 / 300 / 600 mm/s
//   PgUp PgDn     height of the right screen against the left one (0.5 mm, Shift 5 mm)
//   Left Right    bezel gap (0.5 mm, Shift 5 mm)
//   Up Down       lag of the selected screen (0.5 ms, Shift 5 ms)       Tab  select which screen's lag
//   S             smooth / film steps       R  rows timing on / off       P  stripes on / off (bars only)
//   H             help on / off             Enter  save settings to JSON      Esc  quit (also saves seam-settings-last.json)
// The forced-choice versions of these adjustments are --mode calibrate and --mode x2 (seam-tuner.cpp).
#pragma once
#include "seam-app.h"

#include <memory>
#include <string>

namespace seam {

std::unique_ptr<Session> make_free_session(const std::wstring& out_dir);

}  // namespace seam
