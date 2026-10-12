# Compile with the portable MSYS2 UCRT64 toolchain (nothing installed, PATH and registry untouched).
#   pwsh -File spikes\performer-p1\compile.ps1 [-Flavor lgpl|master] [-Program p3|latch-test] [-Output NAME.exe]
#
#   -Program p3          the performer: bin\perf1-p3-lgpl.exe (default)
#   -Program latch-test  the headless test: bin\latch-sim.exe and bin\cue-selftest.exe
# bin\perf1-lgpl.exe (the first P1 binary) is never written.
param([string]$Flavor = "lgpl", [string]$Program = "p3", [string]$Output = "")
$ErrorActionPreference = "Stop"
$env:MSYSTEM = "UCRT64"
$env:CHERE_INVOKING = "1"
$here = (Resolve-Path $PSScriptRoot).Path -replace "\\", "/"
$extra = if ($Output) { "'$Output'" } else { "" }
& "C:\Users\tamla\msys64\usr\bin\bash.exe" -lc "bash '$here/compile-perf1.sh' $Flavor $Program $extra"
exit $LASTEXITCODE
