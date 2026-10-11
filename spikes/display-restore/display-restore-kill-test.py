"""Bounded test: does a refresh-rate change come back by itself when the process that made it is killed?

Setting: the external monitor only (found by its EDID name EGM22F100H; the script aborts if it is not found),
built-in modes only (100 Hz -> 60 Hz at 1920x1080), never persisted (no CDS_UPDATEREGISTRY, no
SDC_SAVE_TO_DATABASE). Exactly three kill trials, one per method:

  1. cds0            ChangeDisplaySettingsEx(flags = 0)
  2. cds_fullscreen  ChangeDisplaySettingsEx(flags = CDS_FULLSCREEN)
  3. sdc             SetDisplayConfig(SDC_APPLY | SDC_USE_SUPPLIED_DISPLAY_CONFIG | SDC_ALLOW_CHANGES), no save

For each trial a child process (with one small ordinary window, as a player has) makes the change and
reports back; the parent reads the mode back, kills the child with TerminateProcess, then watches the mode for
10 s. Whatever happens, the parent then puts the external back to its original mode and verifies it with a
read-back. The original state is written to disk BEFORE anything changes, so a killed parent can be undone
with:  python display-restore-kill-test.py --restore

Usage:  python display-restore-kill-test.py            run the whole bounded test (3 kills, ~2 minutes)
        python display-restore-kill-test.py --dry-run  find the monitor, validate every change, change nothing
        python display-restore-kill-test.py --restore  put the external back from the saved snapshot
Internal: --child METHOD DEVICE HZ
A counter file stops a fourth kill trial; delete results-*/state.json deliberately to run again.
"""
import argparse
import atexit
import ctypes
import ctypes.wintypes as wt
import importlib.util
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results-2026-10-11"
PROBE_PATH = HERE.parent / "room-probe" / "room-probe.py"
TARGET_NAME_EDID = "EGM22F100H"
ORIGINAL_HZ, TEST_HZ, TEST_SIZE = 100, 60, (1920, 1080)
OBSERVE_SECONDS, POLL_SECONDS, SETTLE_SECONDS, MAX_TRIALS = 10.0, 0.25, 3.0, 3
CDS_UPDATEREGISTRY, CDS_TEST, CDS_FULLSCREEN = 0x1, 0x2, 0x4  # winuser.h (SDK 10.0.26100.0)
DM_BITSPERPEL, DM_PELSWIDTH, DM_PELSHEIGHT, DM_DISPLAYFREQUENCY = 0x40000, 0x80000, 0x100000, 0x400000
SDC_USE_SUPPLIED, SDC_VALIDATE, SDC_APPLY, SDC_ALLOW_CHANGES = 0x20, 0x40, 0x80, 0x400  # SDC_SAVE_TO_DATABASE (0x200) is never used
SDC_USE_DATABASE_CURRENT = 0x1 | 0x2 | 0x4 | 0x8
METHODS = ("cds0", "cds_fullscreen", "sdc")


