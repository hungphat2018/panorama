# Compile the Vulkan spike programs with the portable MSYS2 UCRT64 toolchain (nothing installed, PATH and registry untouched).
#   pwsh -File spikes\performer-p1-vulkan\compile.ps1 [lgpl|master] [all|caps|probe|perf1vk|jsontest]
param([string]$Flavor = "lgpl", [string]$What = "all")
$ErrorActionPreference = "Stop"
$env:MSYSTEM = "UCRT64"
$env:CHERE_INVOKING = "1"
$here = (Resolve-Path $PSScriptRoot).Path -replace "\\", "/"
& "C:\Users\tamla\msys64\usr\bin\bash.exe" -lc "bash '$here/compile-perf1vk.sh' $Flavor $What"
exit $LASTEXITCODE
