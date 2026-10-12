"""Tests of the cache key with a real run of the worker on a small generated clip (software decode, models on the CPU, no GPU needed).

    tools\\venv-analysis\\Scripts\\python.exe -I spikes\\film-analysis\\cache-test.py [--keep]

What it proves:
  file identity   the same file gives the same key; a renamed or moved copy gives the same key and the same cache folder; one
                  changed byte in any of the three sampled 1 MiB blocks, or a changed size, gives a new key; a byte between the
                  samples does not (the full hash, stored later, is what catches that);
  reuse           a second run on a renamed copy finds the folder, scans nothing and rewrites no table;
  versions        a changed model hash, sampling stride or signal-group version means the signals are not reused; a changed
                  version of one derived table makes that table again and leaves every other file of the folder untouched
                  (a new shots table takes slide-paths and subjects with it, the ids they name move).
"""
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

from film_analysis import file_key, manifest as mf, models_check, prescan, refresh, versions  # noqa: E402

PY = sys.executable
MODELS = models_check.model_hashes(os.path.join(HERE, "models-manifest.json"))
STRIDE = 12
RESULTS = []


def check(name):
    def deco(fn):
        try:
            fn()
            RESULTS.append((name, True, ""))
        except Exception:  # noqa: BLE001
            RESULTS.append((name, False, traceback.format_exc(limit=4)))
        return fn
    return deco


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def flip(path, offset):
    with open(path, "r+b") as f:
        f.seek(offset)
        b = f.read(1)
        f.seek(offset)
        f.write(bytes([b[0] ^ 0xFF]))


def make_clip(path):
    """6 s of 1280x536 picture in a 1280x720 frame (92 px bars), two different shots, an SRT track: tiny, but every table is made."""
    srt = os.path.join(os.path.dirname(path), "t.srt")
    with open(srt, "w") as f:
        f.write("1\n00:00:00,500 --> 00:00:02,000\nHello there\n\n2\n00:00:04,000 --> 00:00:05,500\nSecond line\n")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x536:rate=24:duration=3",
           "-f", "lavfi", "-i", "gradients=size=1280x536:rate=24:duration=3:seed=7", "-i", srt, "-filter_complex",
           "[0:v][1:v]concat=n=2:v=1,pad=1280:720:0:92:black,format=yuv420p[v]", "-map", "[v]", "-map", "2:0", "-c:v", "libx264",
           "-g", "24", "-preset", "veryfast", "-crf", "20", "-c:s", "srt", path]
    subprocess.run(cmd, check=True)


