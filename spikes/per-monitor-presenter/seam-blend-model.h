// seam-blend-model.h: when a follower monitor changes film frames so its light matches a reference monitor's.
//
// Two monitors share a seam. The reference (the slowest, the one whose refresh count is the film clock) switches
// whole pictures on its own refreshes: film frame n starts on reference refresh k0 + hold * n, and its row at
// height h gets that picture at ref_light(n, h). The follower (finer refresh, quicker scan) has to change its
// picture so the light of its row at the same height changes at the same moment. Two ways:
//   aimed   switch the whole picture on the refresh whose light at one aim row starts nearest the reference's.
//           Exact at that row; off by up to half a follower refresh there, and by the difference of the two scan
//           speeds above and below it.
//   blend   rolling blend: the one follower refresh that straddles the reference row's switch shows both frames,
//           split by time share and mixed in light. With a = the follower row's light start in that refresh,
//           T its period and e the reference row's switch: new share w = (a + T - e) / T, so the row's
//           light-weighted switch moment is a + T - w * T = e, whatever a, T and e are.
// Everything here is plain maths on Wave snapshots (seam-row-clock.h), so it can be checked without a GPU.
#pragma once
#include "seam-row-clock.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <vector>

namespace seam {

struct SeamClock {
    Wave ref, fol;
    int64_t k0 = 0;     // reference refresh on which film frame 0 starts
    int hold = 4;       // reference refreshes per film frame
    double aim_mm = 0;  // height of the aimed row

