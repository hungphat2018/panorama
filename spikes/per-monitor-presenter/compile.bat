@echo off
rem Compile the per-monitor spikes with MSVC (VS 2022) into bin\.
rem   compile.bat             every target
rem   compile.bat presenter   per-monitor-presenter.exe only (other targets: tuner, probe)
setlocal
set HERE=%~dp0
set TARGET=%~1
if "%TARGET%"=="" set TARGET=all
call "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat" -arch=amd64 -host_arch=amd64 -no_logo >nul
if errorlevel 1 exit /b 1
cd /d "%HERE%"
if not exist bin mkdir bin
set CL_FLAGS=/nologo /std:c++17 /EHsc /O2 /W3 /utf-8
if "%TARGET%"=="all" goto presenter
if "%TARGET%"=="presenter" goto presenter
if "%TARGET%"=="tuner" goto tuner
if "%TARGET%"=="probe" goto probe
echo unknown target "%TARGET%": use presenter, tuner, probe or nothing for all
exit /b 2

:presenter
cl %CL_FLAGS% per-monitor-presenter.cpp /Fobin\ /Febin\per-monitor-presenter.exe || exit /b 1
if not "%TARGET%"=="all" exit /b 0

:tuner
cl %CL_FLAGS% seam-tuner.cpp seam-render.cpp seam-settings.cpp seam-free-session.cpp seam-x2-session.cpp seam-calibration-session.cpp seam-self-check.cpp /Fobin\ /Febin\seam-tuner.exe || exit /b 1
if not "%TARGET%"=="all" exit /b 0

:probe
cl %CL_FLAGS% scanline-probe.cpp /Fobin\ /Febin\scanline-probe.exe || exit /b 1
exit /b 0