def load_probe():
    spec = importlib.util.spec_from_file_location("room_probe", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rp = load_probe()
user32 = rp.user32
user32.ChangeDisplaySettingsExW.argtypes = [wt.LPCWSTR, ctypes.POINTER(rp.DEVMODEW), ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]
user32.ChangeDisplaySettingsExW.restype = ctypes.c_long
user32.SetDisplayConfig.argtypes = [rp.u32, ctypes.POINTER(rp.PATH_INFO), rp.u32, ctypes.POINTER(rp.MODE_INFO), rp.u32]
user32.SetDisplayConfig.restype = ctypes.c_long


class Abort(Exception):
    """Something unexpected happened: restore and stop."""


# ---------------------------------------------------------------- reading state

def active_displays():
    """One row per active path: GDI name, friendly EDID name, position, size, exact refresh, current/registry GDI mode."""
    paths, modes = rp.query_display_config(rp.QDC_ONLY_ACTIVE_PATHS)
    rows = []
    for i, p in enumerate(paths):
        s, t = p.sourceInfo, p.targetInfo
        sn = rp.device_info(rp.SOURCE_NAME, 1, s.adapterId, s.id)
        tn = rp.device_info(rp.TARGET_NAME, 2, t.adapterId, t.id)
        src = modes[s.modeInfoIdx].u.sourceMode if s.modeInfoIdx < len(modes) else None
        tgt = modes[t.modeInfoIdx].u.targetMode if t.modeInfoIdx < len(modes) else None
        gdi = sn.viewGdiDeviceName if sn else None
        cur, reg = (rp.devmode(gdi, rp.ENUM_CURRENT), rp.devmode(gdi, rp.ENUM_REGISTRY)) if gdi else (None, None)
        rows.append({"index": i, "gdi": gdi, "edid_name": tn.monitorFriendlyDeviceName if tn else None,
                     "tech": rp.TECH.get(t.outputTechnology), "x": src.x if src else None, "y": src.y if src else None,
                     "w": src.width if src else None, "h": src.height if src else None,
                     "refresh": [t.refreshRate.Numerator, t.refreshRate.Denominator],
                     "signal_refresh": [tgt.vSyncFreq.Numerator, tgt.vSyncFreq.Denominator] if tgt else None,
                     "pixel_rate": tgt.pixelRate if tgt else None,
                     "gdi_now": rp.dm_dict(cur) if cur else None, "gdi_registry": rp.dm_dict(reg) if reg else None,
                     "target_id": t.id})
    return rows


def find_external(rows):
    hits = [r for r in rows if r["edid_name"] == TARGET_NAME_EDID]
    if len(hits) != 1:
        raise Abort(f"expected exactly one active monitor named {TARGET_NAME_EDID}, found {len(hits)}")
    ext = hits[0]
    if ext["tech"] != "HDMI" or (ext["w"], ext["h"]) != TEST_SIZE:
        raise Abort(f"{TARGET_NAME_EDID} is not the expected HDMI {TEST_SIZE} output: {ext['tech']} {ext['w']}x{ext['h']}")
    return ext


def mode_hz(row):
    return row["gdi_now"]["hz_int"] if row.get("gdi_now") else None


def others_equal(before, after, device):
    key = lambda r: (r["gdi"], r["x"], r["y"], r["w"], r["h"], tuple(r["refresh"]))
    return sorted(key(r) for r in before if r["gdi"] != device) == sorted(key(r) for r in after if r["gdi"] != device)


# ---------------------------------------------------------------- the three change methods

def cds_change(device, hz, flags):
    dm = rp.devmode(device, rp.ENUM_CURRENT)
    dm.dmDisplayFrequency = hz
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    return user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, flags, None)


def edid_first_dtd(row):
    paths, _ = rp.query_display_config(rp.QDC_ONLY_ACTIVE_PATHS)
    t = paths[row["index"]].targetInfo
    edid = rp.read_target(t.adapterId, t.id).get("edid_raw")
    return rp.parse_edid(edid)["dtds"][0] if edid else None


def sdc_config(row, hz, variant):
    """Active config with only the external's refresh changed. variant 'auto': let Windows pick the timing;
    variant 'dtd': write the monitor's own 60 Hz detailed timing into the target mode."""
    paths, modes = rp.query_display_config(rp.QDC_ONLY_ACTIVE_PATHS)
    p = paths[row["index"]]
    p.targetInfo.refreshRate.Numerator, p.targetInfo.refreshRate.Denominator = hz, 1
    if variant == "auto":
        p.targetInfo.modeInfoIdx = 0xFFFFFFFF  # DISPLAYCONFIG_PATH_MODE_IDX_INVALID
    else:
        dtd = edid_first_dtd(row)
        sig = modes[p.targetInfo.modeInfoIdx].u.targetMode
        sig.pixelRate = dtd["pixel_clock_hz"]
        sig.vSyncFreq.Numerator, sig.vSyncFreq.Denominator = dtd["refresh"]
        sig.hSyncFreq.Numerator, sig.hSyncFreq.Denominator = dtd["pixel_clock_hz"], dtd["h_total"]
        sig.totalSize.cx, sig.totalSize.cy = dtd["h_total"], dtd["v_total"]
    return paths, modes


