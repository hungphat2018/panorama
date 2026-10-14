// Two levers for sharing the GPU with another process, for the E1 test (does the performer keep every frame on time while
// an AI model runs on the same GPU?). Both default to what perf1 always did, so a run without the options is unchanged.
//
//   --frame-latency N    render-ahead depth: IDXGISwapChain2::SetMaximumFrameLatency(N) on every monitor's swapchain (a
//                        waitable swapchain takes its latency from itself; IDXGIDevice1's value does not apply to it). Up to
//                        N presents may wait behind the one on screen, so the GPU may finish a picture up to N - 1 refreshes
//                        later than with N = 1 before a refresh is missed. N > 2 also gets N + 1 buffers (one on screen, N
//                        queued or being drawn: libplacebo's own rule for the swapchains it creates).
//                        The engine still prepares "the next refresh after now" (engine_service in perf1-engine.c): with a
//                        full queue a present reaches the screen N - 1 refreshes after the refresh it was planned for. Each
//                        monitor keeps its pace (every frame its planned number of refreshes); the seam does not, because
//                        the delay is N - 1 refreshes of each monitor's own rate.
//   --gpu-priority high  D3DKMTSetProcessSchedulingPriorityClass(HIGH) before the process creates its D3D11 device, then
//                        IDXGIDevice::SetGPUThreadPriority(+7) on the device. Both are accepted without admin rights on
//                        Setup A; whether they change anything is what E1 measures.
//   --vram-reserve-mb N  IDXGIAdapter3::SetVideoMemoryReservation(local, N MB) on the device's adapter: the OS keeps that
//                        much of the player's VRAM resident when another process (the AI sides' painter) asks for more
//                        (E1's rule for the live path). 0 (the default) asks nothing.
//
// What was asked, what each call returned and what reads back afterwards are printed and written to OUT/gpu-share.json.
// Real displays only: --headless creates no device and no swapchain, so there the options change nothing.
#pragma once

#define COBJMACROS
#include <stdbool.h>
#include <windows.h>
#include <d3d11.h>
#include <dxgi1_3.h>

// One of its options at argv[*i]: 1 taken, 0 not its, -1 malformed (the convention of picture_arg).
int gpu_share_arg(int argc, char **argv, int *i);
void gpu_share_usage(void);

// Buffers for a swapchain perf1 makes with `base` buffers: base, or N + 1 when the requested depth N needs more.
UINT gpu_share_buffer_count(UINT base);
// Sets the requested maximum frame latency on one monitor's swapchain and records the result under `monitor`.
void gpu_share_frame_latency(IDXGISwapChain2 *sc, const char *monitor);

// --gpu-priority high: the process's GPU scheduling class. Called before the process creates any GPU device.
void gpu_share_before_device(void);
// --gpu-priority high: the device's GPU thread priority. Then prints everything recorded and writes OUT/gpu-share.json.
void gpu_share_after_device(ID3D11Device *dev, const char *out_dir);
