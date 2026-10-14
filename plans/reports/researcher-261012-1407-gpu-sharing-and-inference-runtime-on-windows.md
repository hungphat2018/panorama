---
title: "GPU sharing and inference runtime on Windows: where can the outpainting generator live?"
date: 2026-10-12
type: research
sources: ~50 + local probes
---

# GPU sharing + inference runtime on Windows (RTX laptop, frame-exact presenter)

Environment: 2026-10-12, Win11 26200, RTX 5070 Ti Laptop 12 GB (driver 617.42, WDDM 3.2, HAGS on), AMD 610M iGPU. Research only: the probes below are read-only queries (DXGI, D3D12, Vulkan, dxdiag); nothing was run on the GPU.
Tags: **[V]** primary page/source read this pass · **[S]** search snippet/secondhand · **[R]** recalled, not verified · **[E]** my arithmetic/inference · **[L]** measured/queried on this PC (2026-10-12 unless dated).

Terms (each once, with an example):
- **WDDM**: Windows' GPU driver model; its scheduler hands GPU time to each process's queue.
- **Time slice**: when two processes both have GPU work, the GPU alternates in turns (~2 ms each) and pays a switch cost between turns.
- **Preemption / granularity**: stopping a running GPU job so another can run / the smallest unit it can stop at (whole dispatch, a pixel).
- **Dispatch**: one kernel launch (one compute call); one diffusion step = hundreds of dispatches.
- **HAGS**: hardware-accelerated GPU scheduling; the GPU, not the CPU, does the turn-taking.
- **CiG (CUDA in Graphics)**: a CUDA context created inside a D3D12/Vulkan queue so compute and graphics share one GPU context and run together instead of taking turns.
- **TDR**: Windows resets a GPU that does not answer for 2 s.
- **Render-ahead depth**: finished frames waiting in the swapchain queue (`SetMaximumFrameLatency`); each extra one absorbs one more refresh of stall and costs one refresh of latency.
- **EP**: ONNX Runtime "execution provider", the plug-in that runs a model on one backend. **AOT/JIT**: build the engine ahead of time / on the user's PC at first run.
- **NT handle / fence**: a Windows handle that lets another process open the same GPU texture / a GPU-side counter ("slot 17 written") a reader can check without blocking.

## 1. Executive summary

Decision: where the generator lives. Ranked, with the protecting mechanism and its evidence level:
1. **Ahead of time (rank 1)**: separate worker makes per-shot plates, disk cache; performer only samples/blends a plate when it is ready and never waits. Protection = no GPU overlap while the presenter runs [E]; the in-playback alternatives are all weaker [L][V].
2. **Tiny per-frame CNN inside the performer's own passes (rank 1 for that class)**: same in-order command stream, cost is one more pass, amortised per film frame (24/s) not per refresh (240/s). Protection = by construction [V libplacebo hook API][E].
3. **Separate process + real-time shared-texture ring (rank 3, best-effort)**: no hard protection on GeForce/Windows (no MPS [V], green contexts make no cross-process claim [V], stream priority is a hint [V], Vulkan global priority LOW/MEDIUM only [L], compute preempts at DISPATCH granularity [L], ~2 ms time slice [V]). Measured safe: calls ≤ ~20 ms (batch ≤ 8) [L 10-11]. Ship only after E1.
4. **Diffusion inside the loop via CiG (rank 4, park)**: the one vendor path to true concurrency [V], but D3D12/Vulkan only and the performer is D3D11; untested.
5. Add regardless: render-ahead depth 2-3 (spike uses 1 [L]); presenter GPU priority (calls accepted without admin, effect unproven [L]); stable P-state via NVAPI "Power management mode" [V].
6. Budget: 240 Hz → T 4.17 ms, presenter ≈ 1.14 ms → ~3 ms of blocking allowed = one 2 ms slice + 0.145 ms switch (71%); two in a row break it. 100 Hz → ~9 ms [E].
7. Load shape [L]: per-shot work 0.25/s, per-drawing work 6.3/s (burst every ~160 ms). A 100 ms burst = 62% GPU + presenter 23% → ~85%: per-drawing diffusion in real time is out; per-drawing must be tiny [E].
8. **Runtime**: ORT 1.31 **C API** + NvTensorRtRtx **plugin EP** (TensorRT-RTX 1.6.1, <200 MB, AOT+JIT <30 s) for C-callable inference; PyTorch 2.14.1 / TensorRT 11.4 in the Python worker for diffusion. TRT-RTX and TensorRT document C++/Python only (no C API) [V].
9. **FP8 verified** (docs + 113.2 TFLOPS dense measured [L]). **NVFP4**: verified on TensorRT 10.8 / RTX 5090 [V]; "FP4 GEMM" in TRT-RTX support matrix [V]; in no TRT-RTX 1.3-1.6 release note [V]; not verified on this laptop.
10. **VRAM**: ~10.1 GB free [L]; ≤ 5B at FP8 (5 GB) or NVFP4 (2.8 GB) fits next to the performer; 5B BF16 (10 GB) does not [E]. First experiment: E1 (section 5).

## 2. Invariant and lever map

