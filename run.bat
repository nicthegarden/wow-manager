@echo off
rem WoW Realmlist Manager - Windows launcher (Tk version, stock Python, no extra deps).
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 realmlist_manager.py
) else (
    python realmlist_manager.py
)
if errorlevel 1 pause
