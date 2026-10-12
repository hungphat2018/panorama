// seam-film-path.h: where the white bar is at each film frame of a film-step clip.
//
// The bar is tall (full screen height), white on black, 30 mm wide, and moves left to right in film steps of
// speed / 24 mm. A pass starts with the bar beyond the left edge of the region under test and ends beyond the
// right edge. It is placed so that one step puts the bar's centre exactly on the centre of the bezel (the real
// gap, or the fake strip drawn inside the laptop screen): that frame shows the bar on both sides at once, and
// the frames either side of it hand it over. A clip repeats the pass, with a short rest between, until it is
// about 3 s long.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>

namespace seam {

constexpr double kFilmFps = 24.0;
constexpr double kBarWidthMm = 30.0;
constexpr double kAbsentMm = -1.0e6;  // left edge meaning "no bar this frame"

struct BarPath {
    double step_mm = 12.5;
    double centre_mm = 0;       // world x of the bezel centre
    double width_mm = kBarWidthMm;
    int k_min = 0, k_max = 0;   // bar centre = centre_mm + k * step_mm for k in [k_min, k_max]
    int rest = 6;               // empty frames after each pass

    int pass_frames() const { return k_max - k_min + 1; }
    int cycle_frames() const { return pass_frames() + rest; }
    // Left edge (world mm) of the bar at film frame n; kAbsentMm before the clip and between passes.
    double left_mm(int64_t n) const {
        if (n < 0) return kAbsentMm;
        int p = int(n % cycle_frames());
        if (p >= pass_frames()) return kAbsentMm;
        return centre_mm + double(k_min + p) * step_mm - 0.5 * width_mm;
    }
};

// Region under test: x in [0, region_right_mm]. reach_frames > 0 keeps only that many frames either side of the
// crossing, with a shorter rest (short clips for smoke tests).
inline BarPath make_bar_path(double speed_mm_s, double bezel_centre_mm, double region_right_mm, int reach_frames) {
    BarPath p;
    p.step_mm = speed_mm_s / kFilmFps;
    p.centre_mm = bezel_centre_mm;
    p.k_min = int(std::floor((-0.5 * p.width_mm - bezel_centre_mm) / p.step_mm));
    p.k_max = int(std::ceil((region_right_mm + 0.5 * p.width_mm - bezel_centre_mm) / p.step_mm));
    if (reach_frames > 0) {
        p.k_min = std::max(p.k_min, -reach_frames);
        p.k_max = std::min(p.k_max, reach_frames);
        p.rest = 2;
    }
    return p;
}

// Whole passes that make about `seconds` of film.
inline int64_t clip_frames(const BarPath& p, double seconds) {
    int64_t cycles = std::max<int64_t>(1, std::llround(seconds * kFilmFps / double(p.cycle_frames())));
    return cycles * p.cycle_frames();
}

}  // namespace seam
