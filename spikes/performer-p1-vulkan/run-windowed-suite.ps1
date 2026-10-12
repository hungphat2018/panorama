# Windowed smoke tests of the Vulkan spike, in the order of the report. Half-size, not-topmost, not-activated windows;
# no full screen, no display change. Every run is at most about 30 s. It refuses to start a run while a game is running.
#
#   pwsh -File spikes\performer-p1-vulkan\run-windowed-suite.ps1 [-Only selftest,probe,lp,layers,own,decode] [-Out DIR] [-PresentMon PATH]
#
# Steps (default: all, in this order)
#   selftest  NO WINDOW: vk-timing-probe --selftest makes its Vulkan device on each monitor's GPU and exits (about a second
#             each); if it fails the suite stops before any window opens.
#   probe   vk-timing-probe on both monitors: present id / wait / timing on a Win32 surface, first pixel out vs the scan line
#   lp      the presenter on libplacebo's swapchain, Judas (23.976 fps) and Hotel (25 fps), loader layers as installed
#   layers  the same with the third-party Vulkan layers off, and with every implicit layer off (hitch / jitter hunt)
#   own     the presenter on our own swapchain (present_wait2 pacing, present timing feedback)
#   decode  the presenter with Vulkan Video decoding (libplacebo's device adopted by FFmpeg)
#
# -PresentMon PATH   also trace every presenter run with PresentMon (Intel, signed): it reports per swapchain whether the
#                    window reached "Hardware: Independent Flip" or stayed "Composed: Flip". It starts without administrator
#                    rights here (this account is in the "Performance Log Users" group); unelevated it warns that
#                    short-running processes may show as <unknown>, the presenter runs 25 s, so it should be matched by name.
#
# -LayersOff none|third-party|all-implicit   run EVERY step with the Vulkan implicit layers off (the programs' --layers-off
#                    flag, which sets VK_LOADER_LAYERS_DISABLE for that process only). The results go to "<Out>-layers-<mode>",
#                    so a second pass of the whole suite is an A/B against the first. Step "layers" is the built-in A/B for
#                    the presenter alone: it needs no -LayersOff.
param(
    [string[]]$Only = @("selftest", "probe", "lp", "layers", "own", "decode"),
    [string]$Out = "results-2026-10-11\windowed",
    [string[]]$Games = @("DD2"),
    [string]$PresentMon = "",
    [ValidateSet("none", "third-party", "all-implicit")]
    [string]$LayersOff = "none"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
# "pwsh -File ... -Only probe,own" delivers one string "probe,own": split it, and refuse names that are not steps
$Only = @($Only | ForEach-Object { $_ -split "," } | Where-Object { $_ })
$Games = @($Games | ForEach-Object { $_ -split "," } | Where-Object { $_ })
$known = "selftest", "probe", "lp", "layers", "own", "decode"
$unknown = @($Only | Where-Object { $known -notcontains $_ })
if ($unknown) { throw "Unknown step(s): $($unknown -join ', '). Steps: $($known -join ', ')" }
if ($PresentMon) { $PresentMon = (Resolve-Path $PresentMon).Path }   # Start-Process resolves a relative path against the process folder
if ($LayersOff -ne "none") { $Out = "$Out-layers-$LayersOff" }
$run = ".\bin\run.cmd"
$judas = "C:/Users/tamla/Downloads/[Judas] Nama-Anaru - S01E02.mkv"
$hotel = "C:/Users/tamla/Downloads/260723_HOTEL_AMAN_43_V3_MAIN_50s_MASTER_16x9_VA_H264.mp4"

function Assert-NoGame {
    $running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $Games -contains $_.Name }
    if ($running) {
        throw "A game is running ($(($running | ForEach-Object { $_.Name }) -join ', ')): no window is opened. Close it and run again."
    }
}

function Invoke-Step([string]$Name, [scriptblock]$Body) {
    Assert-NoGame
    New-Item -ItemType Directory -Force $Out | Out-Null
    Write-Host "=== $Name  $(Get-Date -Format HH:mm:ss)"
    # the programs print to stderr; with "Stop" Windows PowerShell 5.1 would turn the first such line into an error
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Body } finally { $ErrorActionPreference = $previous }
    Start-Sleep -Seconds 3
}

