@echo off
rem Runs one of the small C programs in bin\ with the performer's LGPL FFmpeg DLLs and the portable MSYS2 runtime on PATH
rem for this process only (same recipe as spikes\performer-p1\bin\run.cmd; nothing installed, PATH and registry untouched).
rem   run-native-check.cmd frame-times.exe FILE [--hw d3d11va] [--threads N]
rem   run-native-check.cmd dovi-probe.exe FILE [--max N] [--keyframes]
setlocal
set "PATH=%~dp0bin;C:\Users\tamla\msys64\home\tamla\p0\prefix\ffmpeg-lgpl\bin;C:\Users\tamla\msys64\ucrt64\bin;%PATH%"
set "EXE=%~1"
set "ARGS="
for /f "tokens=1,* delims= " %%a in ("%*") do set "ARGS=%%b"
"%~dp0bin\%EXE%" %ARGS%
exit /b %ERRORLEVEL%
