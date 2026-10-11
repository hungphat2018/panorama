## Windowed playback runs

Steady = after the first 2 s. Times in ms. CPU % is of one core (the machine has 32 logical CPUs).

| run | status | s | first frame (from process start) | render CPU p50 / p99 / max | GPU passes p50 / p99 | loop interval p50 / p99 / max | late >1.5x / >2.5x of steady | hitches >50 ms | skipped refreshes (est.) | queue dropped / stalls | refreshes per film frame | process CPU | render thread CPU | decode thread CPU s | GPU 3D / video-decode / copy (mean %) | VRAM MB | peak WS MB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| av1b-d3d11-d3d11va | ok | 4.0 | 415 | 0.024 / 0.515 / 0.99 | 0.008 / 0.273 | 4.168 / 4.27 / 4.6 | 0 / 0 of 480 | 0 | 0.0 | 1 / 0 | 7x1 9x50 10x49 | 27.3 % | 0.4 % | 0.02 | - / - / - | - | 153 |
| av1b-d3d11-sw | ok | 4.0 | 442 | 0.025 / 0.153 / 0.19 | 0.018 / 3.961 | 4.168 / 4.27 / 4.5 | 0 / 0 of 480 | 0 | 0.0 | 1 / 0 | 6x1 8x1 9x49 10x48 11x1 | 42.6 % | 1.6 % | 0.02 | - / - / - | - | 286 |
| av1b-vulkan-vulkan | ok | 4.0 | 816 | 0.076 / 0.602 / 0.80 | 0.004 / 0.171 | 4.166 / 6.58 / 8.3 | 9 / 0 of 479 | 0 | -0.0 | 1 / 0 | 1x1 3x1 6x1 8x1 9x48 10x50 | 30.5 % | 21.5 % | 0.09 | - / - / - | - | 289 |
| hotel-d3d11-d3d11va | ok | 62.0 | 436 | 0.028 / 0.351 / 0.53 | 0.011 / 0.294 | 4.165 / 4.26 / 12.4 | 1 / 1 of 14408 | - | -0.0 | 0 / 0 | 7x2 9x780 10x781 11x3 | 11.0 % | 1.4 % | 1.44 | 5.2 / 1.4 / 0.07 | 153 | 157 |
| hotel-d3d11-sw | ok | 62.0 | 435 | 0.026 / 0.151 / 0.33 | 0.016 / 4.125 | 4.165 / 4.28 / 4.5 | 0 / 0 of 14408 | - | 0.0 | 0 / 0 | 6x1 9x781 10x783 11x1 | 27.7 % | 2.8 % | 0.84 | 5.9 / 0.0 / 0.07 | 91 | 259 |
| hotel-vulkan-sw-depth1 | ok | 62.6 | 1088 | 0.077 / 0.633 / 1.06 | 0.011 / 0.191 | 4.168 / 6.37 / 6.6 | 502 / 0 of 14553 | - | 0.1 | 0 / 0 | 1x1 4x1 6x1 9x636 10x929 | 20.2 % | 6.6 % | 0.03 | 9.9 / 0.0 / 0.33 | 224 | 512 |
| hotel-vulkan-vk-depth1 | ok | 62.6 | 876 | 0.102 / 0.714 / 1.17 | 0.013 / 0.220 | 4.165 / 6.48 / 6.8 | 306 / 0 of 14542 | - | 0.2 | 0 / 0 | 1x1 4x1 6x1 9x648 10x917 | 23.0 % | 20.8 % | 1.02 | 10.4 / 1.1 / 0.30 | 231 | 307 |
| judas-d3d11-d3d11va | ok | 60.0 | 438 | 0.029 / 0.389 / 0.67 | 0.014 / 0.298 | 4.166 / 4.30 / 4.5 | 0 / 0 of 13920 | - | 0.0 | 1 / 0 | 8x1 10x1437 11x1 | 2.1 % | 0.2 % | 0.12 | 5.2 / 2.0 / 0.07 | 239 | 152 |
| judas-d3d11-sw | ok | 60.0 | 428 | 0.028 / 0.160 / 0.43 | 0.015 / 4.111 | 4.165 / 4.30 / 5.2 | 0 / 0 of 13920 | - | 0.0 | 1 / 0 | 8x1 10x1437 11x1 | 16.7 % | 0.1 % | 0.02 | 6.3 / 0.0 / 0.07 | 106 | 398 |
| judas-default-worst-d3d11-sw | ok | 20.0 | 427 | 0.087 / 0.182 / 0.41 | 0.282 / 4.292 | 4.165 / 4.29 / 4.9 | 0 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 7x2 8x1 9x1 10x474 11x1 | 9.0 % | 0.3 % | 0.00 | 12.4 / 0.0 / 0.07 | 106 | 386 |
| judas-default-worst-vulkan-sw | ok | 20.0 | 1075 | 0.450 / 0.874 / 1.37 | 0.179 / 0.362 | 4.168 / 6.24 / 7.2 | 39 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 1x1 2x1 6x1 9x14 10x450 11x14 | 16.6 % | 3.0 % | 0.05 | 13.9 / 0.0 / 0.60 | 249 | 641 |
| judas-hq-worst-d3d11-d3d11va | ok | 20.0 | 586 | 0.099 / 0.346 / 0.45 | 0.392 / 0.789 | 4.166 / 4.27 / 4.3 | 0 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 8x2 10x477 11x1 | 5.1 % | 0.2 % | 0.02 | 14.3 / 1.4 / 0.06 | 241 | 160 |
| judas-hq-worst-d3d11-sw | ok | 20.0 | 650 | 0.096 / 0.204 / 0.55 | 0.295 / 0.592 | 4.165 / 4.29 / 4.8 | 0 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 7x1 8x1 10x476 11x1 | 17.5 % | 0.3 % | 0.03 | 13.1 / 0.0 / 0.07 | 104 | 389 |
| judas-hq-worst-vulkan-sw | ok | 20.0 | 1102 | 0.433 / 0.908 / 2.01 | 0.318 / 0.642 | 4.171 / 6.93 / 8.4 | 147 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 1x1 3x1 6x1 9x5 10x468 11x5 | 28.1 % | 13.3 % | 0.08 | 19.0 / 0.0 / 0.59 | 247 | 644 |
| judas-hq-worst-vulkan-vk | ok | 20.0 | 874 | 0.348 / 0.752 / 1.13 | 0.325 / 0.646 | 4.169 / 6.80 / 8.3 | 142 / 0 of 4320 | 0 | -0.0 | 1 / 0 | 1x1 2x1 5x1 9x1 10x475 11x2 | 13.9 % | 11.2 % | 0.06 | 19.9 / 1.0 / 0.27 | 746 | 307 |
| judas-own-present-d3d11-d3d11va | ok | 20.0 | 419 | 0.030 / 0.308 / 0.43 | 0.016 / 0.297 | 4.162 / 4.48 / 5.4 | 0 / 0 of 4320 | 0 | 0.1 | 1 / 0 | 5x1 8x1 10x478 | 4.0 % | 0.2 % | 0.03 | 4.9 / 1.9 / 0.07 | 235 | 157 |
| judas-own-present-d3d11-sw | ok | 20.0 | 424 | 0.030 / 0.138 / 0.49 | 0.016 / 0.335 | 4.161 / 5.00 / 5.5 | 0 / 0 of 4320 | 0 | 0.1 | 1 / 0 | 5x1 8x1 10x478 | 18.9 % | 12.5 % | 0.02 | 5.6 / 0.0 / 0.07 | 101 | 386 |
| judas-sync-d3d11-d3d11va | ok | 20.0 | 414 | 0.025 / 0.291 / 0.65 | 0.016 / 0.293 | 4.167 / 5.37 / 7.6 | 1 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 2x1 3x1 6x1 8x1 10x477 | 5.2 % | 0.1 % | 0.02 | 6.1 / 2.1 / 0.06 | 239 | 155 |
| judas-sync-d3d11-sw | ok | 20.0 | 441 | 0.024 / 0.139 / 0.45 | 0.016 / 0.320 | 4.166 / 5.33 / 6.2 | 0 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 5x1 7x1 9x15 10x446 11x15 15x1 | 16.6 % | 0.2 % | 0.00 | 7.0 / 0.0 / 0.07 | 106 | 387 |
| judas-vulkan-sw-depth1 | ok | 60.0 | 1127 | 0.083 / 0.639 / 1.20 | 0.011 / 0.203 | 4.171 / 6.58 / 8.3 | 520 / 0 of 13920 | - | -0.0 | 1 / 0 | 9x2 10x1435 11x2 | 20.6 % | 4.3 % | 0.03 | 10.1 / 0.0 / 0.58 | 247 | 654 |
| judas-vulkan-sw-depth3 | ok | 20.0 | 1135 | 0.097 / 0.584 / 1.26 | 0.019 / 0.345 | 3.901 / 12.11 / 720.7 | 1296 / 115 of 3979 | - | 214.4 | 17 / 2 | 1x1 2x2 5x2 7x1 8x1 9x169 10x126 11x119 12x27 13x1 | 16.4 % | 6.2 % | 0.03 | 5.2 / 0.0 / 0.56 | 255 | 643 |
| judas-vulkan-vk-depth1 | ok | 60.0 | 840 | 0.106 / 0.701 / 1.62 | 0.013 / 0.217 | 4.168 / 6.60 / 301.9 | 470 / 6 of 13772 | - | 147.9 | 6 / 2 | 1x3 3x1 4x3 6x1 8x1 9x2 10x1416 11x2 12x1 | 4.6 % | 3.0 % | 0.06 | 11.0 / 1.2 / 0.29 | 748 | 305 |
| judas-vulkan-vk-depth3 | ok | 20.0 | 848 | 0.110 / 0.641 / 0.78 | 0.019 / 0.390 | 3.866 / 10.24 / 10.8 | 1440 / 29 of 4320 | - | -1.0 | 1 / 0 | 2x1 3x1 9x203 10x124 11x100 12x52 | 2.8 % | 0.6 % | 0.02 | 6.7 / 1.9 / 0.30 | 757 | 303 |
| lgpl-hotel-d3d11-d3d11va | ok | 20.0 | 424 | 0.029 / 0.383 / 0.58 | 0.015 / 0.295 | 4.167 / 4.28 / 4.9 | 0 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 6x1 9x251 10x252 11x1 | 24.8 % | 13.7 % | 0.16 | 6.0 / 2.0 / 0.07 | 153 | 133 |
| lgpl-judas-d3d11-d3d11va | ok | 20.0 | 416 | 0.031 / 0.385 / 0.59 | 0.011 / 0.182 | 4.166 / 4.28 / 5.2 | 0 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 8x1 10x477 11x1 | 51.5 % | 19.4 % | 1.25 | 6.6 / 1.4 / 0.05 | 239 | 135 |
| lgpl-judas-d3d11-sw | ok | 20.0 | 419 | 0.026 / 0.148 / 0.40 | 0.016 / 4.101 | 4.166 / 4.28 / 4.7 | 0 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 6x1 10x478 11x1 | 58.8 % | 15.9 % | 0.44 | 7.1 / 0.0 / 0.07 | 106 | 360 |
| lgpl-judas-vulkan-sw | ok | 20.0 | 1079 | 0.080 / 0.660 / 0.94 | 0.010 / 0.202 | 4.172 / 10.74 / 12.7 | 434 / 32 of 4170 | 0 | 150.0 | 1 / 0 | 1x1 4x1 6x1 7x1 8x32 9x114 10x304 11x27 | 36.8 % | 1.6 % | 0.02 | 12.7 / 0.0 / 0.57 | 249 | 631 |
| lgpl-judas-vulkan-vk | ok | 20.0 | 844 | 0.104 / 0.683 / 1.20 | 0.013 / 0.220 | 4.172 / 10.76 / 13.4 | 421 / 34 of 4174 | 0 | 145.0 | 1 / 0 | 1x1 3x1 5x1 6x1 8x29 9x111 10x312 11x25 | 27.3 % | 2.2 % | 0.05 | 13.2 / 1.2 / 0.27 | 748 | 295 |
| packaged-judas-d3d11-d3d11va | ok | 20.0 | 414 | 0.031 / 0.427 / 0.60 | 0.013 / 0.260 | 4.166 / 4.26 / 5.3 | 0 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 10x478 11x1 | 5.3 % | 0.1 % | 0.05 | 5.3 / 2.0 / 0.07 | 239 | 153 |
| packaged-judas-d3d11-sw | ok | 20.0 | 437 | 0.030 / 0.138 / 0.30 | 0.014 / 4.067 | 4.165 / 4.27 / 4.9 | 0 / 0 of 4320 | 0 | 0.0 | 1 / 0 | 7x1 10x478 12x1 | 17.3 % | 0.1 % | 0.00 | 6.4 / 0.0 / 0.07 | 106 | 385 |
| packaged-judas-vulkan-vk | ok | 20.0 | 926 | 0.099 / 0.656 / 1.18 | 0.013 / 0.214 | 4.168 / 7.33 / 11.6 | 186 / 4 of 4316 | 0 | 4.1 | 1 / 0 | 1x1 4x1 6x1 7x1 9x5 10x471 11x1 | 23.0 % | 20.8 % | 0.06 | 11.4 / 1.1 / 0.28 | 748 | 319 |
| stallhunt-d3d11-sw-run1 | ok | 60.0 | 423 | 0.027 / 0.146 / 0.33 | 0.015 / 4.049 | 4.166 / 4.26 / 5.1 | 0 / 0 of 13920 | 0 | 0.0 | 1 / 0 | 8x1 10x1437 11x1 | 51.2 % | 6.9 % | 0.27 | - / - / - | - | 397 |
| stallhunt-vulkan-sw-depth1-run1 | ok | 60.0 | 1101 | 0.085 / 0.630 / 0.97 | 0.010 / 0.194 | 4.167 / 6.54 / 12.2 | 314 / 1 of 13919 | 0 | 1.0 | 1 / 0 | 8x1 9x5 10x1426 11x6 12x1 | 17.4 % | 0.2 % | 0.02 | - / - / - | - | 654 |
| stallhunt-vulkan-sw-depth1-run2 | ok | 60.0 | 1080 | 0.084 / 0.636 / 0.97 | 0.010 / 0.193 | 4.165 / 5.87 / 8.5 | 68 / 0 of 13920 | 0 | 0.1 | 1 / 0 | 9x11 10x1417 11x9 12x2 | 18.8 % | 0.6 % | 0.02 | - / - / - | - | 653 |
| stallhunt-vulkan-sw-depth1-run3 | ok | 60.0 | 1084 | 0.084 / 0.637 / 1.03 | 0.010 / 0.194 | 4.163 / 6.02 / 7.2 | 18 / 0 of 13920 | 0 | 0.1 | 1 / 0 | 1x1 4x1 6x1 9x1 10x1436 11x1 | 33.1 % | 0.1 % | 0.00 | - / - / - | - | 653 |

