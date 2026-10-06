@echo off
setlocal EnableExtensions
title TimeTablePro - Stop
set "TT_ROOT=%~dp0"

rem This file is the ENGINE. Normally you stop the app with "Stop TimeTablePro" (it runs this file with NO window
rem and writes everything below to logs\stop.log).
echo.
echo  TimeTablePro band ho raha hai...

rem 1) stop whatever is listening on the backend (8000) and frontend (5173) ports - including its child programs
for %%P in (8000 5173) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:":%%P .*LISTENING"') do (
    taskkill /F /T /PID %%A >nul 2>nul
  )
)

rem 2) the servers run hidden (no windows). Also stop any hidden piece of THIS project that never reached its port
rem    (for example a server that was still starting up when it was stopped)
powershell -NoProfile -Command "$root = $env:TT_ROOT; Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.Name -match '^(cmd|python|node)\.exe$' -and ($_.CommandLine -like ('*' + $root + '*') -or $_.ExecutablePath -like ($root + '*')) -and $_.CommandLine -match 'uvicorn|vite|npm' } | ForEach-Object { & taskkill /F /T /PID $_.ProcessId *> $null }" >nul 2>nul

rem 3) older versions opened two visible black windows - close them too if they are still around
taskkill /F /FI "WINDOWTITLE eq TimeTablePro BACKEND*" >nul 2>nul
taskkill /F /FI "WINDOWTITLE eq TimeTablePro FRONTEND*" >nul 2>nul

echo  Ho gaya. TimeTablePro band hai.
echo.
if not defined TT_SILENT ping -n 4 127.0.0.1 >nul
exit /b 0
