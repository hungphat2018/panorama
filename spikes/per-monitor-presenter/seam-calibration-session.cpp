// seam-calibration-session.cpp: forced-choice calibration (see seam-calibration-session.h).
#define _CRT_SECURE_NO_WARNINGS
#include "seam-calibration-session.h"

#include "seam-settings.h"

#include <algorithm>
#include <cstdio>
#include <random>

namespace seam {

namespace {

enum Param { kHeight = 0, kGap = 1, kLag = 2, kParams = 3 };

struct ParamInfo {
    const char* id;
    const wchar_t* unit;
    double start_step;      // first round compares centre -/+ this
    unsigned flags;         // pattern parts shown
    const wchar_t* title;
    const wchar_t* look;    // what "straight" means for this parameter, in the anchor
    const wchar_t* ask;     // the question after the two settings
};

const ParamInfo kInfo[kParams] = {
    {"height", L"mm", 4.0, kLines, L"Height (1 of 3)",
     L"Look at the thin horizontal lines. Behind the fake bezel (the black band inside this screen, to the right of this text) each line runs straight on: "
     L"both sides of the band are one panel. That is what 'straight' looks like.\n\n"
     L"At the real bezel between the two screens the right screen may sit a little higher or lower, so the lines step.",
     L"in which did the thin horizontal lines run straighter across the real bezel?"},
    {"gap", L"mm", 8.0, kStripes, L"Gap (2 of 3)",
     L"Look at the diagonal stripes. Behind the fake bezel (the black band inside this screen, to the right of this text) each stripe runs on as one "
     L"straight line. That is what 'straight' looks like.\n\nAt the real bezel a wrong gap makes a stripe jump sideways.",
     L"in which did the diagonal stripes run on straighter across the real bezel?"},
    {"lag", L"ms", 4.0, kStripes, L"Lag of the right screen (3 of 3)",
     L"The stripes now slide right, then left. Behind the fake bezel (the black band inside this screen, to the right of this text) they stay straight both "
     L"ways. That is what 'straight' looks like.\n\n"
     L"At the real bezel a lag error breaks the stripes one way sliding right and the other way sliding left.",
     L"in which did the stripes stay straighter across the real bezel, sliding right and sliding left?"},
};

double get_param(const Settings& s, int p) { return p == kHeight ? s.height_mm : (p == kGap ? s.gap_mm : s.lag_ms[1]); }

void set_param(Settings* s, int p, double v) {
    if (p == kHeight) s->height_mm = v;
    else if (p == kGap) s->gap_mm = std::max(0.0, v);
    else s->lag_ms[1] = v;
}

std::wstring num(double v, const wchar_t* unit) {
    wchar_t buf[48];
    swprintf(buf, 48, L"%+.3f %s", v, unit);
    return buf;
}

class CalibrationSession : public Session {
public:
    explicit CalibrationSession(const CalibrationOptions& o) : opt_(o) {
        auto_ = !o.auto_answer.empty();
        show_s_ = o.quick ? 0.25 : 2.5;
        auto_delay_ = o.quick ? 0.03 : 0.4;
    }

    void start() override {
        rng_.seed(opt_.seed ? opt_.seed : std::random_device{}());
        log_name_ = L"seam-calibration-" + local_stamp() + L".jsonl";
        log_ = make_dirs(opt_.out_dir) ? _wfopen((opt_.out_dir + L"\\" + log_name_).c_str(), L"w") : nullptr;
        if (!log_) {
            fprintf(stderr, "can't write the calibration log in %ls\n", opt_.out_dir.c_str());
            g_stop = true;
            return;
        }
        fprintf(log_, "{\"type\":\"session\",\"time\":\"%s\",\"rounds\":%d,\"windowed\":%s,\"quick\":%s,\"auto_answer\":\"%s\",\"refresh_hz\":{", local_iso().c_str(),
                opt_.rounds, g_windowed ? "true" : "false", opt_.quick ? "true" : "false", opt_.auto_answer.c_str());
        for (size_t i = 0; i < g_monitors.size(); ++i)
            fprintf(log_, "%s\"%s\":%.4f", i ? "," : "", utf8(g_monitors[i]->label).c_str(), 1.0 / g_monitors[i]->clock.wave().period);
        fprintf(log_, "},\"start\":{\"height_mm\":%.3f,\"gap_mm\":%.3f,\"lag_ms\":%.3f}}\n", g_set.height_mm, g_set.gap_mm, g_set.lag_ms[1]);
        begin_param();
    }