def sdc_call(paths, modes, flags):
    path_array = (rp.PATH_INFO * len(paths))(*paths)  # the query returns lists; the API wants C arrays
    mode_array = (rp.MODE_INFO * len(modes))(*modes)
    return user32.SetDisplayConfig(len(paths), path_array, len(modes), mode_array, flags)


def sdc_validate(row, hz):
    """Return the first variant Windows accepts as a pure validation (changes nothing), or (None, last_rc)."""
    rc = None
    for variant in ("auto", "dtd"):
        paths, modes = sdc_config(row, hz, variant)
        rc = sdc_call(paths, modes, SDC_VALIDATE | SDC_USE_SUPPLIED | SDC_ALLOW_CHANGES)
        if rc == 0:
            return variant, 0
    return None, rc


def cds_validate(device, hz, flags_test_only=CDS_TEST):
    return cds_change(device, hz, flags_test_only)


# ---------------------------------------------------------------- child process (makes the change, then idles)

def make_window():
    """One small ordinary top-level window on the laptop panel, as a player process would own."""
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    hwnd = user32.CreateWindowExW(0x80 | 0x08000000, "STATIC", "display-restore test child (safe to ignore)",
                                  0x00C00000 | 0x10000000, 200, 200, 460, 80, None, None, None, None)
    user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
    return hwnd


def pump_forever():
    msg = wt.MSG()
    while True:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.02)


def child_main(method, device, hz):
    make_window()
    if method == "cds0":
        rc = cds_change(device, hz, 0)
    elif method == "cds_fullscreen":
        rc = cds_change(device, hz, CDS_FULLSCREEN)
    else:
        row = find_external(active_displays())
        variant, vrc = sdc_validate(row, hz)
        if variant is None:
            print(f"APPLIED {method} rc={vrc} (validation failed)", flush=True)
            return
        paths, modes = sdc_config(row, hz, variant)
        rc = sdc_call(paths, modes, SDC_APPLY | SDC_USE_SUPPLIED | SDC_ALLOW_CHANGES)  # no SDC_SAVE_TO_DATABASE
    print(f"APPLIED {method} rc={rc}", flush=True)
    pump_forever()


# ---------------------------------------------------------------- parent: trials, restore, bookkeeping

def read_child_line(child, timeout):
    box = []
    t = threading.Thread(target=lambda: box.append(child.stdout.readline()), daemon=True)
    t.start()
    t.join(timeout)
    return box[0].strip() if box else None


def restore_external(device, log):
    """Put the external back: first the product's own way (reset to the saved mode), then explicit fallbacks."""
    steps = []

    def now_hz():
        return mode_hz(next(r for r in active_displays() if r["gdi"] == device))

    if now_hz() == ORIGINAL_HZ:
        return {"needed": False, "final_hz": ORIGINAL_HZ}
    t0 = time.perf_counter()
    rc = user32.ChangeDisplaySettingsExW(device, None, None, 0, None)  # NULL devmode + 0 = back to the saved mode
    time.sleep(SETTLE_SECONDS)
    steps.append({"step": "reset_to_saved_mode (ChangeDisplaySettingsEx NULL,0)", "rc": rc, "hz_after": now_hz(),
                  "seconds": round(time.perf_counter() - t0, 2)})
    if now_hz() != ORIGINAL_HZ:
        rc = cds_change(device, ORIGINAL_HZ, 0)
        time.sleep(SETTLE_SECONDS)
        steps.append({"step": "explicit_100hz (ChangeDisplaySettingsEx flags 0)", "rc": rc, "hz_after": now_hz()})
    if now_hz() != ORIGINAL_HZ:
        reg = next(r for r in active_displays() if r["gdi"] == device)["gdi_registry"]["hz_int"]
        if reg != ORIGINAL_HZ:  # only if a trial persisted something by accident: undo that persistence
            rc = user32.ChangeDisplaySettingsExW(device, ctypes.byref(_dm_with_hz(device, ORIGINAL_HZ)), None, CDS_UPDATEREGISTRY, None)
            time.sleep(SETTLE_SECONDS)
            steps.append({"step": "explicit_100hz_persisted (registry had changed)", "rc": rc, "hz_after": now_hz()})
    final = now_hz()
    log(f"restore -> {final} Hz via {[s['step'] for s in steps]}")
    return {"needed": True, "steps": steps, "final_hz": final}


