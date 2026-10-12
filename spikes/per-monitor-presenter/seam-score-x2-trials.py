"""Score the film-step test (X2) from the answer files seam-tuner.exe --mode x2 writes.

Each answer line carries the viewer's pick and the hidden truth (which clip held the effect), so the score is a
count of right picks per condition: comparison a (real bezel: aimed row vs rolling blend; the right answer is the
aimed-row clip) or b (laptop alone, fake bezel: plain switch vs rolling blend; the right answer is the blend), at
each speed.

Pass test (zero-delay research, section 8), per condition:
  9 or 10 of 10 right   visible: the viewer sees the effect (p <= 0.025 for guessing)
  6 or fewer of 10      not visible: no better than guessing
  7 or 8 of 10          between: run 10 more trials of that condition and score the files together
With 20 or more answers in a condition the same two thresholds apply as binomial rules (visible: p <= 0.025,
15 of 20; not visible: at most 60% right, 12 of 20), as in the seam-threshold test.

What a verdict means:
  a visible      the scan-speed difference shows at the bezel at that speed, and the rolling blend removes it
  a not visible  not visible at that speed: today's aimed row is good enough there
  b visible      the blend's one-refresh double edge can be seen: it has a visible cost
  b not visible  the blend can't be seen (it costs nothing you can see)
  reversed       also flagged, when 9 or 10 of 10 are wrong: the viewer tells the clips apart but picks the other one,
                 so something else stands out more than the effect asked about (in a: the blend's own double edge)

Usage: python seam-score-x2-trials.py ANSWERS.jsonl [MORE.jsonl ...]     (several files are pooled)
       python seam-score-x2-trials.py --self-test
"""
import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

WHAT = {"a": "real bezel: aimed row vs rolling blend", "b": "laptop alone, fake bezel: plain switch vs blend"}
MEANING = {
    ("a", "visible"): "the scan-speed difference shows at the bezel and the rolling blend removes it",
    ("a", "not visible"): "not visible at this speed: today's aimed row is good enough here",
    ("b", "visible"): "the blend's double edge can be seen: it has a visible cost",
    ("b", "not visible"): "the blend can't be seen",
}


REVERSED_HINT = {
    "a": "Something in the blend clip stands out more than the top/bottom timing (its own faint double edge?): ask what they saw.",
    "b": "The plain switch looks more doubled than the blend, which is unexpected: ask what they saw.",
}


def binomial_tail(k, n):
    """P(at least k right of n by guessing)."""
    return sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n


def verdict(right, n):
    if n < 10:
        return "too few answers (need 10)"
    if binomial_tail(right, n) <= 0.025:
        return "visible"
    if right <= 0.6 * n:
        return "not visible"
    return "between: run 10 more"


def read_answers(paths):
    sessions, answers, problems = [], [], []
    for path in paths:
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as err:
                problems.append(f"{path}:{number}: not JSON ({err})")
                continue
            if row.get("type") == "session":
                sessions.append(row)
            elif row.get("type") == "answer":
                row["_file"] = str(path)
                answers.append(row)
    return sessions, answers, problems


def score(sessions, answers):
    groups = defaultdict(list)
    for row in answers:
        groups[(row["comparison"], row["speed_mm_s"])].append(row)
    table = []
    for (comp, speed), rows in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        right = sum(1 for r in rows if r["correct"])
        n = len(rows)
        # Far fewer right than guessing gives (9+ of 10 wrong): the viewer tells the clips apart but picks the other one.
        reversed_ = n >= 10 and binomial_tail(n - right, n) <= 0.025
        table.append({"comparison": comp, "speed_mm_s": speed, "answers": n, "right": right,
                      "p_guessing": round(binomial_tail(right, n), 4), "verdict": verdict(right, n), "reversed": reversed_})
    notes = []
    fps = sorted({round(s.get("film_fps", 0), 2) for s in sessions})
    if any(abs(f / 24.0 - 1) > 0.005 for f in fps):
        notes.append(f"film rate was {fps} fps, not 24: the pace is uneven or off; run with --refresh DISPLAY2=96")
    if any(s.get("windowed") for s in sessions):
        notes.append("some answers come from windowed runs (Windows composes them: timing is not representative)")
    if any(s.get("quick") for s in sessions):
        notes.append("some answers come from --quick runs (short clips: smoke tests, not a measurement)")
    auto = sum(1 for r in answers if str(r.get("source", "")).startswith("auto"))
    if auto:
        notes.append(f"{auto} answers were given by --auto-answer, not by a person")
    glitchy = [r["trial"] for r in answers
               if any(v for v in r.get("late_presents", {}).values()) or any(v for v in r.get("early_presents", {}).values())]
    if glitchy:
        notes.append(f"trials with a present that reached a different refresh than planned (rerun them if many; if it is every trial, "
                     f"the lead time is off: see --lead-ms): {glitchy}")
    replays = sum(r.get("replays", 0) for r in answers)
    return table, notes, replays


