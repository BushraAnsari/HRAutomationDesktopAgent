@echo off
cd /d %~dp0
REM pythonw.exe, not python.exe -- the windowless variant, so this never
REM pops a visible console window on login. python.exe (which you've been
REM using to test manually so you CAN see the logs/errors) still works
REM fine if you ever want to run this by hand again -- pythonw.exe is only
REM for the silent, auto-started case.
venv\Scripts\pythonw.exe -m agent.main
