// seam-x2-session.cpp: the film-step test (see seam-x2-session.h).
//
// Timeline of a trial, all on the reference monitor's refresh clock so both screens agree on it:
//   pre-roll card (the question, on the laptop)  ->  clip 1  ->  black  ->  clip 2  ->  question card until 1 or 2.
// Each clip starts on the reference refresh that aim_start() picks (best alignment at the aim row) at least the
// lead time after it is posted; clip frames are numbered from there, so the render threads need nothing else.
#define _CRT_SECURE_NO_WARNINGS
#include "seam-x2-session.h"

#include "seam-settings.h"

#include <algorithm>
#include <cstdio>
#include <map>
#include <random>

namespace seam {

namespace {

struct Timing {
    double pre_roll, replay_roll, black, clip_s, auto_delay;
    int reach_frames;  // bar path limited to this many frames either side of the bezel; 0 = whole screens
};
const Timing kNormal{1.5, 0.8, 0.5, 3.0, 0.4, 0};
const Timing kQuick{0.2, 0.2, 0.1, 0.3, 0.03, 3};

struct Trial {
    char comp;          // 'a' real bezel: aimed vs blend; 'b' laptop alone with a fake bezel: plain vs blend
    int speed;          // mm/s
    bool target_first;  // the clip that shows the effect (aimed for a, blend for b) is clip 1
};

const char* cond_name(Cond c) { return c == Cond::Aimed ? "aimed" : "blend"; }

class X2Session : public Session {
public:
    explicit X2Session(const X2Options& o) : opt_(o), timing_(o.quick ? kQuick : kNormal) { auto_ = !o.auto_answer.empty(); }

    void start() override {
        const Wave ref = g_monitors[size_t(g_master)]->clock.wave();
        const Wave fol = g_monitors[0]->clock.wave();
        hold_ = int(std::max<long long>(1, std::llround(1.0 / (kFilmFps * ref.period))));
        fol_hold_ = int(std::max<long long>(1, std::llround(1.0 / (kFilmFps * fol.period))));
        film_fps_ = 1.0 / (double(hold_) * ref.period);
        seed_ = opt_.seed ? opt_.seed : std::random_device{}();
        rng_.seed(seed_);
        make_trials();
        path_ = opt_.out_dir + L"\\seam-x2-answers-" + local_stamp() + L".jsonl";
        log_ = make_dirs(opt_.out_dir) ? _wfopen(path_.c_str(), L"w") : nullptr;
        if (!log_) {
            fprintf(stderr, "can't write %ls\n", path_.c_str());
            g_stop = true;
            return;
        }
        write_session_line();
        wprintf(L"film clock: %.3f fps (reference %s: %d refreshes per frame; laptop: %d)%s\n", film_fps_,
                g_monitors[size_t(g_master)]->label.c_str(), hold_, fol_hold_,
                std::fabs(film_fps_ / kFilmFps - 1) > 0.005 ? L"  NOT 24 fps: use --refresh for an even 24 fps pace" : L"");
        wprintf(L"%zu trials; answers go to %s\n", trials_.size(), path_.c_str());
        show_anchor(false);
    }

    void on_key(WPARAM key, bool) override {
        if (phase_ == Phase::Anchor && key == VK_RETURN) {
            if (anchor_from_question_) show_question();
            else begin_trial();
        } else if (phase_ == Phase::Question) {
            if (key == '1' || key == '2') answer(int(key - '0'), "key");
            else if (key == 'R') { ++replays_; play_clips(timing_.replay_roll); }
            else if (key == 'A') show_anchor(true);
        }
    }

