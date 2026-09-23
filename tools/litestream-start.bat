@echo off
REM Litestream auto-start untuk UMAR ERP backup ke Cloudflare R2.
REM Dipicu Windows Task Scheduler saat user login.
REM Log dirotasi harian YYYYMMDD (locale-safe via PowerShell Get-Date).

set BASEDIR=C:\09 UGS2\SC ERP\ERP UMAR
set LITESTREAM=%BASEDIR%\tools\litestream.exe
set CONFIG=%BASEDIR%\litestream.yml
set LOGDIR=%BASEDIR%\logs

for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"`) do set TODAY=%%i
set LOGFILE=%LOGDIR%\litestream-%TODAY%.log

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d "%BASEDIR%"
"%LITESTREAM%" replicate -config "%CONFIG%" >> "%LOGFILE%" 2>&1