## First frame (process creation to first presented frame), ms

| config | cold (run 1, no pl_cache file) | warm runs 2-5 (median / min / max) |
|---|---|---|
| d3d11-d3d11va | 405 | 265 / 260 / 270 |
| d3d11-sw | 462 | 269 / 267 / 273 |
| vulkan-sw | 1068 | 951 / 945 / 957 |
| vulkan-vulkan | 809 | 707 / 706 / 715 |

### DXGI refreshes between presents (D3D11 only, sampled; most runs: every present on the next refresh)

- av1b-d3d11-d3d11va: 1x942 2x1 4x1 7+x1
- av1b-d3d11-sw: 1x939 2x1 4x1 7+x1
- hotel-d3d11-d3d11va: 1x4687 2x2 3x1 7+x1
- hotel-d3d11-sw: 1x14875 4x1 7+x1
- judas-d3d11-d3d11va: 1x14388 3x1 7+x1
- judas-d3d11-sw: 1x14388 3x1 7+x1
- judas-default-worst-d3d11-sw: 1x4778 5x2
- judas-hq-worst-d3d11-d3d11va: 1x4784 2x1 3x1 7+x1
- judas-hq-worst-d3d11-sw: 1x4782 3x1 4x1 7+x1
- judas-own-present-d3d11-d3d11va: 1x4786 5x1
- judas-own-present-d3d11-sw: 1x4786 5x1
- judas-sync-d3d11-d3d11va: 1x4790 3x1 7+x1
- judas-sync-d3d11-sw: 1x4786 4x1 7+x1
- lgpl-hotel-d3d11-d3d11va: 1x4787 4x1 7+x1
- lgpl-judas-d3d11-d3d11va: 1x4788 3x1 7+x1
- lgpl-judas-d3d11-sw: 1x4787 4x1 7+x1
- packaged-judas-d3d11-d3d11va: 1x4792 7+x1
- packaged-judas-d3d11-sw: 1x4791
- stallhunt-d3d11-sw-run1: 1x14388 3x1 7+x1