def report(paths):
    sessions, answers, problems = read_answers(paths)
    for p in problems:
        print("warning:", p, file=sys.stderr)
    if not answers:
        print("no answers found", file=sys.stderr)
        return 1
    table, notes, replays = score(sessions, answers)
    print(f"\n{len(answers)} answers from {len(set(r['_file'] for r in answers))} file(s); {replays} replays")
    print(f"{'':2}{'comparison':48} {'speed':>9} {'right':>7} {'p(guess)':>9}  verdict")
    for row in table:
        print(f"{row['comparison']:2}{WHAT[row['comparison']]:48} {row['speed_mm_s']:>6} mm/s {row['right']:>3}/{row['answers']:<3} "
              f"{row['p_guessing']:>9.3f}  {row['verdict']}")
    print()
    for row in table:
        meaning = MEANING.get((row["comparison"], row["verdict"]))
        if meaning:
            print(f"  {row['comparison']} at {row['speed_mm_s']} mm/s: {meaning}")
        if row["reversed"]:
            print(f"  {row['comparison']} at {row['speed_mm_s']} mm/s: REVERSED: only {row['right']} of {row['answers']} right means the viewer "
                  f"tells the two clips apart but picks the other one. {REVERSED_HINT[row['comparison']]}")
    for note in notes:
        print("note:", note)
    out = Path(paths[0]).with_suffix(".score.json")
    out.write_text(json.dumps({"files": [str(p) for p in paths], "conditions": table, "notes": notes, "replays": replays}, indent=1),
                   encoding="utf-8")
    print(f"wrote {out}")
    return 0


def self_test():
    """The pass rule on known counts, then the whole path (read, group, score) on synthetic answer files."""
    expect = {(10, 10): "visible", (9, 10): "visible", (8, 10): "between: run 10 more", (7, 10): "between: run 10 more",
              (6, 10): "not visible", (5, 10): "not visible", (0, 10): "not visible", (15, 20): "visible",
              (14, 20): "between: run 10 more", (13, 20): "between: run 10 more", (12, 20): "not visible", (3, 7): "too few answers (need 10)"}
    for (right, n), want in expect.items():
        got = verdict(right, n)
        assert got == want, f"{right} of {n}: got {got!r}, want {want!r}"
    # Reversed: 9+ of 10 wrong (p <= 0.025 for guessing), so 0 or 1 right; 2 right is still plain "not visible".
    for right, want in ((0, True), (1, True), (2, False), (5, False), (10, False)):
        row = score([], [{"comparison": "a", "speed_mm_s": 300, "correct": i < right} for i in range(10)])[0][0]
        assert row["reversed"] is want, f"{right} of 10: reversed {row['reversed']}, want {want}"
    rows = []
    for comp, speed, right in (("a", 300, 10), ("a", 600, 8), ("b", 300, 6), ("b", 600, 9)):
        for i in range(10):
            rows.append({"type": "answer", "trial": len(rows) + 1, "comparison": comp, "speed_mm_s": speed, "correct": i < right,
                         "source": "auto-right", "late_presents": {"DISPLAY1": 0}, "replays": 0})
    session = {"type": "session", "film_fps": 24.0, "windowed": False}
    table, notes, _ = score([session], rows)
    got = {(t["comparison"], t["speed_mm_s"]): (t["right"], t["verdict"]) for t in table}
    assert got == {("a", 300): (10, "visible"), ("a", 600): (8, "between: run 10 more"), ("b", 300): (6, "not visible"),
                   ("b", 600): (9, "visible")}, got
    assert any("auto-answer" in n for n in notes), notes
    _, notes, _ = score([{"type": "session", "film_fps": 25.0}], rows)
    assert any("not 24" in n for n in notes), notes
    print("self-test passed")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("answers", nargs="*", type=Path, help="answer files (JSONL); several are pooled")
    parser.add_argument("--self-test", action="store_true", help="check the pass rule and the scoring path on synthetic answers")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.answers:
        parser.error("give at least one answers file, or --self-test")
    return report(args.answers)


if __name__ == "__main__":
    sys.exit(main())
