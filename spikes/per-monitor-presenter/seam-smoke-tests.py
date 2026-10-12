"""Smoke tests for the seam tuner (seam-tuner.exe): every mode, windowed, answered by --auto-answer. About a minute.

Opens half-size, non-topmost windows on the screens for a few seconds at a time (no full screen, no display change,
no --refresh), so nothing here is a timing measurement: Windows composes those windows. It checks the logic:
  1. --self-check: the rolling blend's light-weighted switch moments match the reference rows' within 1 microsecond
     (on the exact 240 + 96 Hz timing and on the monitors' real row clocks)
  2. x2, always-right answers: 10 of 10 right, the scorer says "visible"
  3. x2, always-wrong answers: 0 of 10 right, the scorer says "not visible" and flags it as reversed
  4. x2, random answers over all four conditions: 12 answers, speeds in ascending blocks, the log carries the truth
  5. calibrate, always-right answers against a simulated truth: each parameter ends within its last step of it,
     the settings JSON keeps its old fields and has the new ones
Build the tuner first (compile.bat tuner). Usage: python seam-smoke-tests.py [--out DIR]
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXE = HERE / "bin" / "seam-tuner.exe"
SCORER = HERE / "seam-score-x2-trials.py"
SIM_TRUTH = {"height_mm": -1.3, "gap_mm": 23.7, "lag_ms": 2.6}
failures = []


def check(ok, what):
    print(("  ok    " if ok else "  FAIL  ") + what)
    if not ok:
        failures.append(what)


def run_tuner(args, timeout=60):
    proc = subprocess.run([str(EXE), *args], capture_output=True, text=True, timeout=timeout, errors="replace")
    return proc.returncode, proc.stdout + proc.stderr


def answers_file(directory):
    files = sorted(Path(directory).glob("seam-x2-answers-*.jsonl"))
    return files[-1] if files else None


def read_answers(path):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r["type"] == "answer"], [r for r in rows if r["type"] == "session"]


def score(path):
    return subprocess.run([sys.executable, "-I", str(SCORER), str(path)], capture_output=True, text=True).stdout


def test_self_check():
    print("self-check")
    code, out = run_tuner(["--self-check"])
    check(code == 0, f"exit code 0 (got {code})")
    m = re.search(r"largest blend error ([\d.]+) us", out)
    check(bool(m) and float(m.group(1)) < 1.0, f"largest blend error {m.group(1) if m else '?'} us is under 1 us")
    check("self-check passed" in out, "the check reports passed")


def test_x2(out, name, conditions, answer, trials, seed):
    directory = out / name
    code, text = run_tuner(["--mode", "x2", "--windowed", "--quick", "--auto-answer", answer, "--conditions", conditions, "--trials", str(trials),
                            "--seconds", "17", "--seed", str(seed), "--out", str(directory)])
    check(code == 0, f"{name}: exit code 0 (got {code})")
    path = answers_file(directory)
    check(path is not None, f"{name}: answers file written")
    if path is None:
        return None, None
    answers, sessions = read_answers(path)
    want = len(conditions.split(",")) * trials
    check(len(answers) == want, f"{name}: {len(answers)} answers (want {want})")
    check(all("truth" in a and a["truth"] in (1, 2) for a in answers), f"{name}: every answer carries the hidden truth")
    check(sessions and sessions[0]["auto_answer"] == answer, f"{name}: the session line records the auto answer")
    return answers, score(path)


def test_calibration(out):
    print("calibrate, always-right answers")
    directory = out / "calibrate"
    code, text = run_tuner(["--mode", "calibrate", "--windowed", "--quick", "--auto-answer", "right", "--seconds", "19", "--seed", "44",
                            "--out", str(directory)])
    check(code == 0, f"exit code 0 (got {code})")
    last = directory / "seam-settings-last.json"
    check(last.exists(), "seam-settings-last.json written")
    if not last.exists():
        return
    s = json.loads(last.read_text(encoding="utf-8"))
    old_fields = ["gap_mm", "right_screen_lower_mm", "lag_ms", "scan_fraction", "refresh_hz", "rows_timing", "note"]
    check(all(k in s for k in old_fields), "the original settings fields are all still there")
    check(all(k in s for k in ("version", "line_us", "scan_ms", "blank_ms", "calibration", "brightness_note")), "the new fields are there")
    check("laptop backlight 44%" in s.get("brightness_note", ""), "the brightness note names the laptop backlight level")
    cal = s.get("calibration", {})
    check(cal.get("status") == "complete" and cal.get("done") == ["height", "gap", "lag"], f"all three parameters finished: {cal.get('done')}")
    rounds = cal.get("rounds_per_parameter", 7)
    got = {"height_mm": s["right_screen_lower_mm"], "gap_mm": s["gap_mm"], "lag_ms": list(s["lag_ms"].values())[1]}
    first_step = {"height_mm": 4.0, "gap_mm": 8.0, "lag_ms": 4.0}
    for key, truth in SIM_TRUTH.items():
        tolerance = first_step[key] / 2 ** (rounds - 1)  # the last round's step
        check(abs(got[key] - truth) <= tolerance + 1e-9, f"{key}: {got[key]:.3f} within the last step {tolerance:.3f} of the simulated truth {truth}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None, help="where the logs go (default: a temporary folder)")
    args = parser.parse_args()
    if not EXE.exists():
        raise SystemExit(f"missing {EXE}: run compile.bat tuner first")
    out = args.out or Path(tempfile.mkdtemp(prefix="seam-smoke-"))
    out.mkdir(parents=True, exist_ok=True)
    print(f"logs in {out}\n")

    test_self_check()

    print("x2, always-right answers (comparison a, 300 mm/s)")
    answers, text = test_x2(out, "x2-right", "a:300", "right", 10, 41)
    if answers:
        check(sum(a["correct"] for a in answers) == 10, "10 of 10 right")
        check("visible" in text and "not visible" not in text, "the scorer says visible")
        check(any(a["clip1"] == "aimed" for a in answers) and any(a["clip1"] == "blend" for a in answers), "the clip order is mixed")

    print("x2, always-wrong answers (comparison b, 600 mm/s)")
    answers, text = test_x2(out, "x2-wrong", "b:600", "wrong", 10, 42)
    if answers:
        check(sum(a["correct"] for a in answers) == 0, "0 of 10 right")
        check("not visible" in text and "REVERSED" in text, "the scorer says not visible and flags it as reversed")

    print("x2, random answers over all four conditions (3 each)")
    answers, text = test_x2(out, "x2-mix", "a:300,b:300,a:600,b:600", "random", 3, 43)
    if answers:
        speeds = [a["speed_mm_s"] for a in answers]
        check(speeds == sorted(speeds), "speeds run in ascending blocks (300 then 600)")
        check({(a["comparison"], a["speed_mm_s"]) for a in answers} == {("a", 300), ("b", 300), ("a", 600), ("b", 600)}, "all four conditions ran")
        check(all(a["target"] == ("aimed" if a["comparison"] == "a" else "blend") for a in answers), "the target clip is aimed for a and blend for b")

    test_calibration(out)

    print(f"\n{'all checks passed' if not failures else str(len(failures)) + ' checks FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
