"""Planner stand-in for the hot-swap trials: refines the cue table while mpv plays it.

It talks to mpv only through mpv's JSON IPC pipe and shader files, as the real
planner would (ADR 0014). Every --every seconds it reads the playhead, sets a
commit time C = playhead + --horizon, writes a new version of the cue shader in
which only shots starting at or after C change (earlier shots keep their values,
so no shot can change while it is on screen, whenever the swap lands), and tells
mpv to load it. In version k, every shot starting at or after C_k gets level
(base + k) % 4, so each version is visibly different from the one before.

Each version gets its own file name: mpv caches parsed shaders by path, so a
rewritten file loaded under the same path is ignored (checked at the end of the
"broken" mode). Files are written to a temporary name and renamed, so mpv never
reads half a file.

Every swap is confirmed: the OUTPUT pass description carries "(cue table vN)",
and mpv's vo-passes property reports the description of every pass it drew, so
the planner waits until mpv reports drawing the new version. An mpv error first,
or no confirmation within 1 s, makes it revert to the last confirmed file.

Modes:
  identical  fixed-size table; GLSL identical in every version (only texture data changes)
  recompile  version compiled into the GLSL (naive generator): every swap compiles a shader
  broken     like identical, but every third version is written truncated (it fails to
             parse, and mpv plays on without any shader until the revert). Ends with a
             same-path rewrite: new content under the path already loaded.
  reload     control for measure-swap-memory.py: after version 1, alternate between the two
             files mpv has already parsed, so every swap rebuilds mpv's render state
             without parsing a new file.

Usage: python swap-planner.py PIPE TRUTH_JSON OUT_DIR LOG_JSONL --mode MODE
                              [--every S] [--horizon S] [--capacity N] [--main-stripe]
OUT_DIR must already hold cue-v000.glsl (version 0, the table mpv starts with).
"""
import argparse
import importlib.util
import json
import os
import re
import sys
import time
from fractions import Fraction
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIRM_TIMEOUT = 1.0                     # s; a swap normally shows within 1-2 frames
VERSION_TAG = re.compile(r"\(cue table v(\d+)\)")


def load_sibling(name):
    """Import a hyphenated sibling script as a module."""
    sys.dont_write_bytecode = True
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MpvIpc:
    """Synchronous JSON IPC client on mpv's Windows named pipe. One thread only: a blocking
    read on a synchronous pipe handle would also block writes from another thread."""

    def __init__(self, path, timeout=15.0):
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.pipe = open(path, "r+b", buffering=0)
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)
        self.buffer, self.next_id, self.events = b"", 1, []

    def _message(self):
        while b"\n" not in self.buffer:
            chunk = self.pipe.read(65536)
            if not chunk:
                raise EOFError("mpv closed the IPC pipe")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def command(self, *args):
        """Send a command and return its data; events read meanwhile go to self.events."""
        request_id, self.next_id = self.next_id, self.next_id + 1
        self.pipe.write(json.dumps({"command": list(args), "request_id": request_id}).encode() + b"\n")
        while True:
            message = self._message()
            if message.get("request_id") == request_id:
                return message.get("data") if message.get("error") == "success" else None
            if "event" in message:
                self.events.append(message)

    def take_errors(self):
        errors = [e for e in self.events if e.get("event") == "log-message" and e.get("level") == "error"]
        self.events = []
        return errors


def drawn_versions(ipc):
    """Cue-table versions in the passes mpv drew last, read from the pass descriptions."""
    passes = ipc.command("get_property", "vo-passes") or {}
    return {int(v) for kind in ("fresh", "redraw") for p in passes.get(kind) or []
            for v in VERSION_TAG.findall(p.get("desc", ""))}


def wait_until_drawn(ipc, version, since, timeout=CONFIRM_TIMEOUT):
    """(ms from `since` until mpv reports drawing `version`, or None; mpv errors seen meanwhile)."""
    errors = []
    while time.perf_counter() - since < timeout:
        if version in drawn_versions(ipc):
            return round((time.perf_counter() - since) * 1000, 1), errors
        errors += ipc.take_errors()
        if errors:
            return None, errors
        time.sleep(0.01)
    return None, errors


