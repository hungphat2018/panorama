"""Short attention kernels for the painter (phase 3, GPU sharing): the queries of every unmasked attention call are split
into chunks of at most `max_queries` rows, each its own kernel, with the CPU waiting for the GPU between chunks (the
same one-in-flight rule as painter_core.throttle_queue). A ~1 MP plate's attention kernels otherwise run ~10-13 ms
(SDPA; ~4.6 ms with comfy_kitchen's INT8 attention), longer than a 240 Hz refresh (4.2 ms): the player's frame then
waits behind one. Rows of a query never interact, so chunking changes no value (up to kernel rounding).

Installed by replacing the name `optimized_attention` that ComfyUI's Qwen-Image-2.1 model module imported (its
block-causal and prefix-cached attention closures call it at run time); masked calls (the few text rows of the first
step) are passed through whole.
"""
from __future__ import annotations

_state = {"installed": False, "calls": 0, "chunks": 0}


def install(max_queries: int):
    """Chunk the Qwen-Image-2.1 DiT's attention calls (painter_core.init_comfy must have run). max_queries <= 0: off."""
    if max_queries <= 0 or _state["installed"]:
        return
    import torch
    import comfy.ldm.qwen_image21.model as qm
    from comfy.ldm.modules.attention import AttentionTensorContainer
    inner = qm.optimized_attention

    def chunked(q, k, v, heads, mask=None, **kwargs):
        qt = q.take() if hasattr(q, "take") else q
        n = qt.shape[1]
        if mask is not None or n <= max_queries:
            return inner(AttentionTensorContainer(qt) if hasattr(q, "take") else qt, k, v, heads, mask=mask, **kwargs)
        kt = k.take() if hasattr(k, "take") else k
        vt = v.take() if hasattr(v, "take") else v
        outs = []
        _state["calls"] += 1
        for s in range(0, n, max_queries):
            part = qt[:, s:s + max_queries]
            outs.append(inner(AttentionTensorContainer(part), AttentionTensorContainer(kt), AttentionTensorContainer(vt),
                              heads, mask=None, **kwargs))
            torch.cuda.current_stream().synchronize()
            _state["chunks"] += 1
        return torch.cat(outs, dim=1)

    qm.optimized_attention = chunked
    _state["installed"] = True


def stats() -> dict:
    return dict(_state)