if ($Only -contains "selftest") {
    # No window: the probe makes its Vulkan device on each monitor's GPU, prints what it offers and exits. A broken set-up
    # (layers, driver, device creation) stops the suite here and not in the middle of a windowed run.
    Assert-NoGame
    New-Item -ItemType Directory -Force $Out | Out-Null
    Write-Host "=== selftest  $(Get-Date -Format HH:mm:ss)"
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"   # the programs print to stderr
    foreach ($mon in 0, 1) {
        & $run vk-timing-probe.exe --monitor $mon --selftest --layers-off $LayersOff 2>&1 | Tee-Object -FilePath "$Out\selftest-probe-monitor$mon.txt"
        if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = $previous; throw "The probe's self-test failed on monitor $mon (exit $LASTEXITCODE); no window was opened." }
    }
    # the presenter's device as each backend / decoder combination would create it
    foreach ($combo in @("", "--backend own", "--decode vulkan", "--backend own --decode vulkan")) {
        $name = "selftest-presenter" + ($combo -replace "[^a-z]+", "-")
        $comboArgs = @($combo -split " " | Where-Object { $_ })
        & $run perf1vk-lgpl.exe --device-test @comboArgs --low-priority --layers-off $LayersOff 2>&1 | Tee-Object -FilePath "$Out\$name.txt"
        if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = $previous; throw "The presenter's device test failed for '$combo' (exit $LASTEXITCODE); no window was opened." }
    }
    $ErrorActionPreference = $previous
}

if ($Only -contains "probe") {
    # two lists per monitor so that each run stays under 30 s: the basic reports first, then the relative targets
    $lists = @{
        "a" = "v1-wait,v2-wait,v2-wait-timing,v2-acquire-timing,rel-1,rel-2"
        "b" = "rel-3,rel-5,rel-2.5,rel-2.5-nearest,rel-2.4-nearest,rel-2-acquire"
    }
    foreach ($part in "a", "b") {
        foreach ($mon in 0, 1) {
            Invoke-Step "probe $part monitor $mon" {
                & $run vk-timing-probe.exe --monitor $mon --windowed --seconds-each 2 --experiments $lists[$part] --layers-off $LayersOff --out "$Out\probe-$part" 2>&1 | Tee-Object -FilePath "$Out\probe-$part-monitor$mon.txt"
            }
        }
        python -I .\analyze-vk-timing.py "$Out\probe-$part" | Tee-Object -FilePath "$Out\probe-$part-analysis.txt"
    }
}

function Invoke-Presenter([string]$Name, [string]$File, [string[]]$Extra) {
    Invoke-Step $Name {
        $pm = $null
        if ($PresentMon) {
            $pmArgs = @("--process_name", "perf1vk-lgpl.exe", "--output_file", "$Out\$Name-presentmon.csv", "--v2_metrics",
                        "--write_display_metadata", "--no_console_stats", "--terminate_on_proc_exit", "--stop_existing_session",
                        "--session_name", "perf1vk-spike")
            $pm = Start-Process -FilePath $PresentMon -ArgumentList $pmArgs -WindowStyle Hidden -WorkingDirectory $PSScriptRoot -PassThru
            Start-Sleep -Seconds 2   # let the trace session start before the windows open
        }
        # the global -LayersOff applies unless this run names its own --layers-off
        $layerArgs = if ($Extra -contains "--layers-off") { @() } else { @("--layers-off", $LayersOff) }
        & $run perf1vk-lgpl.exe --file $File --windowed --seconds 15 --out "$Out\$Name" @layerArgs @Extra 2>&1 | Tee-Object -FilePath "$Out\$Name-stdout.txt"
        if ($pm) {
            if (-not $pm.WaitForExit(8000)) {
                & $PresentMon --terminate_existing_session --session_name perf1vk-spike | Out-Null
                if (-not $pm.HasExited) { $pm.Kill() }
            }
            if (Test-Path "$Out\$Name-presentmon.csv") {
                python -I .\summarize-presentmon.py "$Out\$Name-presentmon.csv" | Tee-Object -FilePath "$Out\$Name-presentmon.txt"
            }
        }
    }
    python -I .\analyze-perf1vk.py "$Out\$Name" | Tee-Object -FilePath "$Out\$Name-analysis.txt"
}

if ($Only -contains "lp") {
    Invoke-Presenter "lp-judas" $judas @("--event-level", "debug")
    Invoke-Presenter "lp-hotel" $hotel @()
}
if ($Only -contains "layers") {
    Invoke-Presenter "lp-judas-third-party-off" $judas @("--layers-off", "third-party")
    Invoke-Presenter "lp-judas-all-implicit-off" $judas @("--layers-off", "all-implicit")
}
if ($Only -contains "own") {
    Invoke-Presenter "own-judas" $judas @("--backend", "own", "--vk-depth", "2")
    Invoke-Presenter "own-judas-premultiplied" $judas @("--backend", "own", "--vk-depth", "2", "--alpha", "premultiplied")
}
if ($Only -contains "decode") {
    Invoke-Presenter "lp-judas-vkdecode" $judas @("--decode", "vulkan")
    Invoke-Presenter "lp-hotel-vkdecode" $hotel @("--decode", "vulkan")
}
Write-Host "done $(Get-Date -Format HH:mm:ss)"
