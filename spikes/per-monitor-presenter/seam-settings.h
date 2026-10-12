// seam-settings.h: the seam-settings JSON the tuner writes (gap, height, per-monitor lag) and reads back.
//
// The original fields stay as they were (gap_mm, right_screen_lower_mm, lag_ms, scan_fraction, refresh_hz,
// rows_timing, note). Added: the exact signal timing per monitor, the brightness note, and, after a forced-choice
// calibration, a "calibration" block saying which parameters were set.
#pragma once
#include "seam-app.h"

#include <string>
#include <vector>

namespace seam {

struct CalibrationInfo {
    bool present = false;
    bool complete = false;
    std::vector<std::string> done;  // parameters whose rounds finished: "height", "gap", "lag"
    int rounds = 0;                 // rounds per parameter
    std::string log_name;           // the per-round answer log
};

// "20261011-194210" style local time stamp, and an ISO-like one for log lines.
std::wstring local_stamp();
std::string local_iso();

// Creates the directory and any missing parents; false (after printing why) if it can't.
bool make_dirs(const std::wstring& path);

// Writes the settings; returns false (after printing why) if the file can't be written.
bool save_settings(const std::wstring& path, const Settings& s, const CalibrationInfo* cal);

// Reads gap, height and lags back from a file this tuner wrote. Missing fields keep their current value.
bool load_settings(const std::wstring& path, Settings* s);

}  // namespace seam
