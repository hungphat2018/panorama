@echo off
rem Compile the per-monitor spikes with MSVC (VS 2022): per-monitor-presenter.exe and seam-tuner.exe in bin\.
setlocal
set HERE=%~dp0
call "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat" -arch=amd64 -host_arch=amd64 -no_logo >nul
if errorlevel 1 exit /b 1
cd /d "%HERE%"
if not exist bin mkdir bin
cl /nologo /std:c++17 /EHsc /O2 /W3 /utf-8 per-monitor-presenter.cpp /Fobin\ /Febin\per-monitor-presenter.exe || exit /b 1
cl /nologo /std:c++17 /EHsc /O2 /W3 /utf-8 seam-tuner.cpp /Fobin\ /Febin\seam-tuner.exe || exit /b 1