def _dm_with_hz(device, hz):
    dm = rp.devmode(device, rp.ENUM_CURRENT)
    dm.dmDisplayFrequency, dm.dmFields = hz, DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    return dm


def run_trial(method, device, before, log):
    trial = {"method": method}
    child = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "--child", method, device, str(TEST_HZ)],
                             stdout=subprocess.PIPE, text=True, bufsize=1)
    trial["child_pid"] = child.pid
    try:
        line = read_child_line(child, 30)
        trial["child_report"] = line
        if not line or not line.startswith("APPLIED") or "rc=0" not in line:
            raise Abort(f"{method}: child did not apply the change cleanly: {line!r}")
        time.sleep(SETTLE_SECONDS)
        rows = active_displays()
        ext = next(r for r in rows if r["gdi"] == device)
        trial["readback_before_kill"] = {"gdi_hz": mode_hz(ext), "ccd_refresh": ext["refresh"], "registry_hz": ext["gdi_registry"]["hz_int"]}
        log(f"{method}: applied, read back {mode_hz(ext)} Hz (ccd {ext['refresh']}), registry says {ext['gdi_registry']['hz_int']} Hz")
        if mode_hz(ext) != TEST_HZ:
            raise Abort(f"{method}: expected {TEST_HZ} Hz after the change, saw {mode_hz(ext)}")
        if not others_equal(before, rows, device):
            raise Abort(f"{method}: another display changed")
        t_kill = time.perf_counter()
        child.kill()  # Popen.kill() is TerminateProcess on Windows
        child.wait(10)
        trial["killed_exit_code"] = child.returncode
        samples, reverted_at = [], None
        while time.perf_counter() - t_kill < OBSERVE_SECONDS:
            ext = next(r for r in active_displays() if r["gdi"] == device)
            t = round(time.perf_counter() - t_kill, 2)
            samples.append([t, mode_hz(ext), ext["gdi_registry"]["hz_int"]])
            if reverted_at is None and mode_hz(ext) == ORIGINAL_HZ:
                reverted_at = t
            time.sleep(POLL_SECONDS)
        changes = [s for i, s in enumerate(samples) if i == 0 or s[1:] != samples[i - 1][1:]]
        trial.update({"observed_seconds": OBSERVE_SECONDS, "samples_t_gdi_hz_registry_hz_changes_only": changes,
                      "last_sample": samples[-1], "reverted_by_itself": reverted_at is not None, "reverted_after_s": reverted_at})
        log(f"{method}: after kill -> {'REVERTED after ' + str(reverted_at) + ' s' if reverted_at is not None else 'stayed at ' + str(samples[-1][1]) + ' Hz for 10 s'}")
    finally:
        if child.poll() is None:
            child.kill()
    return trial


