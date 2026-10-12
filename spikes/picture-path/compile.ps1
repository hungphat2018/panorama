# Compile the picture-path harness with the portable MSYS2 UCRT64 toolchain (nothing installed, PATH and registry untouched).
#   pwsh -File spikes\picture-path\compile.ps1 [-Target harness|geometry-test]
param([string]$Target = "harness")
$ErrorActionPreference = "Stop"
$env:MSYSTEM = "UCRT64"
$env:CHERE_INVOKING = "1"
$here = (Resolve-Path $PSScriptRoot).Path -replace "\\", "/"
& "C:\Users\tamla\msys64\usr\bin\bash.exe" -lc "bash '$here/compile-picture-path.sh' $Target"
exit $LASTEXITCODE
