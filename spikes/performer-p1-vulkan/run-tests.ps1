# Compiles the two log-block test programs and runs every Python test of the Vulkan spike. No window, no GPU work.
#   pwsh -File spikes\performer-p1-vulkan\run-tests.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\compile.ps1 lgpl jsontest
if ($LASTEXITCODE -ne 0) { throw "compile failed" }
$failed = 0
foreach ($t in "test-analyze-perf1vk.py", "test-analyze-vk-timing.py", "tests\test-own-swap-json.py", "tests\test-probe-json.py") {
    Write-Host "== $t"
    python -I $t
    if ($LASTEXITCODE -ne 0) { $failed++ }
}
if ($failed) { throw "$failed test file(s) failed" }
Write-Host "all tests passed"