## Decode-only throughput (12 s per run, frames discarded)

| run | decoder | frames/s | x real time | process CPU (% of one core) |
|---|---|---|---|---|
| hotel-d3d11va | h264 d3d11/nv12 | 1746 | 69.8 | 8 |
| hotel-sw | h264 software | 1888 | 75.5 | 1159 |
| hotel-vulkan | h264 vulkan/nv12 | 1505 | 60.2 | 28 |
| judas-d3d11va | hevc d3d11/p010le | 1785 | 74.5 | 9 |
| judas-sw | hevc software | 1121 | 46.8 | 789 |
| judas-vulkan | hevc vulkan/p010le | 1311 | 54.7 | 22 |

## Headless render benchmark (1920x1080 frame, caches bypassed, GPU awaited per draw)

| run | libplacebo | GPU | backend | preset | source | target | wall p50 | wall p99 | GPU passes p50 | GPU passes p99 |
|---|---|---|---|---|---|---|---|---|---|---|
| d3d11-AMD-default-sw-2560x1440 | master 7.375 | AMD Radeon(TM) 610M | d3d11 | default | 1920x1080 | 2560x1440 | 8.410 | 8.615 | 8.170 | 8.252 |
| d3d11-AMD-fast-sw-2560x1440 | master 7.375 | AMD Radeon(TM) 610M | d3d11 | fast | 1920x1080 | 2560x1440 | 1.348 | 1.427 | 1.204 | 1.210 |
| d3d11-AMD-hq-sw-2560x1440 | master 7.375 | AMD Radeon(TM) 610M | d3d11 | hq | 1920x1080 | 2560x1440 | 19.047 | 19.335 | 18.759 | 18.917 |
| d3d11-NVIDIA-default-sw-2560x1440 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | d3d11 | default | 1920x1080 | 2560x1440 | 0.427 | 0.657 | 0.299 | 0.303 |
| d3d11-NVIDIA-default-sw-3840x2160 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | d3d11 | default | 1920x1080 | 3840x2160 | 0.634 | 0.889 | 0.481 | 0.489 |
| d3d11-NVIDIA-fast-sw-2560x1440 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | d3d11 | fast | 1920x1080 | 2560x1440 | 0.116 | 0.248 | 0.031 | 0.032 |
| d3d11-NVIDIA-hq-d3d11va-2560x1440 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | d3d11 | hq | 1920x1080 | 2560x1440 | 0.829 | 1.129 | 0.664 | 0.912 |
| d3d11-NVIDIA-hq-sw-2560x1440 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | d3d11 | hq | 1920x1080 | 2560x1440 | 0.820 | 1.132 | 0.667 | 0.692 |
| d3d11-NVIDIA-hq-sw-3840x2160 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | d3d11 | hq | 1920x1080 | 3840x2160 | 1.363 | 1.662 | 1.148 | 1.442 |
| vulkan-AMD-default-sw-2560x1440 | master 7.375 | AMD Radeon(TM) 610M | vulkan | default | 1920x1080 | 2560x1440 | 8.713 | 8.944 | 8.516 | 8.688 |
| vulkan-AMD-fast-sw-2560x1440 | master 7.375 | AMD Radeon(TM) 610M | vulkan | fast | 1920x1080 | 2560x1440 | 1.173 | 1.265 | 1.047 | 1.094 |
| vulkan-AMD-hq-sw-2560x1440 | master 7.375 | AMD Radeon(TM) 610M | vulkan | hq | 1920x1080 | 2560x1440 | 18.617 | 19.189 | 18.183 | 18.684 |
| vulkan-NVIDIA-default-sw-2560x1440 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | vulkan | default | 1920x1080 | 2560x1440 | 0.460 | 0.653 | 0.296 | 0.307 |
| vulkan-NVIDIA-default-sw-3840x2160 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | vulkan | default | 1920x1080 | 3840x2160 | 0.659 | 0.860 | 0.484 | 0.494 |
| vulkan-NVIDIA-fast-sw-2560x1440 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | vulkan | fast | 1920x1080 | 2560x1440 | 0.122 | 0.183 | 0.036 | 0.036 |
| vulkan-NVIDIA-hq-sw-2560x1440 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | vulkan | hq | 1920x1080 | 2560x1440 | 0.994 | 1.428 | 0.699 | 0.733 |
| vulkan-NVIDIA-hq-sw-3840x2160 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | vulkan | hq | 1920x1080 | 3840x2160 | 1.611 | 2.039 | 1.281 | 1.505 |
| vulkan-NVIDIA-hq-vulkan-2560x1440 | master 7.375 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | vulkan | hq | 1920x1080 | 2560x1440 | 0.928 | 1.132 | 0.693 | 0.698 |
| packaged-d3d11-NVIDIA-default-2560x1440 | package 7.360 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | d3d11 | default | 1920x1080 | 2560x1440 | 0.382 | 0.601 | 0.251 | 0.260 |
| packaged-d3d11-NVIDIA-hq-2560x1440 | package 7.360 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | d3d11 | hq | 1920x1080 | 2560x1440 | 0.794 | 1.043 | 0.648 | 0.659 |
| packaged-vulkan-NVIDIA-default-2560x1440 | package 7.360 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | vulkan | default | 1920x1080 | 2560x1440 | 0.460 | 0.607 | 0.292 | 0.302 |
| packaged-vulkan-NVIDIA-hq-2560x1440 | package 7.360 | NVIDIA GeForce RTX 5070 Ti Laptop GPU | vulkan | hq | 1920x1080 | 2560x1440 | 0.982 | 1.109 | 0.693 | 0.703 |
