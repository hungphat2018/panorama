"""Temporarily change one display's refresh rate, and put it back.

ChangeDisplaySettingsEx without CDS_UPDATEREGISTRY: the change is not saved,
Windows keeps the user's own mode in the registry, and restore() (or signing
out) returns to it. Only the refresh rate changes; resolution, position and
orientation stay as they are. The rate must be one the driver offers at the
current resolution (a custom mode counts once it has been created).

Usage: python display-refresh.py list
       python display-refresh.py set DEVICE HZ        e.g. set \\\\.\\DISPLAY2 96
       python display-refresh.py restore DEVICE
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import time

ENUM_CURRENT_SETTINGS, EDS_RAWMODE = -1, 0x2
DM_PELSWIDTH, DM_PELSHEIGHT, DM_DISPLAYFREQUENCY = 0x80000, 0x100000, 0x400000
# CDS_TEST only validates. (0x4 is CDS_FULLSCREEN, a change Windows undoes when the calling process exits;
# this tool sets the mode in one process and restores it from another, so it must not use it.)
CDS_TEST, DISP_CHANGE_SUCCESSFUL = 0x2, 0
SETTLE_S = 3.0  # a monitor takes a moment to resync after a mode change


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("DeviceName", wt.WCHAR * 32), ("DeviceString", wt.WCHAR * 128),
                ("StateFlags", wt.DWORD), ("DeviceID", wt.WCHAR * 128), ("DeviceKey", wt.WCHAR * 128)]


class DEVMODEW(ctypes.Structure):
    _fields_ = [("dmDeviceName", wt.WCHAR * 32), ("dmSpecVersion", wt.WORD), ("dmDriverVersion", wt.WORD),
                ("dmSize", wt.WORD), ("dmDriverExtra", wt.WORD), ("dmFields", wt.DWORD),
                ("dmPositionX", wt.LONG), ("dmPositionY", wt.LONG), ("dmDisplayOrientation", wt.DWORD),
                ("dmDisplayFixedOutput", wt.DWORD), ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short), ("dmCollate", ctypes.c_short),
                ("dmFormName", wt.WCHAR * 32), ("dmLogPixels", wt.WORD), ("dmBitsPerPel", wt.DWORD),
                ("dmPelsWidth", wt.DWORD), ("dmPelsHeight", wt.DWORD), ("dmDisplayFlags", wt.DWORD),
                ("dmDisplayFrequency", wt.DWORD), ("dmICMMethod", wt.DWORD), ("dmICMIntent", wt.DWORD),
                ("dmMediaType", wt.DWORD), ("dmDitherType", wt.DWORD), ("dmReserved1", wt.DWORD),
                ("dmReserved2", wt.DWORD), ("dmPanningWidth", wt.DWORD), ("dmPanningHeight", wt.DWORD)]


user32 = ctypes.windll.user32
user32.ChangeDisplaySettingsExW.argtypes = [wt.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]


def current(device):
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    if not user32.EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        raise SystemExit(f"{device}: cannot read the current mode")
    return dm


def rates(device):
    """Refresh rates the driver offers at the device's current resolution."""
    cur, found, i = current(device), set(), 0
    while True:
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(dm)
        if not user32.EnumDisplaySettingsExW(device, i, ctypes.byref(dm), EDS_RAWMODE):
            return sorted(found)
        i += 1
        if (dm.dmPelsWidth, dm.dmPelsHeight) == (cur.dmPelsWidth, cur.dmPelsHeight):
            found.add(dm.dmDisplayFrequency)


def devices():
    out, i = [], 0
    while True:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            return out
        i += 1
        if dd.StateFlags & 0x1:  # attached to the desktop
            out.append(dd.DeviceName)


def set_refresh(device, hz):
    """Switch `device` to `hz` at its current resolution, unsaved. Raises if the driver refuses."""
    if hz not in rates(device):
        raise SystemExit(f"{device}: {hz} Hz is not offered at this resolution (offered: {rates(device)})")
    dm = current(device)
    dm.dmDisplayFrequency = hz
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    for flags in (CDS_TEST, 0):
        result = user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, flags, None)
        if result != DISP_CHANGE_SUCCESSFUL:
            raise SystemExit(f"{device}: switching to {hz} Hz failed (code {result}, test={flags == CDS_TEST})")
    time.sleep(SETTLE_S)


def restore(device):
    """Return `device` to the mode saved in the registry (the user's own setting)."""
    result = user32.ChangeDisplaySettingsExW(device, None, None, 0, None)
    time.sleep(SETTLE_S)
    return result == DISP_CHANGE_SUCCESSFUL


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["list", "set", "restore"])
    parser.add_argument("device", nargs="?")
    parser.add_argument("hz", nargs="?", type=int)
    args = parser.parse_args()
    if args.action == "list":
        for device in devices():
            print(f"{device}: now {current(device).dmDisplayFrequency} Hz, offered {rates(device)}")
    elif args.action == "set":
        set_refresh(args.device, args.hz)
        print(f"{args.device}: now {current(args.device).dmDisplayFrequency} Hz")
    else:
        print(f"{args.device}: restored={restore(args.device)}, now {current(args.device).dmDisplayFrequency} Hz")


if __name__ == "__main__":
    main()
