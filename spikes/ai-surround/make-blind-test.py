"""Blind "real or generated?" test: the by-eye quality gate for the AI sides, with the truth as the anchor.

Each trial plays two versions of the same few seconds side by side: the real frame, and the real middle half with the outer
quarters drawn by a candidate (the hidden-sides test at s = 0.5). The viewer picks the real one. A candidate picked as real
about half the time cannot be told from what the animators drew (the strict bar); 0% means it was always spotted. The
optional "what gave it away" ticks (seam, flicker, art style, colour, size, composition, motion) feed the practical bar.

    python -I make-blind-test.py SCENES_DIR RUNS_DIR OUT_DIR [--side 0.5] [--catch held-dialogue,edge-person-still] [--seed N]
                                 [--scenes a,b]

Trials, per scene, from whatever exists under RUNS_DIR (re-run after new results land; the order is reshuffled). The first
version on disk wins:
  plate          qwen21-outpaint-v2-fitlive                Qwen-Image-2.1 + outpaint LoRA still, colour fit + live seam pull
  video          vace-1.3b-fit-freeze, vace-1.3b-fit       VACE-1.3B alone (reference), freeze wrap
  video-key      vace-1.3b-key-qwen21-outpaint-v2-fit-live-freeze   VACE-1.3B carrying the Qwen still as its key frame
  video-14b-key  vace-fun-a14b-q8-key-qwen21-outpaint-v2-fit[-live-freeze]   Wan2.2-VACE-Fun-A14B, same key frame
  video-14b      vace-fun-a14b-q8-fit[-freeze]             Wan2.2-VACE-Fun-A14B alone
  scroll         scroll-mosaic-qwen21, scroll-qwen21-outpaint-v2   the shot's stitched background, completed by the
                                                           painter, slid with the camera (camera-slide shots only)
  catch          blur-extend, only for the --catch scenes  non-AI blur: should always be spotted (attention check)
Both clips of a trial have the same length and go through the same H.264 encode, so only the sides differ.
Writes OUT_DIR/index.html (open it in a browser; the score shows at the end), OUT_DIR/clips/ with neutral names and
OUT_DIR/truth/ (real clips, reused between builds).
"""
import argparse
import json
import os
import random
import shutil
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # python -I leaves the script's folder off sys.path
import hidden_sides as hs  # noqa: E402

