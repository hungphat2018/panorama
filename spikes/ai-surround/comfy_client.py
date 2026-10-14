"""Minimal client for the pinned portable ComfyUI in tools/: start a local server (127.0.0.1 only), queue an API workflow,
wait for it, read what its SaveImage nodes wrote. Shared by comfy-plates.py and comfy-vace-clips.py.

The server is started only when nothing answers on the port (never a duplicate) and is owned by the caller, which stops
it with stop_server when its run ends.
"""
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import cv2

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COMFY = os.path.join(REPO, "tools", "ComfyUI_windows_portable")
COMFY_APP = os.path.join(COMFY, "ComfyUI")
INPUT_DIR = os.path.join(COMFY_APP, "input")
OUTPUT_DIR = os.path.join(COMFY_APP, "output")
# For runs whose models together exceed this laptop's 32 GB of RAM (Qwen-Image 14 GB + its 17.5 GB text encoder; the
# 14B experts 2 x 18.7 GB + umT5 11 GB): no node output is kept, so each model leaves RAM once its consumers ran. The
# default cache kept everything (54 GB committed, system critically low on memory, 2026-10-13 00:20).
LOW_RAM = ("--cache-none",)


def ping(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats", timeout=2).read()
        return True
    except (urllib.error.URLError, OSError):
        return False


def start_server(port, log_path, extra_args=()):
    """The ComfyUI server as a child process, or None when one already answers on the port. extra_args go to main.py
    (e.g. --cache-none: each node's output is dropped once its consumers ran, so a loaded model leaves RAM after use)."""
    if ping(port):
        print(f"using the ComfyUI server already on port {port}")
        return None
    cmd = [os.path.join(COMFY, "python_embeded", "python.exe"), "-s", os.path.join(COMFY_APP, "main.py"),
           "--listen", "127.0.0.1", "--port", str(port), "--disable-auto-launch",
           "--extra-model-paths-config", os.path.join(COMFY_APP, "extra_model_paths.yaml"), *extra_args]
    proc = subprocess.Popen(cmd, cwd=COMFY_APP, stdout=open(log_path, "w", encoding="utf-8"), stderr=subprocess.STDOUT)
    for _ in range(600):
        if ping(port):
            print(f"ComfyUI server pid {proc.pid} on port {port}", flush=True)
            return proc
        if proc.poll() is not None:
            raise SystemExit(f"ComfyUI exited with {proc.returncode}; see {log_path}")
        time.sleep(1)
    proc.terminate()
    raise SystemExit("ComfyUI did not answer within 10 minutes")


def stop_server(proc):
    if proc is not None:
        proc.terminate()
        proc.wait(timeout=60)
        print(f"ComfyUI server pid {proc.pid} stopped", flush=True)


def run_graph(port, graph):
    """Queue one API workflow and wait for it; returns its outputs (per output node id)."""
    body = json.dumps({"prompt": graph, "client_id": uuid.uuid4().hex}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/prompt", data=body, headers={"Content-Type": "application/json"})
    try:
        pid = json.load(urllib.request.urlopen(req))["prompt_id"]
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"ComfyUI refused the workflow: {e.read().decode(errors='replace')[:3000]}")
    while True:
        hist = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/history/{pid}"))
        if pid in hist:
            status = hist[pid].get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(f"workflow failed: {json.dumps(status.get('messages', []))[:3000]}")
            if status.get("completed"):
                return hist[pid]["outputs"]
        time.sleep(1)


def free(port, settle_seconds=15):
    """Unload every model and drop the server's caches (POST /free); the worker does it at once when idle and collects
    garbage about 10 s later, so wait that long before the next prompt."""
    body = json.dumps({"unload_models": True, "free_memory": True}).encode()
    urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/free", data=body,
                                                  headers={"Content-Type": "application/json"})).read()
    time.sleep(settle_seconds)


def saved_images(outputs, node):
    """Every RGB picture a SaveImage node wrote, in order (alpha dropped: some VAEs decode RGBA)."""
    pics = []
    for info in outputs[node]["images"]:
        img = cv2.imread(os.path.join(OUTPUT_DIR, info.get("subfolder", ""), info["filename"]), cv2.IMREAD_UNCHANGED)
        pics.append(cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2RGB))
    return pics
