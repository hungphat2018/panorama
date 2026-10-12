// seam-row-clock.h: one monitor's row timing for the seam tuner.
//
// Wraps scanline-clock.h. The signal timing (line time, scan, blank) comes from QueryDisplayConfig; the top-row
// times come from D3DKMTGetScanLine samples: a background thread takes about one a millisecond and refits the
// grid every 200 ms over the last 10 s. Refreshes are numbered: refresh k has its top row at
// ref_time + (k - ref_index) * period. The numbering survives refits (each refit re-anchors on the same refresh),
// so "reference refresh k0 + hold * n" stays one film clock for as long as the process runs.
//
// A Wave is a snapshot of that model plus the monitor's geometry and panel lag: plain data, no locking, so the
// timing maths in seam-blend-model.h works on copies.
#pragma once
#include "per-monitor-common.h"
#include "scanline-clock.h"

#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <string>
#include <thread>

namespace seam {

struct Wave {
    double ref_time = 0;      // top-row time (QPC s) of refresh ref_index
    int64_t ref_index = 0;
    double period = 0;        // s per refresh
    double line_s = 0;        // s per picture row
    double scan_s = 0;        // top row to the end of the bottom row
    double blank_s = 0;       // end of the bottom row to the next top row
    double lag_s = 0;         // panel lag: the light comes this much after the signal
    double pitch_mm = 0.25;   // mm per pixel row
    double top_mm = 0;        // height of this monitor's top edge below the first monitor's top edge
    int rows = 0;             // picture rows

    double top_time(int64_t k) const { return ref_time + double(k - ref_index) * period; }
    int64_t index_near(double t) const { return ref_index + int64_t(std::llround((t - ref_time) / period)); }
    // First refresh whose top row comes at or after t.
    int64_t index_after(double t) const { return ref_index + int64_t(std::ceil((t - ref_time) / period - 1e-9)); }
    // When the light of row y (0 = top) of refresh k comes on: top row + y lines + panel lag.
    double row_light(int64_t k, double y) const { return top_time(k) + y * line_s + lag_s; }
    // Row (fractional) at physical height h_mm.
    double row_at(double h_mm) const { return (h_mm - top_mm) / pitch_mm; }
    double height_mm() const { return double(rows) * pitch_mm; }
};

class RowClock {
public:
    RowClock() = default;
    RowClock(const RowClock&) = delete;
    RowClock& operator=(const RowClock&) = delete;
    ~RowClock() {
        stop();
        scan_grid_free(&grid_);
        if (opened_) scan_source_close(&src_);
    }

    // Reads the exact signal timing and opens the scan-line source. gdi_name: L"\\\\.\\DISPLAY1".
    bool open(const wchar_t* gdi_name, std::string* why) {
        if (!scan_timing_query(gdi_name, &tm_)) { *why = "QueryDisplayConfig has no active path with timing for this display"; return false; }
        if (tm_.interlaced) { *why = "interlaced mode"; return false; }
        if (!scan_source_open(gdi_name, &src_)) { *why = "D3DKMTOpenAdapterFromGdiDisplayName failed"; return false; }
        opened_ = true;
        double t;
        scan_sample_top(&src_, &tm_, &t);  // resolves D3DKMTGetScanLine once, here, before any sampler thread runs
        nominal_ = 1.0 / tm_.refresh_hz;
        return true;
    }

    void start() {
        stop_ = false;
        thread_ = std::thread([this] { run(); });
    }
    void stop() {
        stop_ = true;
        if (thread_.joinable()) thread_.join();
    }

    // True once the fit rests on about a second of samples.
    bool ready() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return fits_ >= 3 && used_ >= 500;
    }

    // The timing model; geometry and lag are left for the caller to fill in.
    Wave wave() const {
        Wave w;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            w.ref_time = ref_time_;
            w.ref_index = ref_index_;
            w.period = period_;
        }
        w.line_s = tm_.line_s;
        w.scan_s = tm_.scan_s;
        w.blank_s = tm_.blank_s;
        return w;
    }

    const scan_timing& timing() const { return tm_; }

    void quality(double* p50_us, double* p95_us, size_t* used) const {
        std::lock_guard<std::mutex> lock(mutex_);
        *p50_us = p50_us_;
        *p95_us = p95_us_;
        *used = used_;
    }

    // Refits refused because they disagreed with the running model (expected: 0).
    int rejects() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return rejects_;
    }

private:
    static constexpr double kWindowS = 10.0;     // fit over the last 10 s
    static constexpr size_t kKeepMax = 40000;    // samples kept before the oldest half goes
    static constexpr size_t kKeepTo = 20000;

    void run() {
        SetThreadPriority(GetCurrentThread(), THREAD_PRIORITY_HIGHEST);
        double last_fit = 0;
        while (!stop_) {
            double t;
            if (scan_sample_top(&src_, &tm_, &t)) scan_grid_add(&grid_, t);
            double now = scan_now_s();
            if (now - last_fit >= 0.2) {
                last_fit = now;
                refit(now);
            }
            Sleep(1);
        }
    }

    void refit(double now) {
        if (grid_.n > kKeepMax) {
            std::memmove(grid_.t, grid_.t + (grid_.n - kKeepTo), kKeepTo * sizeof(double));
            grid_.n = kKeepTo;
        }
        if (!scan_grid_fit(&grid_, nominal_, kWindowS)) return;
        double anchor = scan_grid_top_nearest(&grid_, now - 0.05);  // a fitted top row just behind the newest samples
        std::lock_guard<std::mutex> lock(mutex_);
        if (fits_) {
            // The new line must agree with the running one to well under a refresh (they differ by drift: ~1 us),
            // or the refresh numbering would slip by one. If not, keep the old model and count the refusal.
            double steps = std::round((anchor - ref_time_) / period_);
            if (std::fabs(anchor - (ref_time_ + steps * period_)) > 0.25 * period_) {
                ++rejects_;
                return;
            }
            ref_index_ += int64_t(steps);
        }
        ref_time_ = anchor;
        period_ = grid_.period;
        p50_us_ = grid_.resid_p50_us;
        p95_us_ = grid_.resid_p95_us;
        used_ = grid_.used;
        ++fits_;
    }

    scan_timing tm_{};
    scan_source src_{};
    scan_grid grid_{};
    bool opened_ = false;
    double nominal_ = 0;
    std::atomic<bool> stop_{false};
    std::thread thread_;
    mutable std::mutex mutex_;
    double ref_time_ = 0, period_ = 0, p50_us_ = 0, p95_us_ = 0;
    int64_t ref_index_ = 0;
    size_t used_ = 0;
    int fits_ = 0, rejects_ = 0;
};

}  // namespace seam
