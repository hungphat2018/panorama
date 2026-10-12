# Compile the C programs of this folder with the portable MSYS2 UCRT64 toolchain (nothing installed, PATH and registry untouched).
#   pwsh -File spikes\film-analysis\native-checks\compile-native-checks.ps1
$ErrorActionPreference = "Stop"
$env:MSYSTEM = "UCRT64"
$env:CHERE_INVOKING = "1"
$here = (Resolve-Path $PSScriptRoot).Path -replace "\\", "/"
& "C:\Users\tamla\msys64\usr\bin\bash.exe" -lc "bash '$here/compile-native-checks.sh'"
exit $LASTEXITCODE
