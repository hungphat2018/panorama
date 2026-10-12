// Shared by the per-monitor spikes: a QPC clock in seconds, a precise sleep, and each monitor's refresh grid.
#pragma once
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <vector>

inline double& qpc_freq() {
    static double freq = [] { LARGE_INTEGER f; QueryPerformanceFrequency(&f); return double(f.QuadPart); }();
    return freq;
}

inline double now_s() {
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return double(t.QuadPart) / qpc_freq();
}

inline void sleep_until(double t) {
    for (;;) {
        double left = t - now_s();
        if (left <= 0) return;
        if (left > 0.002) Sleep(DWORD((left - 0.0015) * 1000));
        else YieldProcessor();
    }
}

// Refresh grid of one monitor, fitted to its recent vblank times: vblank k at base + k * period.
// Each vblank is indexed from the gap to the previous one (robust to jitter and to a missed wake-up),
// then a least-squares line over the last 240 vblanks gives period and phase.
class VblankClock {
public:
    void add(double t) {
        std::lock_guard<std::mutex> lock(mutex_);
        log_.push_back(t);
        if (period_ == 0) {
            if (log_.size() < 32) return;
            std::vector<double> d;  // first estimate: median spacing of the first vblanks
            for (size_t i = 1; i < log_.size(); ++i) d.push_back(log_[i] - log_[i - 1]);
            std::nth_element(d.begin(), d.begin() + d.size() / 2, d.end());
            period_ = d[d.size() / 2];
            index_.assign(1, 0);
            for (size_t i = 1; i < log_.size(); ++i) index_.push_back(next_index(i));
        } else {
            index_.push_back(next_index(log_.size() - 1));
        }
        size_t first = log_.size() > 240 ? log_.size() - 240 : 0;
        double sk = 0, st = 0, skk = 0, skt = 0, n = double(log_.size() - first);
        for (size_t i = first; i < log_.size(); ++i) {
            double k = double(index_[i]), ti = log_[i] - log_[first];
            sk += k; st += ti; skk += k * k; skt += k * ti;
        }
        double denom = n * skk - sk * sk;
        if (denom > 0) {
            double period = (n * skt - sk * st) / denom;
            if (period > 0.001 && period < 0.1) {
                period_ = period;
                base_ = log_[first] + (st - period * sk) / n;  // time of vblank index 0 on the fitted line
            }
        }
        valid_ = true;
    }
    bool valid() { std::lock_guard<std::mutex> lock(mutex_); return valid_; }
    double period() { std::lock_guard<std::mutex> lock(mutex_); return period_; }
    // First predicted vblank at or after t.
    double next_after(double t) {
        std::lock_guard<std::mutex> lock(mutex_);
        double k = std::ceil((t - base_) / period_ - 1e-9);
        return base_ + k * period_;
    }
    double nearest(double t) {
        std::lock_guard<std::mutex> lock(mutex_);
        return base_ + std::round((t - base_) / period_) * period_;
    }
    // Index of the refresh nearest t, and the fitted time of refresh index i.
    int64_t index_near(double t) {
        std::lock_guard<std::mutex> lock(mutex_);
        return int64_t(std::llround((t - base_) / period_));
    }
    double time_of(int64_t i) {
        std::lock_guard<std::mutex> lock(mutex_);
        return base_ + double(i) * period_;
    }
    // The fitted line as a whole, read under one lock: refresh index i starts at base + i * period.
    void line(double* base, double* period) {
        std::lock_guard<std::mutex> lock(mutex_);
        *base = base_;
        *period = period_;
    }
    std::vector<double> log() { std::lock_guard<std::mutex> lock(mutex_); return log_; }

private:
    int64_t next_index(size_t i) {
        double gap = (log_[i] - log_[i - 1]) / period_;
        return index_.back() + std::max<int64_t>(1, int64_t(std::llround(gap)));
    }
    std::mutex mutex_;
    std::vector<double> log_;
    std::vector<int64_t> index_;
    double base_ = 0, period_ = 0;
    bool valid_ = false;
};