    void on_key(WPARAM key, bool) override {
        if (phase_ == Phase::Anchor && key == VK_RETURN) {
            if (back_to_question_) show_question();
            else begin_round();
        } else if (phase_ == Phase::Question) {
            if (key == '1' || key == '2') answer(int(key - '0'), "key");
            else if (key == 'R') show_setting(0);
            else if (key == 'A') show_anchor(true);
        }
    }

    void tick(double now) override {
        switch (phase_) {
            case Phase::Anchor:
                if (auto_ && now >= auto_at_) begin_round();
                break;
            case Phase::Show:
                if (now >= show_end_) {
                    if (showing_ == 0) show_setting(1);
                    else show_question();
                }
                break;
            case Phase::Question:
                if (auto_ && now >= auto_at_) answer(auto_choice(), ("auto-" + opt_.auto_answer).c_str());
                break;
            case Phase::Done:
                if (auto_ && now >= auto_at_) finished_ = true;
                break;
        }
    }

    bool finished() const override { return finished_; }

    void finish() override {
        if (log_) {
            fprintf(log_, "{\"type\":\"end\",\"done\":%zu,\"time\":\"%s\"}\n", done_.size(), local_iso().c_str());
            fclose(log_);
            log_ = nullptr;
        }
        if (done_.empty()) {
            wprintf(L"no parameter finished: settings not written\n");
            return;
        }
        CalibrationInfo cal;
        cal.present = true;
        cal.complete = done_.size() == kParams;
        cal.done = done_;
        cal.rounds = opt_.rounds;
        cal.log_name = utf8(log_name_);
        save_settings(opt_.out_dir + L"\\seam-settings-" + local_stamp() + L".json", g_set, &cal);
        save_settings(opt_.out_dir + L"\\seam-settings-last.json", g_set, &cal);
    }

private:
    enum class Phase { Anchor, Show, Question, Done };

    void begin_param() {
        centre_ = get_param(g_set, p_);
        step_ = kInfo[p_].start_step;
        round_ = 0;
        show_anchor(false);
    }

    // The pattern at the real seam with parameter p_ set to v and the others at their best so far.
    std::shared_ptr<Scene> pattern_scene(double v, bool anchor) const {
        Settings s = g_set;
        set_param(&s, p_, v);
        auto sc = new_scene(s);
        sc->kind = Kind::Tuner;
        sc->flags = kInfo[p_].flags;
        sc->rows = true;
        if (p_ == kLag) {
            sc->speed = 300;
            sc->half_cycle = anchor ? 1.25 : 0.5 * show_s_;  // one slide right, one slide left per setting
            sc->motion_t0 = now_s();
        }
        return sc;
    }

    void show_anchor(bool from_question) {
        phase_ = Phase::Anchor;
        back_to_question_ = from_question;
        auto sc = pattern_scene(centre_, true);
        sc->strip_x0 = kFakeBezelCentreMm - 0.5 * kFakeBezelWidthMm;
        sc->strip_x1 = kFakeBezelCentreMm + 0.5 * kFakeBezelWidthMm;
        std::wstring text = std::wstring(kInfo[p_].title) + L"\n\n";
        if (!from_question && !result_line_.empty()) text += result_line_ + L"\n\n";
        text += std::wstring(kInfo[p_].look) + L"\n\nYou will see two settings in turn, 1 then 2 (" +
                std::to_wstring(opt_.rounds) + L" rounds). Say in which the pattern runs straighter across the REAL bezel.\n\n" +
                (from_question ? L"Enter: back to the question" : L"Enter: start      Esc: quit and save");
        sc->text = {card_text(text, 900)};
        scene_post(sc);
        auto_at_ = now_s() + 0.4;
    }

    void begin_round() {
        double a = centre_ - step_, b = centre_ + step_;
        if (rng_() & 1) std::swap(a, b);
        shown_[0] = a;
        shown_[1] = b;
        show_setting(0);
    }

