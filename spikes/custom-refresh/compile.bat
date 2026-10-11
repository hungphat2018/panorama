@echo off
rem Compile custom-refresh-mode.exe with MSVC (VS 2022). NVAPI headers in nvapi\ come from
rem https://github.com/NVIDIA/nvapi (MIT, see nvapi\License.txt); the DLL is loaded from the driver at run time.
setlocal
set HERE=%~dp0
call "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat" -arch=amd64 -host_arch=amd64 -no_logo >nul
if errorlevel 1 exit /b 1
cd /d "%HERE%"
if not exist bin mkdir bin
cl /nologo /std:c++17 /EHsc /O2 /W3 /utf-8 custom-refresh-mode.cpp /Fobin\ /Febin\custom-refresh-mode.exe
