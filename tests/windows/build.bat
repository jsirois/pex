@echo off

set here=%~dp0.

cl /nologo /Fo"%here%\win.obj" /Fe"%here%\win.exe" "%here%\win.c"

