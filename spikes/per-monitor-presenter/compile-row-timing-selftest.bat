@echo off
rem Compile row-timing-selftest.c with MSVC (VS 2022) as C++17, the way the presenter uses row-timing.h, and run it.
rem The same file builds as C11 with gcc for perf1; see the command in its header comment.
rem Needs no display and no window. Exit code 0 = every check passed.
setlocal
set HERE=%~dp0
call "C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat" -arch=amd64 -host_arch=amd64 -no_logo >nul
if errorlevel 1 exit /b 1
cd /d "%HERE%"
if not exist bin mkdir bin
cl /nologo /std:c++17 /EHsc /O2 /W3 /utf-8 /TP row-timing-selftest.c /Fobin\ /Febin\row-timing-selftest.exe /link user32.lib || exit /b 1
bin\row-timing-selftest.exe
exit /b %ERRORLEVEL%