    double frame_s() const { return double(hold) * ref.period; }
    // When film frame n's light starts on the reference's row at height h.
    double ref_light(int64_t n, double h_mm) const { return ref.row_light(k0 + int64_t(hold) * n, ref.row_at(h_mm)); }
    // When refresh j's light starts on the follower's row at height h.
    double fol_light(int64_t j, double h_mm) const { return fol.row_light(j, fol.row_at(h_mm)); }
};

// Middle of the height both monitors cover.
inline double shared_aim_mm(const Wave& a, const Wave& b) {
    double top = std::max(a.top_mm, b.top_mm);
    double bottom = std::min(a.top_mm + a.height_mm(), b.top_mm + b.height_mm());
    return 0.5 * (top + bottom);
}

// Film frame shown by the reference in its refresh k; -1 before the clip.
inline int64_t ref_frame(const SeamClock& s, int64_t k) {
    return k >= s.k0 ? (k - s.k0) / s.hold : -1;
}

// Film frame the follower shows in refresh j when it switches whole pictures aimed at one row: it takes frame n
// on the refresh whose light at the aim row starts nearest the reference's, i.e. when that switch is less than
// half a follower refresh after this refresh's start. -1 before the clip.
inline int64_t aimed_frame(const SeamClock& s, int64_t j) {
    const double a = s.fol_light(j, s.aim_mm), half = 0.5 * s.fol.period;
    int64_t guess = int64_t(std::floor((a - s.ref_light(0, s.aim_mm)) / s.frame_s()));
    int64_t best = -1;
    for (int64_t n = std::max<int64_t>(0, guess - 2); n <= guess + 2; ++n)
        if (s.ref_light(n, s.aim_mm) < a + half) best = n;
    return best;
}

// Follower refresh j under the rolling blend: film frame `newer` fades in over `newer - 1`. The share of `newer`
// in light is clamp(w_top + w_per_row * row, 0, 1) (the shader clamps); it is linear in the row because both
// scans are.
struct Blend {
    int64_t newer = 0;
    double w_top = 0, w_per_row = 0;
};

inline Blend rolling_blend(const SeamClock& s, int64_t j) {
    const double T = s.fol.period;
    const double y_last = double(std::max(1, s.fol.rows - 1));
    const double h0 = s.fol.top_mm, h1 = s.fol.top_mm + y_last * s.fol.pitch_mm;
    const double a0 = s.fol_light(j, h0), a1 = s.fol_light(j, h1);
    // The newest film frame whose switch starts before this refresh ends, on the first or the last row. Switches
    // are many refreshes apart, so it is within a frame or two of the one that switched at the first row's start.
    int64_t guess = int64_t(std::floor((a0 - s.ref_light(0, h0)) / s.frame_s()));
    int64_t newer = std::max<int64_t>(0, guess - 2);
    for (int64_t n = newer; n <= guess + 3; ++n)
        if (std::min(s.ref_light(n, h0) - a0, s.ref_light(n, h1) - a1) < T) newer = n;
    Blend b;
    b.newer = newer;
    double w0 = 1.0 - (s.ref_light(newer, h0) - a0) / T;
    double w1 = 1.0 - (s.ref_light(newer, h1) - a1) / T;
    b.w_top = w0;
    b.w_per_row = (w1 - w0) / y_last;
    return b;
}

// Aimed start: the reference refresh (among `candidates` from `first`) on which film frame 0 should start so that
// the follower's nearest refresh lands closest to the reference's switch at the aim row. The clocks keep a fixed
// ratio, so every later switch (a whole number of frames on) falls in the same alignment class. *resid_s =
// follower light start minus reference light start at the aim row.
inline int64_t aim_start(SeamClock s, int64_t first, int candidates, double* resid_s) {
    const double y = s.fol.row_at(s.aim_mm);
    int64_t best_k = first;
    double best = 1e30;
    for (int i = 0; i < candidates; ++i) {
        s.k0 = first + i;
        double e = s.ref_light(0, s.aim_mm);
        int64_t j = s.fol.index_near(e - (y * s.fol.line_s + s.fol.lag_s));
        double r = s.fol.row_light(j, y) - e;
        if (std::fabs(r) < std::fabs(best) - 1e-6) { best = r; best_k = s.k0; }
    }
    if (resid_s) *resid_s = best;
    return best_k;
}

// What the aimed switch leaves: follower light start minus reference light start for film frame n, at the
// follower's top row, the aim row and its bottom row.
struct AimedOffsets {
    double top_s = 0, aim_s = 0, bottom_s = 0;
};

inline AimedOffsets aimed_offsets(const SeamClock& s, int64_t n) {
    const double y_aim = s.fol.row_at(s.aim_mm);
    const double e_aim = s.ref_light(n, s.aim_mm);
    int64_t j = s.fol.index_near(e_aim - (y_aim * s.fol.line_s + s.fol.lag_s));
    auto off = [&](double y) {
        double h = s.fol.top_mm + y * s.fol.pitch_mm;
        return s.fol.row_light(j, y) - s.ref_light(n, h);
    };
    return {off(0), off(y_aim), off(double(s.fol.rows - 1))};
}

// ---- numeric self-check -------------------------------------------------------------------------------------

struct BlendCheckRow {
    int64_t film_frame = 0;
    int row = 0;
    double height_mm = 0;
    double straddle_us = 0;   // reference switch minus the start of the straddling follower refresh
    double share_new = 0;     // new-frame share in that refresh
    double err_us = 0;        // light-weighted switch moment of the follower row minus the reference row's switch
};

// For a few film frames and rows: the moment a follower row's light switches, weighted by light, using the same
// blend numbers (cast to float, clamped like the shader) the renderer sends, against the reference row's switch.
// The row's new-frame light over the refreshes around the switch is w_j of refresh j; an ideal step at time t* has
// the same total as t* = t_first + sum_j (1 - w_j) * T. Returns the largest |error| in microseconds.
inline double check_blend(const SeamClock& s, std::vector<BlendCheckRow>* rows_out) {
    const double T = s.fol.period;
    const int last = std::max(1, s.fol.rows - 1);
    const int rows[] = {0, 1, last / 8, last / 4, last / 2, (3 * last) / 4, last - 1, last};
    double worst = 0;
    for (int64_t n : {int64_t(5), int64_t(12), int64_t(19)}) {
        for (int row : rows) {
            const double h = s.fol.top_mm + double(row) * s.fol.pitch_mm;
            const double e = s.ref_light(n, h);
            const int64_t mid = s.fol.index_near(e - (double(row) * s.fol.line_s + s.fol.lag_s));
            const int64_t j_first = mid - 3;
            const double t_first = s.fol.row_light(j_first, row);
            double sum = 0, straddle_share = 0, straddle_us = 0;
            for (int64_t j = j_first; j <= mid + 3; ++j) {
                Blend b = rolling_blend(s, j);
                float wt = float(b.w_top), wr = float(b.w_per_row);
                float w = std::min(1.0f, std::max(0.0f, wt + wr * float(row)));
                double share = b.newer == n ? double(w) : (b.newer > n ? 1.0 : 0.0);
                if (share > 0 && share < 1) {
                    straddle_share = share;
                    straddle_us = (e - s.fol.row_light(j, row)) * 1e6;
                }
                sum += (1.0 - share) * T;
            }
            double err = (t_first + sum - e) * 1e6;
            worst = std::max(worst, std::fabs(err));
            if (rows_out) rows_out->push_back({n, row, h, straddle_us, straddle_share, err});
        }
    }
    return worst;
}

}  // namespace seam
