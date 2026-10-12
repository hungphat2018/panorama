# Compile the finish passes and their test driver with the portable MSYS2 UCRT64 toolchain (nothing installed, PATH and
# registry untouched).
#   pwsh -File spikes\picture-path\finish\compile.ps1 [-Target test|unit|all]
param([string]$Target = "test")
$ErrorActionPreference = "Stop"
$env:MSYSTEM = "UCRT64"
$env:CHERE_INVOKING = "1"
$here = (Resolve-Path $PSScriptRoot).Path -replace "\\", "/"
& "C:\Users\tamla\msys64\usr\bin\bash.exe" -lc "bash '$here/compile-finish.sh' $Target"
exit $LASTEXITCODE