    void tick(double now) override {
        switch (phase_) {
            case Phase::Anchor:
                if (auto_ && now >= auto_at_) begin_trial();
                break;
            case Phase::Clips:
                if (slot_ == 0 && now >= t_end_[0] + 0.02) {
                    slot_ = 1;
                    post_clip(1, t_end_[0] + timing_.black);
                } else if (slot_ == 1 && now >= t_end_[1] + 0.02) {
                    show_question();
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
        if (!log_) return;
        fprintf(log_, "{\"type\":\"end\",\"answered\":%d,\"of\":%zu,\"time\":\"%s\"}\n", idx_, trials_.size(), local_iso().c_str());
        fclose(log_);
        log_ = nullptr;
        std::map<std::pair<char, int>, std::pair<int, int>> tally;  // (comparison, speed) -> right, answered
        for (const auto& r : results_) {
            auto& t = tally[{r.first.first, r.first.second}];
            t.first += r.second ? 1 : 0;
            t.second += 1;
        }
        for (const auto& t : tally)
            wprintf(L"  %c at %d mm/s: %d of %d right\n", wchar_t(t.first.first), t.first.second, t.second.first, t.second.second);
        wprintf(L"%d of %zu trials answered; score with: python seam-score-x2-trials.py \"%s\"\n", idx_, trials_.size(), path_.c_str());
    }

private:
    enum class Phase { Anchor, Clips, Question, Done };

    // Per speed block, per condition: trials_per_condition trials, the effect-bearing clip first in half of them,
    // shuffled within the block (speeds run in ascending order).
    void make_trials() {
        std::vector<int> speeds;
        for (const auto& c : opt_.conditions)
            if (std::find(speeds.begin(), speeds.end(), c.second) == speeds.end()) speeds.push_back(c.second);
        std::sort(speeds.begin(), speeds.end());
        for (int speed : speeds) {
            std::vector<Trial> block;
            for (const auto& c : opt_.conditions) {
                if (c.second != speed) continue;
                int n = opt_.trials_per_condition, first = n / 2 + ((n % 2) ? int(rng_() & 1) : 0);
                for (int i = 0; i < n; ++i) block.push_back({c.first, speed, i < first});
            }
            std::shuffle(block.begin(), block.end(), rng_);
            trials_.insert(trials_.end(), block.begin(), block.end());
        }
    }

    void write_session_line() {
        const double aim = make_seam(0, g_set.height_mm, g_set.lag_ms).aim_mm;
        fprintf(log_, "{\"type\":\"session\",\"tool\":\"seam-tuner x2\",\"time\":\"%s\",\"seed\":%u,\"film_fps\":%.5f,", local_iso().c_str(),
                seed_, film_fps_);
        fprintf(log_, "\"hold_reference\":%d,\"hold_follower\":%d,\"refresh_hz\":{", hold_, fol_hold_);
        for (size_t i = 0; i < g_monitors.size(); ++i)
            fprintf(log_, "%s\"%s\":%.4f", i ? "," : "", utf8(g_monitors[i]->label).c_str(), 1.0 / g_monitors[i]->clock.wave().period);
        fprintf(log_, "},\"reference\":\"%s\",\"windowed\":%s,\"quick\":%s,\"auto_answer\":\"%s\",", utf8(g_monitors[size_t(g_master)]->label).c_str(),
                g_windowed ? "true" : "false", opt_.quick ? "true" : "false", opt_.auto_answer.c_str());
        fprintf(log_, "\"gap_mm\":%.3f,\"height_mm\":%.3f,\"lag_ms\":[", g_set.gap_mm, g_set.height_mm);
        for (size_t i = 0; i < g_set.lag_ms.size(); ++i) fprintf(log_, "%s%.3f", i ? "," : "", g_set.lag_ms[i]);
        fprintf(log_, "],\"bar_width_mm\":%.1f,\"clip_seconds\":%.1f,\"trials\":%zu,\"aim_mm\":%.2f}\n", kBarWidthMm, timing_.clip_s,
                trials_.size(), aim);
    }

    BarPath path_for(int speed, bool fake) const {
        const Monitor& first = *g_monitors[0];
        if (fake) return make_bar_path(speed, kFakeBezelCentreMm, first.width_mm, timing_.reach_frames);
        double right = world_left_mm(g_master, g_set.gap_mm) + g_monitors[size_t(g_master)]->width_mm;
        return make_bar_path(speed, first.width_mm + 0.5 * g_set.gap_mm, right, timing_.reach_frames);
    }

    // Posts a clip starting on the best-aligned reference refresh at or after `earliest`. slot: 0 or 1 of the trial.
    void post_clip(int slot, double earliest) {
        const Trial& t = trials_[size_t(idx_)];
        FilmClip c;
        c.cond = order_[slot];
        c.fake_bezel = t.comp == 'b';
        c.hold = hold_;
        c.path = path_for(t.speed, c.fake_bezel);
        c.frames = clip_frames(c.path, timing_.clip_s);
        SeamClock s = make_seam(0, g_set.height_mm, g_set.lag_ms);
        s.hold = hold_;
        c.k0 = aim_start(s, s.ref.index_after(earliest), 24, nullptr);
        s.k0 = c.k0;
        aimed_[slot] = aimed_offsets(s, 5);  // what the aimed switch leaves with this start (logged with the answer)
        t_end_[slot] = s.ref.top_time(c.k0 + int64_t(c.hold) * c.frames);
        auto sc = new_scene(g_set);
        sc->kind = Kind::Film;
        sc->clip = c;
        sc->text_until_clip = true;
        if (c.fake_bezel) {
            sc->strip_x0 = kFakeBezelCentreMm - 0.5 * kFakeBezelWidthMm;
            sc->strip_x1 = kFakeBezelCentreMm + 0.5 * kFakeBezelWidthMm;
        }
        if (slot == 0) sc->text = {card_text(pre_roll_text())};
        scene_post(sc);
    }

    void show_anchor(bool from_question) {
        phase_ = Phase::Anchor;
        anchor_from_question_ = from_question;
        const int speed = trials_.empty() ? 300 : trials_[size_t(std::min<size_t>(size_t(idx_), trials_.size() - 1))].speed;
        FilmClip c;
        c.cond = Cond::Aimed;  // plain whole-picture switch: both sides of the strip are one panel
        c.fake_bezel = true;
        c.hold = hold_;
        c.path = path_for(speed, true);
        c.frames = int64_t(1) << 40;  // loops until Enter
        SeamClock s = make_seam(0, g_set.height_mm, g_set.lag_ms);
        s.hold = hold_;
        c.k0 = aim_start(s, s.ref.index_after(now_s() + 0.3), 24, nullptr);
        auto sc = new_scene(g_set);
        sc->kind = Kind::Film;
        sc->clip = c;
        sc->strip_x0 = kFakeBezelCentreMm - 0.5 * kFakeBezelWidthMm;
        sc->strip_x1 = kFakeBezelCentreMm + 0.5 * kFakeBezelWidthMm;
        std::wstring text = L"Anchor: what zero looks like\n\n"
                            L"The white bar crosses a fake bezel: a 20 mm black strip inside this screen, to the right of this text. "
                            L"You can't see the strip itself, only the bar disappearing behind it and coming out the other side. "
                            L"Both sides of the strip are one panel, so the top and bottom of the bar change picture together there.\n\n"
                            L"In each trial you see two clips, 1 and 2, and say which one shows the effect asked about. "
                            L"The question is shown before each trial and again after the clips.\n\n";
        if (!from_question) {
            const size_t minutes = (trials_.size() * 12 + 59) / 60;  // about 12 s a trial
            text += std::to_wstring(trials_.size()) + (trials_.size() == 1 ? L" trial, about " : L" trials, about ") + std::to_wstring(minutes) +
                    (minutes == 1 ? L" minute.\n\n" : L" minutes.\n\n");
        }
        if (std::fabs(film_fps_ / kFilmFps - 1) > 0.005) text += L"Note: the film runs at " + std::to_wstring(film_fps_).substr(0, 5) + L" fps, not 24.\n\n";
        text += from_question ? L"Enter: back to the question" : L"Enter: start the trials      Esc: quit and save";
        sc->text = {card_text(text, 860)};
        scene_post(sc);
        auto_at_ = now_s() + 0.6;
    }

    void begin_trial() {
        const Trial& t = trials_[size_t(idx_)];
        Cond target = t.comp == 'a' ? Cond::Aimed : Cond::Blend;
        Cond other = target == Cond::Aimed ? Cond::Blend : Cond::Aimed;
        order_[0] = t.target_first ? target : other;
        order_[1] = t.target_first ? other : target;
        replays_ = 0;
        for (size_t i = 0; i < g_monitors.size(); ++i) {
            late_at_start_[i] = g_monitors[i]->late.load();
            early_at_start_[i] = g_monitors[i]->early.load();
        }
        // The question card is read in full for the first trials; later it only needs a glance.
        play_clips(idx_ < 4 ? timing_.pre_roll : 0.65 * timing_.pre_roll);
    }

    void play_clips(double lead) {
        phase_ = Phase::Clips;
        slot_ = 0;
        post_clip(0, now_s() + lead);
    }

    std::wstring effect_question() const {
        return trials_[size_t(idx_)].comp == 'a' ? L"in which do the bar's top and bottom come out of the bezel at different moments?"
                                                 : L"which shows a faint double edge?";
    }

    // The same thing in other words, for anyone unsure what to look for.
    std::wstring effect_gloss() const {
        return trials_[size_t(idx_)].comp == 'a'
                   ? L"(The upper part of the bar reappears a moment before or after its lower part.)"
                   : L"(A second, fainter copy of the bar's edge right beside it, for a moment.)";
    }

    std::wstring pre_roll_text() const {
        const Trial& t = trials_[size_t(idx_)];
        std::wstring s = L"Trial " + std::to_wstring(idx_ + 1) + L" of " + std::to_wstring(trials_.size()) + (replays_ ? L"  (replay)" : L"") + L"\n\n";
        s += t.comp == 'a' ? L"Two clips follow. Watch the bar come out of the real bezel, between the two screens.\n\n"
                           : L"Two clips follow, on this screen only. Watch the bar's edges as it passes behind the fake bezel (the invisible black strip).\n\n";
        return s + L"Question: " + effect_question() + L"\n" + effect_gloss();
    }

    void show_question() {
        phase_ = Phase::Question;
        auto sc = new_scene(g_set);
        sc->kind = Kind::Black;
        sc->text = {card_text(L"Trial " + std::to_wstring(idx_ + 1) + L" of " + std::to_wstring(trials_.size()) + L"\n\n1 or 2: " + effect_question() +
                              L"\n" + effect_gloss() + L"\n\nPress 1 or 2.\nR: replay both clips      A: see the anchor again      Esc: quit and save", 480)};
        scene_post(sc);
        question_t0_ = now_s();
        auto_at_ = question_t0_ + timing_.auto_delay;
    }

    int auto_choice() {
        const Trial& t = trials_[size_t(idx_)];
        int truth = t.target_first ? 1 : 2;
        if (opt_.auto_answer == "right") return truth;
        if (opt_.auto_answer == "wrong") return 3 - truth;
        return 1 + int(rng_() & 1);
    }

    void answer(int choice, const char* source) {
        const Trial& t = trials_[size_t(idx_)];
        const int truth = t.target_first ? 1 : 2;
        const bool correct = choice == truth;
        const Cond target = t.comp == 'a' ? Cond::Aimed : Cond::Blend;
        fprintf(log_, "{\"type\":\"answer\",\"trial\":%d,\"comparison\":\"%c\",\"speed_mm_s\":%d,\"clip1\":\"%s\",\"clip2\":\"%s\",", idx_ + 1, t.comp,
                t.speed, cond_name(order_[0]), cond_name(order_[1]));
        fprintf(log_, "\"target\":\"%s\",\"truth\":%d,\"answer\":%d,\"correct\":%s,\"source\":\"%s\",\"replays\":%d,\"seconds_to_answer\":%.2f,", cond_name(target),
                truth, choice, correct ? "true" : "false", source, replays_, now_s() - question_t0_);
        fprintf(log_, "\"aimed_offsets_ms\":{\"top\":%.3f,\"aim\":%.3f,\"bottom\":%.3f},\"late_presents\":{", aimed_[0].top_s * 1e3, aimed_[0].aim_s * 1e3,
                aimed_[0].bottom_s * 1e3);
        for (size_t i = 0; i < g_monitors.size(); ++i)
            fprintf(log_, "%s\"%s\":%d", i ? "," : "", utf8(g_monitors[i]->label).c_str(), g_monitors[i]->late.load() - late_at_start_[i]);
        fprintf(log_, "},\"early_presents\":{");
        for (size_t i = 0; i < g_monitors.size(); ++i)
            fprintf(log_, "%s\"%s\":%d", i ? "," : "", utf8(g_monitors[i]->label).c_str(), g_monitors[i]->early.load() - early_at_start_[i]);
        fprintf(log_, "}}\n");
        fflush(log_);
        results_.push_back({{t.comp, t.speed}, correct});
        wprintf(L"trial %d/%zu  %c at %d mm/s: answered %d (%hs), truth %d -> %hs\n", idx_ + 1, trials_.size(), wchar_t(t.comp), t.speed, choice,
                source, truth, correct ? "right" : "wrong");
        ++idx_;
        if (size_t(idx_) >= trials_.size()) {
            phase_ = Phase::Done;
            auto sc = new_scene(g_set);
            sc->kind = Kind::Black;
            sc->text = {card_text(L"Done: " + std::to_wstring(trials_.size()) + L" answers saved.\n\nScore them with\npython seam-score-x2-trials.py\non the answers file named in the console.\n\nEsc: quit", 360)};
            scene_post(sc);
            auto_at_ = now_s() + 0.5;
        } else {
            begin_trial();
        }
    }

    X2Options opt_;
    Timing timing_;
    std::mt19937 rng_;
    uint32_t seed_ = 0;
    std::vector<Trial> trials_;
    std::vector<std::pair<std::pair<char, int>, bool>> results_;
    int hold_ = 4, fol_hold_ = 10;
    double film_fps_ = 24;
    Phase phase_ = Phase::Anchor;
    bool anchor_from_question_ = false, finished_ = false;
    bool auto_ = false;
    int idx_ = 0, slot_ = 0, replays_ = 0;
    Cond order_[2] = {Cond::Aimed, Cond::Blend};
    double t_end_[2] = {};  // when each clip of the trial ends (QPC s)
    AimedOffsets aimed_[2];
    double question_t0_ = 0, auto_at_ = 0;
    int late_at_start_[8] = {}, early_at_start_[8] = {};
    std::wstring path_;
    FILE* log_ = nullptr;
};

}  // namespace

bool x2_can_run(std::string* why) {
    if (g_monitors.size() < 2) { *why = "the film-step test needs two monitors"; return false; }
    if (g_master != 1) { *why = "the film-step test needs the slower monitor on the right (the laptop on the left follows it)"; return false; }
    return true;
}

std::unique_ptr<Session> make_x2_session(const X2Options& options) {
    return std::make_unique<X2Session>(options);
}

}  // namespace seam
