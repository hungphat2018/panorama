@echo off
rem Runs perf1 with the portable MSYS2 UCRT64 runtime DLLs and the self-built LGPL FFmpeg / libplacebo DLLs on PATH
rem for this process only (nothing is installed, the user PATH and registry are not touched).
rem   bin\run.cmd perf1-lgpl.exe --probe
setlocal
set "PATH=%~dp0;C:\Users\tamla\msys64\home\tamla\p0\prefix\ffmpeg-lgpl\bin;C:\Users\tamla\msys64\home\tamla\p0\prefix\libplacebo-master\bin;C:\Users\tamla\msys64\ucrt64\bin;%PATH%"
set "EXE=%~1"
set "ARGS="
for /f "tokens=1,* delims= " %%a in ("%*") do set "ARGS=%%b"
"%~dp0%EXE%" %ARGS%
exit /b %ERRORLEVEL%
