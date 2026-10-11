@echo off
rem Runs one of the executables in this folder with the portable MSYS2 UCRT64 runtime DLLs on PATH
rem for this process only (nothing is installed, the user PATH and registry are not touched).
rem   bin\run.cmd perf0-master.exe --probe
rem   bin\run.cmd plplay-master.exe -w glfw-d3d11 "C:\path\to\video.mkv"
setlocal
set "PATH=%~dp0;C:\Users\tamla\msys64\ucrt64\bin;%PATH%"
set "EXE=%~1"
set "ARGS="
for /f "tokens=1,* delims= " %%a in ("%*") do set "ARGS=%%b"
"%~dp0%EXE%" %ARGS%
exit /b %ERRORLEVEL%