GROUPS = (
    ("plate", ("qwen21-outpaint-v2-fitlive",)),
    ("video", ("vace-1.3b-fit-freeze", "vace-1.3b-fit")),
    ("video-key", ("vace-1.3b-key-qwen21-outpaint-v2-fit-live-freeze",)),
    ("video-14b-key", ("vace-fun-a14b-q8-key-qwen21-outpaint-v2-fit-live-freeze", "vace-fun-a14b-q8-key-qwen21-outpaint-v2-fit")),
    ("video-14b", ("vace-fun-a14b-q8-fit-freeze", "vace-fun-a14b-q8-fit")),
    ("scroll", ("scroll-mosaic-qwen21", "scroll-qwen21-outpaint-v2")),
)

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Real or generated</title>
<style>
  :root { color-scheme: dark; --bg: #101010; --fg: #e6e6e6; --muted: #9a9a9a; --line: #3a3a3a; --accent: #7fb3ff; }
  body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.45 system-ui, "Segoe UI", sans-serif; }
  header, .why, #result { padding: 10px 16px; }
  h1 { font-size: 18px; margin: 0 0 4px; }
  .muted { color: var(--muted); }
  .pair { display: flex; gap: 8px; padding: 0 8px; }
  .pair figure { flex: 1 1 0; margin: 0; min-width: 0; }
  video { width: 100%; display: block; background: #000; }
  figcaption { text-align: center; padding: 8px 0; }
  button { font: inherit; padding: 8px 20px; border-radius: 6px; border: 1px solid var(--line); background: #222;
           color: var(--fg); cursor: pointer; }
  button:hover { border-color: var(--accent); }
  .why label { margin-right: 14px; white-space: nowrap; }
  table { border-collapse: collapse; margin: 8px 0; }
  td, th { border: 1px solid var(--line); padding: 4px 10px; text-align: left; }
  textarea { width: 100%; height: 160px; background: #181818; color: var(--fg); border: 1px solid var(--line); }
  @media (max-width: 900px) { .pair { flex-direction: column; } }
</style>
</head>
<body>
<header>
  <h1>Real or generated? <span id="progress" class="muted"></span></h1>
  <div class="muted">Both videos show the same moment. In one, the outer quarter on each side was drawn by AI from the
  middle half; the other is the real frame. Pick the one you think is real. Keys: left arrow / right arrow. Use one
  monitor only (the external one), browser full screen (F11): across two screens of different size the videos would not
  match.</div>
</header>
<main id="test">
  <div class="pair">
    <figure><video id="vl" autoplay loop muted playsinline></video>
      <figcaption><button id="bl">Left is real</button></figcaption></figure>
    <figure><video id="vr" autoplay loop muted playsinline></video>
      <figcaption><button id="br">Right is real</button></figcaption></figure>
  </div>
  <div class="why">What gave the AI one away? (optional, tick before you pick)
    <label><input type="checkbox" value="seam"> seam line</label>
    <label><input type="checkbox" value="flicker"> flicker / boiling</label>
    <label><input type="checkbox" value="style"> art style</label>
    <label><input type="checkbox" value="colour"> colour</label>
    <label><input type="checkbox" value="scale"> size / proportions</label>
    <label><input type="checkbox" value="composition"> wrong things / composition</label>
    <label><input type="checkbox" value="motion"> motion</label>
  </div>
</main>
<section id="result" hidden>
  <h2>Result</h2>
  <p class="muted">"Picked as real" is how often the AI version fooled you. About 50% means you could not tell them apart
  (strict bar); 0% means it was always spotted. "catch" is the plain blur: it should always be spotted.</p>
  <table id="summary"></table>
  <h3>Per trial</h3>
  <table id="detail"></table>
  <p>Paste this into the chat, or keep the downloaded file:</p>
  <textarea id="json" readonly></textarea>
  <p><button id="dl">Download results.json</button> <button id="again">Take it again</button></p>
</section>
<script>
const TRIALS = __TRIALS__;
const BUILT = "__BUILT__";
const $ = id => document.getElementById(id);
let i = 0;
const answers = [];

function show() {
  if (i >= TRIALS.length) return finish();
  const t = TRIALS[i];
  $("progress").textContent = `${i + 1} / ${TRIALS.length}`;
  $("vl").src = `clips/${t.id}-l.mp4`;
  $("vr").src = `clips/${t.id}-r.mp4`;
  document.querySelectorAll(".why input").forEach(c => { c.checked = false; });
}

function pick(side) {
  if (i >= TRIALS.length) return;
  const t = TRIALS[i];
  const why = [...document.querySelectorAll(".why input:checked")].map(c => c.value);
  answers.push({trial: t.id, scene: t.scene, group: t.group, version: t.version, picked: side,
                correct: side === t.real, why});
  i += 1;
  show();
}

function finish() {
  $("test").hidden = true;
  $("result").hidden = false;
  $("progress").textContent = "done";
  const by = {};
  for (const a of answers) {
    const g = by[a.group] ??= {n: 0, fooled: 0, versions: new Set()};
    g.n += 1;
    if (!a.correct) g.fooled += 1;
    g.versions.add(a.version);
  }
  let s = "<tr><th>Candidate</th><th>Trials</th><th>Picked as real</th></tr>";
  for (const [g, v] of Object.entries(by)) {
    s += `<tr><td>${g} (${[...v.versions].join(", ")})</td><td>${v.n}</td>` +
         `<td>${v.fooled} (${Math.round(100 * v.fooled / v.n)}%)</td></tr>`;
  }
  $("summary").innerHTML = s;
  let d = "<tr><th>#</th><th>Scene</th><th>Candidate</th><th>You picked</th><th>Gave it away</th></tr>";
  for (const a of answers) {
    d += `<tr><td>${a.trial}</td><td>${a.scene}</td><td>${a.version}</td>` +
         `<td>${a.correct ? "the real one" : "the AI one"}</td><td>${a.why.join(", ")}</td></tr>`;
  }
  $("detail").innerHTML = d;
  const out = JSON.stringify({built: BUILT, taken: new Date().toISOString(), answers}, null, 1);
  $("json").value = out;
  $("dl").onclick = () => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([out], {type: "application/json"}));
    link.download = "blind-test-results.json";
    link.click();
  };
}

