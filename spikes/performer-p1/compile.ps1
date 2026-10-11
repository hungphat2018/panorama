# Compile perf1 with the portable MSYS2 UCRT64 toolchain (nothing installed, PATH and registry untouched).
#   pwsh -File spikes\performer-p1\compile.ps1 [lgpl|master]
param([string]$Flavor = "lgpl")
$ErrorActionPreference = "Stop"
$env:MSYSTEM = "UCRT64"
$env:CHERE_INVOKING = "1"
$here = (Resolve-Path $PSScriptRoot).Path -replace "\\", "/"
& "C:\Users\tamla\msys64\usr\bin\bash.exe" -lc "bash '$here/compile-perf1.sh' $Flavor"
exit $LASTEXITCODE