def run_all(args):
    RESULTS.mkdir(exist_ok=True)
    state_path, snap_path, out_path = RESULTS / "state.json", RESULTS / "original-state.json", RESULTS / "kill-test.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"trials_done": 0}
    if state["trials_done"] >= MAX_TRIALS and not args.dry_run:
        raise SystemExit(f"{state['trials_done']} kill trials already done; delete {state_path} on purpose to run more")
    result = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "windows": rp.system_info(), "dry_run": args.dry_run, "trials": [],
              "notes": []}
    lines = []

    def log(msg):
        lines.append(msg)
        print(msg, flush=True)

    rows = active_displays()
    ext = find_external(rows)
    device = ext["gdi"]
    if mode_hz(ext) != ORIGINAL_HZ or ext["gdi_registry"]["hz_int"] != ORIGINAL_HZ:
        raise Abort(f"expected current and saved mode {ORIGINAL_HZ} Hz, saw {mode_hz(ext)} / {ext['gdi_registry']['hz_int']}")
    if TEST_HZ not in {m[2] for m in rp.gdi_modes(device) if (m[0], m[1]) == TEST_SIZE}:
        raise Abort(f"{TEST_HZ} Hz is not a built-in mode at {TEST_SIZE}")
    log(f"external = {device} ({ext['edid_name']}, {ext['tech']}), {ext['w']}x{ext['h']} @ {mode_hz(ext)} Hz, saved mode {ext['gdi_registry']['hz_int']} Hz")
    result["original"] = {"displays": rows, "external_gdi": device}
    snap_path.write_text(json.dumps(result["original"], indent=1))  # dirty snapshot, on disk before any change
    atexit.register(lambda: restore_external(device, log) if not args.dry_run else None)

    # validate every change without applying it
    cds_rc = cds_validate(device, TEST_HZ)
    cdsfs_rc = cds_validate(device, TEST_HZ, CDS_TEST | CDS_FULLSCREEN)
    variant, sdc_rc = sdc_validate(ext, TEST_HZ)
    result["validation"] = {"cds0_CDS_TEST_rc": cds_rc, "cds_fullscreen_CDS_TEST_rc": cdsfs_rc, "sdc_validate_variant": variant, "sdc_validate_rc": sdc_rc}
    log(f"validation: cds0 rc={cds_rc}, cds_fullscreen rc={cdsfs_rc}, sdc variant={variant} rc={sdc_rc}")
    if args.dry_run:
        log("dry run: nothing was changed")
        out_path.with_name("dry-run.json").write_text(json.dumps(result, indent=1))
        return result
    methods = [m for m in METHODS if not (m == "cds0" and cds_rc) and not (m == "cds_fullscreen" and cdsfs_rc) and not (m == "sdc" and variant is None)]
    result["methods_run"] = methods
    try:
        for method in methods:
            if state["trials_done"] >= MAX_TRIALS:
                break
            before = active_displays()
            state["trials_done"] += 1
            state_path.write_text(json.dumps(state))  # count the trial before it starts
            trial = run_trial(method, device, before, log)
            trial["restore"] = restore_external(device, log)
            after = active_displays()
            trial["others_unchanged"] = others_equal(before, after, device)
            ext_now = next(r for r in after if r["gdi"] == device)
            trial["final_state"] = {"gdi_hz": mode_hz(ext_now), "ccd_refresh": ext_now["refresh"], "registry_hz": ext_now["gdi_registry"]["hz_int"],
                                    "position": [ext_now["x"], ext_now["y"]]}
            result["trials"].append(trial)
            if trial["restore"]["final_hz"] != ORIGINAL_HZ:
                raise Abort(f"{method}: could not restore {ORIGINAL_HZ} Hz")
            time.sleep(2)
    except Exception as exc:  # noqa: BLE001 - any surprise: restore and stop
        result["aborted"] = f"{type(exc).__name__}: {exc}"
        log(f"ABORT: {result['aborted']}")
    finally:
        result["final_restore"] = restore_external(device, log)
        final_rows = active_displays()
        result["final_displays"] = final_rows
        result["all_displays_same_as_original"] = {"others": others_equal(rows, final_rows, device),
                                                   "external_hz": mode_hz(next(r for r in final_rows if r["gdi"] == device)),
                                                   "external_registry_hz": next(r for r in final_rows if r["gdi"] == device)["gdi_registry"]["hz_int"]}
        out_path.write_text(json.dumps(result, indent=1))
        log(f"final: external {result['all_displays_same_as_original']['external_hz']} Hz, saved mode "
            f"{result['all_displays_same_as_original']['external_registry_hz']} Hz, others unchanged = {result['all_displays_same_as_original']['others']}")
    return result


def restore_from_snapshot():
    snap = json.loads((RESULTS / "original-state.json").read_text())
    device = snap["external_gdi"]
    result = restore_external(device, print)
    print(json.dumps(result, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--child", nargs=3, metavar=("METHOD", "DEVICE", "HZ"))
    args = ap.parse_args()
    if args.child:
        child_main(args.child[0], args.child[1], int(args.child[2]))
    elif args.restore:
        restore_from_snapshot()
    else:
        try:
            run_all(args)
        except Abort as exc:
            print(f"ABORT before any change: {exc}")
            sys.exit(2)


if __name__ == "__main__":
    main()
