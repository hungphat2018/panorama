"""Processes: finding ffmpeg / ffprobe, below-normal priority, a job object that owns the worker tree, subprocess helpers.

Why a job object: the worker starts ffmpeg children and pool processes. Putting the whole tree in one Windows job gives
(a) CPU time of every process in the tree (the accounting the throughput report quotes), (b) an optional memory cap, and
(c) "kill on close": if the worker dies, Windows ends its children, so no ffmpeg is left behind.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
from ctypes import wintypes
from typing import Optional

IS_WINDOWS = os.name == "nt"
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
CREATE_NO_WINDOW = 0x08000000

_tool_cache: dict = {}


def find_tool(name: str) -> str:
    """Path of ffmpeg / ffprobe: PANORAMA_FFMPEG / PANORAMA_FFPROBE if set, else the first one on PATH."""
    if name in _tool_cache:
        return _tool_cache[name]
    override = os.environ.get("PANORAMA_" + name.upper())
    path = override if override and os.path.isfile(override) else shutil.which(name)
    if not path:
        raise FileNotFoundError(f"{name} not found: put it on PATH or set PANORAMA_{name.upper()}")
    _tool_cache[name] = path
    return path


def tool_version(name: str) -> str:
    """First line of '<tool> -version', e.g. 'ffmpeg version 8.1-full_build-www.gyan.dev ...'."""
    out = subprocess.run([find_tool(name), "-hide_banner", "-version"], capture_output=True, text=True,
                         creationflags=CREATE_NO_WINDOW if IS_WINDOWS else 0).stdout
    return out.splitlines()[0].strip() if out else "unknown"


def lower_own_priority() -> bool:
    """BELOW_NORMAL for this process; children started afterwards inherit it. Returns True when it took effect."""
    if IS_WINDOWS:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        return bool(k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS))
    try:
        os.nice(5)
        return True
    except OSError:
        return False


def popen(args: list, **kwargs) -> subprocess.Popen:
    """Popen without a console window and at below-normal priority (Windows); arguments as a list, never a shell string."""
    if IS_WINDOWS:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS
    return subprocess.Popen(args, **kwargs)


def run_text(args: list, timeout: Optional[float] = None, check: bool = False) -> subprocess.CompletedProcess:
    """Runs a command to completion, capturing stdout/stderr as UTF-8 text (undecodable bytes replaced)."""
    flags = (CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS) if IS_WINDOWS else 0
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          check=check, creationflags=flags)


def filter_quote_path(path: str) -> str:
    """A file name as an ffmpeg filter option value: quoted, forward slashes, colons escaped (the form the research used)."""
    p = os.path.abspath(path).replace("\\", "/").replace(":", "\\:")
    if "'" in p:
        raise ValueError(f"a quote in the path cannot be passed to an ffmpeg filter: {path!r}")
    return "'" + p + "'"


# --- job object -----------------------------------------------------------------------------------------------------

class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit), ("IoInfo", _IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]


_JOB_LIMIT_JOB_MEMORY = 0x00000200
_JOB_LIMIT_KILL_ON_CLOSE = 0x00002000
_INFO_BASIC_ACCOUNTING = 1
_INFO_EXTENDED_LIMIT = 9


class JobObject:
    """A Windows job holding this process and everything it starts afterwards. A no-op object off Windows or when the job
    cannot be created (then `active` is False and `cpu_seconds()` returns None)."""

    def __init__(self, memory_cap_bytes: Optional[int] = None, kill_on_close: bool = True):
        self.active = False
        self.error: Optional[str] = None
        self._handle = None
        self._k32 = None
        if not IS_WINDOWS:
            self.error = "not Windows"
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                  ctypes.c_void_p]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            self.error = f"CreateJobObject failed ({ctypes.get_last_error()})"
            return
        info = _ExtendedLimit()
        if kill_on_close:
            info.BasicLimitInformation.LimitFlags |= _JOB_LIMIT_KILL_ON_CLOSE
        if memory_cap_bytes:
            info.BasicLimitInformation.LimitFlags |= _JOB_LIMIT_JOB_MEMORY
            info.JobMemoryLimit = int(memory_cap_bytes)
        if not k32.SetInformationJobObject(handle, _INFO_EXTENDED_LIMIT, ctypes.byref(info), ctypes.sizeof(info)):
            self.error = f"SetInformationJobObject failed ({ctypes.get_last_error()})"
            k32.CloseHandle(handle)
            return
        if not k32.AssignProcessToJobObject(handle, k32.GetCurrentProcess()):
            # a parent job without nesting support refuses; the worker still runs, only the accounting is missing
            self.error = f"AssignProcessToJobObject failed ({ctypes.get_last_error()})"
            k32.CloseHandle(handle)
            return
        self._handle = handle
        self._k32 = k32
        self.active = True

    def cpu_seconds(self) -> Optional[dict]:
        """User and kernel CPU seconds of every process that ever ran in the job (terminated ones included)."""
        if not self.active:
            return None
        acc = _Accounting()
        ok = self._k32.QueryInformationJobObject(self._handle, _INFO_BASIC_ACCOUNTING, ctypes.byref(acc),
                                                              ctypes.sizeof(acc), None)
        if not ok:
            return None
        return {"user_s": acc.TotalUserTime / 1e7, "kernel_s": acc.TotalKernelTime / 1e7,
                "processes": int(acc.TotalProcesses)}

    def peak_memory_bytes(self) -> Optional[int]:
        if not self.active:
            return None
        info = _ExtendedLimit()
        ok = self._k32.QueryInformationJobObject(self._handle, _INFO_EXTENDED_LIMIT, ctypes.byref(info),
                                                              ctypes.sizeof(info), None)
        return int(info.PeakJobMemoryUsed) if ok else None


def python_info() -> dict:
    return {"python": sys.version.split()[0], "executable": sys.executable}


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(text: str, limit: int = 60) -> str:
    return _SAFE_NAME.sub("_", text)[:limit] or "x"


class _MemStatus(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD), ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]


def memory_headroom() -> Optional[dict]:
    """Free physical memory and free commit (what processes can still allocate before Windows refuses), in bytes."""
    if not IS_WINDOWS:
        return None
    st = _MemStatus()
    st.dwLength = ctypes.sizeof(st)
    if not ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(st)):
        return None
    return {"free_physical": int(st.ullAvailPhys), "free_commit": int(st.ullAvailPageFile), "load_percent": int(st.dwMemoryLoad)}


def free_vram_mib() -> Optional[int]:
    """Free GPU memory of the first NVIDIA GPU in MiB, or None when nvidia-smi is not there."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    r = run_text([exe, "--query-gpu=memory.free", "--format=csv,noheader,nounits"], timeout=20)
    try:
        return int(r.stdout.strip().splitlines()[0])
    except (ValueError, IndexError):
        return None