**Invariant**: every refresh of every monitor finds its picture pass finished before its flip, and every film frame stays up the same number of refreshes, while the generator runs.
- Blocking budget B = T − c − margin. 240 Hz: T = 4.17 ms, c ≈ 1.14 ms (two monitors, ADR 0026) → B ≈ 3 ms. External: T = 10 ms at 100 Hz (dxdiag now 100 Hz, ADR says 96 [L]).
- What can block the presenter: another process's running slice or dispatch; clock/P-state drops; VRAM eviction; CPU submit delays.
- Load shape [L, coordinator]: ~0.25 shots/s (per-shot work), ~6.3 new drawings/s (per-drawing work) → GPU bursts every ~160 ms on average; duty = burst / 160 ms.
- Earlier sweep [L 10-11, mpv 240 Hz]: 0 dropped / 0 mistimed in every trial (mpv's own counters); delayed frames 1-10 (27 with sleep duty-cycle); mpv's vsync-jitter estimate rose ×1000 and estimated rate fell to 230-238 Hz at batch ≥ 16. These are mpv estimates, not present timestamps: the failure threshold is not established.

| # | Lever | Evidence | Verdict |
|---|---|---|---|
| L1 | Don't overlap in time (prepare, pre-roll, cache) | logic [E] | Best; rank 1 |
| L2 | Tiny nets as passes in the presenter's own stream | libplacebo `pl_hook` / own compute [V] | Best for tiny nets |
| L3 | Short dispatches + flush often | compute preempts at DISPATCH [L]; "preemption at WDDM submission boundaries" [V]; batch ≤ 8 ok [L] | Use; limit unknown → E1 |
| L4 | Render-ahead depth 2-3 | turns a hard deadline into throughput [E]; spike uses 1 [L] | Add; pacing owner must check slide-mode row times |
| L5 | Laptop panel to 120 Hz | doubles B [E] | Last resort (user value) |
| L6 | Presenter priority up | D3DKMT HIGH/REALTIME, `SetGPUThreadPriority` +7, D3D12 HIGH queue all accepted non-admin; REALTIME queue denied 0x887A002B [L] | Effect unproven → E1 |
| L7 | Generator priority down | D3DKMT IDLE did nothing [L 10-11]; Vulkan global LOW exists [L]; CUDA stream priority hint [V] | Weak; cheap |
| L8 | Cap generator share | green contexts partition SMs inside one app, no cross-process claim [V]; `VK_NV_compute_occupancy_priority` priority + throttling [V][L] | Vulkan/NVIDIA only; cross-process unverified |
| L9 | Same-context concurrency (CiG, D3D12 async compute) | [V] | Rank 4 |
| L10 | Different silicon | iGPU for strict monitors in Setup B [E]; NVDEC/NVENC and an optical-flow queue exist [L] | Niche |
| L11 | Stable clocks | `PREFERRED_PSTATE_ID` 0x1057EB71: ADAPTIVE / PREFER_MAX / PREFER_CONSISTENT [V]; CUDA → P2 memory clock [S]; 120 W cap [L] | Test via E2 |
| L12 | VRAM guard | `SetVideoMemoryReservation` [V]; sysmem fallback slows silently [S] | Add |
| L13 | Make generator output cheap to use | low-res plate + warp/blend in surround pass (ADR 0026 pass 2) [E] | Design rule |
| L14 | Feedback throttle | performer watches its own present slack, pauses worker [E] | Safety net |
| L15 | "Run in the gap after each present" | no evidence; sleep duty-cycle was worse (27 delayed, n=1) [L 10-11] | Skip |
| L16 | Patch libplacebo/FFmpeg | not needed: `pl_d3d11_create` adopts our device, `pl_vulkan_import` adopts our VkDevice [V] | None |
| L17 | MPS / MIG / TCC | MPS Linux/QNX only [V prior pass]; TRT-RTX: TCC unsupported [V]; GeForce no MIG [S] | n/a |

## 3. Findings per question

### 3.1 Q1 GPU time-sharing

**How Windows shares the GPU**
- Since WDDM 1.2 the OS forbids drivers disabling DMA-packet preemption and sends preempt requests before TDR; finer mid-buffer granularity = better experience; the driver reports its granularity [V MS Learn, upd. 2025-07-18].
- HAGS moves quanta and context switching to a GPU scheduler; "Windows continues to control prioritization" [V MS blog 2020-06-30]. NVIDIA: CUDA scheduling features need HAGS (Win11 default) [V NVIGI].
- This PC [L dxdiag]: WDDM 3.2; NVIDIA HAGS `Enabled:True`, AMD `AlwaysOff`.
- This PC [L `IDXGIAdapter2::GetDesc2`, enums checked in SDK 10.0.26100 headers]: NVIDIA graphics **PIXEL**, compute **DISPATCH**; AMD 610M graphics PRIMITIVE, compute **DMA_BUFFER** (coarsest). Reading [E]: a running compute dispatch is atomic to Windows, so worst blocking ≥ longest dispatch + switch; the iGPU is far worse.
- NVIDIA on Windows [V GTC 2019 S9957, pdftotext]: compute preemption works between processes (Pascal+, Win10 RS4+); "preemption occurs at internal WDDM submission boundaries"; "hard to purposely take advantage of"; batching submissions is the performance advice.

**Measured time-slicing and priorities**
- RTX 3090 (OS not stated): fixed ~2 ms round-robin slice, ~145 µs average switch gap, slice and process priority not configurable; stream priorities never interrupt running thread blocks; MPS has no prioritisation [V arXiv 2110.00459 via digest; numbers not re-checked against the PDF].
- CUDA docs: stream priorities "will not preempt already executing work" [V].
- Process class `D3DKMTSetProcessSchedulingPriorityClass`: the Learn page (108 words) has no privilege or effect text [V]. Non-elevated, HIGH and REALTIME return NTSTATUS 0 [L]; accepted ≠ effective (IDLE was accepted and changed nothing [L 10-11]).
- OBS sets HIGH if HAGS else REALTIME, plus `SetGPUThreadPriority`; since 2019 "OBS would be deprioritized by Windows over fullscreen games, causing OBS to lag"; it logs "failed (not admin?)" [V source + commits 2019-10-07, 2023-05-18]. Our non-admin success suggests that message is stale or the kernel clamps silently [E].
- `IDXGIDevice::SetGPUThreadPriority`: relative −7..+7; absolute 0-31 (16-29 soft realtime "periodically yields", 30 hard realtime "does not yield") [V, upd. 2025-05-21]. Non-admin [L]: +7 and −7 accepted and read back; absolute 16 accepted but readback unchanged; absolute 30 refused 0x8876086A.
- D3D12 queue priority [L]: `CheckFeatureSupport` says NVIDIA supports NORMAL/HIGH/GLOBAL_REALTIME on DIRECT/COMPUTE/COPY (AMD: no REALTIME on DIRECT). Non-admin: HIGH queues created, GLOBAL_REALTIME → `DXGI_ERROR_ACCESS_DENIED`. Doc: realtime "won't silently downgrade", it is "guaranteed to execute before any other queue" or fails [V]. TRT-RTX's CiG recipe uses a HIGH compute queue [V].

**Vulkan on NVIDIA/Windows** [L]
- 1.4.351; `VK_KHR/EXT_global_priority`, `globalPriorityQuery`; every queue family offers only **LOW, MEDIUM** (AMD async-compute family also HIGH). We can demote a Vulkan generator, not promote the presenter.
- Also exposed: `VK_NV_compute_occupancy_priority` (priority 0..1 default 0.5; **throttling 0..1**, "higher = more restrictive occupancy limits"; spec: relative to "other simultaneously running workloads", cross-process effect not stated [V]), `VK_NV_external_compute_queue`, `VK_KHR_internally_synchronized_queues`, a compute-only family with 8 queues, an optical-flow family.

**CUDA green contexts**: SM / work-queue partition of one device; the page contrasts them with MPS ("MPS targets different processes, while green contexts is applicable within a single process too"); Windows not mentioned; concurrency "not guaranteed"; CUDA graphs cannot pick a context per kernel node [V]. Nothing says they remove time-slicing between processes or help a D3D11 presenter [E].

**CiG, the vendor answer to games + AI**
- TRT-RTX: CUDA ≥ 12.6, driver ≥ 555; context from `cuCtxCreate_v4` with `CUctxCigParam{D3D12 queue}`; HIGH compute queue for light graphics, NORMAL for heavy; shared-memory cap 48 KiB (84 KiB on Blackwell only with HIGH) → "generally reduced performance"; Turing excluded; WDDM only; Vulkan "planned" [V].
- NVIGI 1.6.0: D3D async compute for D3D plugins, CiG for CUDA; modes `kPrioritizeGraphics / kPrioritizeCompute / kBalance` are hints; driver ≥ 575 (CUDA+D3D), 580.88 (CUDA+Vulkan); one CiG context per process, mixing with compute-only contexts "can cause CUDA API errors and crashes"; AI pipelines run async to frames with budgets of "hundreds of milliseconds"; no frame-time numbers published; advice = toggle the feature, compare total frame time [V].
- Vulkan CiG exists below TRT-RTX: `CU_DEVICE_ATTRIBUTE_VULKAN_CIG_SUPPORTED`, blob from `vkGetExternalComputeQueueDataNV`, and ORT's EP has the code path [V].
- D3D11 presenter + CiG: undocumented → E3.

**Our own sweep** [L 10-11, YOLOv8s ORT-CUDA]: batch ≤ 8 (≤ 20 ms/call) no jitter at 79% GPU; batch 16/64 jitter ×1000; idle class no effect; sleep duty-cycle worse. Mechanism unknown (dispatch length, submission size, slice contention, clocks); dispatch granularity alone does not explain 8 vs 16 [E].

**Strategies, with evidence**
- Short kernels: [L] DISPATCH preempt, [V] GTC, [L] own sweep → use, find the limit (E1).
- Run in the gap after each present: no evidence; the one data point is negative [L].
- Cap generator share: Vulkan throttling [V] (NVIDIA-only), green contexts [V] (partition one app's SMs; no cross-process claim); nothing documented that caps a CUDA process from outside.
- Lower presenter refresh: B ≈ 3 ms (240 Hz) vs ≈ 7 ms (120 Hz) vs ≈ 9 ms (100 Hz) [E].

### 3.2 Q2 Inference runtimes (Windows, RTX; later AMD/Intel)

**TensorRT-RTX 1.6.1** (GitHub 2026-07-28, docs upd. 2026-07-27)
- "<200 MB", AOT + JIT "typically under 30 seconds total", Turing → Blackwell 12.x, Windows + Linux, WDDM only [V].
- API = C++, Python, "native runtime API"; **no C API** [V].
- FP8 convs/GEMMs yes (alignment limits, no FP8 deconv); "FP4 GEMM" is a precision filter in the support matrix, but FP4/NVFP4 appear in no 1.3-1.6 release note [V].
- Diffusion fusion speedups (1.6), SDXL UNet fix (1.5); engines not forward-compatible; deprecated APIs live 7-12 months [V].

**ORT NvTensorRtRtx EP**
- Built-in EP **deprecated**; use the plugin ABI `onnxruntime_providers_nv_tensorrt_rtx.dll` (Apache-2.0, v0.4.2 2026-09-22, prebuilt `…-cu13.zip` 99 MB; ORT docs still say "build from source" = stale) [V].
- EP 0.3 ↔ ORT ≥ 1.25 ↔ TRT-RTX 1.5.x, "CIG Interop" [V].
- Session keys: `enable_cuda_graph` (default on), `nv_runtime_cache_path`, `user_compute_stream`, dynamic-shape profiles; the page is silent on FP8/FP4 and IOBinding/D3D12 [V].

**ORT 1.31.0** (2026-10-09, ~monthly cadence)
- Pure C API: `RegisterExecutionProviderLibrary`, `GetEpDevices`, `SessionOptionsAppendExecutionProvider_V2`, `OrtInteropApi` (1.24), `OrtGraphicsInteropConfig` D3D12 / Vulkan (1.25; **no D3D11**) [V header].
- CUDA EP also ships as a plugin (`plugin-ep-cuda` v0.3.0 tag) [V].

**TensorRT 11.4** (docs upd. 2026-10-05; the "10.x" in the brief is superseded)
- 11.0: strongly typed by default; implicit quantization, weak typing, static libs removed; Windows 10 support ends Oct 2026. C++/Python only, no C API mentioned; FP4 appears in 11.0 notes ("FP4 double quantization … not refittable") [V].
- TensorRT 10.8 ran FLUX FP4 on RTX 5090: FC layers up to 3.1× FP8; schnell 4 steps 590 ms FP4 vs 913 FP8 vs 4427 FP16 (the FP16 row used low-VRAM mode) [V NVIDIA blog 2025-05-14]. Per-GPU engine build time not verified [R: minutes].

**Windows ML** (docs upd. 2026-09 / 10)
- Microsoft's maintained copy of ORT, "same ONNX APIs"; EPs downloaded via `ExecutionProviderCatalog` on Win11 24H2+; lines 1.8.x and 2.x [V].
- EPs: NvTensorRtRtx (RTX 30+, driver 32.0.15.5585, CUDA 12.5), MIGraphX (AMD RDNA3+, no GenAI), OpenVINO (Intel), QNN, VitisAI (NPU), WebGPU (**experimental**); DirectML "legacy", included [V].
- C use = WinRT activation for the catalog, ORT C API after; the plugin-DLL route (`RegisterExecutionProviderLibrary`) avoids WinRT [E].

**Other runtimes**
- DirectML EP: "sustained engineering", opset ≤ 20; takes the app's `ID3D12CommandQueue` (DIRECT/COMPUTE, same parent device) [V]. The one EP built around the app's own D3D12 queue; no FP8/FP4 [R].
- WebGPU EP (plugin; Dawn → D3D12/Vulkan; wraps an existing WebGPU device; "cross-vendor"): the AMD/Intel path; no numbers, op/precision coverage unlisted [V].
- PyTorch 2.14.1+cu130 (2026-09-30): sm_120 ok [V prior pass]; dense matmul 52.8 TFLOPS BF16, 113.2 FP8 on this laptop [L coordinator]. `torch.compile` with CUDA on Windows needs `triton-windows` (moved to the triton-lang org; v3.8.0-windows.post29, 2026-09-28; "torch.compile just work" is the fork's own claim) [V].
- Hybrid laptop: CUDA/TensorRT enumerate NVIDIA only; DirectML/WebGPU can pick the iGPU, so pin by LUID [V prior pass].

### 3.3 Q3 Zero-copy interop and a cross-process texture ring

**Paths**
- Avoid legacy `cudaGraphicsD3D11RegisterResource` / Map / Unmap in a real-time loop: NVIDIA lists hidden reallocation, local copies and "extra heavy synchronization" and recommends explicit external memory + semaphores [V GTC 2019]. It takes an in-process COM pointer, so it cannot cross processes [E]; its own doc page was not fetched.
- Modern CUDA path [V runtime API docs]: external memory `D3D11Resource` (NT handle from `IDXGIResource1::CreateSharedHandle`, dedicated flag required), D3D12 Resource/Heap, OpaqueWin32 (Vulkan); semaphores `D3D11Fence`, `D3D12Fence`, `KeyedMutex`, `TimelineSemaphoreWin32`; fence = signal sets a value, wait until ≥ value. NT handles hold a reference; the page never says "cross-process".
- D3D11 side [V local SDK headers]: `D3D11_RESOURCE_MISC_SHARED_NTHANDLE` (0x800) / `SHARED_KEYEDMUTEX` (0x100); `ID3D11Device1::OpenSharedResource1`; `ID3D11Device5::CreateFence` with `D3D11_FENCE_FLAG_SHARED` (+ `SHARED_CROSS_ADAPTER`), `OpenSharedFence`; `ID3D11DeviceContext4::Signal / Wait`. `CreateFence` is "equivalent to D3D12 … in interop scenarios" [V Learn].

**How libplacebo imports** (GitHub mirror master, read 2026-10-12) [V]
- `pl_d3d11_wrap` wraps a same-device `D3D11_USAGE_DEFAULT` texture (no mips / MSAA). The D3D11 backend has **no** import/export handle support, so open the shared texture yourself first. `pl_d3d11_create` adopts our `ID3D11Device`.
- Vulkan: `pl_vulkan_import` adopts our VkDevice and queues; `pl_vulkan_wrap` wraps a VkImage; the backend imports/exports `PL_HANDLE_WIN32/KMT` memory; `VK_KHR_internally_synchronized_queues` honoured.
- ADR 0026 pass 2 is our own code, so a plate can simply be an SRV there [E].

**ORT interop** (NvTensorRtRtx EP source) [V]: imports D3D12 resource/heap and Vulkan opaque Win32 memory, D3D12 fence and Vulkan timeline semaphore; **no D3D11 handle types**. Issue #26821 (D3D12 interop proposal) is open, last comment 2026-01-27, but the code is in main.

**Ring protocol (recommended)** [E]
- Performer creates N = 3 shared textures (small, e.g. 1280×720) + one shared fence; generator imports once, writes slot k, signals fence = frame id.
- Performer **never waits**: each refresh reads the highest completed id (`GetCompletedValue()`), else keeps the previous plate. A "released" counter in shared memory tells the generator which slot is still being read.
- Generator crash, hang or TDR = stale periphery, never a late present. Keyed mutex only with zero timeout.
- Cost: import once (ms); per update one fence signal + one CPU counter read; plate copy is microseconds at these sizes.
- Cross-adapter (Setup B iGPU monitor): fence flag exists [V]; cross-adapter texture sharing not verified → plan a host-memory copy of the small plate.

### 3.4 Q4 VRAM
- This PC [L]: 11,944 MiB dedicated (DXGI); fresh Vulkan process budget 10.91 of 11.66 GiB; coordinator: ~10.1 GB free with the desktop up. Performer spike peak 106-239 MB (D3D11 / D3D11VA), Vulkan Video 748 MB [L repo report]; ADR 0026 canvases + swapchains + prepared frames + decoder ≈ 0.4-0.8 GB [E, unmeasured].
- Generator allowance ≈ 10.1 − 0.8 (performer) − 1.0 (margin so WDDM never demotes the presenter) ≈ **8 GB**, including the CUDA context (~0.3-0.5 GB [R]).
- Weights only [E]; NVFP4 = 4.5 bit/value (16-value E4M3-scaled blocks + FP32 tensor scale) [V NVIDIA blog 2025-06-24]:

| params | BF16 | FP8 / INT8 | NVFP4 |
|---|---|---|---|
| 1B | 2.0 GB | 1.0 | 0.56 |
| 2B | 4.0 | 2.0 | 1.13 |
| 5B | 10.0 | 5.0 | 2.81 |

- Activations + VAE decode + workspace at ~1 MP: ~1.5-3 GB for 1-5B DiTs [E, wide]. Anchor [V]: FLUX.1-dev 12B on RTX 5090, GB default / low-VRAM: FP16 39.3 / 23.9, FP8 24.6 / 14.9, **FP4 21.67 / 11.1**. A 12B model does not fit next to anything on 12 GB. 5B FP8 + 3 GB ≈ 8 GB is tight; 5B NVFP4 ≈ 6 GB is comfortable [E].
- Text/image encoders are per-shot constants: run once per shot, then release [E].
- Guards: `SetVideoMemoryReservation` for the performer ("physical memory they cannot go without") [V]. Driver ≥ 536.40 can spill CUDA allocations to system RAM (silent slowdown); "Prefer No Sysmem Fallback" exists [S, third-party reposts]. Cap arenas.

### 3.5 Q5 Small CNN inside the render pass

**libplacebo hooks** [V headers]
- `pl_mpv_user_shader_parse(gpu, text, len)` → `pl_hook`; users may write their own `pl_hook` (callback gets `pl_dispatch`, `get_tex`); stages `LUMA_INPUT` / `NATIVE` / `LINEAR` / `PRE_KERNEL` … `OUTPUT`.
- `pl_custom_shader`: `prelude` (for `#extension`), `compute`, `compute_group_size`, `compute_shmem`. D3D11 backend: compute allowed unless `no_compute`.
- ADR 0026 already prepares a linear-light picture once per film frame, so a CNN hook there costs 24×/s [E].

**Cost of the nets**
- Anime4K v3.2 x2 (MIT; parsed shader files): MACs per input pixel S 972, M 2,060, L 4,248, VL 8,472, UL 17,316 → 4.0 / 8.5 / 17.6 / 35.1 / 71.8 GFLOP per 1080p input frame; 5-25 fragment passes, M has no `//!COMPUTE` [V + E]. README gives no ms (Vega 64 only) [V].
- ArtCNN (MIT): C4F16 ~12k params, C4F32 ~48k, R8F64 ~926k, R16F96 ~4M; C-series ship as GLSL + ONNX, R-series ONNX only [V]. ≈ 12k / 48k MAC/px → ~50 / ~200 GFLOP per 1080p frame [E].
- GLSL vs TensorRT: no published ms for either on RTX [V search]. Estimate [E, ±3×]: FP32 peak ≈ 25-35 TFLOPS [R, 46 SMs unverified] at 15-30% pass efficiency → Anime4K M ≈ 1-2 ms, UL ≈ 8-20 ms, plus vec4 texture traffic. TRT-RTX fuses layers and can use tensor cores (52.8 TFLOPS dense BF16 [L]) but 16-channel convs under-fill them. Expect ≤ 3-5× gain, minus interop copy + fence → E4.

**Neural shading APIs**
- D3D12 SM 6.9 retail (Agility 1.619, 2026-02-26) has Long Vector (≤ 1024 elements). **Cooperative Vector "deprecated in favor of a future design unifying matrix-matrix and vector-matrix operations … SM 6.10"** [V MS blog].
- Vulkan on this driver [L]: `VK_KHR_cooperative_matrix`, `VK_NV_cooperative_matrix2`, `VK_NV_cooperative_vector`; FP8 and BF16 cooperative-matrix features true; FP4 not seen in the printed feature structs (property tables not printed). D3D11 has none [E].
- A libplacebo Vulkan hook could put `#extension` in `prelude`; whether shaderc/glslang accepts cooperative-matrix GLSL there is untested [E].
- Verdict [E]: nets ≤ ~2k MAC/px (Anime4K S/M) → in-shader; larger → worker + TRT-RTX; cooperative matrix = later Vulkan-only spike.

## 4. Comparison tables

### 4.1 Where the generator lives (trade-offs)

| Option | Presenter guarantee | Latency to first result | Engineering | Maintenance | Evidence | Rank |
|---|---|---|---|---|---|---|
| Ahead of time + cache | Hard (no overlap) | prepare step; progressive fallback = plain surround | Low (ADR 0024 worker pattern) | Low | [E]+[L][V] on limits | **1** |
| In-stream tiny passes | By construction | none | Medium (pass budget scheduler) | Medium | [V][E] | **1 (tiny nets)** |
| Separate process, real-time ring | Best-effort | seconds | Medium-high (ring, throttle, E1 gate) | Medium | [L][V] soft | 3 |
| In-loop diffusion via CiG | True concurrency, ~unmeasured | ~real time | High (D3D12/Vulkan presenter, one CiG context) | High | [V] docs, no numbers | 4 |

### 4.2 Sharing mechanisms (presenter = D3D11, generator = CUDA unless noted)

| Mechanism | Scope | This PC | Evidence | Protects presenter? | Verdict |
|---|---|---|---|---|---|
| No overlap (offline / pre-roll) | time | n/a | [E] | Hard | **1** |
| In-stream tiny passes | same queue | yes | [V][E] | By construction | **1 (tiny)** |
| Dispatch ≤ d + flush | process | DISPATCH preempt [L] | [L][V] | Bounds wait to d + slice | Use |
| Render-ahead depth 2-3 | presenter | trivial | [E] | Absorbs (n−1)·T | Add |
| D3DKMT class / `SetGPUThreadPriority` | process / device | accepted [L] | effect unproven | Maybe | E1 |
| D3D12 HIGH queue | queue | ok non-admin [L] | [V][L] | Only if presenter is D3D12 | Later |
| D3D12 GLOBAL_REALTIME | queue | needs admin [L] | [V][L] | Strong, admin | No |
| Vulkan global priority | process | LOW/MEDIUM only [L] | [L] | Demote generator only | Weak |
| `VK_NV_compute_occupancy_priority` | cmd buffer | exposed [L] | [V] | Unknown cross-process | Spike |
| CUDA stream priority | context | yes | [V] | No (hint) | No |
| Green contexts | one app's SMs | Windows unstated | [V] | No cross-process claim | No |
| MPS / MIG | n/a | no | [V][S] | n/a | n/a |
| CiG (D3D12 or Vulkan) | same HW context | needs D3D12/Vulkan presenter | [V] | True concurrency | Rank 4 |
| 120 Hz laptop panel | display | user choice | [E] | Doubles B | Last resort |

### 4.3 Runtimes

| Runtime | C API | FP8 | NVFP4 | Build / JIT | Windows, RTX 50 laptop | Cross-vendor | Adoption risk |
|---|---|---|---|---|---|---|---|
| ORT 1.31 + NvTensorRtRtx plugin EP | **Yes (ORT C)** | via TRT-RTX | unverified | AOT + JIT < 30 s | yes; prebuilt zip [V] | NVIDIA only | Plugin ABI young (v0.4.x); pin ORT + EP + TRT-RTX together |
| TensorRT-RTX 1.6.1 native | No (C++/Py) | yes | "FP4 GEMM" listed | < 30 s | yes | NVIDIA | ~6 minors in ~12 mo [E]; engines not forward-compatible |
| TensorRT 11.4 | No (C++/Py) | yes | yes (10.8 FLUX FP4; 11.0 FP4 notes) | per-GPU build [R: minutes] | yes (Win10 ends Oct 2026) | NVIDIA | 10 → 11 migration (strong typing) |
| ORT CUDA EP (wheel / plugin) | Yes | n/v | n/v | none | Blackwell ok since 1.27 [V prior] | NVIDIA | monthly ORT |
| Windows ML 2.x | WinRT, then ORT C | via EP | via EP | JIT | 24H2+ for IHV EPs | Yes (EP catalog) | EP via Windows Update; WebGPU EP experimental |
| DirectML EP | via ORT C | no [R] | no | none | any D3D12 | Any DX12 | sustained engineering, opset ≤ 20 |
| WebGPU EP (plugin) | via ORT C | ? | ? | none | any DX12 | Yes | experimental in Windows ML, no numbers |
| PyTorch 2.14.1 | libtorch C++ | 113 TFLOPS [L] | unverified | `torch.compile` needs triton-windows | yes | CUDA / ROCm | fork dependency for compile |

### 4.4 Interop paths

| Path | Cross-process | Sync | Verified | libplacebo / performer side |
|---|---|---|---|---|
| CUDA legacy D3D11 register / map | no (COM pointer) [E] | implicit | "hidden operations" [V] | avoid |
| CUDA external memory D3D11 NT handle + D3D11 fence | yes | explicit, GPU-side | [V] | `OpenSharedResource1` → SRV or `pl_d3d11_wrap` |
| D3D12 shared resource/heap + D3D12 fence (ORT interop) | yes | explicit | [V source] | D3D11 ↔ D3D12 via NT handle [R] |
| Vulkan opaque Win32 memory + timeline semaphore | yes | explicit | exts on both GPUs [L]; ORT path [V] | `pl_vulkan_wrap` / `PL_HANDLE_WIN32` [V] |
| Cross-adapter (iGPU monitor) | yes | fence flag exists [V] | texture sharing unverified | host copy of small plate [E] |

## 5. What to try first on this laptop (cheap, decision-changing)

- **E1 (≈1 h, decides rank 3)**: present misses vs generator chunk size.
  - Presenter = P1 two-window spike (240 + 100 Hz) with its own per-present log (not mpv jitter).
  - Load = separate process, GEMM/spin kernel in chunks d ∈ {0.25, 0.5, 1, 2, 4, 8, 16} ms, `synchronize` after each, saturating and at 50% duty.
  - Matrix: MaxFrameLatency {1, 3} × presenter priority {default, `SetGPUThreadPriority(+7)` + D3DKMT HIGH}; 5 min per cell; measure missed refreshes and p99.9 present-interval error.
  - Pass: 0 misses at d ≥ 2 ms with depth 3 → rank 3 viable. Fail at d ≤ 0.5 ms → drop rank 3.
- **E2 (free, during E1)**: `nvidia-smi --query-gpu=pstate,clocks.sm,clocks.mem,power.draw,clocks_throttle_reasons.active --format=csv -lms 100`. Misses that line up with clock / P-state / power events mean the fix is NVAPI `PREFER_MAX` / `PREFER_CONSISTENT`, not scheduling.
- **E3 (≈2 h)**: CiG under a D3D11 presenter. Create an `ID3D12Device` + HIGH compute queue on the same LUID in the performer process, `cuCtxCreate_v4` with `CUctxCigParam`, run E1's load there; compare misses with E1's plain separate process. Misses gone → rank 4 needs no D3D12 renderer; otherwise CiG requires porting the presenter.
- **E4 (≈15 min)**: tiny-CNN pass cost. mpv `--vo=gpu-next` with Anime4K M/UL and ArtCNN C4F16/C4F32 GLSL on the 1080p episode, per-pass times from mpv's stats page; same ONNX (ArtCNN C-series) through ORT + NvTensorRtRtx. Decides shader vs worker for tiny nets and the 24 fps budget.
- **E5 (optional)**: Vulkan generator with global LOW + `occupancyThrottling` vs the presenter, only if E1 shows a soft-failure zone.
- Not worth doing now: NVFP4 throughput microbench (model agent's job), green-context tests (cannot protect another process).

## 6. Sources (fetched 2026-10-12 unless noted)

Microsoft: [GPU preemption](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/gpu-preemption) V upd 2025-07-18 · [HAGS blog](https://devblogs.microsoft.com/directx/hardware-accelerated-gpu-scheduling/) V 2020-06-30 · [SetGPUThreadPriority](https://learn.microsoft.com/en-us/windows/win32/api/dxgi/nf-dxgi-idxgidevice-setgputhreadpriority) V 2025-05-21 · [D3D12_COMMAND_QUEUE_PRIORITY](https://learn.microsoft.com/en-us/windows/win32/api/d3d12/ne-d3d12-d3d12_command_queue_priority) V · [D3DKMTSetProcessSchedulingPriorityClass](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/d3dkmthk/nf-d3dkmthk-d3dkmtsetprocessschedulingpriorityclass) V · [SetVideoMemoryReservation](https://learn.microsoft.com/en-us/windows/win32/api/dxgi1_4/nf-dxgi1_4-idxgiadapter3-setvideomemoryreservation) V · [ID3D11Device5::CreateFence](https://learn.microsoft.com/en-us/windows/win32/api/d3d11_4/nf-d3d11_4-id3d11device5-createfence) V · [SM 6.9 retail](https://devblogs.microsoft.com/directx/shader-model-6-9-retail-and-more/) V 2026-02-26 · [Windows ML overview](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/overview) V upd 2026-09-08 · [Windows ML EPs](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers) V upd 2026-10-07.
NVIDIA: [CUDA PG async / stream priorities](https://docs.nvidia.com/cuda/cuda-programming-guide/02-basics/asynchronous-execution.html) V · [green contexts](https://docs.nvidia.com/cuda/cuda-programming-guide/04-special-topics/green-contexts.html) V · [CUDA ext. resource interop](https://docs.nvidia.com/cuda/cuda-runtime-api/group__CUDART__EXTRES__INTEROP.html) V · [CUctxCigParam](https://docs.nvidia.com/cuda/archive/13.3.0/cuda-driver-api/structCUctxCigParam.html) V · [TRT-RTX 1.6 docs](https://docs.nvidia.com/deeplearning/tensorrt-rtx/latest/index.html) V upd 2026-07-27 · [TRT-RTX simultaneous compute + graphics](https://docs.nvidia.com/deeplearning/tensorrt-rtx/latest/inference-library/compute-graphics.html) V · TRT-RTX release notes [1.3](https://docs.nvidia.com/deeplearning/tensorrt-rtx/latest/getting-started/release-notes-1/1.3.html) [1.4](https://docs.nvidia.com/deeplearning/tensorrt-rtx/latest/getting-started/release-notes-1/1.4.html) [1.5](https://docs.nvidia.com/deeplearning/tensorrt-rtx/latest/getting-started/release-notes-1/1.5.html) [1.6](https://docs.nvidia.com/deeplearning/tensorrt-rtx/latest/getting-started/release-notes-1/1.6.html) V · [support matrix](https://docs.nvidia.com/deeplearning/tensorrt-rtx/latest/getting-started/support-matrix.html) V (filters only) · [NVIGI GPU Scheduling for AI 1.6.0](https://docs.nvidia.com/nvigi-sdk/1.6.0/docs/nvigi_core/docs/GpuSchedulingForAI.html) V · [TensorRT 11.0.0 notes](https://docs.nvidia.com/deeplearning/tensorrt/latest/getting-started/release-notes-11/11.0.0.html) V upd 2026-10-05 · [FLUX FP4 blog](https://developer.nvidia.com/blog/nvidia-tensorrt-unlocks-fp4-image-generation-for-nvidia-blackwell-geforce-rtx-50-series-gpus) V 2025-05-14 · [NVFP4 blog](https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/) V 2025-06-24 · [GTC 2019 S9957 CUDA on Windows](https://developer.download.nvidia.com/video/gputechconf/gtc/2019/presentation/s9957-using-cuda-on-windows.pdf) V (pdftotext) · [NVAPI DRS header](https://github.com/NVIDIA/nvapi) V · [TensorRT-RTX-EP-ABI](https://github.com/NVIDIA/TensorRT-RTX-EP-ABI) V v0.4.2.
Khronos: [VK_NV_compute_occupancy_priority](https://docs.vulkan.org/refpages/latest/refpages/source/VK_NV_compute_occupancy_priority.html) V · [VkComputeOccupancyPriorityParametersNV](https://docs.vulkan.org/refpages/latest/refpages/source/VkComputeOccupancyPriorityParametersNV.html) V · [VK_NV_external_compute_queue](https://docs.vulkan.org/refpages/latest/refpages/source/VK_NV_external_compute_queue.html) V · [VK_KHR_global_priority](https://docs.vulkan.org/refpages/latest/refpages/source/VK_KHR_global_priority.html) V.
ONNX Runtime and others: [TRT-RTX EP](https://onnxruntime.ai/docs/execution-providers/TensorRTRTX-ExecutionProvider.html) · [DirectML EP](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html) · [WebGPU EP](https://onnxruntime.ai/docs/execution-providers/WebGPU-ExecutionProvider.html) · [OrtInteropApi](https://onnxruntime.ai/docs/api/c/struct_ort_interop_api.html) · [issue 26821](https://github.com/microsoft/onnxruntime/issues/26821) · ORT v1.31.0 C header + `nv_provider_factory.cc` (main) + releases API · PyTorch / triton-windows releases API · [libplacebo mirror headers + src](https://github.com/haasn/libplacebo) master · [OBS d3d11-subsystem.cpp + commits ec769ef00, 9de6b18d5](https://github.com/obsproject/obs-studio) · [Gilman et al. arXiv 2110.00459](https://arxiv.org/abs/2110.00459) via ar5iv digest · [Anime4K](https://github.com/bloc97/Anime4K) · [ArtCNN](https://github.com/Artoriuz/ArtCNN). [S]: sysmem-fallback reposts (Guru3D / Cognex), CUDA Force-P2 (SignalRGB / Adobe forums), Tegra GCAPS paper (50-750 µs switch).
Local [L]: `dxdiag /t`; `IDXGIAdapter2::GetDesc2`; `vulkaninfo`; Vulkan queue-family global-priority query; D3D12 `CheckFeatureSupport` + `CreateCommandQueue` probes; D3DKMT / `SetGPUThreadPriority` probe; `nvidia-smi`; repo `spikes/` and `plans/reports/researcher-261011-1247-film-analysis-content-model.md`. Probe scripts: session scratchpad (`dxgi-preemption-query.ps1`, `vk-global-priority-query.ps1`, `d3d12-queue-priority-query.ps1`, `d3d12-queue-priority-permission.ps1`, `gpu-priority-permission-probe.ps1`).

## 7. Unresolved questions and limitations

1. Does CiG protect or benefit a **D3D11** presenter (same process, separate D3D12 queue)? Undocumented (E3).
2. Why batch 8 safe and 16 not: dispatch length, submission size, slice contention, or clocks? Needs real present timestamps (E1 / E2).
3. Do D3DKMT HIGH/REALTIME and `SetGPUThreadPriority(+7)` change anything under HAGS-on NVIDIA? Calls succeed non-admin; effect unknown.
4. `VK_NV_compute_occupancy_priority`: does it act across processes? Spec says only "other simultaneously running workloads".
5. NVFP4 on this laptop: TRT-RTX release notes silent; PyTorch / ModelOpt NVFP4 GEMM on a sm_120 laptop untested.
6. Not verified: Windows ML GA date and C-only usability without WinRT; green-context Windows support (NVIDIA silent); TensorRT classic build times.
7. Cross-adapter texture sharing for Setup B (LG UltraFine on the iGPU) not researched; AMD 610M preempts compute only at DMA-buffer boundaries [L], so keep every generator off that GPU.
8. AMD / Intel diffusion paths (MIGraphX RDNA3+ non-GenAI, OpenVINO, WebGPU) have no numbers; probably precompute only.
9. Performer VRAM with ADR 0026 passes is estimated, not measured; sysmem-fallback behaviour only from third-party posts.
10. Product question for the coordinator: is a "prepare" step before play acceptable (e.g. ~5 min per episode at an illustrative 1 s per plate × 300 shots [E]), or must the first shots play with the plain surround while plates arrive?
11. Limits of this pass: no GPU load tests (rule); model speed / quality out of scope; WebFetch digests are small-model summaries (numbers not re-checked against PDFs); web budget exceeded (~55 WebFetch/WebSearch + ~35 API/curl pulls vs ~40) because CiG, `VK_NV_*` and the ORT interop API surfaced mid-pass and each changed a ranking.