def run_worker(file, cache_root):
    cmd = [PY, "-I", os.path.join(HERE, "analyze.py"), file, "--cache-root", cache_root, "--decode-path", "software",
           "--models-on", "cpu", "--workers", "2", "--segment-seconds", "3"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr[-800:]
    return r.stderr


@contextlib.contextmanager
def patched(**names):
    """Sets attributes of the versions module for a moment (a changed analyzer)."""
    old = {k: getattr(versions, k) for k in names}
    for k, v in names.items():
        setattr(versions, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(versions, k, v)


def snapshot(folder):
    files = ["shots.json", "slide-paths.json", "subjects.json", "subjects-raw.jsonl", "file-facts.json", "signals.bin", "signals.json",
             os.path.join("subtitles", "track-1.json")]
    return {f: (sha(os.path.join(folder, f)), os.stat(os.path.join(folder, f)).st_mtime_ns) for f in files}


def rewritten(before, after):
    """Files whose modification time moved: the ones a refresh wrote again."""
    return sorted(f for f in before if before[f][1] != after[f][1])


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="cache-test-")
    keep = "--keep" in sys.argv
    try:
        suite(tmp)
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
    bad = 0
    for name, ok, detail in RESULTS:
        print(("ok   " if ok else "FAIL ") + name)
        if not ok:
            bad += 1
            print(detail)
    print(f"{len(RESULTS) - bad} of {len(RESULTS)} checks passed")
    return 1 if bad else 0


def suite(tmp):
    @check("key: same file same key; copy under another name and folder same key and cache folder")
    def _():
        p = os.path.join(tmp, "big.bin")
        with open(p, "wb") as f:
            f.write(os.urandom(8 * file_key.MIB))
        k = file_key.sample_key(p)
        assert k == file_key.sample_key(p) and k["scheme"] == file_key.SCHEME
        os.makedirs(os.path.join(tmp, "elsewhere"))
        q = os.path.join(tmp, "elsewhere", "renamed.mkv")
        shutil.copy2(p, q)
        assert file_key.sample_key(q) == k and file_key.cache_folder_name(file_key.sample_key(q)) == file_key.cache_folder_name(k)

    @check("key: one changed byte in each of the three sampled blocks, or a changed size, gives a new key")
    def _():
        mib = file_key.MIB
        base = os.path.join(tmp, "base.bin")
        with open(base, "wb") as f:
            f.write(os.urandom(8 * mib))
        k0 = file_key.sample_key(base)
        size = 8 * mib
        offsets = {"first block": 1234, "first block, last byte": mib - 1, "middle block, first byte": size // 2 - mib // 2,
                   "middle block": size // 2, "middle block, last byte": size // 2 + mib // 2 - 1, "last block, first byte": size - mib,
                   "last block, last byte": size - 1}
        for what, off in offsets.items():
            p = os.path.join(tmp, "flip.bin")
            shutil.copyfile(base, p)
            flip(p, off)
            assert file_key.sample_key(p)["sample_sha256"] != k0["sample_sha256"], what
        p = os.path.join(tmp, "grown.bin")                      # same samples, one byte longer in the unsampled stretch is a new size
        shutil.copyfile(base, p)
        with open(p, "ab") as f:
            f.write(b"\0")
        assert file_key.sample_key(p)["size_bytes"] == size + 1 and file_key.sample_key(p) != k0

    @check("key: a byte between the samples does not change the sample key but does change the full hash")
    def _():
        mib = file_key.MIB
        base = os.path.join(tmp, "base.bin")
        p = os.path.join(tmp, "gap.bin")
        shutil.copyfile(base, p)
        flip(p, 2 * mib)                                       # first block ends at 1 MiB, the middle one starts at 3.5 MiB
        assert file_key.sample_key(p) == file_key.sample_key(base)
        assert file_key.full_sha256(p) != file_key.full_sha256(base)
        a, b = file_key.sample_key(base), file_key.sample_key(p)
        a["full_sha256"], b["full_sha256"] = file_key.full_sha256(base), file_key.full_sha256(p)
        assert not file_key.same_file(a, b)                   # the later, full hash tells them apart
        assert file_key.same_file(file_key.sample_key(base), a)            # a key without the full hash still matches the one with it

    @check("key: files up to 3 MiB are hashed whole; the empty file and a file shorter than one block still have keys")
    def _():
        p = os.path.join(tmp, "small.bin")
        with open(p, "wb") as f:
            f.write(os.urandom(3 * file_key.MIB))
        k = file_key.sample_key(p)
        flip(p, 2 * file_key.MIB)
        assert file_key.sample_key(p)["sample_sha256"] != k["sample_sha256"]
        open(p, "wb").close()
        assert file_key.sample_key(p)["size_bytes"] == 0

    clip = os.path.join(tmp, "tiny.mkv")
    make_clip(clip)
    cache = os.path.join(tmp, "cache")
    key = file_key.sample_key(clip)
    folder = os.path.join(cache, file_key.cache_folder_name(key))
    state = {}

    @check("run: the worker makes every table in the folder named by the key")
    def _():
        run_worker(clip, cache)
        assert os.path.isdir(folder) and os.listdir(cache) == [os.path.basename(folder)]
        man = mf.load(folder)
        assert file_key.same_file(man["file"]["key"], key) and man["scan"]["state"] == "complete"
        for name in ("shots", "slide-paths", "subjects", "subtitles", "file-facts"):
            assert man["tables"][name]["version"] == versions.table_versions()[name], name
        assert mf.stale_tables(man, versions.table_versions(), folder) == []
        state["snap"] = snapshot(folder)

    @check("reuse: a renamed copy finds the folder, scans nothing and rewrites no file")
    def _():
        moved = os.path.join(tmp, "copy-of-clip.mkv")
        shutil.copy2(clip, moved)
        err = run_worker(moved, cache)
        assert "reusing the stored pre-scan" in err and "nothing to scan" in err, err[-500:]
        assert os.listdir(cache) == [os.path.basename(folder)]
        assert snapshot(folder) == state["snap"]

    @check("reuse: a changed byte makes a different key, a different folder, and leaves the first folder alone")
    def _():
        other = os.path.join(tmp, "other.mkv")
        shutil.copy2(clip, other)
        flip(other, 5000)
        run_worker(other, cache)
        assert len(os.listdir(cache)) == 2 and snapshot(folder) == state["snap"]

    @check("full hash: --full-hash stores the hash of every byte in the key, and the folder is still found by the sample key")
    def _():
        cmd = [PY, "-I", os.path.join(HERE, "analyze.py"), clip, "--cache-root", cache, "--decode-path", "software", "--models-on", "cpu",
               "--full-hash"]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode == 0, r.stderr[-500:]
        stored = mf.load(folder)["file"]["key"]
        assert stored["full_sha256"] == file_key.full_sha256(clip) and file_key.same_file(stored, key)
        assert json.load(open(os.path.join(folder, "file-facts.json")))["file"]["key"]["full_sha256"] == stored["full_sha256"]
        run_worker(clip, cache)
        assert len(os.listdir(cache)) == 2 and mf.load(folder)["file"]["key"]["full_sha256"] == stored["full_sha256"]

    def context():
        ctx = prescan.load_context(clip, folder, MODELS, STRIDE)
        assert ctx is not None
        return ctx

    @check("versions: signals are reused only with the same model hashes, stride and signal-group versions")
    def _():
        assert prescan.load_context(clip, folder, MODELS, STRIDE) is not None
        assert prescan.load_context(clip, folder, MODELS, 6) is None                              # other frames sampled
        changed = dict(MODELS, **{"anime-face-v1.4s": "0" * 64})
        assert prescan.load_context(clip, folder, changed, STRIDE) is None                         # another face model
        changed = dict(MODELS, transnetv2="1" * 64)
        assert prescan.load_context(clip, folder, changed, STRIDE) is None                         # another shot model
        for group in ("MOTION", "CROP", "BARS_TEXT", "FRAME_TABLE", "SUBJECTS_RAW"):
            with patched(**{group: "9"}):
                assert prescan.load_context(clip, folder, MODELS, STRIDE) is None, group
        other_file = os.path.join(tmp, "other.mkv")
        assert prescan.load_context(other_file, folder, MODELS, STRIDE) is None                    # a different file in this folder

    def reset():
        """Back to the current analyzer: whatever a test made again with a changed version is made again once more."""
        ctx = context()
        refresh.refresh_stale(ctx, mf.stale_tables(ctx.manifest, versions.table_versions(), folder))
        assert mf.stale_tables(mf.load(folder), versions.table_versions(), folder) == []

    def one_table(name, attr, expect_written):
        ctx = context()
        before = snapshot(folder)
        with patched(**{attr: "2"}):
            stale = mf.stale_tables(ctx.manifest, versions.table_versions(), folder)
            assert stale == [name], stale
            refresh.refresh_stale(ctx, stale)
            assert mf.stale_tables(mf.load(folder), versions.table_versions(), folder) == []
        after = snapshot(folder)
        written = rewritten(before, after)
        assert written == sorted(expect_written), (written, expect_written)
        assert mf.load(folder)["tables"][name]["version"] == "2"
        if name in ("slide-paths", "subjects"):
            assert json.load(open(os.path.join(folder, name + ".json")))["version"] == "2"        # the document says so too
        reset()
        return before, after

    @check("versions: a new slide-paths version makes slide-paths.json again and no other file")
    def _():
        before, after = one_table("slide-paths", "SLIDE_PATHS", ["slide-paths.json"])
        assert before["shots.json"][0] == after["shots.json"][0]

    @check("versions: a new subjects version makes subjects.json again and no other file")
    def _():
        one_table("subjects", "SUBJECTS", ["subjects.json"])

    @check("versions: a new subtitles version makes the subtitle tracks again and no other file")
    def _():
        one_table("subtitles", "SUBTITLES", [os.path.join("subtitles", "track-1.json")])

    @check("versions: a new file-facts version makes file-facts.json again, keeping what the scan added to it")
    def _():
        facts_before = json.load(open(os.path.join(folder, "file-facts.json")))
        one_table("file-facts", "FILE_FACTS", ["file-facts.json"])
        facts_after = json.load(open(os.path.join(folder, "file-facts.json")))
        for field in ("black_level", "crop_vocabulary", "text_in_bars", "slide_region_of_interest"):
            assert facts_after[field] == facts_before[field], field
        assert facts_after["timeline"]["time_base"] == facts_before["timeline"]["time_base"]

    @check("versions: a new shots version makes shots, slide-paths and subjects again (they name shot ids) and never the signals")
    def _():
        ctx = context()
        before = snapshot(folder)
        with patched(SHOTS_TABLE="2"):
            stale = mf.stale_tables(ctx.manifest, versions.table_versions(), folder)
            assert stale == ["shots"] and mf.tables_to_make(stale) == {"shots", "slide-paths", "subjects"}
            refresh.refresh_stale(ctx, stale)
        after = snapshot(folder)
        assert rewritten(before, after) == ["file-facts.json", "shots.json", "slide-paths.json", "subjects.json"]
        for f in ("signals.bin", "signals.json", "subjects-raw.jsonl", os.path.join("subtitles", "track-1.json")):
            assert before[f] == after[f], f
        reset()

    @check("versions: a deleted table file counts as stale")
    def _():
        ctx = context()
        os.remove(os.path.join(folder, "slide-paths.json"))
        assert mf.stale_tables(ctx.manifest, versions.table_versions(), folder) == ["slide-paths"]
        refresh.refresh_stale(ctx, ["slide-paths"])
        assert os.path.isfile(os.path.join(folder, "slide-paths.json"))


if __name__ == "__main__":
    sys.exit(main())
