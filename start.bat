@echo off
setlocal EnableExtensions
title TimeTablePro - Starter
cd /d "%~dp0"

rem This file is the ENGINE. Normally you start the app with "Start TimeTablePro" (it runs this file with NO window
rem and writes everything below to logs\start.log). When TT_SILENT=1 (set by that launcher) this file never waits for a key.

rem ---------- ONLY ONE start AT A TIME
rem If the app is started twice (or two copies run together), each copy used to open its own browser tab.
rem Now the first copy takes a "lock" (a small folder); a second copy sees it and politely stops.
rem A lock older than 3 minutes (window closed half-way) is treated as old and removed.
set "TT_LOCK=%TEMP%\timetablepro_start.lock"
mkdir "%TT_LOCK%" 2>nul
if errorlevel 1 (
  powershell -NoProfile -Command "if (((Get-Date) - (Get-Item -LiteralPath '%TT_LOCK%').CreationTime).TotalMinutes -gt 3) { exit 0 } else { exit 1 }" >nul 2>nul
  if errorlevel 1 (
    echo.
    echo  TimeTablePro pehle se start ho raha hai - dobara click mat karo.
    echo  Thoda wait karo, browser khud khul jayega.
    echo  ^(Agar 3 minute baad bhi kuch na ho, to "Start TimeTablePro" phir se chalao.^)
    echo.
    if not defined TT_SILENT ping -n 7 127.0.0.1 >nul
    exit /b 0
  )
  rd "%TT_LOCK%" 2>nul
  mkdir "%TT_LOCK%" 2>nul
)

call :main
set "TT_EXIT=%errorlevel%"
rd "%TT_LOCK%" 2>nul
exit /b %TT_EXIT%


:main
echo.
echo  ==================================================
echo     TimeTablePro start ho raha hai...  (thoda wait)
echo  ==================================================
echo.

rem ---------- 0) settings file (.env) must exist
if not exist ".env" (
  echo  [!] ".env" file nahi mili.
  echo      Is file me Supabase ki keys hoti hain. Claude se help le lo.
  echo.
  rd "%TT_LOCK%" 2>nul
  if not defined TT_SILENT pause
  exit /b 1
)

rem ---------- 1) backend: Python environment (first time only) + packages
if not exist "backend\.venv\Scripts\python.exe" (
  echo  [1/4] Pehli baar setup: Python taiyaar ho raha hai - 2-3 minute lag sakte hain...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3.13 -m venv "backend\.venv" >nul 2>nul
    if not exist "backend\.venv\Scripts\python.exe" py -3 -m venv "backend\.venv"
  ) else (
    python -m venv "backend\.venv"
  )
  if not exist "backend\.venv\Scripts\python.exe" goto :fail_python
)
echo  [1/4] Backend packages check ho rahe hain...
"backend\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r "backend\requirements.txt"
if errorlevel 1 goto :fail_pip

rem ---------- 2) frontend packages
echo  [2/4] Frontend packages check ho rahe hain...
pushd "frontend"
call npm install --no-audit --no-fund --loglevel=error
set NPMERR=%errorlevel%
popd
if not "%NPMERR%"=="0" goto :fail_npm

rem ---------- 3) database update (only does something when a new phase added new tables)
echo  [3/4] Database check ho raha hai...
pushd "backend"
".venv\Scripts\python.exe" -m scripts.migrate
set DBERR=%errorlevel%
popd
if not "%DBERR%"=="0" goto :fail_db

rem ---------- 4) start whichever of the two servers is not running yet
set "BACK_UP="
set "FRONT_UP="
netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul && set "BACK_UP=1"
netstat -ano | findstr /R /C:":5173 .*LISTENING" >nul && set "FRONT_UP=1"

if defined BACK_UP if defined FRONT_UP (
  echo.
  echo  TimeTablePro pehle se chal raha hai. Browser khol raha hoon...
  if not defined TT_NO_BROWSER start "" "http://localhost:5173"
  if not defined TT_SILENT ping -n 4 127.0.0.1 >nul
  exit /b 0
)

echo  [4/4] Servers start ho rahe hain (peeche, bina kisi window ke)...
if not exist "%~dp0logs" mkdir "%~dp0logs"
rem both servers run HIDDEN (no window); everything they print goes to logs\backend.log and logs\frontend.log
rem (the helper itself is started with NUL as input and output, so the servers cannot inherit and lock this run's own log file)
if not defined BACK_UP powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start_hidden.ps1" -WorkDir "%~dp0backend" -Log "%~dp0logs\backend.log" -Cmd ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-server-header" <nul >nul 2>&1
if not defined FRONT_UP powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start_hidden.ps1" -WorkDir "%~dp0frontend" -Log "%~dp0logs\frontend.log" -Cmd "npm run dev" <nul >nul 2>&1

rem wait until both answer (max ~60 seconds)
set /a TRIES=0
:wait_loop
set /a TRIES+=1
curl -s -f -o nul "http://127.0.0.1:8000/api/health" && curl -s -f -o nul "http://127.0.0.1:5173/" && goto :ready
if %TRIES% GEQ 60 goto :not_ready
ping -n 2 127.0.0.1 >nul
goto :wait_loop

:ready
echo.
echo  ==================================================
echo   TimeTablePro chal gaya!
echo   Browser me khulega:  http://localhost:5173
echo   Band karne ke liye:  "Stop TimeTablePro" par double-click
echo   Koi dikkat ho to dekho:  logs\start.log, logs\backend.log, logs\frontend.log
echo  ==================================================
if not defined TT_NO_BROWSER start "" "http://localhost:5173"
if not defined TT_SILENT ping -n 5 127.0.0.1 >nul
exit /b 0

:not_ready
echo.
echo  [!] Servers 1 minute me ready nahi hue.
echo      Neeche dono log files ki aakhri lines hain - inme jo laal (error) likha ho use Claude ko dikhao.
echo      (Agar port 8000 ya 5173 kisi aur program ne le rakha ho, to pehle "Stop TimeTablePro" chalao.)
echo.
powershell -NoProfile -Command "foreach ($n in 'backend','frontend') { $f = '%~dp0logs\' + $n + '.log'; Write-Host (' ---- logs\' + $n + '.log ----'); if (Test-Path -LiteralPath $f) { Get-Content -LiteralPath $f -Tail 8 } else { Write-Host ' (file nahi bani)' } }"
echo.
rd "%TT_LOCK%" 2>nul
if not defined TT_SILENT pause
exit /b 1

:fail_python
echo.
echo  [!] Python nahi mila ya setup fail ho gaya. Python 3.13 install hona chahiye.
echo.
rd "%TT_LOCK%" 2>nul
if not defined TT_SILENT pause
exit /b 1

:fail_pip
echo.
echo  [!] Backend packages install nahi hue. Internet check karo, phir dobara "Start TimeTablePro" chalao.
echo.
rd "%TT_LOCK%" 2>nul
if not defined TT_SILENT pause
exit /b 1

:fail_npm
echo.
echo  [!] Frontend packages install nahi hue. Internet check karo, phir dobara "Start TimeTablePro" chalao.
echo.
rd "%TT_LOCK%" 2>nul
if not defined TT_SILENT pause
exit /b 1

:fail_db
echo.
echo  [!] Database se connect nahi ho paya. Internet aur .env ki SUPABASE_DB_URL check karo.
echo.
rd "%TT_LOCK%" 2>nul
if not defined TT_SILENT pause
exit /b 1
