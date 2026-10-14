"""Offline check of ComfyUI API graphs with this ComfyUI's own validator: no server, no port, no GPU.

Run by ComfyUI's embedded Python (painter-speed.py --dry-run calls it):

    tools/ComfyUI_windows_portable/python_embeded/python.exe -s comfy-validate-graphs.py GRAPHS_JSON REPORT_JSON

GRAPHS_JSON maps a name to one API graph. Each graph goes through execution.validate_prompt, the check the server runs
before it queues a prompt: node classes exist, required inputs are present, every link points at an existing node and an
output of the right type, combo values (model, LoRA, VAE, text-encoder and input-image file names) are in this install's
lists (models through extra_model_paths.yaml), numbers are in range, LoadImage files exist in the input folder. ComfyUI
ignores input keys a node does not declare, so a mistyped optional input would pass silently: each key is also checked
against the node's declared inputs (dynamic ones such as images.image_1 expanded as the validator does).

The GPU is hidden (CUDA_VISIBLE_DEVICES=-1) and ComfyUI runs in --cpu mode, both set before torch loads, so nothing here
can touch the GPU. Writes {name: {"ok": bool, "errors": [text, ...]}} to REPORT_JSON; exit code 1 if any graph fails.
"""
import asyncio
import json
import os
import sys

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"                         # before torch loads: no GPU is visible to this process
GRAPHS_PATH, REPORT_PATH = sys.argv[1], sys.argv[2]
COMFY_APP = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                         "tools", "ComfyUI_windows_portable", "ComfyUI")
sys.argv = [sys.argv[0], "--cpu", "--disable-dynamic-vram"]       # what comfy.cli_args parses below
sys.path.insert(0, COMFY_APP)
os.chdir(COMFY_APP)

import comfy.options  # noqa: E402

comfy.options.enable_args_parsing()
import execution  # noqa: E402
import nodes  # noqa: E402
import server  # noqa: E402
import utils.extra_config  # noqa: E402
from app.assets.manager import default_asset_manager  # noqa: E402
from comfy_api.internal import _ComfyNodeInternal  # noqa: E402
from comfy_api.latest import _io  # noqa: E402


def declared_inputs(class_, inputs):
    """Input names the node accepts for these live inputs (dynamic inputs expanded like validate_inputs does)."""
    spec = class_.INPUT_TYPES()
    if issubclass(class_, _ComfyNodeInternal):
        spec, hidden, _ = _io.get_finalized_class_inputs(spec, inputs, include_hidden=True)
        spec = dict(spec, hidden=hidden or {})
    return {name for part in ("required", "optional", "hidden") for name in (spec.get(part) or {})}


def check(loop, name, graph):
    errors = []
    for nid, node in graph.items():
        class_ = nodes.NODE_CLASS_MAPPINGS.get(node.get("class_type"))
        if class_ is not None:
            unknown = sorted(set(node.get("inputs", {})) - declared_inputs(class_, node.get("inputs", {})))
            if unknown:
                errors.append(f"node {nid} {node['class_type']}: inputs it does not declare: {unknown}")
    ok, error, _, node_errors = loop.run_until_complete(execution.validate_prompt(name, graph, None))[:4]
    if not ok:
        errors.append(f"{error['message']}: {error['details']}")
    for nid, info in node_errors.items():
        for e in info["errors"]:
            errors.append(f"node {nid} {info['class_type']}: {e['message']}: {e['details']}")
    return {"ok": not errors, "errors": errors}


def main():
    utils.extra_config.load_extra_path_config(os.path.join(COMFY_APP, "extra_model_paths.yaml"))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    server.PromptServer(loop, default_asset_manager())             # some node modules register routes on import; never started
    loop.run_until_complete(nodes.init_extra_nodes(init_custom_nodes=False, init_api_nodes=False))
    graphs = json.load(open(GRAPHS_PATH, encoding="utf-8"))
    report = {name: check(loop, name, graph) for name, graph in graphs.items()}
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    bad = [n for n, r in report.items() if not r["ok"]]
    print(f"validated {len(report)} graphs with ComfyUI's validator (CPU, GPU hidden): {len(report) - len(bad)} ok, "
          f"{len(bad)} failed {bad[:5]}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