def write_atomically(path, text):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pipe")
    parser.add_argument("truth", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("log", type=Path)
    parser.add_argument("--mode", choices=["identical", "recompile", "broken", "reload"], required=True)
    parser.add_argument("--every", type=float, default=5.0)
    parser.add_argument("--horizon", type=float, default=1.0)
    parser.add_argument("--capacity", type=int, default=8192)
    parser.add_argument("--main-stripe", action="store_true")
    args = parser.parse_args()

    shaders = load_sibling("make-cue-shader")
    truth = json.loads(args.truth.read_text())
    cues = shaders.cue_list(truth)
    starts = [t for t, _ in cues]
    base = [level for _, level in cues]
    duration = truth["frames"] / float(Fraction(truth["fps"]))
    log = open(args.log, "w")

    def record(entry):
        entry["wall"] = round(time.perf_counter(), 4)
        log.write(json.dumps(entry) + "\n")
        log.flush()

    record({"type": "plan", "mode": args.mode, "every": args.every, "horizon": args.horizon,
            "capacity": args.capacity, "main_stripe": args.main_stripe, "cue_starts": starts, "levels": base})
    ipc = MpvIpc(args.pipe)
    ipc.command("request_log_messages", "error")

    def shader(levels, version):
        return shaders.shader_text(list(zip(starts, levels)), truth["layout"], args.truth.name, args.capacity,
                                   version, args.mode == "recompile", args.main_stripe)

    good_levels, good_path, version, next_swap = base, args.out_dir / "cue-v000.glsl", 0, None
    try:
        while True:
            position = ipc.command("get_property", "time-pos")
            errors = ipc.take_errors()
            if errors:  # a failed parse also dumps the whole shader, one error line per line
                record({"type": "mpv-errors", "count": len(errors), "first": [e.get("text", "").strip() for e in errors[:3]]})
            if position is None or position < 3.0:
                time.sleep(0.05)
                continue
            now = time.perf_counter()
            next_swap = next_swap or now
            if now < next_swap or position > duration - 3.0:
                time.sleep(0.02)
                continue
            next_swap += args.every
            version += 1
            commit = position + args.horizon
            levels = [(b + version) % 4 if s >= commit else g for s, b, g in zip(starts, base, good_levels)]
            broken = args.mode == "broken" and version % 3 == 0
            drawn = version % 2 if args.mode == "reload" and version >= 2 else version
            path = args.out_dir / f"cue-v{drawn:03d}.glsl"
            if drawn == version:
                text = shader(levels, version)
                write_atomically(path, text[:len(text) // 2] if broken else text)
            sent = time.perf_counter()
            ipc.command("set_property", "glsl-shaders", [str(path)])
            ack_ms = round((time.perf_counter() - sent) * 1000, 2)
            confirm_ms, errors = wait_until_drawn(ipc, drawn, sent)
            record({"type": "version", "k": version, "request_pos": position, "commit": commit, "path": path.name,
                    "broken": broken, "ack_ms": ack_ms, "confirm_ms": confirm_ms, "levels": levels})
            if confirm_ms is not None:
                good_levels, good_path = levels, path
                continue
            # Not drawn: a file that fails to parse leaves mpv playing on without any shader.
            detected = time.perf_counter()
            ipc.command("set_property", "glsl-shaders", [str(good_path)])
            record({"type": "revert", "k": version, "to": good_path.name, "by": "error" if errors else "timeout",
                    "detect_ms": round((detected - sent) * 1000, 1), "error_lines": len(errors),
                    "errors": [e.get("text", "").strip() for e in errors[:3]]})
            if args.mode == "broken" and version >= 9:
                # Same path, new content: rewrite the loaded file with the next version, then make
                # mpv rebuild its shader list twice. Expected: mpv keeps the table it already parsed.
                version += 1
                levels = [(b + version) % 4 if s >= position + args.horizon else g
                          for s, b, g in zip(starts, base, good_levels)]
                write_atomically(good_path, shader(levels, version))
                sent = time.perf_counter()
                ipc.command("set_property", "glsl-shaders", [str(good_path), str(good_path)])
                ipc.command("set_property", "glsl-shaders", [str(good_path)])
                drawn_ms, _ = wait_until_drawn(ipc, version, sent)
                record({"type": "same-path", "k": version, "path": good_path.name, "request_pos": position,
                        "drawn_ms": drawn_ms})
                break
    except (EOFError, OSError):
        pass
    record({"type": "end", "versions": version})


if __name__ == "__main__":
    main()
