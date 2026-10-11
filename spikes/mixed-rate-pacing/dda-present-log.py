"""Log when Windows updates each monitor, and which video frame each update showed.

Desktop Duplication (DXGI) on every output that holds one of the span test
video's barcodes: one thread and one D3D11 device per output. For every desktop
update it records the OS present time (QueryPerformanceCounter ticks: one clock
for all outputs), how many updates Windows merged into it, and the frame index
read from that output's barcode. A second thread per output records that
output's own refresh times (IDXGIOutput::WaitForVBlank, then QPC): the desktop
updates show when Windows composed a new image, the refresh times show when
the screen could pick it up. Read-only: it never changes display settings.

FFmpeg's capture filters are not used here: neither gives a clock shared by two
captures (ddagrab stamps frames with its own polling time; gfxcapture subtracts
each capture's first timestamp). A window that is promoted to a hardware overlay
plane is invisible to Desktop Duplication (see the exact-frame spike); a window
spanning several monitors is expected to be composed, which this tool checks.

Usage: python dda-present-log.py TRUTH_JSON OUT_JSON [--seconds 30]
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import threading
import uuid
from pathlib import Path

import numpy as np

HRESULT = ctypes.c_long
DXGI_ERROR_ACCESS_LOST = ctypes.c_long(0x887A0026).value
DXGI_ERROR_WAIT_TIMEOUT = ctypes.c_long(0x887A0027).value
DXGI_FORMAT_B8G8R8A8_UNORM, DXGI_FORMAT_R16G16B16A16_FLOAT = 87, 10
D3D11_SDK_VERSION, D3D11_USAGE_STAGING, D3D11_CPU_ACCESS_READ, D3D11_MAP_READ = 7, 3, 0x20000, 1
PATCH = 3  # half-size of the square sampled at each barcode block centre


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text):
        u = uuid.UUID(text)
        return cls(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


IID_IDXGIFactory1 = GUID.parse("770aae78-f26f-4dba-a829-253c83d1b387")
IID_IDXGIOutput1 = GUID.parse("00cddea8-939b-4b83-a340-a685226666cc")
IID_IDXGIOutput5 = GUID.parse("80a07424-ab52-42eb-833c-0c42fd282d98")
IID_ID3D11Texture2D = GUID.parse("6f15aaf2-d208-4e89-9ab4-489535d34f9c")


class DXGI_OUTPUT_DESC(ctypes.Structure):
    _fields_ = [("DeviceName", wt.WCHAR * 32), ("DesktopCoordinates", wt.RECT), ("AttachedToDesktop", wt.BOOL),
                ("Rotation", ctypes.c_uint), ("Monitor", ctypes.c_void_p)]


class DXGI_OUTDUPL_DESC(ctypes.Structure):
    _fields_ = [("Width", ctypes.c_uint), ("Height", ctypes.c_uint), ("RefreshNum", ctypes.c_uint),
                ("RefreshDen", ctypes.c_uint), ("Format", ctypes.c_uint), ("ScanlineOrdering", ctypes.c_uint),
                ("Scaling", ctypes.c_uint), ("Rotation", ctypes.c_uint), ("DesktopImageInSystemMemory", wt.BOOL)]


class DXGI_OUTDUPL_FRAME_INFO(ctypes.Structure):
    _fields_ = [("LastPresentTime", ctypes.c_int64), ("LastMouseUpdateTime", ctypes.c_int64),
                ("AccumulatedFrames", ctypes.c_uint), ("RectsCoalesced", wt.BOOL),
                ("ProtectedContentMaskedOut", wt.BOOL), ("PointerX", ctypes.c_long), ("PointerY", ctypes.c_long),
                ("PointerVisible", wt.BOOL), ("TotalMetadataBufferSize", ctypes.c_uint),
                ("PointerShapeBufferSize", ctypes.c_uint)]


class D3D11_TEXTURE2D_DESC(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint) for n in ("Width", "Height", "MipLevels", "ArraySize", "Format", "SampleCount",
                                             "SampleQuality", "Usage", "BindFlags", "CPUAccessFlags", "MiscFlags")]


class D3D11_BOX(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint) for n in ("left", "top", "front", "right", "bottom", "back")]


class D3D11_MAPPED_SUBRESOURCE(ctypes.Structure):
    _fields_ = [("pData", ctypes.c_void_p), ("RowPitch", ctypes.c_uint), ("DepthPitch", ctypes.c_uint)]


PP = ctypes.POINTER(ctypes.c_void_p)


def com(ptr, slot, *argtypes, restype=HRESULT):
    """Callable for vtable slot `slot` of the COM object at `ptr` (slot numbers from the SDK headers)."""
    vtable = ctypes.cast(ptr, ctypes.POINTER(PP))[0]
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[slot])
    return lambda *args: fn(ptr, *args)


def release(ptr):
    if ptr:
        com(ptr, 2, restype=ctypes.c_ulong)()


def query(ptr, iid):
    out = ctypes.c_void_p()
    hr = com(ptr, 0, ctypes.POINTER(GUID), PP)(ctypes.byref(iid), ctypes.byref(out))
    return out if hr == 0 else None


def check(hr, what):
    if hr != 0:
        raise OSError(f"{what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def qpc():
    value = ctypes.c_int64()
    ctypes.windll.kernel32.QueryPerformanceCounter(ctypes.byref(value))
    return value.value


def enum_outputs():
    """Every (adapter, output, desc) attached to the desktop."""
    create = ctypes.windll.dxgi.CreateDXGIFactory1
    create.argtypes, create.restype = [ctypes.POINTER(GUID), PP], HRESULT
    factory = ctypes.c_void_p()
    check(create(ctypes.byref(IID_IDXGIFactory1), ctypes.byref(factory)), "CreateDXGIFactory1")
    found, a = [], 0
    while True:
        adapter = ctypes.c_void_p()
        if com(factory, 12, ctypes.c_uint, PP)(a, ctypes.byref(adapter)) != 0:  # EnumAdapters1
            break
        a, o = a + 1, 0
        while True:
            output = ctypes.c_void_p()
            if com(adapter, 7, ctypes.c_uint, PP)(o, ctypes.byref(output)) != 0:  # EnumOutputs
                break
            o += 1
            desc = DXGI_OUTPUT_DESC()
            check(com(output, 7, ctypes.POINTER(DXGI_OUTPUT_DESC))(ctypes.byref(desc)), "IDXGIOutput::GetDesc")
            if desc.AttachedToDesktop:
                found.append((adapter, output, desc))
    return found


def create_device(adapter):
    create = ctypes.windll.d3d11.D3D11CreateDevice
    create.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint,
                       ctypes.c_uint, PP, ctypes.POINTER(ctypes.c_uint), PP]
    create.restype = HRESULT
    device, context, level = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_uint()
    check(create(adapter, 0, None, 0, None, 0, D3D11_SDK_VERSION, ctypes.byref(device), ctypes.byref(level),
                 ctypes.byref(context)), "D3D11CreateDevice")
    return device, context


def duplicate(output, device):
    """Duplication delivering 8-bit BGRA, also when the desktop composes in FP16 (Windows colour management)."""
    dupl = ctypes.c_void_p()
    out5 = query(output, IID_IDXGIOutput5)
    if out5:
        formats = (ctypes.c_uint * 1)(DXGI_FORMAT_B8G8R8A8_UNORM)
        hr = com(out5, 26, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint), PP)(
            device, 0, 1, formats, ctypes.byref(dupl))  # DuplicateOutput1
        release(out5)
        if hr == 0:
            return dupl
    out1 = query(output, IID_IDXGIOutput1)
    hr = com(out1, 22, ctypes.c_void_p, PP)(device, ctypes.byref(dupl))  # DuplicateOutput
    release(out1)
    check(hr, "DuplicateOutput")
    return dupl


def decode(img, centers, index_bits, check_bits, fp16):
    """Frame index from the barcode, or -1 when the check bits disagree."""
    threshold = 0.5 if fp16 else 128
    bits = [int(img[y - PATCH:y + PATCH + 1, x - PATCH:x + PATCH + 1, 1].mean() > threshold) for x, y in centers]
    index = int("".join(map(str, bits[:index_bits])), 2)
    check_value = int("".join(map(str, bits[index_bits:])), 2)
    return index if check_value == bin(index).count("1") % (1 << check_bits) else -1


def capture(job, seconds, freq, truth):
    """Thread body: duplicate one output and log every desktop update until the deadline."""
    adapter, output, probe, result = job["adapter"], job["output"], job["probe"], job["result"]
    device, context = create_device(adapter)
    dupl = duplicate(output, device)
    ddesc = DXGI_OUTDUPL_DESC()
    com(dupl, 7, ctypes.POINTER(DXGI_OUTDUPL_DESC), restype=None)(ctypes.byref(ddesc))
    result.update(refresh=[ddesc.RefreshNum, ddesc.RefreshDen], mode=[ddesc.Width, ddesc.Height],
                  dupl_format=ddesc.Format, image_in_system_memory=bool(ddesc.DesktopImageInSystemMemory))
    copy_region = com(context, 46, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                      ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(D3D11_BOX), restype=None)
    map_staging = com(context, 14, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                      ctypes.POINTER(D3D11_MAPPED_SUBRESOURCE))
    unmap_staging = com(context, 15, ctypes.c_void_p, ctypes.c_uint, restype=None)
    x0, y0, w, h = probe["x0"], probe["y0"], probe["w"], probe["h"]
    box = D3D11_BOX(x0, y0, 0, x0 + w, y0 + h, 1)
    staging, fmt = None, None
    events, counts = [], {"timeouts": 0, "access_lost": 0, "pointer_only": 0, "unreadable": 0}
    deadline = qpc() + int(seconds * freq)
    while qpc() < deadline:
        info, resource = DXGI_OUTDUPL_FRAME_INFO(), ctypes.c_void_p()
        hr = com(dupl, 8, ctypes.c_uint, ctypes.POINTER(DXGI_OUTDUPL_FRAME_INFO), PP)(
            100, ctypes.byref(info), ctypes.byref(resource))  # AcquireNextFrame
        if hr == DXGI_ERROR_WAIT_TIMEOUT:
            counts["timeouts"] += 1
            continue
        if hr == DXGI_ERROR_ACCESS_LOST:
            counts["access_lost"] += 1
            release(dupl)
            dupl = duplicate(output, device)
            continue
        check(hr, "AcquireNextFrame")
        try:
            if info.LastPresentTime == 0:
                counts["pointer_only"] += 1
                continue
            tex = query(resource, IID_ID3D11Texture2D)
            if staging is None:
                src = D3D11_TEXTURE2D_DESC()
                com(tex, 10, ctypes.POINTER(D3D11_TEXTURE2D_DESC), restype=None)(ctypes.byref(src))
                fmt = src.Format
                want = D3D11_TEXTURE2D_DESC(w, h, 1, 1, fmt, 1, 0, D3D11_USAGE_STAGING, 0, D3D11_CPU_ACCESS_READ, 0)
                staging = ctypes.c_void_p()
                check(com(device, 5, ctypes.POINTER(D3D11_TEXTURE2D_DESC), ctypes.c_void_p, PP)(
                    ctypes.byref(want), None, ctypes.byref(staging)), "CreateTexture2D")
                result["texture_format"] = fmt
            copy_region(staging, 0, 0, 0, 0, tex, 0, ctypes.byref(box))
            release(tex)
            mapped = D3D11_MAPPED_SUBRESOURCE()
            check(map_staging(staging, 0, D3D11_MAP_READ, 0, ctypes.byref(mapped)), "Map")
            try:
                raw = ctypes.string_at(mapped.pData, mapped.RowPitch * h)
            finally:
                unmap_staging(staging, 0)
            fp16 = fmt == DXGI_FORMAT_R16G16B16A16_FLOAT
            dtype, channels = (np.float16, 4) if fp16 else (np.uint8, 4)
            row = np.frombuffer(raw, dtype).reshape(h, mapped.RowPitch // np.dtype(dtype).itemsize)
            img = row[:, :w * channels].reshape(h, w, channels).astype(np.float32)
            index = decode(img, probe["centers"], truth["index_bits"], truth["check_bits"], fp16)
            counts["unreadable"] += index < 0
            events.append([info.LastPresentTime, qpc(), info.AccumulatedFrames, index])
        finally:
            release(resource)
            com(dupl, 14)()  # ReleaseFrame
    result.update(events=events, counts=counts)
    release(staging)
    release(dupl)
    release(context)
    release(device)


def log_vblanks(job, seconds, freq):
    """Thread body: QPC time right after each refresh of one output, at time-critical priority."""
    k32 = ctypes.windll.kernel32
    k32.GetCurrentThread.restype = ctypes.c_void_p
    k32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
    k32.SetThreadPriority(k32.GetCurrentThread(), 15)  # THREAD_PRIORITY_TIME_CRITICAL
    wait = com(job["output"], 10)  # IDXGIOutput::WaitForVBlank
    times, failures = [], 0
    deadline = qpc() + int(seconds * freq)
    while qpc() < deadline:
        if wait() != 0:
            failures += 1
            k32.Sleep(1)
            continue
        times.append(qpc())
    job["result"].update(vblanks=times, vblank_failures=failures)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("truth", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--seconds", type=float, default=30)
    args = parser.parse_args()

    # Desktop Duplication wants a per-monitor DPI-aware caller (V2 = -4).
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    truth = json.loads(args.truth.read_text())
    freq = ctypes.c_int64()
    ctypes.windll.kernel32.QueryPerformanceFrequency(ctypes.byref(freq))
    half = truth["block_desktop"] // 2
    # The truth file counts from the window's top-left, which --fs-screen=all puts at the virtual
    # screen's origin; that origin is negative when the main display is not the leftmost screen.
    vx, vy = ctypes.windll.user32.GetSystemMetrics(76), ctypes.windll.user32.GetSystemMetrics(77)
    jobs = []
    for adapter, output, desc in enum_outputs():
        r = desc.DesktopCoordinates
        for name, code in truth["barcodes"].items():
            xs, y = [x + vx for x in code["x"]], code["y"] + vy
            if not (r.left <= min(xs) and max(xs) < r.right and r.top <= y < r.bottom):
                continue
            x0, y0 = min(xs) - half - r.left, y - half - r.top
            probe = {"x0": x0, "y0": y0, "w": max(xs) - min(xs) + 2 * half, "h": 2 * half,
                     "centers": [(x - r.left - x0, y - r.top - y0) for x in xs]}
            jobs.append({"adapter": adapter, "output": output, "probe": probe,
                         "result": {"barcode": name, "device": desc.DeviceName,
                                    "desktop": [r.left, r.top, r.right, r.bottom]}})
    if not jobs:
        raise SystemExit("no attached output holds a barcode: is the desktop layout the one in the truth file?")
    threads = [threading.Thread(target=capture, args=(job, args.seconds, freq.value, truth)) for job in jobs]
    threads += [threading.Thread(target=log_vblanks, args=(job, args.seconds, freq.value)) for job in jobs]
    start = qpc()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    report = {"qpc_freq": freq.value, "qpc_start": start, "qpc_end": qpc(),
              "outputs": [job["result"] for job in jobs]}
    args.out.write_text(json.dumps(report))
    for out in report["outputs"]:
        print(f"{out['barcode']:8s} {out['device']} refresh {out.get('refresh')} format {out.get('texture_format')}: "
              f"{len(out.get('events', []))} updates, {len(out.get('vblanks', []))} refreshes "
              f"({out.get('vblank_failures')} failed waits), {out.get('counts')}")


if __name__ == "__main__":
    main()