$("bl").onclick = () => pick("l");
$("br").onclick = () => pick("r");
$("again").onclick = () => location.reload();
document.addEventListener("keydown", e => {
  if (e.key === "ArrowLeft") pick("l");
  if (e.key === "ArrowRight") pick("r");
});
show();
</script>
</body>
</html>
"""


def frame_count(path):
    cap = cv2.VideoCapture(path)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return n


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("scenes_dir")
    ap.add_argument("runs_dir")
    ap.add_argument("out_dir")
    ap.add_argument("--side", type=float, default=0.5)
    ap.add_argument("--catch", default="held-dialogue,edge-person-still")
    ap.add_argument("--seed", type=int, default=None, help="trial order and sides; a fresh shuffle when left out")
    ap.add_argument("--scenes", default="", help="comma-separated subset (default: every scene)")
    a = ap.parse_args(argv)
    rng = random.Random(a.seed)

    scenes = [d for d in sorted(os.listdir(a.scenes_dir)) if os.path.isfile(os.path.join(a.scenes_dir, d, "clip.mkv"))]
    if a.scenes:
        scenes = [sc for sc in scenes if sc in a.scenes.split(",")]
    catch = set(filter(None, a.catch.split(",")))
    clips_dir, truth_dir = os.path.join(a.out_dir, "clips"), os.path.join(a.out_dir, "truth")
    shutil.rmtree(clips_dir, ignore_errors=True)
    os.makedirs(clips_dir)
    os.makedirs(truth_dir, exist_ok=True)

    trials = []
    for sc in scenes:
        found = []
        for group, versions in GROUPS:
            hits = [(v, os.path.join(a.runs_dir, v, sc, f"s{a.side}.mp4")) for v in versions]
            hit = next(((v, p) for v, p in hits if os.path.isfile(p)), None)       # first version on disk wins
            if hit:
                found.append({"scene": sc, "group": group, "version": hit[0], "path": hit[1]})
        blur = os.path.join(a.runs_dir, "blur-extend", sc, f"s{a.side}.mp4")
        if sc in catch and os.path.isfile(blur):
            found.append({"scene": sc, "group": "catch", "version": "blur-extend", "path": blur})
        # The real clip at each candidate's length, encoded the same way (hs.write_video), made once per scene.
        frames = None
        for t in found:
            t["truth"] = os.path.join(truth_dir, f"{sc}-s{a.side}-{frame_count(t['path'])}.mp4")
            if not os.path.isfile(t["truth"]):
                if frames is None:
                    sdir = os.path.join(a.scenes_dir, sc)
                    frames = [hs.split(f, a.side)[0] for f in hs.read_frames(os.path.join(sdir, "clip.mkv"))]
                    fps = json.load(open(os.path.join(sdir, "scene.json"), encoding="utf-8"))["fps"]
                hs.write_video(frames[:frame_count(t["path"])], t["truth"], fps)
        trials += found
    if not trials:
        raise SystemExit(f"no candidate videos under {a.runs_dir}")

    rng.shuffle(trials)
    page = []
    for k, t in enumerate(trials, 1):
        tid, real = f"t{k:02d}", rng.choice("lr")
        fake = "r" if real == "l" else "l"
        shutil.copyfile(t["truth"], os.path.join(clips_dir, f"{tid}-{real}.mp4"))
        shutil.copyfile(t["path"], os.path.join(clips_dir, f"{tid}-{fake}.mp4"))
        page.append({"id": tid, "scene": t["scene"], "group": t["group"], "version": t["version"], "real": real})
    html = PAGE.replace("__TRIALS__", json.dumps(page)).replace("__BUILT__", time.strftime("%Y-%m-%d %H:%M"))
    with open(os.path.join(a.out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    counts = {}
    for t in page:
        counts[t["version"]] = counts.get(t["version"], 0) + 1
    print(f"{len(page)} trials: " + ", ".join(f"{v} x{n}" for v, n in sorted(counts.items())))
    print(os.path.abspath(os.path.join(a.out_dir, "index.html")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
