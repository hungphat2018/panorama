@echo off
rem Runs a finish program with the portable MSYS2 UCRT64 runtime DLLs and the self-built libplacebo DLL on PATH for this
rem process only (nothing is installed, the user PATH and registry are not touched).
rem   run.cmd finish-test.exe smoke --out out\smoke
rem   run.cmd finish-unit.exe
setlocal
set "PATH=C:\Users\tamla\msys64\home\tamla\p0\prefix\libplacebo-master\bin;C:\Users\tamla\msys64\ucrt64\bin;%PATH%"
set "EXE=%~1"
set "ARGS="
for /f "tokens=1,* delims= " %%a in ("%*") do set "ARGS=%%b"
"%~dp0bin\%EXE%" %ARGS%
exit /b %ERRORLEVEL%
