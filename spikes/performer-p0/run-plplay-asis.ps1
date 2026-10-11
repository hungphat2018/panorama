# Runs libplacebo's own demo player (plplay, built from master 1dd0226 with no changes) for a few
# seconds on a small clip and closes its window cleanly, so its stdout is flushed to the log.
# The window is the size of the clip (plplay offers no option for that), so use a small clip.
#
#   pwsh -NoProfile -File run-plplay-asis.ps1 -Clip C:\path\small.mp4 -Api glfw-d3d11 -OutLog plplay.log
param(
    [Parameter(Mandatory)][string]$Clip,
    [ValidateSet('glfw-d3d11', 'glfw-vk')][string]$Api = 'glfw-d3d11',
    [Parameter(Mandatory)][string]$OutLog,
    [int]$Seconds = 8,
    [string[]]$ExtraArgs = @()   # e.g. -ExtraArgs '--hwdec'
)
$ErrorActionPreference = 'Stop'
$bin = Join-Path $PSScriptRoot 'bin'
# Runtime DLLs for this process only; the user PATH is not touched.
$env:PATH = "$bin;C:\Users\tamla\msys64\ucrt64\bin;$env:PATH"

$out = "$OutLog.stdout"
$err = "$OutLog.stderr"
$p = Start-Process -FilePath (Join-Path $bin 'plplay-master.exe') -ArgumentList (@('-w', $Api) + $ExtraArgs + @("`"$Clip`"")) `
        -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
Start-Sleep -Seconds $Seconds
$null = $p.CloseMainWindow()
if (-not $p.WaitForExit(15000)) {
    Stop-Process -Id $p.Id -Force
    'plplay did not exit after its window was closed; killed' | Add-Content $err
}
$ansi = [regex]'\x1b\[[0-9;]*m'
$lines = (Get-Content $out) + (Get-Content $err) | ForEach-Object { $ansi.Replace($_, '') }
# the GPU texture format table is long and not useful here
$lines | Where-Object { $_ -notmatch '^\s+(r8|rg8|rgba|bgra|bgr|rgb|r16|rg16|r32|rg32|rg11|rg1)\w*\s+(UNORM|SNORM|FLOAT|SINT|UINT)' } |
    Set-Content $OutLog
Remove-Item $out, $err
"exit code: $($p.ExitCode)" | Add-Content $OutLog
