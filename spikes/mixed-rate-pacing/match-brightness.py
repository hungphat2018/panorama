"""Match the laptop panel's brightness to the external screen by eye, against a fixed anchor.

Shows four grey bars that run across the bezel: 100 %, 50 %, 20 % and 5 % grey, the same picture
values on both screens, at the same physical height (tops level, as in the span clips). Up/Down
change the laptop's backlight in 5 % steps, Shift+Up/Down in 1 % steps.

The anchor is the 50 % bar. It is matched when its two halves look equally light from your seat:
you can't say which half is brighter. If unsure, go up until the laptop half is clearly brighter,
then down until it is clearly darker, and settle in the middle. Enter keeps that level; Esc or q
(or closing the window) puts the original level back. The other bars show whether the two screens
also agree at white and near black; one backlight setting can't fix a difference there.

The external stays as it is, as the reference: DDC/CI fails on both screens here, so only the
laptop's backlight (WMI) can be set from software.

Needs only Python and mpv (no OpenCV).

Usage: python match-brightness.py [--mpv MPV_EXE]
       python match-brightness.py --set PERCENT      set the laptop backlight directly, e.g. to put it back
"""
import argparse
import re
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_MPV = HERE.parents[1] / "tools" / "mpv-v0.41.0-x86_64-w64-mingw32" / "mpv.exe"
LAPTOP_W, LAPTOP_H, LAPTOP_MM, EXT_W, EXT_MM = 2560, 1600, 344.0, 1920, 476.0  # as in make-pan-viewing-clips.py
GREYS = [(100, "100%"), (50, "50%   match this bar"), (20, "20%"), (5, "5%")]
BAR_TOP_MM, BAR_MM, GAP_MM, BAR_WIDTH_MM = 22, 32, 14, 110  # every bar fits the laptop's 215 mm height
# One PowerShell stays open so each change takes milliseconds, not a 0.5 s start-up.
# Each input line is a percentage to set, or "?" to only read; it answers with the level Windows reports.
PS_LOOP = ("$m = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods; "
           "while ($null -ne ($line = [Console]::In.ReadLine())) { "
           "if ($line -ne '?') { $m | Invoke-CimMethod -MethodName WmiSetBrightness "
           "-Arguments @{Timeout = [uint32]0; Brightness = [byte]$line} | Out-Null }; "
           "[Console]::Out.WriteLine((Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness).CurrentBrightness) }")
# Keys and on-screen text. LABELS is replaced by the bar labels: {x, y, text}, in window pixels.
LUA = r"""
local labels = LABELS
local level = tonumber(mp.get_opt("match-start"))
local original = level
local overlay = mp.create_osd_overlay("ass-events")
overlay.res_x, overlay.res_y = WINDOW_W, WINDOW_H
local function show()
    local text = {string.format("{\\an7\\pos(60,50)\\fs44\\bord2}Laptop brightness %d%%\\N"
        .. "{\\fs32}Up/Down: 5%% steps    Shift+Up/Down: 1%% steps\\NEnter: keep    Esc: put back %d%%",
        level, original)}
    for _, l in ipairs(labels) do
        text[#text + 1] = string.format("{\\an6\\pos(%d,%d)\\fs40\\bord0\\1c&HC8C8C8&}%s", l[1], l[2], l[3])
    end
    overlay.data = table.concat(text, "\n")
    overlay:update()
end
local function step(delta)
    level = math.max(0, math.min(100, level + delta))
    print("MATCHBRIGHT set " .. level)
    show()
end
local function finish(word)
    print("MATCHBRIGHT " .. word .. " " .. level)
    mp.command("quit")
end
for key, delta in pairs({UP = 5, DOWN = -5, ["Shift+UP"] = 1, ["Shift+DOWN"] = -1}) do
    mp.add_forced_key_binding(key, "step" .. key, function() step(delta) end, {repeatable = true})
end
for _, key in ipairs({"ENTER", "KP_ENTER"}) do mp.add_forced_key_binding(key, "keep" .. key, function() finish("keep") end) end
for _, key in ipairs({"ESC", "q"}) do mp.add_forced_key_binding(key, "undo" .. key, function() finish("undo") end) end
mp.register_event("file-loaded", show)
"""