    void show_setting(int k) {
        phase_ = Phase::Show;
        showing_ = k;
        auto sc = pattern_scene(shown_[k], false);
        TextBlock label;
        label.text = k == 0 ? L"1" : L"2";
        label.x = 30;
        label.y = 20;
        label.w = 190;
        label.h = 200;
        label.font = 2;
        sc->text = {label};
        scene_post(sc);
        show_end_ = now_s() + show_s_;
    }

    void show_question() {
        phase_ = Phase::Question;
        auto sc = new_scene(g_set);
        sc->kind = Kind::Black;
        sc->text = {card_text(std::wstring(kInfo[p_].title) + L", round " + std::to_wstring(round_ + 1) + L" of " + std::to_wstring(opt_.rounds) +
                              L"\n\n1 or 2: " + kInfo[p_].ask + L"\n\nPress 1 or 2.\nR: show both again      A: see the anchor again      Esc: quit and save", 440)};
        scene_post(sc);
        auto_at_ = now_s() + auto_delay_;
    }

    int auto_choice() {
        double truth = p_ == kHeight ? opt_.sim_height_mm : (p_ == kGap ? opt_.sim_gap_mm : opt_.sim_lag_ms);
        int nearer = std::fabs(shown_[0] - truth) <= std::fabs(shown_[1] - truth) ? 1 : 2;
        if (opt_.auto_answer == "right") return nearer;
        if (opt_.auto_answer == "wrong") return 3 - nearer;
        return 1 + int(rng_() & 1);
    }

    void answer(int choice, const char* source) {
        const double picked = shown_[choice - 1];
        fprintf(log_, "{\"type\":\"round\",\"param\":\"%s\",\"round\":%d,\"centre\":%.4f,\"step\":%.4f,\"shown\":[%.4f,%.4f],\"answer\":%d,\"picked\":%.4f,\"source\":\"%s\"}\n",
                kInfo[p_].id, round_ + 1, centre_, step_, shown_[0], shown_[1], choice, picked, source);
        fflush(log_);
        wprintf(L"%hs round %d: %.3f or %.3f -> %d (%hs), now %.3f\n", kInfo[p_].id, round_ + 1, shown_[0], shown_[1], choice, source, picked);
        centre_ = picked;
        step_ *= 0.5;
        if (++round_ < opt_.rounds) begin_round();
        else finish_param();
    }

    void finish_param() {
        set_param(&g_set, p_, centre_);
        done_.push_back(kInfo[p_].id);
        fprintf(log_, "{\"type\":\"result\",\"param\":\"%s\",\"value\":%.4f,\"last_step\":%.4f}\n", kInfo[p_].id, get_param(g_set, p_), step_ * 2);
        fflush(log_);
        std::wstring name = p_ == kHeight ? L"Height" : (p_ == kGap ? L"Gap" : L"Lag of the right screen");
        result_line_ = name + L" set to " + num(get_param(g_set, p_), kInfo[p_].unit) + L".";
        wprintf(L"%s\n", result_line_.c_str());
        if (++p_ < kParams) {
            begin_param();
            return;
        }
        phase_ = Phase::Done;
        auto sc = new_scene(g_set);
        sc->kind = Kind::Black;
        sc->text = {card_text(L"Done.\n\nHeight " + num(g_set.height_mm, L"mm") + L"\nGap " + num(g_set.gap_mm, L"mm") + L"\nLag of the right screen " +
                              num(g_set.lag_ms[1], L"ms") + L"\n\nSaved when you quit.\nEsc: quit", 420)};
        scene_post(sc);
        auto_at_ = now_s() + 0.5;
    }

    CalibrationOptions opt_;
    std::mt19937 rng_;
    Phase phase_ = Phase::Anchor;
    bool auto_ = false, back_to_question_ = false, finished_ = false;
    double show_s_ = 2.5, auto_delay_ = 0.4;
    int p_ = 0, round_ = 0, showing_ = 0;
    double centre_ = 0, step_ = 0, shown_[2] = {};
    double show_end_ = 0, auto_at_ = 0;
    std::wstring result_line_, log_name_;
    std::vector<std::string> done_;
    FILE* log_ = nullptr;
};

}  // namespace

bool calibration_can_run(std::string* why) {
    if (g_monitors.size() < 2) { *why = "calibration needs two monitors"; return false; }
    return true;
}

std::unique_ptr<Session> make_calibration_session(const CalibrationOptions& options) {
    return std::make_unique<CalibrationSession>(options);
}

}  // namespace seam