class Backlight:
    """The laptop panel's backlight through WMI."""

    def __init__(self):
        self.ps = subprocess.Popen(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", PS_LOOP],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

    def _ask(self, line):
        self.ps.stdin.write(line + "\n")
        self.ps.stdin.flush()
        reply = self.ps.stdout.readline().strip()
        if not reply.isdigit():
            raise SystemExit(f"laptop backlight: no answer from WMI (got {reply!r})")
        return int(reply)

    def get(self):
        return self._ask("?")

    def set(self, percent):
        return self._ask(str(max(0, min(100, percent))))

    def close(self):
        self.ps.stdin.close()
        self.ps.wait()


def anchor_pattern():
    """The bars on black, sized like the spanning window (laptop left, external right).

    Returns the image rows (RGB bytes) and the bar labels for the overlay, at the bars' left end on the laptop.
    """
    pitch_l, pitch_e = LAPTOP_MM / LAPTOP_W, EXT_MM / EXT_W
    left, ext_w = LAPTOP_W - round(BAR_WIDTH_MM / pitch_l), round(BAR_WIDTH_MM / pitch_e)
    laptop_grey, ext_grey, labels = [0] * LAPTOP_H, [0] * LAPTOP_H, []
    for i, (percent, text) in enumerate(GREYS):
        top_mm = BAR_TOP_MM + i * (BAR_MM + GAP_MM)
        grey = round(255 * percent / 100)
        for rows, pitch in ((laptop_grey, pitch_l), (ext_grey, pitch_e)):
            rows[round(top_mm / pitch):round((top_mm + BAR_MM) / pitch)] = [grey] * (
                round((top_mm + BAR_MM) / pitch) - round(top_mm / pitch))
        labels.append((left - 40, round((top_mm + BAR_MM / 2) / pitch_l), text))
    rows = [bytes(3 * left) + bytes([gl] * 3) * (LAPTOP_W - left) + bytes([ge] * 3) * ext_w + bytes(3 * (EXT_W - ext_w))
            for gl, ge in zip(laptop_grey, ext_grey)]
    return rows, labels


def write_png(path, rows):
    """8-bit RGB PNG from raw rows, no image library needed."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", len(rows[0]) // 3, len(rows), 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                     + chunk(b"IDAT", zlib.compress(b"".join(b"\x00" + row for row in rows), 6)) + chunk(b"IEND", b""))


def match(mpv, extra_mpv_args=()):
    """Show the anchor and follow the keys.

    Returns (original level, final level, how it ended, number of changes); the level is kept only after Enter.
    """
    light = Backlight()
    original, kept, ended, changes, tail = light.get(), None, "closing the window", 0, []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pattern, script = Path(tmp) / "anchor.png", Path(tmp) / "match-keys.lua"
            rows, labels = anchor_pattern()
            write_png(pattern, rows)
            lua_labels = "{" + ", ".join(f'{{{x}, {y}, "{text}"}}' for x, y, text in labels) + "}"
            script.write_text(LUA.replace("LABELS", lua_labels).replace("WINDOW_W", str(LAPTOP_W + EXT_W))
                              .replace("WINDOW_H", str(LAPTOP_H)))
            player = subprocess.Popen([str(mpv), "--no-config", "--fs", "--fs-screen=all", "--vo=gpu-next",
                                       "--gpu-api=d3d11", "--osc=no", "--cursor-autohide=always",
                                       "--image-display-duration=inf", f"--script={script}",
                                       f"--script-opts=match-start={original}", *extra_mpv_args, str(pattern)],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
            for line in player.stdout:
                tail = (tail + [line.rstrip()])[-15:]  # mpv's last words, shown if it fails
                found = re.search(r"MATCHBRIGHT (set|keep|undo) (\d+)", line)
                if found and found[1] == "set":
                    light.set(int(found[2]))
                    changes += 1
                elif found:
                    ended = "Enter" if found[1] == "keep" else "Esc or q"
                    kept = int(found[2]) if found[1] == "keep" else None
            if player.wait() != 0:
                ended = f"an mpv error (exit code {player.returncode}):\n  " + "\n  ".join(tail)
    finally:
        final = light.set(kept if kept is not None else original)
        light.close()
    return original, final, ended, changes


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mpv", type=Path, default=DEFAULT_MPV)
    parser.add_argument("--set", type=int, metavar="PERCENT", help="set the laptop backlight and exit")
    args = parser.parse_args()
    if args.set is not None:
        light = Backlight()
        print(f"laptop backlight: was {light.get()}%, now {light.set(args.set)}%")
        light.close()
        return
    original, final, ended, changes = match(args.mpv)
    print(f"ended by {ended}, after {changes} brightness change(s)")
    if ended == "Enter":
        print(f"laptop backlight: was {original}%, kept at {final}%. "
              f"Put it back with: python {Path(__file__).name} --set {original}")
    else:
        print(f"laptop backlight: put back to {final}% (was {original}%)")


if __name__ == "__main__":
    main()
